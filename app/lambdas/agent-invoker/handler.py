"""Agent Invoker Lambda — called by AWS Step Functions.

Services: AWS Step Functions, Amazon S3, Amazon Bedrock, Amazon DynamoDB.

Invokes an agent via AgentCore Runtime (AG-UI protocol) and forwards each
AG-UI event to WebSocket clients. Pure passthrough — no parsing, no escaping.

The agent emits standard AG-UI events:
  RUN_STARTED, TEXT_MESSAGE_START, TEXT_MESSAGE_CONTENT (with delta),
  TEXT_MESSAGE_END, TOOL_CALL_START, TOOL_CALL_ARGS, TOOL_CALL_END,
  TOOL_CALL_RESULT, STATE_DELTA, RUN_FINISHED.
"""

import os
import re
import json
import time
import uuid
import queue
import threading
import boto3
import yaml

# AWS Step Functions, Amazon Bedrock, and Amazon S3 configuration
# Note: These are non-sensitive resource identifiers, not secrets.
# Secrets (API keys, client secrets) are retrieved via AgentCore Identity Token Vault.
AGENT_RUNTIME_ARN = os.environ["TESSERA_AGENT_RUNTIME_ARN"]
CONNECTIONS_TABLE = os.environ["CONNECTIONS_TABLE"]
WS_ENDPOINT = os.environ["WS_ENDPOINT"]
REGION = os.environ.get("AWS_REGION", "us-east-1")
RUNS_BUCKET = os.environ.get("TESSERA_RUNS_BUCKET", "")

# Stream-silence heartbeat — see docs/features/stream-silence-heartbeat.md.
# If the SSE stream from AgentCore Runtime goes silent for longer than this many
# seconds, the agent is considered stuck and we raise AgentStreamSilenceError
# with structured context.
#
# History: initial value was 90s based on a theoretical "longest plausible
# single Bedrock turn" analysis. That value was empirically too tight — real
# Bedrock turns under load (mid-TOOL_CALL_ARGS generation, throttled responses,
# heavy model thinking) routinely pause longer than 90s and recover. The 90s
# threshold stopped legitimate recoverable pauses on a system that previously
# worked. Bumped to 240s on 2026-05-19 after observing agent durations of
# 155-282s in healthy runs of buyerside on Mount Colah. 240s still gives a
# 1.25x diagnostic speed-up over the legacy 300s SFN-only timeout while keeping
# zero behavioral regression on long-but-recovering Bedrock turns.
#
# Override via TESSERA_STREAM_SILENCE_SECONDS env if real-world data demands
# tighter or looser values per environment.
STREAM_SILENCE_SECONDS = float(os.environ.get("TESSERA_STREAM_SILENCE_SECONDS", "420"))
# How often the consumer wakes to check for silence even when no events arrive.
_SILENCE_POLL_SECONDS = 5.0


class AgentStreamSilenceError(RuntimeError):
    """Raised when the AgentCore Runtime SSE stream goes silent for longer than
    STREAM_SILENCE_SECONDS — indicates the agent is unresponsive (LLM stalled, infinite
    tool loop, deadlock, unresponsive Bedrock).

    Carries structured context (agent_id, last_activity, silent_for_seconds)
    so operators can debug WHICH agent is stuck and WHAT it was doing — vs.
    the legacy bare States.Timeout that conveyed nothing.
    """
    def __init__(self, agent_id: str, last_activity: str, silent_for_seconds: float):
        self.agent_id = agent_id
        self.last_activity = last_activity
        self.silent_for_seconds = silent_for_seconds
        super().__init__(
            f"Agent {agent_id!r} stuck after last_activity={last_activity!r} — "
            f"no SSE events for {silent_for_seconds:.1f}s "
            f"(threshold={STREAM_SILENCE_SECONDS}s). "
            f"Likely cause: LLM unresponsive mid-turn, tool loop stuck, or "
            f"unresponsive Bedrock stream."
        )


# AgentCore Runtime streams SSE for the duration of an agent invocation. Tool calls
# (web_search, code_interpreter, etc.) introduce gaps between chunks that can easily
# exceed boto3's default 60s read timeout. We set the read timeout as a generous
# absolute backstop (matches new Lambda timeout); stream-silence detection above
# fires first for any actually-stuck agent. Connect timeout stays tight.
from botocore.config import Config as _BotoConfig
_AGENTCORE_CFG = _BotoConfig(
    connect_timeout=10,
    read_timeout=890,           # just under new Lambda timeout (900s); silence detection fires first at 90s
    retries={"max_attempts": 1},  # streaming SSE — don't auto-retry mid-stream
)
# Amazon Bedrock Agent Runtime client
agentcore = boto3.client("bedrock-agentcore", region_name=REGION, config=_AGENTCORE_CFG)
# AWS Step Functions client
sfn = boto3.client("stepfunctions", region_name=REGION)
dynamodb = boto3.resource("dynamodb", region_name=REGION)
connections_table = dynamodb.Table(CONNECTIONS_TABLE)
ATTENTION_TABLE = os.environ.get("ATTENTION_TABLE", "")
attention_table = dynamodb.Table(ATTENTION_TABLE) if ATTENTION_TABLE else None
ATTENTION_TOPIC_ARN = os.environ.get("ATTENTION_TOPIC_ARN", "")
_sns = boto3.client("sns", region_name=REGION) if ATTENTION_TOPIC_ARN else None
# Amazon S3 client
s3 = boto3.client("s3", region_name=REGION)


def _write_attention_item(owner_id, session_id, agent_id, item_type, summary, deadline_epoch, context_ref):
    """Write an AttentionItem so a blocked conversation shows in the user's
    async inbox. Best-effort — never breaks the run if the table is missing.
    `ttl` is a far-future housekeeping value (90d), NOT the deadline, so an
    awaiting item is never silently auto-deleted."""
    if attention_table is None:
        return
    import time as _t
    now = int(_t.time())
    try:
        attention_table.put_item(Item={
            "ownerId": owner_id or "unassigned",
            "itemId": f"{session_id}#{agent_id}",
            "session_id": session_id,
            "agent_id": agent_id,
            "type": item_type,
            "summary": summary[:500],
            "created_at": now,
            "deadline": int(deadline_epoch) if deadline_epoch else 0,
            "status": "awaiting",
            "context_ref": context_ref,
            "ttl": now + 90 * 24 * 3600,
        })
    except Exception:
        pass
    # Out-of-app notification (best-effort) so an away operator is told.
    if _sns and ATTENTION_TOPIC_ARN:
        try:
            _sns.publish(
                TopicArn=ATTENTION_TOPIC_ARN,
                Subject=f"Tessera: {item_type} needs you ({session_id})"[:99],
                Message=f"{summary}\n\nSession: {session_id}\nAgent: {agent_id}\nOpen the Attention inbox to respond.",
            )
        except Exception:
            pass


# Per-level checkpoint (challenger:per_level) verdict ranking. A checkpoint
# opens a human gate when the verdict ranks below the configured block_below.
_CHECKPOINT_VERDICT_RANK = {"WEAK": 0, "ADEQUATE": 1, "STRONG": 2}


def _safe(s, n=128):
    return re.sub(r"[^A-Za-z0-9_-]", "-", s or "")[:n]


# Matches `tessera-screenshot://<agent_id>/<filename>` reference tokens that a
# drafter embeds in a deliverable (e.g. <img src="tessera-screenshot://webapp_explorer/screenshot-01-login.jpg">).
# The browser tool emits these instead of raw base64 so image bytes never pass
# through the model; the invoker resolves them to self-contained data: URIs here.
_SCREENSHOT_REF_RE = re.compile(r"tessera-screenshot://([A-Za-z0-9_-]+)/([A-Za-z0-9_.\-]+)")

_IMG_EXT_MIME = {
    ".jpg": "image/jpeg", ".jpeg": "image/jpeg", ".png": "image/png",
    ".gif": "image/gif", ".webp": "image/webp", ".svg": "image/svg+xml",
}

# Operator-uploaded evidence documents are cited in deliverables via
# `tessera-doc://<filename>` tokens (the model never sees an S3 URL). At save time we
# swap each into a presigned S3 URL to the uploaded input at `<session_id>/inputs/<file>`
# (the presign-upload key) so the citation OPENS THE ACTUAL SOURCE DOC from the memo's
# authed iframe — presigned URLs carry their own auth, so no app token is needed.
_DOC_REF_RE = re.compile(r"tessera-doc://([A-Za-z0-9_.\-]+)")
_DOC_PRESIGN_TTL = 7 * 24 * 3600  # 7 days (SigV4 presigned-URL maximum)
_DOC_MIME = {
    ".pdf": "application/pdf",
    ".txt": "text/plain; charset=utf-8", ".md": "text/markdown; charset=utf-8",
    ".csv": "text/csv; charset=utf-8", ".json": "application/json; charset=utf-8",
    ".html": "text/html; charset=utf-8",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
}


def _swap_doc_refs(content: str, session_id: str) -> str:
    """Turn citations of operator-uploaded evidence documents into presigned links that open
    the actual file. Matches BOTH `tessera-doc://<file>` tokens AND bare `href="<file>"`
    where <file> is a real uploaded input (the model often writes the plain filename), so it
    works regardless of how the citation was authored. Presigned inline URL -> opens the PDF
    in a new tab (works from the authed iframe; no app token needed). Unmatched refs are left
    as-is (never a broken rewrite)."""
    if not content or not RUNS_BUCKET:
        return content
    # Resolve this run's uploaded inputs from the launch-written manifest
    # runs/<sid>/_attachments.json = [{name, ref(s3Uri)}]. The files live under the UPLOAD
    # draft prefix (draft-XXX/inputs/), not the run prefix, so we must use the recorded ref.
    inputs = {}  # filename -> (bucket, key)
    try:
        obj = s3.get_object(Bucket=RUNS_BUCKET, Key=f"runs/{_safe(session_id)}/_attachments.json")
        for a in (json.loads(obj["Body"].read()) or []):
            name = (a.get("name") or "").strip()
            ref = a.get("ref") or a.get("content") or ""
            if name and isinstance(ref, str) and ref.startswith("s3://"):
                b, _, k = ref[5:].partition("/")
                inputs[name] = (b, k)
    except Exception as e:
        print(f"WARN: no _attachments.json for {session_id}: {type(e).__name__}")
    if not inputs or ("tessera-doc://" not in content and not any(fn in content for fn in inputs)):
        return content

    presigned: dict = {}

    def _url_for(fname):
        if fname in presigned:
            return presigned[fname]
        url = None
        if fname in inputs:
            bkt, key = inputs[fname]
            try:
                ext = fname[fname.rfind("."):].lower() if "." in fname else ""
                url = s3.generate_presigned_url(
                    "get_object",
                    Params={"Bucket": bkt, "Key": key,
                            "ResponseContentType": _DOC_MIME.get(ext, "application/octet-stream"),
                            "ResponseContentDisposition": f'inline; filename="{fname}"'},
                    ExpiresIn=_DOC_PRESIGN_TTL,
                ).replace("&", "&amp;")
            except Exception as e:
                print(f"WARN: could not presign {key}: {type(e).__name__}: {e}")
        presigned[fname] = url
        return url

    # 1) href="tessera-doc://<file>"  and  2) bare href="<file>" for a real uploaded input.
    def _sub_href(m):
        raw = m.group(1)
        fname = raw[len("tessera-doc://"):] if raw.startswith("tessera-doc://") else raw
        fname = fname.rsplit("/", 1)[-1]
        if fname in inputs:
            u = _url_for(fname)
            if u:
                return f'href="{u}"'
        return m.group(0)

    content = re.sub(r'href="((?:tessera-doc://)?[^"]+)"', _sub_href, content)
    # Any remaining tessera-doc:// tokens NOT inside an href (rare) -> presigned or filename.
    content = _DOC_REF_RE.sub(lambda m: _url_for(m.group(1)) or m.group(1), content)
    return content


# Scroll-to-Text-Fragment deep links (spec: paragraph-level citations). The composer tags an
# authority link with data-quote="<verbatim source phrase>"; here we deterministically build
# `uri#:~:text=<encoded phrase>` so a click lands on + highlights that passage. Encoding is done
# in code (not the model) so it's always correct. HTML targets only — PDFs/#page and
# already-fragmented hrefs are left alone; unmatched text simply degrades to the page top.
_A_QUOTE_TAG_RE = re.compile(r'<a\b[^>]*\bdata-quote="[^"]*"[^>]*>', re.I)


def _text_fragment(quote: str) -> str:
    """Build the `#:~:text=` value from a verbatim phrase. Short phrase -> textStart; long ->
    textStart,textEnd (first/last few words) which the spec matches across element boundaries.
    Each part percent-encoded (dash/comma too); the start,end comma stays literal."""
    from urllib.parse import quote as _q
    words = (quote or "").split()
    if not words:
        return ""
    if len(words) <= 12:
        return _q(" ".join(words), safe="")
    return _q(" ".join(words[:5]), safe="") + "," + _q(" ".join(words[-5:]), safe="")


def _apply_text_fragments(content: str) -> str:
    import html as _html
    if not content or "data-quote=" not in content:
        return content

    def _fix(m):
        tag = m.group(0)
        qm = re.search(r'data-quote="([^"]*)"', tag, re.I)
        hm = re.search(r'href="([^"]*)"', tag, re.I)
        quote = _html.unescape(qm.group(1)).strip() if qm else ""
        tag = re.sub(r'\sdata-quote="[^"]*"', "", tag)   # always strip the helper attr
        if hm and quote:
            href = hm.group(1)
            low = href.lower()
            if (low.startswith(("http://", "https://")) and not low.endswith(".pdf")
                    and "#" not in href):
                frag = _text_fragment(quote)
                if frag:
                    tag = tag.replace(f'href="{href}"', f'href="{href}#:~:text={frag}"', 1)
        # text fragments require a top-level (new-tab) main-frame nav + noopener cross-origin
        if " target=" not in tag.lower():
            tag = tag[:-1] + ' target="_blank">'
        if " rel=" not in tag.lower():
            tag = tag[:-1] + ' rel="noopener">'
        return tag

    return _A_QUOTE_TAG_RE.sub(_fix, content)


# ── Deterministic citation resolution (spec: cite-by-id, model never emits the URL) ──
# The composer cites an authority as `<a href="tessera-cite://<type>/<id>">…</a>` — it never
# writes a URL (URLs are unreliable for LLMs). At save time we resolve the id to the REAL
# source URL the upstream lenses actually cited this run. Lenses emit the universal marker
# `[catalog: <type>/<id> — <uri>]` (from the catalog record's `uri`), so the map is built
# from the run's own agent outputs — general across every eco, no hardcoding. An unresolvable
# id degrades to PLAIN TEXT (link dropped) — never a fabricated/broken link.
_CATALOG_MARK_RE = re.compile(
    r"\[catalog:\s*([A-Za-z0-9][A-Za-z0-9._/\-]*?)\s*[\u2014\u2013-]+\s*(https?://[^\]\s]+?)\s*\]")
_CITE_A_RE = re.compile(r'<a\b([^>]*?)href="tessera-cite://([^"]+)"([^>]*)>(.*?)</a>', re.I | re.S)


