"""tessera_platform.catalog.connectors — declarative fetchers per transport.

A source's `fetch:` block is executed here — no per-domain code. Each transport splits
into a PURE parser (unit-tested with in-memory bytes) and a thin network fetch, so the
parsing logic is verifiable offline.

    fetch(cfg) -> raw structured object   (dispatched on cfg["transport"])

Transports: http_json | http_csv | http_yaml | http_html | github_contents | sdmx
            | s3 | rest_paginated | static

Auth (zero static secrets): cfg["auth"]["credential_provider"] resolves an API key from
AgentCore Identity at call time and injects it (header or query param) — never stored.
"""
import csv
import io
import json
import urllib.parse
import urllib.request


# ── pure parsers (unit-tested) ────────────────────────────────────────────────────
def parse_json(raw_bytes):
    return json.loads(raw_bytes.decode() if isinstance(raw_bytes, (bytes, bytearray)) else raw_bytes)


def parse_csv(text):
    if isinstance(text, (bytes, bytearray)):
        text = text.decode()
    return {"rows": list(csv.DictReader(io.StringIO(text)))}


def parse_yaml(text):
    import yaml
    if isinstance(text, (bytes, bytearray)):
        text = text.decode()
    return yaml.safe_load(text)


def _render_tables_md(html):
    """Render each <table> as pipe-delimited rows so an agent normalizer can read
    tabular data (rates/thresholds) that plain tag-stripping would flatten into an
    unlabeled run of numbers. Best-effort, stdlib-only. Returns '' if no tables."""
    import re
    blocks = []
    for tbl in re.findall(r"(?is)<table[^>]*>.*?</table>", html):
        rows = []
        for tr in re.findall(r"(?is)<tr[^>]*>.*?</tr>", tbl):
            cells = re.findall(r"(?is)<t[hd][^>]*>(.*?)</t[hd]>", tr)
            cells = [re.sub(r"\s+", " ", re.sub(r"(?s)<[^>]+>", " ", c)).strip() for c in cells]
            if any(cells):
                rows.append(" | ".join(cells))
        if rows:
            blocks.append("\n".join(rows))
    return "\n\n".join(blocks)


def _strip_html(html):
    import re
    # drop scripts/styles then tags; collapse whitespace — enough for an agent normalizer
    no_sd = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", html)
    tables_md = _render_tables_md(no_sd)
    text = re.sub(r"(?s)<[^>]+>", " ", no_sd)
    text = re.sub(r"\s+", " ", text).strip()
    # Additive: append a structured rendering of any tables so labels stay paired to
    # values. Prose-only pages are unchanged (tables_md == '').
    if tables_md:
        text = text + "\n\n## TABLES\n" + tables_md
    return text


def parse_html(text, url=None):
    if isinstance(text, (bytes, bytearray)):
        text = text.decode(errors="replace")
    return {"url": url, "html": text, "text": _strip_html(text)}


# ── auth (AgentCore Identity token vault) ─────────────────────────────────────────
def _resolve_api_key(provider_name, region=None):
    """Fetch an API key from AgentCore Identity by credential-provider name. Lazy import
    so offline parsing/tests never touch AWS."""
    import os
    import boto3
    region = region or os.environ.get("AWS_REGION", "us-east-1")
    c = boto3.client("bedrock-agentcore", region_name=region)
    r = c.get_resource_api_key(resourceCredentialProviderName=provider_name)
    return r.get("apiKey") or r.get("secretValue")


def _apply_auth(req_headers, params, cfg):
    auth = cfg.get("auth") or {}
    prov = auth.get("credential_provider")
    if not prov:
        return req_headers, params
    key = _resolve_api_key(prov, cfg.get("region"))
    loc = auth.get("in", "header")
    name = auth.get("name", "Authorization")
    tpl = auth.get("template", "{key}")
    val = tpl.format(key=key)
    if loc == "header":
        req_headers[name] = val
    else:
        params = dict(params or {}); params[name] = val
    return req_headers, params


def _http_get(url, cfg, accept=None):
    headers = dict(cfg.get("headers") or {})
    if accept:
        headers.setdefault("Accept", accept)
    # Some gov WAFs (e.g. ATO) 403 unknown bot UAs; present a common browser UA so
    # legitimate public-page fetches succeed. Overridable per-source via cfg.headers.
    headers.setdefault(
        "User-Agent",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    )
    params = dict(cfg.get("params") or {})
    headers, params = _apply_auth(headers, params, cfg)
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    # SSRF guard: declarative sources are governed, but never let a descriptor point
    # the fetcher at a non-web scheme (file://, gopher://, data:, …).
    if urllib.parse.urlsplit(url).scheme not in ("http", "https"):
        raise ValueError(f"refusing non-http(s) source URL: {url[:60]}")
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=cfg.get("timeout", 120)) as r:  # nosec B310 - scheme validated above
        return r.read()


