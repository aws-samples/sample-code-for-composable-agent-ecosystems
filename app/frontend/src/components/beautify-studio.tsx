"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useClientWebSocket } from "@/hooks/use-client-ws";
import { AskPanel } from "./ask-panel";
import { applyTransforms, verifyInvariant, domOutline, type Transform } from "@/lib/presentation-transforms";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";
const WS_URL = process.env.NEXT_PUBLIC_WS_URL || API_URL.replace(/^http/, "ws");

async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const { fetchAuthSession } = await import("aws-amplify/auth");
  let token = "";
  try {
    const session = await fetchAuthSession({ forceRefresh: false });
    token = session.tokens?.idToken?.toString() || "";
  } catch {}
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(url, { ...init, headers });
}

interface ChatMsg {
  role: "user" | "assistant";
  text: string;
}

const _norm = (s: string) => (s || "").toLowerCase().replace(/\s+/g, " ").trim();

/** Find the report element that best matches a verbatim target snippet — token +
 *  figure fraction scoring, preferring specific/short leaf-ish blocks. Mirrors the
 *  concierge presenter's anchorElement so spotlight lands on the exact passage. */
function findAnchor(doc: Document, target: string): HTMLElement | null {
  const n = _norm(target);
  const words = n.split(" ").filter((w) => w.replace(/[^a-z0-9]/g, "").length >= 4);
  const figures = n.match(/\d[\d.,:§%/-]*\d|\d/g) || [];
  const keys = Array.from(new Set([...words, ...figures]));
  if (!keys.length) return null;
  let best: HTMLElement | null = null;
  let bestScore = 0;
  let bestLen = Infinity;
  const els = Array.from(doc.querySelectorAll("p,li,td,th,h1,h2,h3,h4,h5,blockquote,strong,em,code,span,div"));
  for (const el of els) {
    if (el.children.length > 8) continue;
    const txt = _norm(el.textContent || "");
    if (!txt) continue;
    let hits = 0;
    for (const k of keys) if (txt.includes(k)) hits++;
    if (!hits) continue;
    const frac = hits / keys.length;
    const hasFigure = figures.length > 0 && figures.some((f) => txt.includes(f));
    if (frac < 0.5 && !hasFigure) continue;
    if (frac > bestScore || (frac === bestScore && txt.length < bestLen)) {
      best = el as HTMLElement; bestScore = frac; bestLen = txt.length;
    }
  }
  return best;
}

/** Patch-merge a returned transform list into the current one. The design agent emits
 *  ONLY what changed (a new transform, a replacement for the same type+anchor, or a
 *  {type:"remove"} op) — everything else PERSISTS untouched. This keeps each turn cheap
 *  and, critically, stops an unrelated diagram from being silently regenerated/altered. */
function mergeTransforms(prev: Transform[], patch: Transform[]): Transform[] {
  // Identity is the transform's own id — NOT the anchor text. Anchor wording drifts between
  // turns ("10 · Assurance & Provenance" vs "Assurance & Provenance"); an id doesn't, so
  // re-emitting the same chart updates it instead of adding a second one.
  const keyOf = (t: any) => {
    const p = t?.params || {};
    if (t?.type === "diagram" || t?.type === "section")
      return `${t.type}:${String(p.id || p.title || p.anchor || "").toLowerCase().trim()}`;
    return String(t?.type);
  };
  const out = [...prev];
  for (const p of patch || []) {
    const t: any = p;
    if (t?.type === "remove") {
      // A remove op does two things: drop any layer WE added for that target (so it isn't
      // re-inserted), and keep the op so base-document content is deleted on each apply.
      const target = String((t.params || {}).anchor || (t.params || {}).target || "").toLowerCase().trim();
      if (target) {
        for (let i = out.length - 1; i >= 0; i--) {
          const o: any = out[i];
          const anchor = String((o.params || {}).anchor || "").toLowerCase().trim();
          if (o.type === target || (anchor && (anchor.includes(target) || target.includes(anchor)))) out.splice(i, 1);
        }
      }
      out.push(t);
      continue;
    }
    const k = keyOf(t);
    const at = out.findIndex((o) => keyOf(o) === k);
    if (at >= 0) out[at] = t; else out.push(t);
  }
  return out;
}