def _apply_house_style(content: str, agent_config: dict) -> str:
    """Beautify Phase 4: if the deliverable agent's output_format declares a promoted
    template, inject its CSS override stylesheet LAST in <head> so future runs adopt
    the beautified look. Deterministic — no model reliance. A no-op if the template
    has no CSS (never breaks the deliverable)."""
    of = (agent_config or {}).get("output_format") or {}
    tid = of.get("template_id")
    bucket = os.environ.get("ECOSYSTEMS_BUCKET", "")
    if not tid or not bucket:
        return content
    try:
        css = s3.get_object(Bucket=bucket, Key=f"templates/{tid}/template.css")["Body"].read().decode("utf-8")
    except Exception:
        return content  # no CSS template (e.g. an HTML code_interpreter template) → skip
    if not css.strip():
        return content
    style = f'<style id="tessera-house-style">\n{css}\n</style>'
    if re.search(r"</head>", content, re.I):
        return re.sub(r"</head>", style + "</head>", content, count=1, flags=re.I)
    return style + content


def _harvest_catalog_uris(session_id: str) -> dict:
    """Scan the run's agent outputs for `[catalog: <type>/<id> — <uri>]` markers and build
    {full_id: uri} plus {bare_id: uri} (bare = segment after the last '/') for fuzzy match."""
    out = {}
    if not RUNS_BUCKET:
        return out
    try:
        tok = None
        while True:
            kw = {"Bucket": RUNS_BUCKET, "Prefix": f"runs/{_safe(session_id)}/", "MaxKeys": 1000}
            if tok:
                kw["ContinuationToken"] = tok
            r = s3.list_objects_v2(**kw)
            for o in r.get("Contents", []):
                if not o["Key"].endswith("/output.txt"):
                    continue
                try:
                    txt = s3.get_object(Bucket=RUNS_BUCKET, Key=o["Key"])["Body"].read().decode("utf-8", "replace")
                except Exception:
                    continue
                for cid, uri in _CATALOG_MARK_RE.findall(txt):
                    cid = cid.strip().rstrip(".,;")
                    out.setdefault(cid, uri)
                    out.setdefault(cid.rsplit("/", 1)[-1], uri)  # bare id fallback
            tok = r.get("NextContinuationToken")
            if not tok:
                break
    except Exception as e:
        print(f"WARN: harvest catalog uris failed for {session_id}: {type(e).__name__}: {e}")
    return out


# Option 3 — deterministic authority-NAME linking. The model reliably NAMES coded
# authorities ("CPS 230", "RG 271", "APP 11") but often won't wrap them in a citation id.
# So at save time we link those names against the run's OWN harvested uris, via a GENERIC
# code-token derived from each cited id (NO per-authority table). Safe by construction:
# only tokens the run actually cited, only when a token maps to exactly ONE uri, only exact
# word-boundary matches in visible HTML text (never inside anchors / tags / script / style).
# Self-scoping: an eco that cited no catalog authorities → empty map → complete no-op.
_AUTH_CODE_RE = re.compile(r"\b([A-Za-z]{2,6})[ \-]?(\d{1,4}(?:[.\-]\d{1,3})?)\b")
_HTML_TAG_SPLIT_RE = re.compile(r"(<[^>]+>)")
_RAW_OPEN_RE = re.compile(r"<(script|style|head|title)\b", re.I)
_RAW_CLOSE_RE = re.compile(r"</(script|style|head|title)\s*>", re.I)


def _authority_token(seg: str):
    """Derive a normalized code token (e.g. 'CPS 230') from an id segment, or None."""
    m = _AUTH_CODE_RE.search(seg or "")
    if not m:
        return None
    return f"{m.group(1).upper()} {m.group(2)}"


def _build_authority_token_map(uris: dict) -> dict:
    """token -> uri, derived from FULL ids only, keeping tokens that map to exactly ONE uri."""
    tok_uris: dict = {}
    for cid, uri in uris.items():
        if "/" not in cid:   # skip the bare-id duplicate keys
            continue
        tok = _authority_token(cid.rsplit("/", 1)[-1])
        if tok:
            tok_uris.setdefault(tok, set()).add(uri)
    return {t: next(iter(us)) for t, us in tok_uris.items() if len(us) == 1}


def _link_authority_names(content: str, uris: dict) -> str:
    """Wrap plain-text authority code mentions in <a href=uri> using the single-uri token map.
    Operates ONLY on visible HTML text nodes — never inside <a>, <script>, <style>, or tags."""
    tok_map = _build_authority_token_map(uris)
    if not tok_map:
        return content
    patterns = []
    for tok, uri in tok_map.items():
        letters, num = tok.split(" ", 1)
        patterns.append((re.compile(rf"\b{re.escape(letters)}[ \-]?{re.escape(num)}\b"), uri))
    out, depth_a, depth_raw = [], 0, 0
    for part in _HTML_TAG_SPLIT_RE.split(content):
        if not part:
            continue
        if part.startswith("<") and part.endswith(">"):
            low = part.lower()
            if _RAW_OPEN_RE.match(low):
                depth_raw += 1
            elif _RAW_CLOSE_RE.match(low):
                depth_raw = max(0, depth_raw - 1)
            elif low.startswith("<a") and not low.startswith("</a"):
                depth_a += 1
            elif low.startswith("</a"):
                depth_a = max(0, depth_a - 1)
            out.append(part)
        elif depth_a > 0 or depth_raw > 0:
            out.append(part)  # inside an anchor / script / style — leave untouched
        else:
            seg = part
            for pat, uri in patterns:
                seg = pat.sub(lambda mm, u=uri: f'<a href="{u}" target="_blank" rel="noopener">{mm.group(0)}</a>', seg)
            out.append(seg)
    return "".join(out)


def _resolve_citations(content: str, session_id: str) -> str:
    """Turn authority citations into verified links, deterministically, from THIS run's own
    `[catalog: id — uri]` markers:
      (1) resolve `tessera-cite://<type>/<id>` anchors the model emitted → the record's uri;
      (2) link plain-text authority code names ("CPS 230") the model DIDN'T wrap → same uris.
    Both are no-ops when the run cited no catalog authorities (self-scoping across ecos).
    Runs BEFORE _apply_text_fragments so a resolved anchor's data-quote still becomes #:~:text=."""
    if not content:
        return content
    has_cite = "tessera-cite://" in content
    low = content.lower()
    looks_html = ("<a " in low or "</p>" in low or "<li" in low or "<td" in low or "<div" in low or "<h" in low)
    if not has_cite and not looks_html:
        return content
    uris = _harvest_catalog_uris(session_id)
    if not uris:
        return content

    def _fix(m):
        pre, cid, post, inner = m.group(1), m.group(2).strip(), m.group(3), m.group(4)
        uri = uris.get(cid) or uris.get(cid.rsplit("/", 1)[-1])
        if not uri:
            return inner  # unknown citation -> plain text (never a broken link)
        attrs = pre + post   # preserves any data-quote (-> text fragment downstream)
        if " target=" not in attrs.lower():
            attrs += ' target="_blank"'
        if " rel=" not in attrs.lower():
            attrs += ' rel="noopener"'
        return f'<a href="{uri}"{attrs}>{inner}</a>'

    if has_cite:
        content = _CITE_A_RE.sub(_fix, content)
    if looks_html:
        content = _link_authority_names(content, uris)
    return content


# ── Deterministic decision engine (spec-decision-engine.md) ─────────────────────
# A scoring agent (its agent_config carries a `decision` block, threaded by the compiler)
# emits a `## DECISION ITEMS` fenced-JSON block of items with dimension VALUES + justification
# + evidence — but NOT the final label/verdict. Here we parse it, run the domain-blind engine,
# and attach the COMPUTED labels/outcome/trace to the result so the deliverable agent renders
# them verbatim. Deterministic: same scored inputs -> same labels + verdict.
_DECISION_BLOCK_RE = re.compile(r"##\s*DECISION\s+ITEMS\s*\n+```(?:json)?\s*(.*?)```", re.S | re.I)


def _parse_decision_items(text: str):
    m = _DECISION_BLOCK_RE.search(text or "")
    if not m:
        m = re.search(r"```(?:json)?\s*(\[.*?\])\s*```", text or "", re.S)  # fallback: first JSON array
    if not m:
        return None
    try:
        data = json.loads(m.group(1))
    except Exception:
        return None
    return data if isinstance(data, list) else None


def _evaluate_decision(agent_config: dict, output: str, session_id: str, agent_id: str):
    """If this agent declares a `decision` block, compute labels/outcome/trace from the items
    it emitted. Returns the decision result dict, or None (never raises into the run)."""
    cfg = (agent_config or {}).get("decision")
    if not cfg:
        return None
    items = _parse_decision_items(output)
    if not items:
        print(f"WARN: agent {agent_id} declares `decision` but emitted no parseable DECISION ITEMS block")
        return None
    try:
        from tessera_platform.decision import evaluate as _dec_eval
        result = _dec_eval(cfg, items)
    except Exception as e:
        print(f"WARN: decision evaluate failed for {agent_id}: {type(e).__name__}: {e}")
        return None
    if RUNS_BUCKET:
        try:
            s3.put_object(
                Bucket=RUNS_BUCKET,
                Key=f"runs/{_safe(session_id)}/{_safe(agent_id, 64)}/decision.json",
                Body=json.dumps(result, default=str).encode(), ContentType="application/json")
        except Exception as e:
            print(f"WARN: failed to persist decision.json for {agent_id}: {e}")
    print(f"[decision] {agent_id}: outcome={result.get('outcome')} "
          f"items={len(result.get('items', []))} borderline={len(result.get('borderline', []))}")
    return result


def _render_decision_context(dep_id: str, decision: dict) -> str:
    """Render a computed decision as an authoritative block for the deliverable agent's context."""
    ot = decision.get("outcome_trace") or {}
    lines = [f"[{dep_id} | COMPUTED DECISION — render VERBATIM; do not re-derive]",
             f"OUTCOME: {decision.get('outcome')}  (rule: {ot.get('rule')}; label_counts={ot.get('label_counts')})"]
    for it in decision.get("items", []):
        flag = " [BORDERLINE]" if it.get("borderline") else ""
        lines.append(f"  - {it.get('id')}: {str(it.get('label','')).upper()} — "
                     f"{it.get('title') or ''} [{it.get('rule')}]{flag}")
    conds = decision.get("conditions") or []
    if conds:
        lines.append("CONDITIONS (open, from labels): " + ", ".join(str(c.get("id")) for c in conds))
    return "\n".join(lines)


def _inline_screenshot_refs(content: str, session_id: str) -> str:
    """Replace every `tessera-screenshot://<agent>/<file>` token in `content` with
    a base64 `data:` URI fetched from the run's S3 prefix, making the deliverable
    fully self-contained. The model often paraphrases the filename in a ref (e.g.
    `screenshot-03-hedge.jpg` for the real `screenshot-02-hedge-fund-...jpg`), so an
    exact-key miss falls back to fuzzy resolution against the producer's actual
    files (by the `screenshot-NN` index, then keyword overlap). Best-effort: an
    unresolvable ref degrades to an empty data URI (alt text) and is logged."""
    import base64 as _b64
    if not content or "tessera-screenshot://" not in content or not RUNS_BUCKET:
        return content
    cache: dict = {}
    listing: dict = {}  # producer -> [filenames]

    def _list_producer(producer):
        if producer in listing:
            return listing[producer]
        prefix = f"runs/{_safe(session_id)}/{_safe(producer, 64)}/"
        files = []
        try:
            token = None
            while True:
                kw = {"Bucket": RUNS_BUCKET, "Prefix": prefix}
                if token:
                    kw["ContinuationToken"] = token
                resp = s3.list_objects_v2(**kw)
                for o in resp.get("Contents", []):
                    fn = o["Key"].rsplit("/", 1)[-1]
                    if fn and fn.rsplit(".", 1)[-1].lower() in ("jpg", "jpeg", "png", "gif", "webp", "svg"):
                        files.append(fn)
                if resp.get("IsTruncated"):
                    token = resp.get("NextContinuationToken")
                else:
                    break
        except Exception as e:
            print(f"WARN: could not list screenshots for {producer}: {e}")
        listing[producer] = files
        return files

    def _best_match(filename, files):
        if filename in files:
            return filename
        # match by leading screenshot-NN index (survives paraphrased tails)
        mnum = re.match(r"(screenshot-0*\d+)\b", filename)
        if mnum:
            idx = re.match(r"screenshot-0*(\d+)", filename).group(1)
            for f in files:
                fm = re.match(r"screenshot-0*(\d+)", f)
                if fm and fm.group(1) == idx:
                    return f
        # else keyword overlap on the filename tokens
        def toks(s):
            return set(re.findall(r"[a-z]{3,}", s.lower())) - {"screenshot", "jpg", "jpeg", "png", "tessera"}
        want = toks(filename)
        best, best_sc = None, 0
        for f in files:
            sc = len(toks(f) & want)
            if sc > best_sc:
                best, best_sc = f, sc
        return best if best_sc > 0 else None

    def _get(producer, fname):
        key = f"runs/{_safe(session_id)}/{_safe(producer, 64)}/{fname}"
        return s3.get_object(Bucket=RUNS_BUCKET, Key=key)["Body"].read()

    def _resolve(m):
        producer, filename = m.group(1), m.group(2)
        cache_key = f"{producer}/{filename}"
        if cache_key in cache:
            return cache[cache_key]
        ext = filename[filename.rfind("."):].lower() if "." in filename else ".jpg"
        mime = _IMG_EXT_MIME.get(ext, "image/jpeg")
        raw = None
        try:
            raw = _get(producer, filename)  # exact
        except Exception:
            match = _best_match(filename, _list_producer(producer))  # fuzzy
            if match:
                try:
                    raw = _get(producer, match)
                    mext = match[match.rfind("."):].lower() if "." in match else ext
                    mime = _IMG_EXT_MIME.get(mext, mime)
                except Exception as e:
                    print(f"WARN: fuzzy-matched {cache_key} -> {match} but fetch failed: {e}")
        if raw is not None:
            uri = f"data:{mime};base64,{_b64.b64encode(raw).decode()}"
        else:
            print(f"WARN: could not inline screenshot ref {cache_key} (no exact or fuzzy match)")
            uri = f"data:{mime};base64,"  # degrade gracefully to alt text
        cache[cache_key] = uri
        return uri

    return _SCREENSHOT_REF_RE.sub(_resolve, content)


def _assurance_block(fmt: str, session_id: str, eco_id: str, roster: list, ts: str) -> str:
    """Phase 5h: deterministic, platform-stamped 'Assurance & Provenance' block that
    replaces the [[ASSURANCE_PROVENANCE]] token a deliverable agent emits. Machine truth
    from the run record (run id, ecosystem, timestamp, contributing specialists) — never
    LLM-narrated statistics. Format-aware (HTML vs markdown/text)."""
    names = ", ".join(roster) if roster else "the ecosystem's specialist agents"
    method = ("Produced by a multi-specialist agentic assessment with independent adversarial "
              "challenge, authoritative-source grounding, and human-review checkpoints. "
              f"Contributing specialists: {names}.")
    trail = ("The full evidentiary trail — each specialist's findings, source citations, "
             "grounding checks and challenge record — is retained with the run record under "
             "the reference above.")
    if (fmt or "").lower() == "html":
        return (
            '<section style="margin-top:24px;padding:14px 16px;border:1px solid #d0d7de;'
            'border-radius:6px;background:#f6f8fa;font-size:12px;color:#444;line-height:1.5;">'
            '<div style="font-weight:600;margin-bottom:6px;">Assurance &amp; Provenance '
            '(platform-verified)</div>'
            f'<div>Run reference: <code>{session_id}</code> &middot; Ecosystem: '
            f'<code>{eco_id}</code> &middot; Generated: {ts} UTC</div>'
            f'<div style="margin-top:4px;">{method}</div>'
            f'<div style="margin-top:4px;">{trail}</div></section>'
        )
    return (f"\n\n---\n**Assurance & Provenance (platform-verified)**\n\n"
            f"Run reference: `{session_id}` &middot; Ecosystem: `{eco_id}` &middot; Generated: {ts} UTC\n\n"
            f"{method}\n\n{trail}\n")