# ── transports ────────────────────────────────────────────────────────────────────
def _t_http_json(cfg):
    return parse_json(_http_get(cfg["url"], cfg, accept="application/json"))


def _t_http_csv(cfg):
    return parse_csv(_http_get(cfg["url"], cfg, accept="text/csv"))


def _t_http_yaml(cfg):
    return parse_yaml(_http_get(cfg["url"], cfg))


def _t_http_html(cfg):
    return parse_html(_http_get(cfg["url"], cfg, accept="text/html"), url=cfg["url"])


def _t_static(cfg):
    """Inline data declared in the descriptor (constant sources)."""
    return cfg.get("data")


def _t_github_contents(cfg):
    """GitHub contents API dir listing -> download + parse each matching file ->
    {"files": [parsed, ...]}. exts defaults to yaml/yml/json."""
    exts = tuple(cfg.get("exts") or (".yaml", ".yml", ".json"))
    listing = parse_json(_http_get(cfg["url"], cfg, accept="application/json"))
    files = [f for f in listing if isinstance(f, dict) and f.get("name", "").endswith(exts) and f.get("download_url")]
    if cfg.get("limit"):
        files = files[: int(cfg["limit"])]
    out = []
    for f in files:
        try:
            body = _http_get(f["download_url"], cfg)
            out.append(parse_yaml(body) if f["name"].endswith((".yaml", ".yml")) else parse_json(body))
        except Exception:
            continue
    return {"files": out}


def _t_sdmx(cfg):
    """SDMX-JSON REST (e.g. ABS Data API). Returns parsed SDMX-JSON."""
    return parse_json(_http_get(cfg["url"], cfg, accept="application/vnd.sdmx.data+json"))


def _t_s3(cfg):
    import os
    import boto3
    s3 = boto3.client("s3", region_name=cfg.get("region") or os.environ.get("AWS_REGION", "us-east-1"))
    bucket = cfg.get("bucket") or os.environ.get("CATALOG_CONTENT_BUCKET") or os.environ.get("ECOSYSTEMS_BUCKET")
    body = s3.get_object(Bucket=bucket, Key=cfg["key"])["Body"].read()
    key = cfg["key"].lower()
    if key.endswith(".json"):
        return parse_json(body)
    if key.endswith((".yaml", ".yml")):
        return parse_yaml(body)
    if key.endswith(".csv"):
        return parse_csv(body)
    return {"text": body.decode(errors="replace")}


def _t_rest_paginated(cfg):
    """Follow pages via a `page_param` (incrementing) or a `next` JMESPath pointer.
    Collects items from `items_path` (JMESPath) into {"items": [...]}."""
    import jmespath
    items_path = cfg.get("items_path", "items")
    max_pages = int(cfg.get("max_pages", 20))
    url = cfg["url"]
    collected, page = [], 1
    while page <= max_pages and url:
        c = dict(cfg)
        if cfg.get("page_param"):
            c = dict(cfg, params=dict(cfg.get("params") or {}, **{cfg["page_param"]: page}))
        data = parse_json(_http_get(url, c, accept="application/json"))
        batch = jmespath.search(items_path, data) or []
        collected.extend(batch)
        if cfg.get("next_path"):
            url = jmespath.search(cfg["next_path"], data)
            page += 1
            if not url:
                break
        elif cfg.get("page_param"):
            if not batch:
                break
            page += 1
        else:
            break
    return {"items": collected}


CONNECTORS = {
    "http_json": _t_http_json,
    "http_csv": _t_http_csv,
    "http_yaml": _t_http_yaml,
    "http_html": _t_http_html,
    "github_contents": _t_github_contents,
    "sdmx": _t_sdmx,
    "s3": _t_s3,
    "rest_paginated": _t_rest_paginated,
    "static": _t_static,
}


def fetch(cfg):
    """Execute a declarative `fetch:` block -> raw structured object."""
    transport = (cfg or {}).get("transport")
    if transport not in CONNECTORS:
        raise ValueError(f"unknown transport {transport!r}; known: {sorted(CONNECTORS)}")
    return CONNECTORS[transport](cfg)