/**
 * Beautify & Standardize studio (Phase 1) — a WYSIWYG deliverable design loop.
 *
 * Loads a run's produced HTML deliverable, renders it live in a sandboxed iframe,
 * and lets the user collaboratively refine the DESIGN with a designer agent
 * (suggest-ecosystem, mode="beautify"). Each turn sends the current HTML +
 * instruction; the agent streams a reply then returns the full revised HTML,
 * which is swapped into the preview.
 *
 * Phase 1 is design-only: no persistence, no template promotion (see
 * docs/spec-beautify-standardize.md). "Edit content" toggles direct in-preview
 * editing so edits are folded into the HTML sent on the next turn.
 */
export function BeautifyStudio({
  sessionId,
  artifactKey,
  artifactLabel,
  onClose,
}: {
  sessionId: string;
  artifactKey: string;
  artifactLabel: string;
  onClose: () => void;
}) {
  const { clientId, subscribe } = useClientWebSocket(WS_URL);

  const [baseHtml, setBaseHtml] = useState<string>("");
  const [overrides, setOverrides] = useState<string>("");
  // Presentation transforms — declarative structural re-presentation (tabs/heatmap/…)
  // stacked UNDER the CSS layer. verifyInvariant guards that content is unchanged.
  const [transforms, setTransforms] = useState<Transform[]>([]);
  const [invariant, setInvariant] = useState<{ ok: boolean; note?: string; unmatched?: string[] } | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [savedVersion, setSavedVersion] = useState<number | null>(null);

  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [streamingReply, setStreamingReply] = useState("");
  const [jobId, setJobId] = useState<string | null>(null);
  const [editMode, setEditMode] = useState(false);
  const [saving, setSaving] = useState(false);
  const [promoting, setPromoting] = useState(false);
  const [actionMsg, setActionMsg] = useState<string | null>(null);
  // Studio mode: "design" (restyle looks) vs "ask" (agent-routed run Q&A, shared AskPanel).
  const [mode, setMode] = useState<"design" | "ask">("design");
  const [spotlightQuery, setSpotlightQuery] = useState("");
  const [spotlightSeq, setSpotlightSeq] = useState<{ target: string; note: string }[] | null>(null);
  const [spotlightIdx, setSpotlightIdx] = useState(0);
  const [spotlightBusy, setSpotlightBusy] = useState(false);
  const [stageRect, setStageRect] = useState<{ left: number; top: number; width: number; height: number } | null>(null);
  const lockedElRef = useRef<HTMLElement | null>(null);

  const iframeRef = useRef<HTMLIFrameElement>(null);
  const baseRef = useRef<string>("");
  const jobIdRef = useRef<string | null>(null);
  useEffect(() => { baseRef.current = baseHtml; }, [baseHtml]);
  useEffect(() => { jobIdRef.current = jobId; }, [jobId]);

  const OVERRIDE_ID = "__tessera_beautify_overrides";

  // Compose the preview/output: the fixed base document + our layered CSS overrides,
  // injected LAST in <head> so they win over the document's own styles. The result
  // is a self-contained HTML document (used for both the live preview and Save).
  const composeDoc = useCallback((base: string, css: string): string => {
    const styleTag = `<style id="${OVERRIDE_ID}">\n${css}\n</style>`;
    if (/<\/head>/i.test(base)) return base.replace(/<\/head>/i, `${styleTag}</head>`);
    return styleTag + base;
  }, []);
  const composed = composeDoc(baseHtml, overrides);

  // Fidelity: diagrams must only visualise data present in the section (no fabrication).
  // tabs carry no data risk. Recompute whenever transforms/base change.
  useEffect(() => {
    const hasDiagram = transforms.some((t) => t.type === "diagram");
    if (!baseHtml || !hasDiagram) { setInvariant(null); return; }
    try { const r = verifyInvariant(baseHtml, transforms); setInvariant({ ok: r.ok, note: r.note, unmatched: r.unmatched }); }
    catch { setInvariant(null); }
  }, [baseHtml, transforms]);
  // Fidelity is ADVISORY, never a blocker: a diagram whose labels don't all appear
  // verbatim in the section (abbreviations, rounded axis ticks, wrapped words) is
  // usually fine — the user decides. We surface a note; we never refuse to save.
  const invariantWarn = invariant != null && !invariant.ok;

  const hasTx = (t: string) => transforms.some((x) => x.type === t);
  const toggleTransform = (t: string) =>
    setTransforms((prev) => (prev.some((x) => x.type === t) ? prev.filter((x) => x.type !== t) : [...prev, { type: t } as Transform]));

  // Spotlight — concierge "stage" style: an LLM picks verbatim sections + notes; we step
  // through, DIMMING the view and spotlighting the exact passage (cutout) with a callout.
  const rectInStudio = useCallback((el: HTMLElement) => {
    const iframe = iframeRef.current;
    if (!iframe) return null;
    const ir = iframe.getBoundingClientRect();
    const er = el.getBoundingClientRect(); // relative to the iframe's own viewport
    return { left: ir.left + er.left, top: ir.top + er.top, width: er.width, height: er.height };
  }, []);

  const goToStep = useCallback((seq: { target: string; note: string }[], i: number) => {
    if (!seq.length) return;
    const idx = Math.max(0, Math.min(seq.length - 1, i));
    setSpotlightIdx(idx);
    const doc = iframeRef.current?.contentDocument;
    if (!doc) return;
    const el = findAnchor(doc, seq[idx].target);
    lockedElRef.current = el;
    if (!el) { setStageRect(null); return; }
    el.scrollIntoView({ behavior: "smooth", block: "center" });
    // let the smooth scroll settle, then measure the cutout rect
    setTimeout(() => { if (lockedElRef.current === el) setStageRect(rectInStudio(el)); }, 380);
  }, [rectInStudio]);

  const closeSpotlight = useCallback(() => {
    lockedElRef.current = null; setStageRect(null); setSpotlightSeq(null); setSpotlightIdx(0);
  }, []);

  const generateSpotlight = useCallback(async (focus: string) => {
    if (!focus.trim() || spotlightBusy) return;
    setSpotlightBusy(true);
    setActionMsg(null);
    const doc = iframeRef.current?.contentDocument;
    const deliverableText = (doc?.body?.innerText || baseRef.current.replace(/<[^>]+>/g, " ")).slice(0, 60000);
    try {
      const res = await apiFetch(`${API_URL}/api/ecosystems/suggest`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "spotlight", focus, deliverableText }),
      });
      const data = await res.json();
      const secs = (data.sections || []).filter((s: any) => s && s.target);
      if (secs.length) { setSpotlightSeq(secs); goToStep(secs, 0); }
      else setActionMsg(data.error ? `⚠ ${data.error}` : "Couldn't find sections to spotlight for that.");
    } catch (e) {
      setActionMsg(`⚠ Spotlight failed: ${e}`);
    } finally {
      setSpotlightBusy(false);
    }
  }, [spotlightBusy, goToStep]);

  // Keep the cutout glued to the passage as the report scrolls / the window resizes.
  useEffect(() => {
    if (!spotlightSeq) return;
    const recompute = () => { const el = lockedElRef.current; if (el) setStageRect(rectInStudio(el)); };
    const win = iframeRef.current?.contentWindow;
    win?.addEventListener("scroll", recompute, true);
    window.addEventListener("resize", recompute);
    return () => { win?.removeEventListener("scroll", recompute, true); window.removeEventListener("resize", recompute); };
  }, [spotlightSeq, rectInStudio]);

  // Esc always gets you out: close the spotlight stage if it's up, else exit the studio.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "Escape") return;
      if (spotlightSeq) closeSpotlight();
      else onClose();
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [spotlightSeq, closeSpotlight, onClose]);

  // Load the produced deliverable HTML (inline-safe via run-artifact).
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const res = await apiFetch(
          `${API_URL}/api/runs/${encodeURIComponent(sessionId)}/artifacts/${artifactKey}?inline=true`,
        );
        const data = await res.json();
        if (!active) return;
        if (typeof data?.content === "string") {
          // Reopen the EDITED (flattened) copy when one exists — otherwise the studio would
          // always show the pristine original and a user's saved work would look lost.
          let html = data.content;
          if (data.saved_latest_key) {
            try {
              const r2 = await apiFetch(
                `${API_URL}/api/runs/${encodeURIComponent(sessionId)}/artifacts/${data.saved_latest_key}?inline=true`);
              const d2 = await r2.json();
              if (typeof d2?.content === "string" && d2.content.length > 0) {
                html = d2.content;
                if (active) setSavedVersion(data.saved_versions || 1);
              }
            } catch { /* fall back to the original */ }
          }
          if (active) setBaseHtml(html);
        } else setLoadError(data?.error || "Could not load the deliverable HTML.");
      } catch (e) {
        if (active) setLoadError(`Load failed: ${e}`);
      } finally {
        if (active) setLoading(false);
      }
    })();
    return () => { active = false; };
  }, [sessionId, artifactKey]);

  // Receive streamed reply + final revised HTML over the WebSocket.
  useEffect(() => {
    return subscribe((event: any) => {
      if (!event || event.jobId !== jobIdRef.current) return;
      if (event.type === "BEAUTIFY_DELTA") {
        setStreamingReply((s) => s + (event.text || ""));
      } else if (event.type === "BEAUTIFY_RESULT") {
        setBusy(false);
        setJobId(null);
        setStreamingReply("");
        if (event.error) {
          setMessages((m) => [...m, { role: "assistant", text: `⚠ ${event.error}` }]);
        } else {
          if (typeof event.css === "string") setOverrides(event.css);
          if (Array.isArray(event.transforms)) setTransforms((prev) => mergeTransforms(prev, event.transforms));
          setMessages((m) => [...m, { role: "assistant", text: event.reply || "Updated the design." }]);
        }
      }
    });
  }, [subscribe]);

  // Toggle direct content editing inside the preview iframe.
  useEffect(() => {
    const doc = iframeRef.current?.contentDocument;
    if (!doc) return;
    try { doc.designMode = editMode ? "on" : "off"; } catch {}
  }, [editMode, composed]);

  // Capture in-preview content edits back into the BASE html (stripping our
  // injected override <style> so overrides stay layered separately).
  const captureEdits = useCallback((): string => {
    if (!editMode) return baseRef.current;
    const doc = iframeRef.current?.contentDocument;
    if (!doc?.documentElement) return baseRef.current;
    const clone = doc.documentElement.cloneNode(true) as HTMLElement;
    clone.querySelector(`#${OVERRIDE_ID}`)?.remove();
    const captured = "<!doctype html>\n" + clone.outerHTML;
    setBaseHtml(captured);
    return captured;
  }, [editMode]);

  const send = useCallback(async () => {
    const msg = input.trim();
    if (!msg || busy) return;
    const currentHtml = captureEdits();
    const history = messages.map((m) => ({ role: m.role, text: m.text }));
    setMessages((m) => [...m, { role: "user", text: msg }]);
    setInput("");
    setBusy(true);
    setStreamingReply("");
    try {
      const res = await apiFetch(`${API_URL}/api/ecosystems/suggest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "beautify", message: msg, history, currentHtml, currentCss: overrides, outline: domOutline(currentHtml),
          // Inventory only — the SVG bodies are large and the agent must NOT re-emit
          // unchanged transforms (patch semantics), so sending them back wastes tokens.
          currentTransforms: transforms.map((t: any) => ({ type: t.type, ...(t.params ? { id: t.params.id, anchor: t.params.anchor, mode: t.params.mode, title: t.params.title } : {}) })),
          clientId }),
      });
      const data = await res.json();
      if (data.status === "pending" && data.jobId) {
        setJobId(data.jobId);
      } else if (data.css !== undefined || data.reply !== undefined) {
        // sync fallback (no clientId / no WS)
        setBusy(false);
        if (typeof data.css === "string") setOverrides(data.css);
        if (Array.isArray(data.transforms)) setTransforms((prev) => mergeTransforms(prev, data.transforms));
        setMessages((m) => [...m, { role: "assistant", text: data.reply || "Updated the design." }]);
      } else {
        setBusy(false);
        setMessages((m) => [...m, { role: "assistant", text: `⚠ ${data.error || "No job started."}` }]);
      }
    } catch (e) {
      setBusy(false);
      setMessages((m) => [...m, { role: "assistant", text: `⚠ Request failed: ${e}` }]);
    }
  }, [input, busy, messages, clientId, captureEdits, overrides, transforms]);

  // Phase 2: persist the beautified deliverable back to the run (versioned).
  /** FLATTEN — build the saved document DETERMINISTICALLY: base (incl. any in-preview text
   *  edits) + CSS overrides + every applied transform, composed into a detached document and
   *  serialised. It does NOT read the iframe: that DOM can be momentarily unavailable (the
   *  preview remounts whenever the transform list changes), and falling back to base+CSS
   *  silently dropped the user's diagram. Tabs are expanded because switching needs JS. */
  const captureFlattened = useCallback((): { html: string; missing: string[] } => {
    const composedHtml = composeDoc(baseRef.current, overrides);
    try {
      const d = new DOMParser().parseFromString(composedHtml, "text/html");
      if (transforms.length) applyTransforms(d, transforms);
      d.querySelectorAll(".tx-tabbar").forEach((n) => n.remove());
      d.querySelectorAll<HTMLElement>(".tx-tab-panel").forEach((n) => { n.style.display = ""; });
      // Verify every additive transform actually landed — never save a doc that silently
      // lost an edit.
      const missing: string[] = [];
      for (const t of transforms as any[]) {
        if (t.type === "diagram" && !d.querySelector("[data-tx-diagram]")) missing.push(`diagram (${(t.params || {}).anchor || "?"})`);
        if (t.type === "section" && !d.querySelector("[data-tx-section]")) missing.push(`section (${(t.params || {}).title || "?"})`);
      }
      return { html: "<!doctype html>\n" + d.documentElement.outerHTML, missing };
    } catch (e) {
      return { html: composedHtml, missing: transforms.length ? ["all layout changes"] : [] };
    }
  }, [composeDoc, overrides, transforms]);

  const saveDeliverable = useCallback(async () => {
    if (saving) return;
    setSaving(true);
    setActionMsg(null);
    captureEdits();
    const { html: finalHtml, missing } = captureFlattened();
    if (missing.length) {   // refuse rather than save a doc missing the user's change
      setSaving(false);
      setActionMsg(`⚠ Not saved — could not bake in: ${missing.join(", ")}. Tell me and I'll fix it.`);
      return;
    }
    const provenance = editMode ? "human-edited" : "beautified";
    try {
      const res = await apiFetch(
        `${API_URL}/api/runs/${encodeURIComponent(sessionId)}/artifacts/${artifactKey}`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ content: finalHtml, provenance }) },
      );
      const data = await res.json();
      if (data.saved) {
        setBaseHtml(finalHtml);
        setOverrides("");
        setTransforms([]);
        setSavedVersion(data.versions || 1);
        setActionMsg(`💾 Saved v${data.versions} · edited copy (original kept)`);
      } else {
        setActionMsg(`⚠ ${data.error || "save failed"}`);
      }
    } catch (e) {
      setActionMsg(`⚠ Save failed: ${e}`);
    } finally {
      setSaving(false);
    }
  }, [saving, captureEdits, captureFlattened, editMode, sessionId, artifactKey]);

  // Phase 3: promote the accumulated look (the CSS overrides) to the ecosystem's
  // template — all future runs of this eco adopt it. LLM-free: the overrides ARE
  // the reusable stylesheet.
  const promoteTemplate = useCallback(async () => {
    if (promoting) return;
    if (!overrides.trim() && transforms.length === 0) { setActionMsg("Make a design change first — nothing to promote yet."); return; }
    const agentId = artifactKey.split("/")[0];
    const ecoId = sessionId.replace(/-[0-9a-f]{8}$/i, "");
    setPromoting(true);
    setActionMsg("Promoting to template…");
    const base = captureEdits();
    try {
      const res = await apiFetch(
        `${API_URL}/api/ecosystems/${encodeURIComponent(ecoId)}/deploy`,
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ action: "promote_template", agentId, templateCss: overrides, exampleHtml: base, sessionId }) },
      );
      const data = await res.json();
      if (data.promoted || data.template_id) {
        setActionMsg(`⬆ Promoted → template "${data.template_id}". Future runs of ${ecoId} adopt it.`);
      } else {
        setActionMsg(`⚠ ${data.error || "promote failed"}`);
      }
    } catch (e) {
      setActionMsg(`⚠ Promote failed: ${e}`);
    } finally {
      setPromoting(false);
    }
  }, [promoting, overrides, transforms, artifactKey, sessionId, captureEdits]);

  return (
    <div className="fixed inset-0 z-[80] flex flex-col bg-tessera-bg">
      {/* Header */}
      <div className="flex items-center justify-between gap-3 px-4 py-2.5 border-b border-tessera-border bg-tessera-card shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-lg">✨</span>
          <span className="text-sm font-bold text-white">Beautify &amp; Standardize</span>
          <span className="text-[11px] text-gray-400 truncate">· {artifactLabel}</span>
          <span className="text-[9px] font-bold tracking-wider uppercase px-1.5 py-0.5 rounded border border-sky-500/40 bg-sky-500/15 text-sky-300">report studio · ask + edit</span>
          {savedVersion && <span className="text-[9px] font-bold tracking-wider uppercase px-1.5 py-0.5 rounded border border-emerald-500/40 bg-emerald-500/15 text-emerald-300" title="You are editing your saved copy; the original run artifact is untouched">edited v{savedVersion}</span>}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {actionMsg && <span className="text-[11px] text-gray-300 mr-1 max-w-[380px] leading-tight line-clamp-2" title={actionMsg}>{actionMsg}</span>}
          <button
            onClick={() => setEditMode((e) => !e)}
            title="Toggle direct in-preview content editing"
            className={`px-2.5 py-1 text-[11px] font-semibold rounded border transition ${
              editMode
                ? "border-tessera-accent/50 bg-tessera-accent/20 text-tessera-accent"
                : "border-tessera-border bg-tessera-card text-gray-300 hover:border-tessera-accent/40"
            }`}
          >
            {editMode ? "✎ Editing content" : "✎ Edit content"}
          </button>
          <button
            onClick={saveDeliverable}
            disabled={saving || loading}
            title="Save this beautified deliverable back to the run (versioned; original untouched)"
            className="px-2.5 py-1 text-[11px] font-semibold rounded border border-emerald-400/40 bg-emerald-500/15 text-emerald-200 hover:bg-emerald-500/25 disabled:opacity-40 transition"
          >
            {saving ? "…" : "💾 Save"}
          </button>
          <button
            onClick={promoteTemplate}
            disabled={promoting || loading || (!overrides.trim() && transforms.length === 0)}
            title="Promote this look to the ecosystem's template — all future runs adopt it"
            className="px-2.5 py-1 text-[11px] font-semibold rounded border border-purple-400/40 bg-purple-500/15 text-purple-200 hover:bg-purple-500/25 disabled:opacity-40 transition"
          >
            {promoting ? "…" : "⬆ Promote to template"}
          </button>
          <button
            onClick={onClose}
            title="Exit the studio and return to the app (Esc)"
            className="px-2.5 py-1 text-[11px] font-semibold rounded border border-gray-400/50 bg-gray-200/10 text-gray-100 hover:bg-gray-200/20 hover:border-gray-300 transition"
          >
            ✕ Exit studio
          </button>
        </div>
      </div>

      {/* Body: chat | preview */}
      <div className="flex flex-1 min-h-0">
        {/* Left panel: Ask (agent-routed Q&A) | Design (restyle) */}
        <div className="w-[360px] shrink-0 flex flex-col border-r border-tessera-border bg-tessera-card">
          <div className="flex items-center gap-1 px-2 py-1.5 border-b border-tessera-border">
            <button
              onClick={() => setMode("ask")}
              className={`px-2.5 py-1 text-[11px] font-semibold rounded ${mode === "ask" ? "bg-tessera-accent/20 text-tessera-accent" : "text-gray-400 hover:text-gray-200"}`}
            >
              💬 Ask
            </button>
            <button
              onClick={() => setMode("design")}
              className={`px-2.5 py-1 text-[11px] font-semibold rounded ${mode === "design" ? "bg-tessera-accent/20 text-tessera-accent" : "text-gray-400 hover:text-gray-200"}`}
            >
              🎨 Design
            </button>
            <span className="ml-auto text-[9px] text-gray-500">{mode === "ask" ? "asks the eco's agents" : "restyles the look"}</span>
          </div>

          {mode === "design" ? (
            <>
              {/* Layout — Tabs (structural) + content-derived diagrams (chat-driven). */}
              <div className="px-3 py-2 border-b border-tessera-border/60 space-y-1.5">
                <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500">Layout</div>
                <div className="flex flex-wrap items-center gap-1.5">
                  <button onClick={() => toggleTransform("tabs")}
                    className={`px-2 py-1 text-[10px] font-semibold rounded border transition ${hasTx("tabs") ? "border-tessera-accent/50 bg-tessera-accent/20 text-tessera-accent" : "border-tessera-border bg-tessera-bg text-gray-300 hover:border-tessera-accent/40"}`}>▦ Tabs by section</button>
                  {transforms.some((t) => t.type === "diagram") && (
                    <span className="text-[10px] text-gray-400">📊 {transforms.filter((t) => t.type === "diagram").length} diagram(s)</span>
                  )}
                  {transforms.length > 0 && <button onClick={() => setTransforms([])} className="text-[10px] text-gray-500 hover:text-red-300 ml-auto">Clear layout</button>}
                </div>
                <div className="text-[10px] text-gray-500 leading-relaxed">
                  Diagrams are generated from a section&rsquo;s data — ask below, e.g. <em>&ldquo;add a risk heatmap for the domains&rdquo;</em> or <em>&ldquo;a bar chart of open blockers by domain&rdquo;</em>.
                </div>
                {invariant && invariant.ok && (
                  <div className="text-[10px] text-emerald-300">✓ {invariant.note || "Diagram data sourced from the section — verified."}</div>
                )}
                {invariant && !invariant.ok && (
                  <div className="text-[10px] text-amber-300 bg-amber-500/10 border border-amber-500/30 rounded px-2 py-1 leading-relaxed">
                    ℹ Some diagram labels weren&apos;t found verbatim in the section (often just abbreviations or
                    rounded axis values) — worth a glance, but saving is not blocked. {invariant.note}
                  </div>
                )}
              </div>
              <div className="flex-1 overflow-y-auto p-3 space-y-2">
                {messages.length === 0 && !streamingReply && (
                  <div className="text-[12px] text-gray-400 leading-relaxed">
                    Ask the designer to refine the look — e.g. <em>&ldquo;make the heatmap bigger&rdquo;</em>,
                    {" "}<em>&ldquo;use a navy palette&rdquo;</em>, <em>&ldquo;tighten the tables&rdquo;</em>,
                    {" "}<em>&ldquo;move Conditions above Findings&rdquo;</em>. The preview updates live.
                  </div>
                )}
                {messages.map((m, i) => (
                  <div
                    key={i}
                    className={`text-[12px] rounded-lg px-2.5 py-1.5 ${
                      m.role === "user" ? "bg-tessera-accent/15 text-gray-100 ml-6" : "bg-gray-700/30 text-gray-200 mr-6"
                    }`}
                  >
                    {m.text}
                  </div>
                ))}
                {busy && (
                  <div className="text-[12px] rounded-lg px-2.5 py-1.5 bg-gray-700/30 text-gray-400 mr-6">
                    {streamingReply || "Designing…"}
                    <span className="inline-block animate-pulse">▋</span>
                  </div>
                )}
              </div>
              <div className="p-2.5 border-t border-tessera-border">
                <div className="flex items-end gap-2">
                  <textarea
                    value={input}
                    onChange={(e) => setInput(e.target.value)}
                    onKeyDown={(e) => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                    placeholder="Describe a design change…"
                    rows={2}
                    disabled={busy || loading}
                    className="flex-1 resize-none text-[12px] rounded-lg bg-tessera-bg border border-tessera-border px-2.5 py-1.5 text-gray-100 placeholder:text-gray-500 focus:outline-none focus:border-tessera-accent/50 disabled:opacity-50"
                  />
                  <button
                    onClick={send}
                    disabled={busy || loading || !input.trim()}
                    className="px-3 py-1.5 text-[12px] font-semibold rounded-lg bg-tessera-accent/20 text-tessera-accent border border-tessera-accent/40 hover:bg-tessera-accent/30 disabled:opacity-30 disabled:cursor-not-allowed transition whitespace-nowrap"
                  >
                    {busy ? "…" : "Send"}
                  </button>
                </div>
                {editMode && (
                  <div className="mt-1.5 text-[10px] text-amber-300/80">
                    Editing content — click text in the preview to edit. Edits are folded into the next design turn.
                  </div>
                )}
              </div>
            </>
          ) : (
            <AskPanel sessionId={sessionId} className="flex-1" onSpotlight={generateSpotlight} />
          )}
        </div>

        {/* Preview */}
        <div className="flex-1 min-w-0 flex flex-col bg-gray-200 overflow-hidden">
          <div className="flex items-center gap-2 px-3 py-1.5 bg-tessera-card border-b border-tessera-border shrink-0">
            <span className="text-[10px] text-gray-400">🔦 Spotlight</span>
            <input
              value={spotlightQuery}
              onChange={(e) => setSpotlightQuery(e.target.value)}
              onKeyDown={(e) => { if (e.key === "Enter") generateSpotlight(spotlightQuery); }}
              placeholder="ask to spotlight… e.g. the open blockers"
              className="flex-1 max-w-[320px] text-[11px] rounded bg-tessera-bg border border-tessera-border px-2 py-1 text-gray-100 placeholder:text-gray-500 focus:outline-none focus:border-tessera-accent/50"
            />
            <button
              onClick={() => generateSpotlight(spotlightQuery)}
              disabled={!spotlightQuery.trim() || spotlightBusy}
              className="px-2.5 py-1 text-[10px] font-semibold rounded border border-tessera-accent/40 bg-tessera-accent/15 text-tessera-accent hover:bg-tessera-accent/25 disabled:opacity-40 transition"
            >
              {spotlightBusy ? "…" : "Spotlight"}
            </button>
          </div>
          <div className="flex-1 min-h-0 relative">
            {loading && (
              <div className="absolute inset-0 flex items-center justify-center text-sm text-gray-500">Loading deliverable…</div>
            )}
            {loadError && (
              <div className="absolute inset-0 flex items-center justify-center text-sm text-red-600 p-6 text-center">{loadError}</div>
            )}
            {!loading && !loadError && (
              <iframe
                ref={iframeRef}
                key={"tx:" + JSON.stringify(transforms)}
                title="Deliverable preview"
                srcDoc={composed}
                sandbox="allow-same-origin"
                onLoad={(e) => {
                  // The report is a sandboxed srcdoc iframe: raw #anchor / external links
                  // would navigate the frame to about:srcdoc (→ blank). Handle clicks from
                  // the parent (allow-same-origin gives us DOM access): scroll internal
                  // anchors, open external links in a new tab.
                  const doc = (e.currentTarget as HTMLIFrameElement).contentDocument;
                  if (!doc) return;
                  // Presentation transforms (tabs/heatmap/…) applied under the CSS layer.
                  try { if (transforms.length) applyTransforms(doc, transforms); } catch { /* non-fatal */ }
                  doc.addEventListener("click", (ev) => {
                    const a = (ev.target as HTMLElement)?.closest?.("a") as HTMLAnchorElement | null;
                    if (!a) return;
                    const href = a.getAttribute("href") || "";
                    if (href.startsWith("#")) {
                      ev.preventDefault();
                      let el: Element | null = null;
                      try { el = doc.querySelector(href); } catch { /* invalid selector */ }
                      if (!el) el = doc.getElementById(decodeURIComponent(href.slice(1)));
                      el?.scrollIntoView({ behavior: "smooth", block: "start" });
                    } else if (/^(https?:|mailto:)/i.test(href)) {
                      ev.preventDefault();
                      window.open(href, "_blank", "noopener,noreferrer");
                    } else if (href) {
                      ev.preventDefault(); // relative/unknown — don't blank the frame
                    }
                  });
                }}
                className="w-full h-full border-0 bg-white"
              />
            )}
          </div>
        </div>
      </div>

      {/* Concierge-style spotlight STAGE: dim everything, cut out the target passage, callout the note. */}
      {spotlightSeq && stageRect && (
        <div className="fixed inset-0 z-[85] pointer-events-none">
          <div
            style={{
              position: "fixed",
              left: Math.max(0, stageRect.left - 12),
              top: Math.max(0, stageRect.top - 12),
              width: stageRect.width + 24,
              height: stageRect.height + 24,
              borderRadius: 10,
              boxShadow: "0 0 0 9999px rgba(6,4,20,0.82)",
              outline: "2px solid rgba(167,139,250,0.9)",
              outlineOffset: 2,
              transition: "all .35s cubic-bezier(.2,.8,.2,1)",
            }}
          />
          <div
            style={{
              position: "fixed",
              left: Math.min(stageRect.left, (typeof window !== "undefined" ? window.innerWidth : 1200) - 360),
              top: (stageRect.top + stageRect.height + 160 < (typeof window !== "undefined" ? window.innerHeight : 800))
                ? stageRect.top + stageRect.height + 14
                : Math.max(12, stageRect.top - 140),
              maxWidth: 340,
            }}
            className="pointer-events-auto bg-tessera-card/95 backdrop-blur border border-tessera-accent/70 rounded-xl shadow-2xl p-3 text-[13px] text-gray-50"
          >
            <div className="flex items-center gap-2 mb-1.5">
              <span className="text-tessera-accent">✨</span>
              <span className="text-[10px] text-purple-300 tabular-nums">{spotlightIdx + 1}/{spotlightSeq.length}</span>
              <div className="ml-auto flex items-center gap-1">
                <button onClick={() => goToStep(spotlightSeq, spotlightIdx - 1)} disabled={spotlightIdx === 0}
                  className="px-2 py-0.5 text-[12px] rounded bg-white/5 hover:bg-white/10 text-purple-200 disabled:opacity-30">‹</button>
                <button onClick={() => goToStep(spotlightSeq, spotlightIdx + 1)} disabled={spotlightIdx === spotlightSeq.length - 1}
                  className="px-2 py-0.5 text-[12px] rounded bg-white/5 hover:bg-white/10 text-purple-200 disabled:opacity-30">›</button>
                <button onClick={closeSpotlight} title="Close spotlight" className="px-2 py-0.5 text-[12px] rounded text-gray-400 hover:text-white">✕</button>
              </div>
            </div>
            <div className="leading-snug">{spotlightSeq[spotlightIdx]?.note || spotlightSeq[spotlightIdx]?.target}</div>
          </div>
        </div>
      )}
    </div>
  );
}