def _checkpoint_feedback_key(session_id, layer):
    return f"runs/{_safe(session_id)}/_checkpoint/{layer}.txt"


def _read_checkpoint_feedback(session_id, layer):
    """Read prior checkpoint WEAKNESSES for a layer (empty string if none)."""
    if not RUNS_BUCKET or session_id is None or layer is None:
        return ""
    try:
        obj = s3.get_object(Bucket=RUNS_BUCKET, Key=_checkpoint_feedback_key(session_id, layer))
        return obj["Body"].read().decode("utf-8")
    except Exception:
        return ""


def _write_checkpoint_feedback(session_id, layer, text):
    if not RUNS_BUCKET:
        return
    try:
        s3.put_object(
            Bucket=RUNS_BUCKET, Key=_checkpoint_feedback_key(session_id, layer),
            Body=(text or "").encode("utf-8"), ContentType="text/plain; charset=utf-8",
        )
    except Exception:
        pass


def _clear_checkpoint_feedback(session_id, layer):
    if not RUNS_BUCKET:
        return
    try:
        s3.delete_object(Bucket=RUNS_BUCKET, Key=_checkpoint_feedback_key(session_id, layer))
    except Exception:
        pass


def _read_operator_guidance(session_id):
    """Read the run-global operator corrective guidance accumulated from gate
    decisions (empty string if none). Injected into every agent as an
    authoritative addendum to the original request."""
    if not RUNS_BUCKET or not session_id:
        return ""
    try:
        obj = s3.get_object(Bucket=RUNS_BUCKET, Key=f"runs/{_safe(session_id)}/_operator_guidance.txt")
        return obj["Body"].read().decode("utf-8")
    except Exception:
        return ""


def _handle_checkpoint_verdict(agent_config, validated, agent_id, swarm, session_id,
                               task_token, apigw, connections, base_payload, user_id=""):
    """Per-level checkpoint gate. `validated` is the challenger dict
    {verdict, weaknesses}. If verdict >= block_below, auto-proceed via
    SendTaskSuccess(output={...,"decision":"proceed"}) so the CheckpointChoice
    routes onward. Otherwise persist the WEAKNESSES for the layer and open a
    human gate (Accept/Loop/Abort) — leaving the task token pending. Returns
    True if it handled completion (caller must NOT send its own task success).
    """
    layer = agent_config.get("_checkpoint_layer")
    block_below = str(agent_config.get("_checkpoint_block_below", "ADEQUATE")).upper()
    verdict = str((validated or {}).get("verdict", "")).upper()
    weaknesses = (validated or {}).get("weaknesses", "") or ""

    thr = _CHECKPOINT_VERDICT_RANK.get(block_below, 1)
    rank = _CHECKPOINT_VERDICT_RANK.get(verdict, 0)

    if rank >= thr:
        # Verdict acceptable — clear any stale feedback and proceed silently.
        print(f"[checkpoint] session={session_id} layer={layer} verdict={verdict} block_below={block_below} rank={rank}>=thr={thr} -> decision=proceed (verdict met threshold)")
        _clear_checkpoint_feedback(session_id, layer)
        out = dict(base_payload)
        out["decision"] = "proceed"
        out["checkpoint_layer"] = layer
        out["verdict"] = verdict
        if task_token:
            sfn.send_task_success(taskToken=task_token, output=json.dumps(out))
        return True
    # Sub-threshold. Two modes (quality.challenger_gate.mode):
    #   advisory (default): annotate the DAG with the verdict and PROCEED, UNLESS this is
    #     a high-confidence failure (a BLOCKER finding) — then pause like blocking. This
    #     kills gate fatigue on borderline-weak layers while still stopping clear breaks.
    #   blocking: pause on any sub-threshold verdict.
    mode = str(agent_config.get("_checkpoint_mode", "advisory")).lower()
    has_blocker = bool(_BLOCKER_RE.search(weaknesses or ""))
    if mode == "advisory" and not has_blocker:
        print(f"[checkpoint] session={session_id} layer={layer} verdict={verdict} block_below={block_below} mode=advisory blocker=False -> decision=proceed (advisory auto-proceed, no human gate)")
        _write_checkpoint_feedback(session_id, layer, weaknesses)
        _broadcast(apigw, connections, {
            "type": "STATE_DELTA", "agent": agent_id, "swarm": swarm,
            "status": "warning", "gate": "challenger",
            "error": f"Layer {layer} checkpoint: {verdict} (advisory — proceeding). {weaknesses[:200]}",
        })
        out = dict(base_payload)
        out["decision"] = "proceed"
        out["checkpoint_layer"] = layer
        out["verdict"] = verdict
        if task_token:
            sfn.send_task_success(taskToken=task_token, output=json.dumps(out))
        return True

    # Sub-threshold — persist the critique for the layer's agents to read on Loop,
    # then open a human gate. The token stays pending until the operator decides.
    print(f"[checkpoint] session={session_id} layer={layer} verdict={verdict} block_below={block_below} mode={mode} blocker={has_blocker} -> HUMAN GATE (pausing, token pending for Accept/Loop/Abort)")
    _write_checkpoint_feedback(session_id, layer, weaknesses)
    gate_meta = {
        "agent_id": agent_id,
        "gate_type": "challenger",
        "checkpoint_layer": layer,
        "verdict": verdict,
        "block_below": block_below,
        "error_summary": f"Layer {layer} checkpoint verdict {verdict} (below {block_below}).",
        "weaknesses": weaknesses[:4000],
        "task_token": task_token,
        "session_id": session_id,
        "ownerId": user_id or "unassigned",
        "base_payload": base_payload,
    }
    if RUNS_BUCKET:
        try:
            s3.put_object(
                Bucket=RUNS_BUCKET,
                Key=f"runs/{_safe(session_id)}/{_safe(agent_id, 64)}/gate_decision.json",
                Body=json.dumps(gate_meta).encode(),
            )
        except Exception:
            pass
    # Async inbox: surface this blocked checkpoint so an away operator is notified.
    import time as _t
    pause = int(agent_config.get("_checkpoint_pause_seconds", 86400) or 86400)
    first_weak = next((ln.strip() for ln in weaknesses.splitlines() if ln.strip()), "")[:160]
    _write_attention_item(
        owner_id=user_id, session_id=session_id, agent_id=agent_id, item_type="challenger",
        summary=f"Checkpoint L{layer}: {verdict} — {first_weak}",
        deadline_epoch=int(_t.time()) + pause,
        context_ref=f"runs/{_safe(session_id)}/{_safe(agent_id, 64)}/gate_decision.json",
    )
    _broadcast(apigw, connections, {
        "type": "STATE_DELTA", "agent": agent_id, "swarm": swarm,
        "status": "gate_decision_required",
        "session_id": session_id,
        "gate_decision_required": gate_meta,
    })
    return True


def _handle_gate_failure(gate_name, error_msg, agent_config, agent_id, swarm, session_id, task_token, apigw, connections, partial_payload=None, user_id=""):
    """Handle a gate failure according to the configured mode (ask/warn/halt).
    
    partial_payload: if provided, saved to S3 for ask-mode Continue so downstream
    gets the real agent output instead of a stub.
    """
    gate_config = agent_config.get("_gate_config", {})
    mode = gate_config.get(gate_name, "ask")
    print(f"[gate] session={session_id} agent={agent_id} gate={gate_name} mode={mode} -> {'HUMAN GATE (paused)' if mode=='ask' else mode} | {error_msg[:120]}")

    if mode == "halt":
        _broadcast(apigw, connections, {
            "type": "STATE_DELTA", "agent": agent_id, "swarm": swarm,
            "status": "error", "error": f"Gate '{gate_name}' HALT: {error_msg[:300]}",
        })
        sfn.send_task_failure(taskToken=task_token, error="GateHalt", cause=f"[{gate_name}] {error_msg[:200]}")
        return "halt"

    if mode == "warn":
        _broadcast(apigw, connections, {
            "type": "STATE_DELTA", "agent": agent_id, "swarm": swarm,
            "status": "warning", "gate": gate_name, "error": f"Gate '{gate_name}' WARNING: {error_msg[:300]}",
        })
        return "warn"

    # mode == "ask" (default) — save partial payload to S3 so Continue can use it
    payload_key = ""
    if partial_payload and RUNS_BUCKET:
        payload_key = f"runs/{session_id}/{agent_id}/gate_payload.json"
        try:
            s3.put_object(Bucket=RUNS_BUCKET, Key=payload_key, Body=json.dumps(partial_payload).encode())
        except Exception:
            payload_key = ""

    # Save gate metadata to S3 so polling can discover pending decisions
    gate_meta = {
        "agent_id": agent_id, "gate_type": gate_name,
        "error_summary": error_msg[:500], "task_token": task_token,
        "session_id": session_id, "payload_key": payload_key,
        "ownerId": user_id or "unassigned",
    }
    # Lead the operator dialogue with the reviewer's ACTUAL assessment (verdict +
    # the specific blocker) when the gated output is a challenger critique — the raw
    # "WEAKNESSES has a BLOCKER but VERDICT is ADEQUATE" string alone is confusing
    # and hides that the checkpoint actually rated the work adequate.
    try:
        _findings = ((partial_payload or {}).get("structured") or {}).get("findings", "") if isinstance(partial_payload, dict) else ""
        gate_meta.update(_summarize_challenger_findings(_findings))
    except Exception:
        pass
    if RUNS_BUCKET:
        try:
            s3.put_object(Bucket=RUNS_BUCKET, Key=f"runs/{session_id}/{agent_id}/gate_decision.json", Body=json.dumps(gate_meta).encode())
        except Exception:
            pass

    # Async inbox: surface this gate so it shows in the Attention menu (not just
    # the in-run dialog). Deadline = the agent task backstop (~30min default).
    import time as _t
    _write_attention_item(
        owner_id=user_id, session_id=session_id, agent_id=agent_id, item_type=gate_name,
        summary=f"{gate_name} gate on {agent_id}: {error_msg[:160]}",
        deadline_epoch=int(_t.time()) + int(agent_config.get("_gate_pause_seconds") or agent_config.get("task_timeout") or 1800),
        context_ref=f"runs/{_safe(session_id)}/{_safe(agent_id, 64)}/gate_decision.json",
    )

    _broadcast(apigw, connections, {
        "type": "STATE_DELTA", "agent": agent_id, "swarm": swarm,
        "status": "gate_decision_required",
        "session_id": session_id,
        "gate_decision_required": gate_meta,
    })
    return "ask"


def handler(event, context):
    """Invoke agent and forward AG-UI events to WebSocket."""
    task_token = event.get("TaskToken", "")
    agent_config = event.get("agent_config", {})
    mission = event.get("mission", "")
    input_context = event.get("input_context", "")
    session_id = event.get("session_id", "")
    user_id = event.get("user_id", "")
    dry_run = bool(event.get("dry_run", False))
    # spec-run-tuning §4: per-run config (model_profile / model_overrides), forwarded
    # to agentcore so it can resolve the per-run model. Empty {} for legacy launches.
    run_config = event.get("run_config", {}) or {}
    agent_id = agent_config.get("id", "unknown")
    swarm = agent_config.get("swarm", "")
    depends_on = agent_config.get("depends_on", [])
    results = event.get("results", {})

    # Compute role from DAG position. Agent receives this in agent_config so the
    # AgentCore agent can apply role-specific framing in its system prompt.
    agent_role = compute_role(agent_id, depends_on)
    agent_config = {**agent_config, "agent_role": agent_role}

    # Build the conversation context from upstream STRUCTURED FINDINGS sections.
    # Raises if any upstream output is missing the required structure — no silent fallback.
    conversation_context = build_conversation(depends_on, results)

    # Per-level checkpoint loop-back: if this agent belongs to a checkpointed
    # layer and a prior checkpoint logged WEAKNESSES for that layer (i.e. the
    # operator chose Loop), inject them as corrective feedback so this re-run
    # explicitly addresses them.
    _layer = agent_config.get("_layer")
    if _layer is not None:
        _fb = _read_checkpoint_feedback(session_id, _layer)
        if _fb.strip():
            input_context = (
                (input_context + "\n\n" if input_context else "")
                + "PRIOR REVIEW FEEDBACK — a checkpoint review judged the previous "
                "attempt at this layer insufficient. You MUST directly address every "
                "point below in this revision:\n" + _fb
            )

    # Operator corrective guidance (run-global) — AUTHORITATIVE directives the
    # operator entered at a gate (Accept or Loop). Injected for EVERY agent so it
    # informs the looped layer AND downstream agents. We PREPEND it as a
    # highest-precedence block (not a soft trailing "clarification") and tell the
    # agent it OVERRIDES the original request and any conflicting upstream
    # findings — otherwise an operator premise (e.g. "assume the property is fully
    # renovated") loses to citation-locked upstream research and is silently
    # dropped. Accumulates across gate decisions.
    _op_guidance = _read_operator_guidance(session_id)
    if _op_guidance.strip():
        _guidance_block = (
            "==================== OPERATOR DIRECTIVES (AUTHORITATIVE — HIGHEST PRECEDENCE) ====================\n"
            "The operator issued the directives below at a human review gate. They are AUTHORITATIVE and\n"
            "OVERRIDE both the original request AND any upstream agent findings wherever they conflict.\n"
            "If a directive sets a fact or assumption (a correction, or 'assume X about the subject'), treat it\n"
            "as TRUE and RE-DERIVE your analysis under it — do NOT defer to contradicting upstream findings or\n"
            "the original wording. Cite a directive you rely on as `[per operator directive: <text>]`. Lines\n"
            "marked '[OPERATOR DECISION — ACCEPTED]' are acknowledgments that a concern was waived — apply no\n"
            "new analysis for those, just do not re-raise the waived concern.\n"
            + _op_guidance.strip() + "\n"
            "================================================================================================="
        )
        input_context = _guidance_block + ("\n\n" + input_context if input_context else "")

    # Phase 5b: resolve ecosystem files for this agent based on file_tags
    file_tags = agent_config.get("file_tags", [])
    if file_tags and RUNS_BUCKET:
        resolved_files = resolve_files_for_agent(agent_config, session_id)
        agent_config = {**agent_config, "files": resolved_files}
        # Inject file content into input_context so the agent sees it
        if resolved_files:
            file_sections = []
            for f in resolved_files:
                if f.get("content"):
                    file_sections.append(f"--- FILE: {f['name']} (tag: {f['tag']}) ---\n{f['content']}")
                else:
                    file_sections.append(f"--- FILE: {f['name']} (tag: {f['tag']}, format: {f['format']}) ---\n[Binary file — use code_interpreter to process from S3: {f['s3Uri']}]")
            input_context = (input_context + "\n\n" if input_context else "") + "\n\n".join(file_sections)

    # AgentCore requires runtimeSessionId >= 33 chars
    runtime_session_id = f"{session_id}-{agent_id}"[:64]
    if len(runtime_session_id) < 33:
        runtime_session_id = f"{runtime_session_id}-{uuid.uuid4().hex}"[:64]

    # AG-UI RunAgentInput payload — agent_config etc. go in forwardedProps.
    # The agent's user message MUST carry input_context (ecosystem inputs / per-Map
    # cluster data): the runtime does not reliably surface forwardedProps.input_context
    # into the model prompt, so fold it into the message content here.
    _user_message = mission or "Run your mission."
    if input_context:
        _user_message += "\n\n## INPUT CONTEXT\n" + input_context
    payload = json.dumps({
        "threadId": session_id or str(uuid.uuid4()),
        "runId": f"{agent_id}-{uuid.uuid4().hex[:8]}",
        "messages": [{
            "id": str(uuid.uuid4()),
            "role": "user",
            "content": _user_message,
        }],
        "tools": [],
        "context": [],
        "state": {},
        "forwardedProps": {
            "agent_config": agent_config,
            "mission": _user_message,
            "conversation_context": conversation_context,
            "input_context": input_context,
            "session_id": session_id,
            "user_id": user_id,
            "dry_run": dry_run,
            "run_config": run_config,
        },
    }).encode()

    response = agentcore.invoke_agent_runtime(
        agentRuntimeArn=AGENT_RUNTIME_ARN,
        runtimeSessionId=runtime_session_id,
        payload=payload,
        qualifier="DEFAULT",
    )

    # Stream AG-UI events directly to WebSocket
    text_buffer = []
    apigw = boto3.client("apigatewaymanagementapi", endpoint_url=WS_ENDPOINT)
    connections = _get_connections(session_id)

    # Emit a "running" lifecycle event so the DAG / ribbon update immediately
    _broadcast(apigw, connections, {
        "type": "STATE_DELTA",
        "agent": agent_id,
        "swarm": swarm,
        "status": "running",
    })

    # Use proper SSE framing — iter_lines() handles message boundaries correctly,
    # so a `data: {...}` event won't be split across two raw chunks.
    sse_stream = response.get("response")
    line_iter = sse_stream.iter_lines() if hasattr(sse_stream, "iter_lines") else _fallback_lines(sse_stream)

    # Tool-result tracking: built up across the AG-UI event stream so we can return
    # them alongside `output` for the grounding-check (and external-grounding) Lambdas.
    # AG-UI emits TOOL_CALL_START → TOOL_CALL_ARGS (deltas) → TOOL_CALL_END → TOOL_CALL_RESULT.
    tool_calls: dict = {}              # toolCallId → {name, args, result}
    tool_results: list = []            # ordered list returned to SFN
    _staged_artifacts: list = []       # [{filename, content}] from save_artifact
    _login_requests: list = []         # [{domain, profile_name, user_id}] from browser request_login
    _usage_holder: dict = {}           # per-agent token usage (spec-run-tuning §5.1)

    # Stream-silence heartbeat — see docs/features/stream-silence-heartbeat.md.
    # iter_lines() blocks on socket reads, so we cannot interrupt it from inside
    # the loop. Run iter_lines() on a daemon producer thread that pushes each
    # line into a Queue; the main thread polls the Queue with a short timeout
    # and computes elapsed-since-last-event on every poll. If silence exceeds
    # STREAM_SILENCE_SECONDS, raise AgentStreamSilenceError with structured
    # context (agent_id, last_event_kind, silent_for) — far more actionable
    # than the legacy bare States.Timeout.
    _STREAM_END = object()
    line_queue: "queue.Queue" = queue.Queue()

    def _line_producer():
        try:
            for raw in line_iter:
                line_queue.put(raw)
        except Exception as exc:
            line_queue.put(exc)
        finally:
            line_queue.put(_STREAM_END)

    producer_thread = threading.Thread(target=_line_producer, name=f"sse-producer-{agent_id}", daemon=True)
    producer_thread.start()

    last_event_at = time.time()
    last_event_kind = "stream_open"
    # Re-fetch the WebSocket connection list periodically so a mid-run page
    # refresh (which creates a NEW connectionId and re-registers the session)
    # is picked up. Without this, the connection list captured at invocation
    # start goes stale on refresh and the stream silently dies for the rest of
    # this agent's run (events post to the old, Gone connection).
    _CONN_REFRESH_SECONDS = 15.0
    last_conn_refresh = time.time()

    while True:
        # Periodically refresh connections so reconnects after a page refresh
        # start receiving this agent's stream.
        if time.time() - last_conn_refresh > _CONN_REFRESH_SECONDS:
            refreshed = _get_connections(session_id)
            if refreshed:
                connections = refreshed
            last_conn_refresh = time.time()
        try:
            line = line_queue.get(timeout=_SILENCE_POLL_SECONDS)
        except queue.Empty:
            silent_for = time.time() - last_event_at
            # Drafters get 600s (heavy code_interpreter). Synthesizers (depends_on != []) get 420s.
            # Per-agent override via stream_silence_seconds in agent config.
            if agent_config.get("stream_silence_seconds"):
                threshold = float(agent_config["stream_silence_seconds"])
            elif agent_config.get("output_kind") == "drafter":
                threshold = 600
            elif agent_config.get("depends_on"):
                threshold = 420
            else:
                threshold = STREAM_SILENCE_SECONDS
            if silent_for > threshold:
                raise AgentStreamSilenceError(
                    agent_id=agent_id,
                    last_activity=last_event_kind,
                    silent_for_seconds=silent_for,
                )
            continue

        if line is _STREAM_END:
            break
        if isinstance(line, BaseException):
            # Producer thread hit an error in iter_lines (e.g. socket read
            # timeout from botocore at the 890s backstop). Re-raise as silence
            # error since by definition the stream stopped producing.
            silent_for = time.time() - last_event_at
            raise AgentStreamSilenceError(
                agent_id=agent_id,
                last_activity=f"{last_event_kind} (then stream errored: {type(line).__name__})",
                silent_for_seconds=silent_for,
            ) from line

        if not line:
            continue
        if isinstance(line, bytes):
            line = line.decode("utf-8")
        line = line.strip()
        if not line.startswith("data: "):
            continue
        try:
            event_data = json.loads(line[6:])
        except (json.JSONDecodeError, ValueError) as e:
            print(f"Failed to parse SSE event: {e} | line={line[:200]!r}")
            continue

        # A meaningful event arrived — reset silence clock.
        last_event_at = time.time()
        last_event_kind = event_data.get("type") or "data"

        # Enrich with agent/swarm metadata for the frontend
        event_data["agent"] = agent_id
        event_data["session_id"] = session_id
        if swarm:
            event_data["swarm"] = swarm
        # Namespace messageIds with agent_id so parallel agents don't collide
        if "messageId" in event_data:
            event_data["messageId"] = f"{agent_id}:{event_data['messageId']}"
        if "toolCallId" in event_data:
            event_data["toolCallId"] = f"{agent_id}:{event_data['toolCallId']}"

        # Buffer text for the Step Functions return value
        evt_type = event_data.get("type")
        if evt_type == "TEXT_MESSAGE_CONTENT":
            text_buffer.append(event_data.get("delta", ""))

        # Track tool-call lifecycle so we can collect tool_results
        elif evt_type == "TOOL_CALL_START":
            tcid = event_data.get("toolCallId")
            if tcid:
                tool_calls[tcid] = {
                    "name": event_data.get("toolCallName") or event_data.get("tool") or "tool",
                    "args": "",
                    "result": None,
                }
        elif evt_type == "TOOL_CALL_ARGS":
            tcid = event_data.get("toolCallId")
            if tcid and tcid in tool_calls:
                tool_calls[tcid]["args"] += event_data.get("delta", "")
        elif evt_type == "TOOL_CALL_RESULT":
            tcid = event_data.get("toolCallId")
            if tcid and tcid in tool_calls:
                tool_calls[tcid]["result"] = event_data.get("result") or event_data.get("content")
                tool_results.append({
                    "toolCallId": tcid,
                    "name": tool_calls[tcid]["name"],
                    "args": tool_calls[tcid]["args"],
                    "result": tool_calls[tcid]["result"],
                })

        # Collect artifacts emitted via STATE_DELTA from save_artifact tool
        elif evt_type == "STATE_DELTA":
            for patch in event_data.get("delta", []):
                if patch.get("path") == "/artifacts/-" and patch.get("op") == "add":
                    art = patch.get("value", {})
                    if art.get("filename") and art.get("content"):
                        _staged_artifacts.append(art)
                # Browser tool raised a human sign-in request (auth-gated site).
                elif patch.get("path") == "/browser_login/-" and patch.get("op") == "add":
                    lr = patch.get("value", {})
                    if isinstance(lr, dict) and lr.get("domain"):
                        _login_requests.append(lr)
                # spec-run-tuning §5.1: per-agent token usage emitted by agentcore.
                elif patch.get("path") == "/usage" and isinstance(patch.get("value"), dict):
                    _usage_holder.update(patch["value"])

        # Forward to all WebSocket connections
        _broadcast(apigw, connections, event_data)

    output = "".join(text_buffer)

    # Write any staged artifacts (from save_artifact tool) to S3
    for art in _staged_artifacts:
        art_key = f"runs/{session_id}/{agent_id}/{art['filename']}"
        try:
            import base64
            if art.get("binary"):
                body = base64.b64decode(art["content"])
            else:
                # Text artifacts (e.g. the presentation deck HTML) may reference
                # web-app screenshots via `tessera-screenshot://<agent>/<file>`
                # tokens. Inline them as base64 data: URIs here — server-side —
                # so the deliverable is fully self-contained WITHOUT ever routing
                # binary image data through the model. See browser tool +
                # docs/features/browser-tool.md.
                content = _inline_screenshot_refs(art["content"], session_id)
                content = _swap_doc_refs(content, session_id)
                content = _resolve_citations(content, session_id)
                content = _apply_text_fragments(content)
                # Beautify Phase 4: apply a promoted house-style stylesheet (if the
                # deliverable agent's output_format.template_id points at one).
                content = _apply_house_style(content, agent_config)
                # Phase 5h: stamp the deterministic Assurance & Provenance block in place
                # of the [[ASSURANCE_PROVENANCE]] token the deliverable agent emitted (only
                # present when the eco declared outcome.audience). No token = no change.
                if "[[ASSURANCE_PROVENANCE]]" in content:
                    _rc = agent_config.get("role_contract") or {}
                    _roster = [o.get("name") or o.get("id")
                               for o in (_rc.get("other_agents_in_ecosystem") or [])]
                    _roster = [n for n in ([agent_config.get("name") or agent_id] + _roster) if n]
                    _fname = art.get("filename") or ""
                    _fmt = _fname.rsplit(".", 1)[-1].lower() if "." in _fname else ""
                    import datetime as _dt
                    _ts = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d %H:%M")
                    content = content.replace(
                        "[[ASSURANCE_PROVENANCE]]",
                        _assurance_block(_fmt, session_id, agent_config.get("_ecosystem_id", ""), _roster, _ts),
                    )
                body = content.encode("utf-8")
            s3.put_object(Bucket=RUNS_BUCKET, Key=art_key, Body=body)
        except Exception as e:
            print(f"WARN: failed to write artifact {art['filename']}: {e}")

    # Browser sign-in requests → raise a NON-BLOCKING `browser_login` Attention
    # item per site so an operator can seed that site's auth profile via live
    # view (browser-login Lambda). The run does NOT pause — it finishes with the
    # partial (pre-login) capture; once the profile is seeded, a FUTURE run is
    # authenticated. See docs/features/browser-tool.md.
    # Browser sign-in requests are handled as a BLOCKING gate at task completion
    # (see the browser_login gate below, right before SendTaskSuccess) so the run
    # PAUSES for the operator to sign in, then re-runs authenticated via GateRetry.

    # Phase 5e: per-kind validation. Each output_kind has its own validator
    # (analyst/synthesizer require STRUCTURED FINDINGS; challenger requires
    # VERDICT+WEAKNESSES with consistency rule; reporter requires prose ≥200
    # chars; drafter requires SUMMARY header + body; chat any non-empty;
    # creative any non-empty ≥50 chars; operator requires RESULT keyword).
    # Default output_kind=analyst preserves pre-Phase-5e behaviour for every
    # existing ecosystem YAML.
    output_kind = _resolve_output_kind(agent_config)
    validator = _OUTPUT_VALIDATORS.get(output_kind, _validate_analyst)
    try:
        validated = validator(output, agent_id)
    except ValueError as ve:
        partial = {
            "agent_id": agent_id, "output_kind": output_kind,
            "structured": {"findings": output[:2000], "open_questions": ""},
            "output_s3_uri": "", "tool_results_s3_uri": "",
            "output_preview": output[:500], "output_size": len(output),
        }
        result = _handle_gate_failure("output_kind", str(ve), agent_config, agent_id, swarm, session_id, task_token, apigw, connections, partial_payload=partial, user_id=user_id)
        if result in ("halt", "ask"):
            return
        # warn: continue with a minimal validated dict
        validated = {"findings": "", "open_questions": ""}

    # Phase 5e: output_format validation. After kind-validation passes, run
    # format-validation if the agent declared a non-markdown format. Parses
    # Phase 5e: per-format validation. For non-markdown types, check if the
    # artifact was saved via save_artifact (S3 path). If so, skip fenced-block
    # parsing — the artifact is already in S3.
    output_format = _resolve_output_format(agent_config)
    parsed_payload = None
    save_as = output_format.get("save_as", "")
    # Skip fenced-block validation when the agent is expected to produce artifacts
    # via code_interpreter (save_as declared + code_interpreter in tools). The
    # artifact will be written to S3 from the STATE_DELTA events.
    agent_tools = agent_config.get("tools") or []
    artifact_via_code = bool(save_as and output_format["type"] != "markdown" and "code_interpreter" in agent_tools)
    artifact_saved_via_tool = any(a["filename"] == save_as for a in _staged_artifacts) if save_as else False

    if artifact_via_code or artifact_saved_via_tool:
        produced_art = next((a for a in _staged_artifacts if a.get("filename") == save_as), None)
        partial = {
            "agent_id": agent_id, "output_kind": output_kind,
            "structured": validated if isinstance(validated, dict) else {"findings": output[:2000], "open_questions": ""},
            "output_s3_uri": "", "tool_results_s3_uri": "",
            "output_preview": output[:500], "output_size": len(output),
        }
        if not produced_art:
            # Artifact expected but not produced — fire format gate
            err_msg = f"Agent {agent_id} declared output_format save_as='{save_as}' and has code_interpreter, but no artifact was produced. The code_interpreter may have timed out or failed to write the file."
            result = _handle_gate_failure("format", err_msg, agent_config, agent_id, swarm, session_id, task_token, apigw, connections, partial_payload=partial, user_id=user_id)
            if result in ("halt", "ask"):
                return
        elif not _artifact_complete(output_format["type"], produced_art.get("content") or "", produced_art.get("binary")):
            # Produced but INCOMPLETE/truncated (e.g. an HTML shell with an empty
            # body) OR a stacked multi-document PDF — gate instead of shipping a
            # broken deliverable as "complete". Tailor the corrective feedback so
            # the drafter fixes the RIGHT thing on the loop.
            _clen = len(produced_art.get("content") or "")
            _stacked = False
            if output_format["type"] == "pdf" and produced_art.get("binary"):
                try:
                    import base64 as _b64
                    _stacked = _b64.b64decode(produced_art.get("content") or "").count(b"%PDF-") > 1
                except Exception:
                    _stacked = False
            if _stacked:
                err_msg = (f"Agent {agent_id} produced '{save_as}' but it is MULTIPLE PDF documents "
                           f"concatenated into one file (you called output()/saved more than once, or "
                           f"appended to the file). Most viewers then show only a cover/blank page. "
                           f"REBUILD as ONE FPDF object: add every page to the SAME object across calls, "
                           f"then call output('{save_as}') EXACTLY ONCE at the very end — never per section.")
            else:
                err_msg = (f"Agent {agent_id} produced '{save_as}' but it is INCOMPLETE/truncated "
                           f"({_clen} chars) — the document was cut off before completion (the drafter "
                           f"likely ran out of output budget mid-write). Loop to regenerate the full artifact.")
            result = _handle_gate_failure("format", err_msg, agent_config, agent_id, swarm, session_id, task_token, apigw, connections, partial_payload=partial, user_id=user_id)
            if result in ("halt", "ask"):
                return
        parsed_payload = None
    elif output_format["validate"] and output_format["type"] in _FORMAT_VALIDATORS:
        format_validator = _FORMAT_VALIDATORS[output_format["type"]]
        try:
            parsed_payload = format_validator(output, agent_id)
        except ValueError as fmt_err:
            result = _handle_gate_failure("format", str(fmt_err), agent_config, agent_id, swarm, session_id, task_token, apigw, connections, user_id=user_id)
            if result in ("halt", "ask"):
                return
            # warn: continue without parsed payload
            parsed_payload = None

    # `structured` kept for backward compat with downstream code that expects
    # the analyst-shaped dict {findings, open_questions}. For analyst/synthesizer
    # the validated dict IS that shape. For other kinds we synthesise a minimal
    # stand-in so the downstream auto-mirror + storage logic doesn't break.
    if output_kind in ("analyst", "synthesizer", "researcher"):
        structured = validated
    else:
        structured = {"findings": "", "open_questions": ""}

    # Phase 4.5: synthesizer agents MUST cite each declared upstream agent.
    # Only applies to claim-emitting kinds (analyst/synthesizer). Other kinds
    # have their own citation conventions baked into their per-kind contract.
    if output_kind in ("analyst", "synthesizer", "researcher"):
        role_contract = agent_config.get("role_contract") or {}
        try:
            _validate_citations(structured, role_contract)
        except ValueError as cite_err:
            result = _handle_gate_failure("citation", str(cite_err), agent_config, agent_id, swarm, session_id, task_token, apigw, connections, user_id=user_id)
            if result in ("halt", "ask"):
                return
            # warn: continue as-is

    # Auto-mirror structured findings to AgentCore Memory for ANY namespace whose
    # YAML declared `auto_persist: true` AND that this agent is authorized to write to.
    # The compiler injects `agent_config.memory.auto_persist_namespaces` with template
    # paths like `/tessera/sessions/{sessionId}/agents/{agentId}/`. We substitute the
    # placeholders at runtime and CreateEvent for each namespace.
    auto_persist_namespaces = (agent_config.get("memory") or {}).get("auto_persist_namespaces") or []
    if auto_persist_namespaces and structured:
        # Agent-mode catalog ingestion passes the per-source write TYPE in the mission
        # (`produces: <type>`) so an auto-persist namespace `/catalog/{catalog_type}/`
        # resolves to the right entity type. Empty for other ecos (harmless).
        _catalog_type = _mission_field(f"{mission}\n{input_context}", "produces")
        _catalog_version = _mission_field(f"{mission}\n{input_context}", "catalog_version")
        _auto_mirror_findings(
            namespaces=auto_persist_namespaces,
            session_id=session_id,
            agent_id=agent_id,
            findings=structured.get("findings", ""),
            open_questions=structured.get("open_questions", ""),
            user_id=user_id,
            entity_key=structured.get("entity_key", ""),
            raw_output=output,
            catalog_type=_catalog_type,
            catalog_version=_catalog_version,
        )

    # Emit "complete" lifecycle event so the DAG / ribbon transitions to done
    _broadcast(apigw, connections, {
        "type": "STATE_DELTA",
        "agent": agent_id,
        "swarm": swarm,
        "status": "complete",
    })

    # Phase 4.5: stage large fields to S3 to keep $.results state under the
    # 256KB Step Functions limit. Output and tool_results can be 10s of KB each;
    # downstream synthesizers only need `structured` to build conversation context.
    # grounding-check fetches output / tool_results from S3 when it needs them.
    # No silent truncation — full content is preserved in S3.
    if not RUNS_BUCKET:
        raise RuntimeError(
            "TESSERA_RUNS_BUCKET env var is not set. Cannot stage execution "
            "artifacts to S3 — without this, $.results state will exceed Step "
            "Functions' 256KB payload limit. Configure "
            "the CDK stack to provision the runs bucket and inject this env."
        )
    safe_session = re.sub(r"[^A-Za-z0-9_-]", "-", session_id or "no-session")[:128]
    safe_agent = re.sub(r"[^A-Za-z0-9_-]", "-", agent_id)[:64]
    output_key = f"runs/{safe_session}/{safe_agent}/output.txt"
    tool_results_key = f"runs/{safe_session}/{safe_agent}/tool_results.json"
    s3.put_object(
        Bucket=RUNS_BUCKET, Key=output_key,
        Body=output.encode("utf-8"), ContentType="text/plain; charset=utf-8",
    )
    s3.put_object(
        Bucket=RUNS_BUCKET, Key=tool_results_key,
        Body=json.dumps(tool_results, default=str).encode("utf-8"),
        ContentType="application/json",
    )
    # spec-run-tuning §5.1: persist per-agent token usage (emitted by agentcore
    # via a /usage STATE_DELTA) so runs-api can build the run manifest's cost +
    # telemetry. Fully best-effort — never fail the run over telemetry.
    try:
        _usage = {
            "model": _usage_holder.get("model", ""),
            "tokens_in": int(_usage_holder.get("tokens_in") or 0),
            "tokens_out": int(_usage_holder.get("tokens_out") or 0),
            "tool_calls": int(_usage_holder.get("tool_calls") or 0) or len(tool_results),
        }
        s3.put_object(
            Bucket=RUNS_BUCKET, Key=f"runs/{safe_session}/{safe_agent}/usage.json",
            Body=json.dumps(_usage).encode("utf-8"), ContentType="application/json",
        )
    except Exception as _ue:
        print(f"WARN: failed to write usage.json for {agent_id}: {_ue}")
    output_uri = f"s3://{RUNS_BUCKET}/{output_key}"
    tool_results_uri = f"s3://{RUNS_BUCKET}/{tool_results_key}"

    # Phase 5e: when a typed output_format is declared (and validates),
    # ALSO save the parsed payload as a typed artifact alongside the raw
    # markdown. Phase 5f's artifact-download path will read these typed
    # files. Today's writers reading output.txt continue to work unchanged.
    typed_artifact_uri = None
    if parsed_payload is not None and output_format["type"] in ("json", "yaml", "html"):
        typed_filename = output_format.get("save_as") or f"output{output_format['file_extension']}"
        typed_key = f"runs/{safe_session}/{safe_agent}/{typed_filename}"
        if output_format["type"] == "json":
            typed_body = json.dumps(parsed_payload, default=str, indent=2).encode("utf-8")
            typed_ct = "application/json"
        elif output_format["type"] == "yaml":
            typed_body = yaml.safe_dump(parsed_payload, sort_keys=False).encode("utf-8")
            typed_ct = "application/x-yaml"
        else:  # html — parsed_payload IS the HTML string
            typed_body = parsed_payload.encode("utf-8") if isinstance(parsed_payload, str) else b""
            typed_ct = "text/html; charset=utf-8"
            if isinstance(parsed_payload, str):
                typed_body = _apply_text_fragments(_resolve_citations(_swap_doc_refs(_inline_screenshot_refs(parsed_payload, session_id), session_id), session_id)).encode("utf-8")
        s3.put_object(
            Bucket=RUNS_BUCKET, Key=typed_key,
            Body=typed_body, ContentType=typed_ct,
        )
        typed_artifact_uri = f"s3://{RUNS_BUCKET}/{typed_key}"

    # Phase 5g: with waitForTaskToken, SFN ignores Lambda return value.
    # Complete the task explicitly via SendTaskSuccess.
    result_payload = {
        "agent_id": agent_id,
        "output_kind": output_kind,
        "output_format": output_format["type"],
        "structured": structured,
        "output_s3_uri": output_uri,
        "tool_results_s3_uri": tool_results_uri,
        "typed_artifact_s3_uri": typed_artifact_uri,
        "output_preview": output[:500],
        "output_size": len(output),
        "tool_count": len(tool_results),
        # Deterministic decision engine: computed labels/outcome/trace when this agent
        # declares a `decision` block (spec-decision-engine.md). None otherwise.
        "decision": _evaluate_decision(agent_config, output, session_id, agent_id),
    }
    # Per-level checkpoint: decide proceed vs human gate based on the verdict.
    # `validated` here is the challenger {verdict, weaknesses} dict.
    if agent_config.get("_checkpoint_layer") is not None:
        _handle_checkpoint_verdict(
            agent_config, validated, agent_id, swarm, session_id,
            task_token, apigw, connections, result_payload, user_id=user_id,
        )
        return result_payload

    # BLOCKING browser-login gate: if the agent hit an auth wall (request_login),
    # PAUSE here — write a browser_login record carrying this task_token + the
    # partial result, raise a `browser_login` Attention item, and DO NOT
    # SendTaskSuccess. The operator signs in via the live-view viewer; resolving
    # the gate either re-runs this agent authenticated (SendTaskFailure GateRetry,
    # which the task's Retry rule catches) or skips (SendTaskSuccess partial).
    if _login_requests and task_token and RUNS_BUCKET:
        _lr = _login_requests[0]
        _dom = _lr.get("domain", "")
        _login_key = f"runs/{_safe(session_id)}/{_safe(agent_id, 64)}/browser_login.json"
        try:
            s3.put_object(Bucket=RUNS_BUCKET, Key=_login_key, Body=json.dumps({
                "type": "browser_login", "domain": _dom,
                "profile_name": _lr.get("profile_name", ""),
                "user_id": _lr.get("user_id") or user_id,
                "session_id": session_id, "agent_id": agent_id, "region": REGION,
                "task_token": task_token,          # gate-decision resolves via this
                "base_payload": result_payload,    # used if the operator SKIPS login
            }).encode())
        except Exception as e:
            print(f"WARN: failed to write browser_login gate record: {e}")
        _write_attention_item(
            owner_id=user_id, session_id=session_id, agent_id=agent_id,
            item_type="browser_login",
            summary=(f"Sign in to {_dom} so Tessera can capture it — the run is PAUSED here. "
                     f"Open the live sign-in, log in (SSO/MFA fine), then Done to continue "
                     f"authenticated (or Skip to proceed without the in-app screenshots)."),
            deadline_epoch=int(time.time()) + int(agent_config.get("_gate_pause_seconds") or 86400),
            context_ref=_login_key,
        )
        _broadcast(apigw, connections, {
            "type": "STATE_DELTA", "agent": agent_id, "swarm": swarm,
            "status": "browser_login_required", "session_id": session_id,
            "browser_login": {"domain": _dom, "context_ref": _login_key, "agent_id": agent_id},
        })
        print(f"[browser-login] session={session_id} agent={agent_id} PAUSED for sign-in to {_dom} (token pending)")
        return result_payload   # token stays pending — run is paused until resolved

    if task_token:
        sfn.send_task_success(taskToken=task_token, output=json.dumps(result_payload))
    return result_payload


def _get_connections(session_id: str) -> list:
    """Get all WebSocket connection IDs that are subscribed to this session."""
    if not session_id:
        print(f"[WS] _get_connections called with empty session_id")
        return []
    resp = connections_table.scan(
        FilterExpression="contains(sessionIds, :sid)",
        ExpressionAttributeValues={":sid": session_id},
    )
    conns = [item["connectionId"] for item in resp.get("Items", [])]
    print(f"[WS] _get_connections('{session_id}'): found {len(conns)} connections")
    return conns


def _fallback_lines(stream):
    """Fallback line iterator — buffers chunks and yields complete lines.

    Used when the streaming object doesn't expose iter_lines(). Ensures SSE
    `data: {...}\\n\\n` frames are not split across chunks.
    """
    buf = b""
    for chunk in stream:
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            yield line
    if buf:
        yield buf


def _broadcast(apigw, connection_ids: list, event_data: dict) -> None:
    """Send an AG-UI event to all connections, prune dead ones."""
    payload = json.dumps(event_data).encode()
    for cid in connection_ids:
        try:
            apigw.post_to_connection(ConnectionId=cid, Data=payload)
        except apigw.exceptions.GoneException:
            try:
                connections_table.delete_item(Key={"connectionId": cid})
            except Exception:
                pass
        except Exception:
            pass


# ─── Structured-context helpers ─────────────────────────────────────────────

# Match "## STRUCTURED FINDINGS" header, capturing content until next "## " or EOF.
_FINDINGS_RE = re.compile(
    r"##\s*STRUCTURED\s+FINDINGS\s*\n(.*?)(?=\n##\s|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_QUESTIONS_RE = re.compile(
    r"##\s*OPEN\s+QUESTIONS\s*\n(.*?)(?=\n##\s|\Z)",
    re.DOTALL | re.IGNORECASE,
)
# Entity-keying (M.1/M.6): optional stable per-entity slug emitted by catalog-writing
# analysts. Used to key the durable catalog record (/catalog/<type>/<entity-key>).
_ENTITY_KEY_RE = re.compile(
    r"##\s*ENTITY\s+KEY\s*\n(.*?)(?=\n##\s|\Z)",
    re.DOTALL | re.IGNORECASE,
)


def _slugify_entity_key(s: str) -> str:
    """Normalize an emitted entity key to a safe, stable namespace segment."""
    s = re.sub(r"[^a-z0-9]+", "-", (s or "").strip().lower()).strip("-")
    return s[:80]


# Reject "keys" that are actually a refusal / prose sentence rather than a real entity
# id. A subject-less bottom-up run used to emit e.g. "Unable to generate entity key — no
# model identified", which got slugified + persisted as a junk raw catalog record
# (`/catalog/model/unable-to-generate-entity-key-no-mode`). Treat these as NO key so the
# empty-key guard skips the write.
_BAD_KEY_PHRASES = ("unable-to", "cannot-be", "could-not", "to-be-determined",
                    "not-applicable", "not-determined", "no-entity", "no-model",
                    "no-mode", "cannot-determine", "yet-to-be", "will-be-determined")
_BAD_KEY_WORDS = {"unable", "cannot", "couldnt", "na", "none", "null", "unknown",
                  "undetermined", "tbd", "pending", "unspecified", "unidentified",
                  "placeholder", "example", "determined", "identify", "identified"}


def _valid_entity_key(k: str) -> bool:
    """True if `k` looks like a real entity slug (not empty, not a refusal/prose line)."""
    if not k or len(k) < 3:
        return False
    low = k.lower()
    if any(p in low for p in _BAD_KEY_PHRASES):
        return False
    parts = low.split("-")
    if set(parts) & _BAD_KEY_WORDS:
        return False
    if len(parts) > 8:   # a sentence, not a slug
        return False
    return True


# Multi-record catalog producers (e.g. risk-synthesis) emit SEVERAL durable entities
# in one output as fenced ```json blocks. Parse them robustly (JSON, not prose) so
# each lands as its own keyed catalog record.
_JSON_BLOCK_RE = re.compile(r"```(?:json)?\s*\n(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_json_records(output: str) -> list:
    """Return a flat list of dict records parsed from fenced json blocks. Each block
    may hold a single object or an array of objects."""
    recs = []
    for m in _JSON_BLOCK_RE.finditer(output or ""):
        try:
            obj = json.loads(m.group(1).strip())
        except Exception:
            continue
        if isinstance(obj, list):
            recs.extend([o for o in obj if isinstance(o, dict)])
        elif isinstance(obj, dict):
            recs.append(obj)
    return recs


def _record_key(rec: dict) -> str:
    """Resolve a record's entity key from common id fields (slugified). '' if none."""
    for k in ("risk_id", "entity_key", "technique_id", "control_id",
              "cve_or_ref", "threat_id", "risk_type", "breach_key",
              "threat_event_id", "gai_risk_id", "id", "key"):
        v = rec.get(k)
        if v:
            return _slugify_entity_key(str(v))
    return ""

# Phase 5e: per-kind output validation regexes.
_VERDICT_RE = re.compile(
    r"##\s*VERDICT\s*\n\s*(STRONG|ADEQUATE|WEAK)\b",
    re.IGNORECASE,
)
_WEAKNESSES_RE = re.compile(
    r"##\s*WEAKNESSES\s*\n(.*?)(?=\n##\s|\Z)",
    re.DOTALL | re.IGNORECASE,
)
_RESULT_RE = re.compile(
    r"##\s*RESULT\s*\n\s*(SUCCESS|FAILURE|DRY_RUN|AWAITING_APPROVAL)\b",
    re.IGNORECASE,
)
_SUMMARY_HEADER_RE = re.compile(
    r"##\s*SUMMARY\b",
    re.IGNORECASE,
)
_BLOCKER_RE = re.compile(
    r"\bseverity\s*[:=]\s*BLOCKER\b",
    re.IGNORECASE,
)


def _summarize_challenger_findings(findings: str) -> dict:
    """Best-effort extraction of the reviewer's verdict, weaknesses text, and the
    first BLOCKER finding from a challenger critique. Used to make the human-gate
    dialogue lead with the actual assessment (e.g. "rated ADEQUATE but flagged a
    BLOCKER about X") instead of the raw rule-violation string. Returns {} when the
    gated output isn't a challenger critique (analyst/drafter/etc.)."""
    out: dict = {}
    if not findings:
        return out
    vm = _VERDICT_RE.search(findings)
    if vm:
        out["verdict"] = vm.group(1).upper()
    wm = _WEAKNESSES_RE.search(findings)
    if wm and wm.group(1).strip():
        weak = wm.group(1).strip()
        out["weaknesses"] = weak[:4000]
        # Pull the first BLOCKER-severity finding's text so the dialogue can name it.
        for item in re.split(r"\n(?=-\s*finding:)", weak):
            if _BLOCKER_RE.search(item):
                fm = re.search(r"finding:\s*(.+)", item)
                if fm:
                    out["blocker"] = fm.group(1).strip()[:300]
                break
    return out


def _resolve_output_kind(agent_config: dict) -> str:
    """Mirror of the runtime's resolution. Kept inline to avoid importing
    runtime code into the Lambda.

    Resolution order:
      1. Explicit `output_kind` field — authoritative
      2. Legacy `chat_mode: true` flag — deprecated alias for "chat"
      3. agent_id-based heuristics for auto-injected platform agents
      4. Default: "analyst"
    """
    valid = {"analyst", "synthesizer", "researcher", "challenger", "reporter",
             "drafter", "creative", "chat", "operator"}
    explicit = agent_config.get("output_kind")
    if explicit and explicit in valid:
        return explicit
    if bool(agent_config.get("chat_mode")):
        return "chat"
    aid = agent_config.get("id", "")
    if aid == "challenger_agent":
        return "challenger"
    if aid == "report_agent":
        return "reporter"
    return "analyst"


# ─── Per-kind validators ─────────────────────────────────────────────────────
#
# Each validator returns either:
#   - dict with structured fields (handed off to downstream agents), or
#   - None if validation passes but there's nothing to extract for handoff
# It raises ValueError on contract violation (which fails the agent loudly).
#
# This replaces the universal extract_structured-or-fail check that forced
# every agent through the analyst contract.

def _artifact_complete(fmt_type: str, content: str, is_binary: bool = False) -> bool:
    """Completeness guard for typed artifacts built via code_interpreter. A
    truncated artifact (e.g. an HTML shell with an empty <body> because the
    drafter ran out of output budget mid-write) 'exists' but is unusable — this
    lets the run GATE on it instead of shipping an empty deliverable as 'complete'."""
    if is_binary:
        if not (content and len(content) > 100):  # base64 payload present
            return False
        # PDF-specific: reject a STACKED multi-document file. Repeating
        # fpdf.output()/appending writes several COMPLETE PDFs into one file
        # (buyerside 2026-06-23: 7 stacked docs → viewers showed cover-only).
        # A clean PDF has exactly ONE '%PDF-' header; >1 means concatenated
        # documents. (We key on the header, not '%%EOF', because a valid
        # incrementally-UPDATED PDF legitimately has multiple %%EOF but one header.)
        if fmt_type == "pdf":
            try:
                import base64
                raw = base64.b64decode(content)
                if raw.count(b"%PDF-") > 1:
                    return False
            except Exception:
                pass  # undecodable → fall through (size check already passed)
        return True
    c = (content or "").strip()
    if not c:
        return False
    low = c.lower()
    if fmt_type == "html":
        if "</html>" not in low or "</body>" not in low:
            return False
        bi = low.find("<body")
        body = low[bi:] if bi >= 0 else low
        text = re.sub(r"<[^>]+>", "", body)  # strip tags → visible text
        return len(text.strip()) > 80
    if fmt_type in ("json", "yaml", "csv"):
        return len(c) > 20  # structure is checked by the format validators
    return len(c) > 40


def _validate_analyst(output: str, agent_id: str) -> dict:
    """Analyst/synthesizer: must have ## STRUCTURED FINDINGS section."""
    s = extract_structured(output)
    if s is None:
        raise ValueError(
            f"Agent {agent_id} (kind=analyst) did not produce required "
            f"'## STRUCTURED FINDINGS' section. Output ended with: "
            f"{output[-300:]!r}"
        )
    return s


def _validate_challenger(output: str, agent_id: str) -> dict:
    """Challenger: must have ## VERDICT (with valid keyword) + ## WEAKNESSES.
    Enforces verdict-consistency: any BLOCKER severity → VERDICT must be WEAK.
    """
    v_match = _VERDICT_RE.search(output or "")
    if not v_match:
        raise ValueError(
            f"Agent {agent_id} (kind=challenger) did not produce a valid "
            f"'## VERDICT' section with one of STRONG/ADEQUATE/WEAK. "
            f"Output ended with: {output[-300:]!r}"
        )
    verdict = v_match.group(1).upper()

    w_match = _WEAKNESSES_RE.search(output or "")
    if not w_match or not w_match.group(1).strip():
        raise ValueError(
            f"Agent {agent_id} (kind=challenger) did not produce a "
            f"'## WEAKNESSES' section."
        )
    weaknesses_text = w_match.group(1)

    has_blocker = bool(_BLOCKER_RE.search(weaknesses_text))
    if has_blocker and verdict != "WEAK":
        raise ValueError(
            f"Agent {agent_id} (kind=challenger) verdict-consistency violation: "
            f"WEAKNESSES contains a BLOCKER finding but VERDICT is {verdict}. "
            f"Any open BLOCKER requires VERDICT: WEAK."
        )

    return {
        "verdict": verdict,
        "weaknesses": weaknesses_text.strip(),
    }


def _validate_reporter(output: str, agent_id: str) -> dict:
    """Reporter: prose narrative ≥200 chars. Must NOT contain STRUCTURED FINDINGS
    section (that's for analysts; reporters synthesise into prose).
    """
    if not output or len(output.strip()) < 200:
        raise ValueError(
            f"Agent {agent_id} (kind=reporter) produced insufficient output "
            f"({len(output.strip())} chars; need ≥200). Reporters must write "
            f"a substantive prose narrative, not a stub."
        )
    return {"prose": output.strip()}


def _validate_chat(output: str, agent_id: str) -> dict:
    """Chat agent: any non-empty response."""
    if not output or not output.strip():
        raise ValueError(
            f"Agent {agent_id} (kind=chat) produced empty output."
        )
    return {"answer": output.strip()}


def _validate_drafter(output: str, agent_id: str) -> dict:
    """Drafter: must contain ## SUMMARY header + a body. The output_format
    layer (Phase 5e.7+) does deeper format/template validation; here we
    only check the contract section is present and the output is substantive.
    """
    if not _SUMMARY_HEADER_RE.search(output or ""):
        raise ValueError(
            f"Agent {agent_id} (kind=drafter) did not produce required "
            f"'## SUMMARY' section. Output ended with: {output[-300:]!r}"
        )
    if len(output.strip()) < 100:
        raise ValueError(
            f"Agent {agent_id} (kind=drafter) produced a stub output "
            f"({len(output.strip())} chars). Drafters must produce the actual "
            f"document body, not just a summary."
        )
    return {"document": output.strip()}


def _validate_creative(output: str, agent_id: str) -> dict:
    """Creative: any non-empty output. Format validation comes from output_format."""
    if not output or len(output.strip()) < 50:
        raise ValueError(
            f"Agent {agent_id} (kind=creative) produced insufficient output "
            f"({len(output.strip())} chars). Empty/stub output rejected."
        )
    return {"artifact": output.strip()}


def _validate_operator(output: str, agent_id: str) -> dict:
    """Operator: must have ## RESULT with one of SUCCESS/FAILURE/DRY_RUN/
    AWAITING_APPROVAL.
    """
    r_match = _RESULT_RE.search(output or "")
    if not r_match:
        raise ValueError(
            f"Agent {agent_id} (kind=operator) did not produce required "
            f"'## RESULT' section with valid status. Output ended with: "
            f"{output[-300:]!r}"
        )
    return {"result": r_match.group(1).upper(), "body": output.strip()}


_OUTPUT_VALIDATORS = {
    "analyst":     _validate_analyst,
    "researcher":  _validate_analyst,
    "synthesizer": _validate_analyst,    # same contract as analyst
    "challenger":  _validate_challenger,
    "reporter":    _validate_reporter,
    "chat":        _validate_chat,
    "drafter":     _validate_drafter,
    "creative":    _validate_creative,
    "operator":    _validate_operator,
}


# ─── Phase 5e: output_format validators ─────────────────────────────────────
#
# Format validation runs AFTER the kind validator. Agents declaring
# output_format: { type: json | yaml, ... } get their fenced code block
# extracted, parsed, and (eventually) schema-validated. Failures fail the
# agent with a clear message; the parsed object propagates downstream so
# consumers don't reparse markdown.

_FENCED_BLOCK_RE = re.compile(
    r"```(?:(?P<lang>\w+))?\s*\n(?P<body>.*?)\n```",
    re.DOTALL,
)

_FORMAT_EXTENSIONS = {
    "markdown": ".txt",
    "json":     ".json",
    "yaml":     ".yaml",
    "html":     ".html",
    "csv":      ".csv",
    "pdf":      ".pdf",
    "docx":     ".docx",
    "png":      ".png",
}


def _resolve_output_format(agent_config: dict) -> dict:
    """Mirror of the runtime's resolution. Default markdown matches
    pre-Phase-5e behaviour (output.txt only).
    """
    raw = agent_config.get("output_format") or {}
    if not isinstance(raw, dict):
        raw = {}
    ftype = raw.get("type", "markdown")
    file_ext = raw.get("file_extension") or _FORMAT_EXTENSIONS.get(ftype, ".txt")
    validate = bool(raw.get("validate", ftype in ("json", "yaml", "html", "csv")))
    return {
        "type": ftype,
        "file_extension": file_ext,
        "schema_id": raw.get("schema_id"),
        "validate": validate,
        "save_as": raw.get("save_as"),
    }


def _extract_fenced_block(output: str, lang: str) -> str | None:
    """Find the LAST fenced code block matching `lang`. Falls back to the
    LAST untyped fenced block if no language-tagged block is present
    (some models drop the lang annotation under load).
    """
    matches = list(_FENCED_BLOCK_RE.finditer(output or ""))
    if not matches:
        return None
    typed = [m for m in matches if (m.group("lang") or "").lower() == lang.lower()]
    if typed:
        return typed[-1].group("body").strip()
    untyped = [m for m in matches if not m.group("lang")]
    if untyped:
        return untyped[-1].group("body").strip()
    return None


def _validate_format_json(output: str, agent_id: str):
    """Parse fenced JSON block. Returns parsed object."""
    body = _extract_fenced_block(output, "json")
    if body is None:
        raise ValueError(
            f"Agent {agent_id} declared output_format type=json but produced no "
            f"fenced ```json ... ``` block. Output ended with: {output[-300:]!r}"
        )
    try:
        return json.loads(body)
    except json.JSONDecodeError as e:
        raise ValueError(
            f"Agent {agent_id} produced an invalid JSON block: {e}. "
            f"Block preview: {body[:300]!r}"
        )


def _validate_format_yaml(output: str, agent_id: str):
    """Parse fenced YAML block. Returns parsed object."""
    body = _extract_fenced_block(output, "yaml")
    if body is None:
        raise ValueError(
            f"Agent {agent_id} declared output_format type=yaml but produced no "
            f"fenced ```yaml ... ``` block. Output ended with: {output[-300:]!r}"
        )
    try:
        return yaml.safe_load(body)
    except yaml.YAMLError as e:
        raise ValueError(
            f"Agent {agent_id} produced an invalid YAML block: {e}. "
            f"Block preview: {body[:300]!r}"
        )


# Minimum viable HTML well-formedness check: must contain <html and </html>
# tags (case-insensitive). Stricter validation (DTD, schema, accessibility)
# can be layered on later. We deliberately don't use html.parser here because
# its tolerance defeats the "fail loud" discipline.
_HTML_OPEN_RE = re.compile(r"<\s*html[\s>]", re.IGNORECASE)
_HTML_CLOSE_RE = re.compile(r"<\s*/\s*html\s*>", re.IGNORECASE)


def _validate_format_html(output: str, agent_id: str):
    """Parse fenced HTML block, basic well-formedness check.
    Returns the HTML string (not parsed — HTML is consumed as text by
    consumers; storage saves it directly to output.html).

    Two-tier extraction: prefer fenced ```html``` block (matches the
    contract). If absent, fall back to finding a complete <html>...</html>
    document anywhere in the output — HTML is self-identifying so this
    is safe and avoids penalising agents that emit the document inline
    instead of fenced.
    """
    body = _extract_fenced_block(output, "html")
    if body is None:
        # Fallback: find inline <html>...</html> document
        m_open = _HTML_OPEN_RE.search(output or "")
        m_close = _HTML_CLOSE_RE.search(output or "")
        if m_open and m_close and m_close.end() > m_open.start():
            body = (output or "")[m_open.start():m_close.end()].strip()
        else:
            raise ValueError(
                f"Agent {agent_id} declared output_format type=html but produced "
                f"neither a fenced ```html ... ``` block nor an inline "
                f"<html>...</html> document. Output ended with: {output[-300:]!r}"
            )
    if not _HTML_OPEN_RE.search(body) or not _HTML_CLOSE_RE.search(body):
        raise ValueError(
            f"Agent {agent_id}'s HTML block is not well-formed — missing "
            f"<html> ... </html> wrapper. Block preview: {body[:300]!r}"
        )
    return body


def _validate_format_csv(output: str, agent_id: str):
    """Parse fenced CSV block. Returns the CSV string (consumed as text).
    Validates: ≥1 header row + ≥1 data row, columns line up.
    """
    import csv
    import io as _io
    body = _extract_fenced_block(output, "csv")
    if body is None:
        raise ValueError(
            f"Agent {agent_id} declared output_format type=csv but produced no "
            f"fenced ```csv ... ``` block. Output ended with: {output[-300:]!r}"
        )
    try:
        reader = csv.reader(_io.StringIO(body))
        rows = list(reader)
    except csv.Error as e:
        raise ValueError(
            f"Agent {agent_id} produced an invalid CSV block: {e}. "
            f"Block preview: {body[:300]!r}"
        )
    if len(rows) < 2:
        raise ValueError(
            f"Agent {agent_id}'s CSV must have at least a header row + one data "
            f"row (got {len(rows)} rows). Block preview: {body[:300]!r}"
        )
    header_cols = len(rows[0])
    if header_cols < 1:
        raise ValueError(
            f"Agent {agent_id}'s CSV header row is empty."
        )
    # Soft check column count consistency — warn on mismatch but don't fail
    # (some CSVs legitimately have variable column counts at the tail).
    return body


_FORMAT_VALIDATORS = {
    "json": _validate_format_json,
    "yaml": _validate_format_yaml,
    "html": _validate_format_html,
    "csv":  _validate_format_csv,
    # pdf / docx / png handled in later phases
}


def compute_role(agent_id: str, depends_on: list) -> str:
    """Classify the agent's role from its DAG position.

    Returns: "challenger" | "reporter" | "leaf" | "synthesizer".
    The agent uses this to apply role-specific framing in its system prompt.
    """
    if agent_id == "challenger_agent":
        return "challenger"
    if agent_id == "report_agent":
        return "reporter"
    if not depends_on:
        return "leaf"
    return "synthesizer"


def extract_structured(output: str) -> dict | None:
    """Parse the mandatory `## STRUCTURED FINDINGS` and optional `## OPEN QUESTIONS`
    sections from an agent's output. Returns None if findings section is missing —
    callers must treat that as an error, not a fallback case.
    """
    f_match = _FINDINGS_RE.search(output or "")
    if not f_match:
        return None
    findings = f_match.group(1).strip()
    if not findings:
        return None
    q_match = _QUESTIONS_RE.search(output or "")
    ek_match = _ENTITY_KEY_RE.search(output or "")
    entity_key = ""
    if ek_match:
        # take the first non-empty line of the section, slugified
        for line in ek_match.group(1).strip().splitlines():
            cleaned = line.strip().lstrip("-").strip().strip("<>").strip("`")
            if cleaned:
                entity_key = _slugify_entity_key(cleaned)
                break
        # Guard: a subject-less run may emit a refusal ("unable to generate entity key…")
        # into this section. Reject non-key prose so it is treated as NO key — the
        # auto-persist empty-key guard then SKIPS the write instead of persisting junk.
        if not _valid_entity_key(entity_key):
            entity_key = ""
    return {
        "findings": findings,
        "open_questions": q_match.group(1).strip() if q_match else "",
        "entity_key": entity_key,
    }


def _fetch_s3_text(uri: str) -> str:
    """Best-effort fetch of an s3://bucket/key object as text. Empty on any failure.
    Used to recover an upstream agent's raw output (which is staged to S3, out of
    SFN state) when it produced no STRUCTURED FINDINGS — so build_conversation can
    degrade gracefully instead of crashing the run."""
    try:
        if not uri or not uri.startswith("s3://"):
            return ""
        bucket, _, key = uri[len("s3://"):].partition("/")
        return s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8", "replace")
    except Exception:
        return ""


def build_conversation(depends_on: list, results: dict) -> str:
    """Build per-upstream-agent context blocks from each upstream's output.

    Phase 5e: dispatch on the upstream agent's output_kind so different
    kinds hand off different content shapes:
      - analyst/synthesizer:  STRUCTURED FINDINGS bullets (the existing
        behaviour; still the dominant case)
      - challenger:           VERDICT + WEAKNESSES section
      - drafter:              the drafted document body
      - creative:             the artifact body
      - reporter/chat:        terminal — they don't hand off (depending on
        them is a YAML smell; we still pass the prose if invoked, since
        skipping it would silently drop context for accidental dependers)
      - operator:             RESULT + DIFF body

    Degrades gracefully and NEVER raises: if an upstream agent produced no
    STRUCTURED FINDINGS (e.g. the operator Accepted its output_kind gate), the
    run must still proceed — we fall back to the agent's raw output (fetched from
    S3) or a clear placeholder. The output_kind/citation gates are the loud-fail
    point; build_conversation must not crash the run downstream of an accepted gate.
    """
    if not depends_on:
        return ""
    if not isinstance(results, dict):
        raise ValueError(f"Expected results dict, got {type(results).__name__}")

    parts = []
    for dep_id in depends_on:
        dep_result = results.get(dep_id)
        if not isinstance(dep_result, dict):
            # NEVER crash the run on a missing/odd upstream result — the operator
            # may have accepted a gate for it. Degrade with a clear placeholder.
            parts.append(f"[{dep_id} — upstream result unavailable]")
            continue

        upstream_kind = dep_result.get("output_kind") or _guess_kind_from_output(dep_result)
        upstream_output = dep_result.get("output", "") or ""

        # Deterministic decision engine: if this upstream computed a decision, surface the
        # COMPUTED labels/outcome so the deliverable agent renders them verbatim (does not re-decide).
        if isinstance(dep_result.get("decision"), dict):
            parts.append(_render_decision_context(dep_id, dep_result["decision"]))

        if upstream_kind in ("analyst", "synthesizer", "researcher"):
            # Extract STRUCTURED FINDINGS + OPEN QUESTIONS.
            structured = dep_result.get("structured")
            if not structured:
                structured = extract_structured(upstream_output)
            if structured:
                block = f"[{dep_id} | analyst]\n{structured['findings']}"
                if structured.get("open_questions"):
                    block += f"\n\nOpen questions from {dep_id}:\n{structured['open_questions']}"
            else:
                # No STRUCTURED FINDINGS section. The output_kind gate already
                # surfaced this to the operator — if they Accepted, the run MUST
                # proceed. NEVER crash here (double-jeopardy with the gate).
                # Degrade to the agent's raw output (staged to S3, so fetch it).
                raw = upstream_output or _fetch_s3_text(dep_result.get("output_s3_uri", ""))
                body = (raw or "").strip()[:4000] or "(agent produced no parseable findings)"
                block = f"[{dep_id} | analyst — no STRUCTURED FINDINGS section; using raw output]\n{body}"

        elif upstream_kind == "challenger":
            # Hand off the verdict + weaknesses to downstream so they can act
            # on the red-team result rather than re-reading prose.
            v_match = _VERDICT_RE.search(upstream_output)
            w_match = _WEAKNESSES_RE.search(upstream_output)
            verdict = v_match.group(1).upper() if v_match else "UNKNOWN"
            weaknesses = w_match.group(1).strip() if w_match else "(no weaknesses section)"
            block = (
                f"[{dep_id} | challenger]\n"
                f"VERDICT: {verdict}\n\n"
                f"WEAKNESSES:\n{weaknesses}"
            )

        elif upstream_kind == "drafter":
            # The drafted document IS the handoff. Downstream gets the body so
            # they can reference / iterate on it.
            block = f"[{dep_id} | drafter — document body below]\n{upstream_output.strip()}"

        elif upstream_kind == "creative":
            block = f"[{dep_id} | creative — artifact below]\n{upstream_output.strip()}"

        elif upstream_kind == "operator":
            r_match = _RESULT_RE.search(upstream_output)
            result = r_match.group(1).upper() if r_match else "UNKNOWN"
            block = f"[{dep_id} | operator]\nRESULT: {result}\n\n{upstream_output.strip()}"

        elif upstream_kind in ("reporter", "chat"):
            # These are terminal kinds. Depending on them is unusual; surface
            # a warning but pass the prose so the downstream agent has context.
            block = (
                f"[{dep_id} | {upstream_kind} — note: this kind is normally terminal]\n"
                f"{upstream_output.strip()}"
            )

        else:
            # Unknown kind — fall back to STRUCTURED FINDINGS extraction with
            # graceful degradation (prose if findings missing).
            structured = extract_structured(upstream_output)
            if structured:
                block = f"[{dep_id}]\n{structured['findings']}"
            else:
                block = f"[{dep_id}]\n{upstream_output.strip()[:2000]}"

        parts.append(block)

    return "\n\n---\n\n".join(parts)


def _guess_kind_from_output(dep_result: dict) -> str:
    """When an upstream result doesn't carry output_kind explicitly (e.g.
    SFN history from a v1 ecosystem compiled before Phase 5e), guess from
    the output text. Conservative fallback: assume analyst.
    """
    agent_id = dep_result.get("agent_id") or ""
    if agent_id == "challenger_agent":
        return "challenger"
    if agent_id == "report_agent":
        return "reporter"
    return "analyst"


# ─── Memory auto-mirror (Option B, YAML-gated) ──────────────────────────────

_MEMORY_ID_ENV = "TESSERA_MEMORY_ID"

# --- Self-managed catalog records --------------------------------------------
# Durable entity CATALOG facts (/tessera/catalog/*) are written DIRECTLY as
# full-fidelity memory records via BatchCreateMemoryRecords against the
# self-managed "tessera_catalog" strategy — NOT as USER-role events on the
# semantic strategy. This MUST match the runtime tool path in
# tessera_platform/agentcore/src/main.py (_catalog_write_record), or auto-
# persisted catalog facts land in the wrong strategy/namespace and the catalog
# read/search path (which filters memoryStrategyId=self-managed) never finds them.
CATALOG_STRATEGY_NAME = "tessera_catalog"
_CATALOG_STRATEGY = {"id": None}


def _is_catalog_ns(ns: str) -> bool:
    """True for a catalog namespace, with or without a leading /tessera."""
    s = (ns or "").strip("/")
    return s == "catalog" or s.startswith("catalog/") or s == "tessera/catalog" or s.startswith("tessera/catalog/")


def _canonical_catalog_ns(ns: str) -> str:
    """Normalize an operator-declared catalog path to the literal /tessera/...
    namespace the runtime read/search path uses (prepend /tessera, strip trailing /)."""
    s = (ns or "").strip("/")
    if s != "tessera" and not s.startswith("tessera/"):
        s = "tessera/" + s
    return "/" + s


def _inject_catalog_version(ns: str, version: str) -> str:
    """Insert a blue/green version segment right after the `catalog` segment so a
    synthesis (or any catalog) write lands in a specific catalog VERSION namespace:
    `/tessera/catalog/<version>/<type>/<key>`. Domain-agnostic — any catalog producer
    honors a `catalog_version` mission field; empty version leaves the path unchanged
    (the legacy/active namespace). Mirrors tessera_platform.catalog.CatalogStore._ns."""
    if not version:
        return ns
    parts = [p for p in (ns or "").strip("/").split("/") if p]
    lead = ["tessera"] if parts[:1] == ["tessera"] else []
    if lead:
        parts = parts[1:]
    if not parts or parts[0] != "catalog":
        return ns
    return "/" + "/".join(lead + ["catalog", version] + parts[1:])


def _catalog_strategy_id(memory_id: str) -> str:
    """Resolve (and cache) the self-managed catalog strategy id by name."""
    if _CATALOG_STRATEGY["id"]:
        return _CATALOG_STRATEGY["id"]
    if not memory_id:
        return ""
    try:
        cpc = boto3.client("bedrock-agentcore-control", region_name=REGION)
        mem = cpc.get_memory(memoryId=memory_id).get("memory", {})
        for s in mem.get("strategies", []) or []:
            if s.get("name") == CATALOG_STRATEGY_NAME or s.get("type") == "CUSTOM":
                _CATALOG_STRATEGY["id"] = s.get("strategyId") or ""
                break
    except Exception as e:
        print(f"[catalog] strategy id resolve failed: {type(e).__name__}: {str(e)[:160]}")
    return _CATALOG_STRATEGY["id"] or ""


def _fit_catalog_content(content: str, limit: int = 15800) -> str:
    """AgentCore Memory rejects any record whose content.text exceeds 16,000 chars
    (BatchCreateMemoryRecords ValidationException). Enriched risk dossiers can exceed
    that, so fit the record UNDER the limit while keeping it valid JSON + all fields:
    first compact the JSON, then iteratively shorten only the LONGEST string leaves
    (verbose prose) until it fits. Non-JSON falls back to a hard truncate."""
    if len(content) <= limit:
        return content
    try:
        obj = json.loads(content)
    except Exception:
        return content[:limit]

    def dump(o):
        return json.dumps(o, separators=(",", ":"), default=str, ensure_ascii=False)

    s = dump(obj)
    if len(s) <= limit:
        return s

    def longest_leaf(o, best):
        if isinstance(o, dict):
            items = o.items()
        elif isinstance(o, list):
            items = enumerate(o)
        else:
            return best
        for k, v in items:
            if isinstance(v, str):
                if len(v) > best[0]:
                    best = (len(v), o, k)
            else:
                best = longest_leaf(v, best)
        return best

    for _ in range(600):
        n, ref, key = longest_leaf(obj, (0, None, None))
        if ref is None or n < 40:
            break
        val = ref[key]
        ref[key] = val[:max(40, int(len(val) * 0.6))].rstrip() + "…"
        s = dump(obj)
        if len(s) <= limit:
            break
    return s if len(s) <= limit else s[:limit]


def _catalog_write_record(ac, memory_id: str, namespace: str, content: str, agent_id: str) -> dict:
    """Upsert one full-fidelity dossier record at the exact catalog namespace
    (delete existing records there, then create) via the self-managed strategy."""
    from datetime import datetime, timezone as _tz
    sid = _catalog_strategy_id(memory_id)
    if not sid:
        return {"error": "catalog strategy unavailable — record not written"}
    ns = _canonical_catalog_ns(namespace)
    deleted = 0
    try:
        existing = ac.list_memory_records(memoryId=memory_id, namespace=ns, maxResults=100)
        ids = [r.get("memoryRecordId") for r in (existing.get("memoryRecordSummaries") or existing.get("memoryRecords") or []) if r.get("memoryRecordId")]
        if ids:
            ac.batch_delete_memory_records(memoryId=memory_id, records=[{"memoryRecordId": i} for i in ids])
            deleted = len(ids)
    except Exception as e:
        print(f"[catalog] upsert delete skipped ns={ns}: {type(e).__name__}: {str(e)[:120]}")
    # Full fidelity: if the dossier exceeds the 16k Memory cap, offload the FULL record to
    # S3 and keep a lean record + content_ref in Memory (blob.pack) — never truncate. Falls
    # back to the fit-shrink only for non-JSON content or when no S3 bucket is configured.
    text = content
    try:
        from tessera_platform.catalog import blob, engine as _eng
        _rec = json.loads(content)
        if isinstance(_rec, dict):
            _stored, _off = blob.pack(_rec, ns, _eng.content_hash(_rec))
            text = json.dumps(_stored, separators=(",", ":"), ensure_ascii=False)
        else:
            text = _fit_catalog_content(content)
    except Exception as e:
        print(f"[catalog] offload skipped ns={ns}: {type(e).__name__}: {str(e)[:120]} — falling back to fit")
        text = _fit_catalog_content(content)
    rec = {
        "requestIdentifier": str(uuid.uuid4()),
        "namespaces": [ns],
        "content": {"text": text},
        "timestamp": datetime.now(_tz.utc),
        "memoryStrategyId": sid,
        "metadata": {"writer_agent_id": {"stringValue": (agent_id or "")[:200]}},
    }
    resp = ac.batch_create_memory_records(memoryId=memory_id, records=[rec])
    failed = resp.get("failedRecords") or []
    if failed:
        return {"error": f"record write failed: {str(failed[0])[:200]}"}
    rid = (resp.get("successfulRecords") or [{}])[0].get("memoryRecordId", "")
    return {"status": "written", "namespace": ns, "memoryRecordId": rid, "replaced": deleted}


def _auto_mirror_findings(namespaces: list, session_id: str, agent_id: str,
                          findings: str, open_questions: str, user_id: str = "",
                          entity_key: str = "", raw_output: str = "",
                          catalog_type: str = "", catalog_version: str = "") -> None:
    """Write the agent's structured findings to AgentCore Memory for each
    auto-persist namespace declared in YAML.

    Templates {sessionId} and {agentId} are substituted at runtime. Best-effort:
    failures are logged but do not raise (we don't want a memory write failure
    to blow up the Step Function run; the in-stream STRUCTURED FINDINGS already
    flow downstream via dependency_context).
    """
    import os
    from datetime import datetime, timezone as _tz

    memory_id = os.environ.get(_MEMORY_ID_ENV, "")
    if not memory_id or not namespaces:
        return

    # Resolve template placeholders in each declared namespace path. The
    # operator-declared paths (e.g. /catalog/properties/, /sessions/{sessionId}/
    # agents/{agentId}/) get {sessionId}/{agentId}/{ecosystemId}/{date}
    # substituted at runtime. We then loop per-namespace and create one event
    # per resolved path, mapping each to (actorId, sessionId) using the same
    # logic memory_read uses. This is critical: writes and reads must agree on
    # the storage location, otherwise readers find nothing.
    namespace_paths = []
    template_vars = _template_vars(session_id=session_id, agent_id=agent_id, catalog_type=catalog_type)
    for raw_ns in namespaces:
        ns = _substitute_template(raw_ns or "", template_vars)
        namespace_paths.append(ns)

    body_parts = []
    # Embed provenance in the body since the Lambda's boto3 bedrock-agentcore
    # model does not accept the `metadata` parameter on create_event (the
    # AgentCore Runtime container ships a newer model that does — but this
    # Lambda runtime does not). Readers need to be able to see who wrote what
    # without external metadata, so we put it in the text.
    body_parts.append(f"_writer_agent_id_: {agent_id}")
    body_parts.append(f"_session_id_: {session_id}")
    if findings:
        body_parts.append("## STRUCTURED FINDINGS\n" + findings.strip())
    if open_questions:
        body_parts.append("## OPEN QUESTIONS\n" + open_questions.strip())
    body = "\n\n".join(body_parts)
    if not body:
        return

    ac = boto3.client("bedrock-agentcore", region_name=REGION)
    succeeded = []
    failed = []
    for ns in namespace_paths:
        # CATALOG (durable entity dossiers) -> self-managed strategy: write the
        # FULL finding DIRECTLY as a memory record (upsert by namespace), full-
        # fidelity + natively retrievable. This MUST match the runtime memory_write
        # tool, or the catalog read/search path (self-managed-scoped) finds nothing.
        # Catalog namespaces are org-shared (no user prefix), so user_id is ignored.
        if _is_catalog_ns(ns):
            # Blue/green: a versioned build passes `catalog_version` in the mission so the
            # synthesized records land in that VERSION's namespace (non-destructive to the
            # live catalog). Generic — any domain's catalog auto-persist honors it.
            ns = _inject_catalog_version(ns, catalog_version)
            # Multi-record producers (risk-synthesis) emit several durable entities as
            # fenced json blocks — write each as its own keyed record. Fall back to the
            # single STRUCTURED-FINDINGS + ## ENTITY KEY text record otherwise.
            json_recs = _extract_json_records(raw_output) if raw_output else []
            keyed = [(_record_key(r), r) for r in json_recs]
            keyed = [(k, r) for k, r in keyed if k]  # empty-key guard (skip unkeyable)
            try:
                if keyed:
                    written, errs = [], []
                    for k, rec in keyed:
                        target = ns.rstrip("/") + "/" + k
                        res = _catalog_write_record(ac, memory_id, target, json.dumps(rec), agent_id)
                        (errs if res.get("error") else written).append(res.get("namespace") or target)
                    if written:
                        succeeded.append(f"{ns} -> {len(written)} entity records {written}")
                    for e in errs:
                        failed.append(f"{e} (catalog record): write error")
                elif entity_key:
                    target = ns.rstrip("/") + "/" + entity_key
                    res = _catalog_write_record(ac, memory_id, target, body, agent_id)
                    if res.get("error"):
                        failed.append(f"{target} (catalog record): {res['error']}")
                    else:
                        succeeded.append(f"{ns} -> record {res.get('namespace')} key={entity_key} (replaced {res.get('replaced', 0)})")
                else:
                    # Empty-key guard: never write to the bare type path (that produced
                    # junk records). A catalog producer that emits neither json records
                    # nor a ## ENTITY KEY is skipped and logged loudly.
                    failed.append(f"{ns}: SKIPPED catalog write — agent emitted no json records and no ## ENTITY KEY")
            except Exception as e:
                failed.append(f"{ns} (catalog record): {type(e).__name__}: {str(e)[:300]}")
            continue
        actor, session = _namespace_to_actor_session(ns)
        # SCOPING — must match the runtime memory_write/memory_read tools exactly,
        # or reads will look under a different actorId than this write used and
        # find nothing. Rule: org-scoped namespaces (/tessera/org/*, /tessera/
        # catalog/*) are shared (no user prefix); everything else is user-scoped.
        is_org_scoped = ns.startswith("/tessera/org/") or ns.startswith("/tessera/catalog/")
        if user_id and not is_org_scoped:
            actor = f"{user_id}-{actor}"
        # AgentCore CreateEvent requires a non-empty sessionId. Single-segment
        # paths map to session="" — fall back to the RUN's session id (matching
        # the runtime tool, which uses run_session_id, NOT "default").
        if not session:
            session = session_id or "default"
        try:
            ac.create_event(
                memoryId=memory_id,
                actorId=actor,
                sessionId=session,
                eventTimestamp=datetime.now(_tz.utc),
                payload=[{
                    "conversational": {
                        # USER role: AgentCore's SEMANTIC strategy only extracts records
                        # from USER-role events (confirmed 2026-06-30) — a lone ASSISTANT
                        # event is never extracted, which is why retrieve_memory_records
                        # was empty. Non-catalog findings (sessions/handoff/org) use USER so
                        # they're searchable; catalog namespaces took the record path above.
                        "role": "USER",
                        "content": {"text": body[:8000]},
                    }
                }],
                # NOTE: metadata={} is intentionally omitted. The Lambda's
                # bundled boto3 bedrock-agentcore model rejects it ("Unknown
                # parameter in input: 'metadata'"). Provenance is embedded in
                # the body text above.
            )
            succeeded.append(f"{ns} -> ({actor}, {session})")
        except Exception as e:
            # Loud, structured failure log. Do not crash the SFN run — the
            # in-stream STRUCTURED FINDINGS still flow via dependency_context,
            # so downstream agents have the data even when memory mirroring
            # fails. But we WANT this visible to operators so it gets fixed.
            failed.append(f"{ns} -> ({actor}, {session}): {type(e).__name__}: {str(e)[:300]}")

    if succeeded:
        print(f"auto-mirror OK agent={agent_id}: " + " | ".join(succeeded))
    if failed:
        print(
            f"auto-mirror FAILED agent={agent_id} session={session_id} — "
            f"{len(failed)}/{len(namespace_paths)} namespaces did not persist. "
            f"Downstream agents reading these namespaces will see empty results. "
            f"Failures: " + " || ".join(failed)
        )


def _template_vars(session_id: str, agent_id: str, catalog_type: str = "") -> dict:
    """Compute substitution values for namespace template placeholders.

    Standard placeholders supported across all ecosystems:
      {sessionId}      — current SFN execution session id
      {agentId}        — invoking agent's id from agent_config
      {ecosystemId}    — derived from session_id prefix (sessions are named
                         <ecosystem-id>-<hex>, e.g. risk-reasoning-2c2a78d1)
      {date}           — UTC date YYYY-MM-DD (useful for daily catalogs)

    Operators can declare additional placeholders in YAML inputs; those land
    on the run state but are out of scope here for now. Future iterations could
    plumb them through agent_config.template_vars.
    """
    from datetime import datetime, timezone as _tz
    eco = ""
    if session_id and "-" in session_id:
        # session id format: <ecosystem-id>-<8hex>
        last = session_id.rsplit("-", 1)[-1]
        if len(last) == 8 and all(c in "0123456789abcdef" for c in last):
            eco = session_id[: -(len(last) + 1)]
    return {
        "sessionId": session_id,
        "agentId": agent_id,
        "ecosystemId": eco,
        "date": datetime.now(_tz.utc).strftime("%Y-%m-%d"),
        # Agent-mode catalog ingestion: the per-source write type (e.g. agentic-threat)
        # so an auto-persist namespace `/catalog/{catalog_type}/` templates correctly.
        "catalog_type": catalog_type or "",
    }


_MISSION_FIELD_RE_TMPL = r"(?im)^\s*{k}\s*[:=]\s*(.+?)\s*$"


def _mission_field(text: str, key: str) -> str:
    """Extract a `key: value` line from the run mission/input_context (best-effort)."""
    import re as _re
    m = _re.search(_MISSION_FIELD_RE_TMPL.format(k=_re.escape(key)), text or "")
    return (m.group(1).strip().strip("`").strip() if m else "")


def _substitute_template(template: str, vars: dict) -> str:
    """Substitute {placeholder} occurrences with values from vars.

    Unrecognised placeholders are LEFT IN PLACE so we don't silently swallow typos.
    The agent-invoker's caller is responsible for surfacing this if it matters.
    """
    if not template:
        return template
    out = template
    for k, v in vars.items():
        out = out.replace("{" + k + "}", str(v) if v is not None else "")
    return out


def _namespace_to_actor_session(namespace: str) -> tuple:
    """Map a logical namespace path like /tessera/catalog/risks to AgentCore
    Memory's (actorId, sessionId) tuple. The strategy template
    /tessera/{actorId}/{sessionId} produces records at exactly the path the
    agent specified.

    First segment after a leading 'tessera' is actorId; remaining segments
    join with '-' to form sessionId. Trailing slash means prefix mode (caller
    handles separately).

    MUST stay in sync with the identical function in
    tessera_platform/agentcore/src/main.py — readers and writers must agree
    on the mapping or events become unreachable.

    Examples:
      /tessera/catalog/risks                -> ("catalog", "risks")
      /tessera/catalog/properties/41-foo    -> ("catalog", "properties-41-foo")
      /catalog/properties/                  -> ("catalog", "properties")  (prefix mode)
      /tessera/sessions/abc/agents/foo      -> ("sessions", "abc-agents-foo")
      /tessera/catalog/                     -> ("catalog", "")  (prefix mode)
      /tessera/org/profile                  -> ("org", "profile")
    """
    if not namespace:
        return ("default", "default")
    import re as _re
    parts = [p for p in namespace.strip("/").split("/") if p]
    # Strip leading "tessera" if present (operator-declared paths may or may not
    # include it; agent-facing tools accept both).
    if parts and parts[0] == "tessera":
        parts = parts[1:]
    if not parts:
        return ("default", "default")
    if len(parts) == 1:
        return (parts[0], "")
    actor = parts[0]
    session = "-".join(parts[1:])
    # AgentCore Memory has limits on actorId/sessionId (length + character set).
    actor = _re.sub(r"[^A-Za-z0-9_-]", "-", actor)[:64] or "default"
    session = _re.sub(r"[^A-Za-z0-9_-]", "-", session)[:64]
    return (actor, session)


def _validate_citations(structured: dict, role_contract: dict) -> None:
    """Phase 4.5: enforce that synthesizer agents cite their declared upstream.

    Raises ValueError loudly when:
      - role_contract.must_cite_upstream is non-empty (= synthesizer), AND
      - findings text contains fewer than `cite_threshold` `[per <upstream_id>:` markers
        for one or more required upstreams, AND
      - findings does NOT contain a `[novel — ` marker (the operator's escape hatch
        for genuinely novel claims with no upstream basis).

    Leaves (no must_cite_upstream) are exempt.

    The format and threshold come from role_contract injected by the save-ecosystem
    compiler at build time, so the rule is configurable per-ecosystem if needed.
    """
    import re as _re
    deps = (role_contract or {}).get("must_cite_upstream") or []
    if not deps:
        return  # leaf agent — no enforcement
    threshold = int((role_contract or {}).get("cite_threshold", 1))
    findings_text = (structured or {}).get("findings", "") or ""
    if not findings_text:
        raise ValueError(
            "synthesizer agent produced empty STRUCTURED FINDINGS. "
            "Synthesizer must extend, reconcile, or challenge upstream findings — not produce nothing."
        )
    # Allow [novel — reason] as an escape hatch for genuinely novel claims.
    has_novel = bool(_re.search(r"\[novel\s+[-—]", findings_text, _re.IGNORECASE))

    missing = []
    for upstream_id in deps:
        # Match `[per <id>` (whitespace-flexible, case-insensitive). Allow `[per id:`,
        # `[per id ` etc. The closing punctuation is whatever the agent wrote.
        pattern = rf"\[per\s+{_re.escape(upstream_id)}\b"
        cites = _re.findall(pattern, findings_text, _re.IGNORECASE)
        if len(cites) < threshold:
            missing.append(f"{upstream_id} (found {len(cites)}, need {threshold})")

    if missing and not has_novel:
        raise ValueError(
            f"synthesizer did not cite required upstream agent(s): {', '.join(missing)}. "
            f"Each upstream must appear as `[per <agent_id>: <upstream finding>]` at least "
            f"{threshold} time(s), OR every claim must be tagged `[novel — <reason>]`. "
            f"Synthesizers exist to build on upstream — standalone findings are rejected."
        )


def resolve_files_for_agent(agent_config, session_id):
    """Resolve ecosystem files matching this agent's file_tags.

    Reads .meta.json files from the ecosystem's files/ prefix in S3,
    matches tags to agent's declared file_tags. For text-readable files,
    reads content and returns it for injection into input_context.
    Max 5 files (Bedrock document block limit).
    """
    file_tags = set(agent_config.get("file_tags", []))
    if not file_tags:
        return []

    eco_id = agent_config.get("_ecosystem_id", "")
    if not eco_id:
        return []

    bucket = os.environ.get("ECOSYSTEMS_BUCKET", "")
    if not bucket:
        return []

    s3 = boto3.client("s3", region_name=REGION)
    prefix = f"ecosystems/{eco_id}/files/"

    try:
        objects = s3.list_objects_v2(Bucket=bucket, Prefix=prefix)
    except Exception:
        return []

    resolved = []
    for obj in objects.get("Contents", []):
        key = obj["Key"]
        if not key.endswith(".meta.json"):
            continue
        try:
            meta = json.loads(s3.get_object(Bucket=bucket, Key=key)["Body"].read())
        except Exception:
            continue
        if meta.get("status") != "ready":
            continue
        if meta.get("tag") in file_tags or "all" in file_tags:
            file_key = key[:-len(".meta.json")]
            # Read file content (text formats only, <500KB)
            content = ""
            if meta.get("format") in ("csv", "txt", "md", "html") and obj.get("Size", meta.get("size", 0)) < 500_000:
                try:
                    content = s3.get_object(Bucket=bucket, Key=file_key)["Body"].read().decode("utf-8", errors="replace")[:50000]
                except Exception:
                    pass
            # For PDFs/docs, check for .extracted.txt
            elif meta.get("extracted"):
                try:
                    content = s3.get_object(Bucket=bucket, Key=file_key + ".extracted.txt")["Body"].read().decode("utf-8", errors="replace")[:50000]
                except Exception:
                    pass

            resolved.append({
                "s3Uri": meta.get("s3Uri", f"s3://{bucket}/{file_key}"),
                "format": meta.get("format", "txt"),
                "name": meta.get("filename", ""),
                "tag": meta.get("tag", ""),
                "summary": meta.get("summary", ""),
                "content": content,
            })
        if len(resolved) >= 5:
            break

    return resolved
