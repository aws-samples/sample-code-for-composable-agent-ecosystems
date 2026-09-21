"use client";

import { useCallback, useEffect, useRef, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const { fetchAuthSession } = await import("aws-amplify/auth");
  let token = "";
  try { token = (await fetchAuthSession({ forceRefresh: false })).tokens?.idToken?.toString() || ""; } catch {}
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(url, { ...init, headers });
}

export interface ArtifactReq { ecoId: string; runId: string; artifactKey?: string; label?: string; }

interface Anchor { selector?: string; text_quote?: string; nth?: number }
interface SectionMark {
  id?: string;
  artifact_key: string;
  anchor: Anchor;
  excerpt: string;
  score: number | null;
  feedback: string;
}

/** Build a resilient anchor for a clicked element: prefer an id, else a
 *  tag + nth-of-tag selector, plus a text quote fallback so a re-rendered
 *  artifact can re-locate the mark. */
function buildAnchor(doc: Document, el: HTMLElement): { anchor: Anchor; excerpt: string } {
  const text = (el.innerText || el.textContent || "").trim().replace(/\s+/g, " ");
  const excerpt = text.slice(0, 300);
  if (el.id) return { anchor: { selector: `#${el.id}`, text_quote: text.slice(0, 80), nth: 0 }, excerpt };
  const tag = el.tagName.toLowerCase();
  const same = Array.from(doc.getElementsByTagName(tag));
  const nth = Math.max(0, same.indexOf(el));
  return { anchor: { selector: tag, text_quote: text.slice(0, 80), nth }, excerpt };
}

/** Re-locate a mark's element in the (re-rendered) doc: id first, then the
 *  nth element of the tag whose text starts with the stored quote. */
function locate(doc: Document, a: Anchor): HTMLElement | null {
  if (!a) return null;
  if (a.selector && a.selector.startsWith("#")) {
    const byId = doc.querySelector(a.selector) as HTMLElement | null;
    if (byId) return byId;
  }
  if (a.selector) {
    const list = Array.from(doc.getElementsByTagName(a.selector)) as HTMLElement[];
    if (a.text_quote) {
      const hit = list.find((e) => (e.innerText || e.textContent || "").trim().startsWith(a.text_quote!));
      if (hit) return hit;
    }
    if (typeof a.nth === "number" && list[a.nth]) return list[a.nth];
  }
  return null;
}

/**
 * Renders a run's HTML deliverable IN-APP in a sandboxed same-origin iframe
 * (no allow-scripts). With "Mark sections" on, the operator clicks blocks in the
 * deliverable to attach a 1–5 rank + feedback per section; marks persist to the
 * run feedback sidecar and can be promoted to standing guidance.
 */
export function ArtifactViewer({ req, onClose }: { req: ArtifactReq | null; onClose: () => void }) {
  const [html, setHtml] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [title, setTitle] = useState<string>("Deliverable");
  const [artifactKey, setArtifactKey] = useState<string>("");

  const [markMode, setMarkMode] = useState(false);
  const [sections, setSections] = useState<SectionMark[]>([]);
  const [draft, setDraft] = useState<{ anchor: Anchor; excerpt: string } | null>(null);
  const [draftScore, setDraftScore] = useState<number>(0);
  const [draftFeedback, setDraftFeedback] = useState<string>("");
  const [busy, setBusy] = useState(false);
  const iframeRef = useRef<HTMLIFrameElement | null>(null);

  // Load the artifact + existing section marks.
  useEffect(() => {
    if (!req) { setHtml(null); setErr(null); setSections([]); setMarkMode(false); return; }
    let cancelled = false;
    setHtml(null); setErr(null); setTitle(req.label || "Deliverable"); setSections([]);
    (async () => {
      try {
        let key = req.artifactKey;
        if (!key) {
          const d = await apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(req.ecoId)}/runs/${encodeURIComponent(req.runId)}`);
          const dj = await d.json();
          const arts = (dj?.outcome?.declared_artifacts || []) as { artifact_key: string; format: string }[];
          key = (arts.find((a) => (a.format || "").toLowerCase() === "html") || arts[0])?.artifact_key;
        }
        if (!key) { if (!cancelled) setErr("No deliverable found for this run."); return; }
        if (!cancelled) setArtifactKey(key);
        const r = await apiFetch(`${API_URL}/api/runs/${encodeURIComponent(req.runId)}/artifacts/${key}?inline=true`);
        const j = await r.json();
        if (cancelled) return;
        if (j?.content) { setHtml(j.content); setTitle(req.label || (key.split("/").pop() || "Deliverable")); }
        else setErr(j?.error || "Could not load the deliverable inline.");
        // Existing section marks for this run.
        const fr = await apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(req.ecoId)}/runs/${encodeURIComponent(req.runId)}/feedback`);
        if (fr.ok && !cancelled) {
          const fb = await fr.json();
          setSections((fb.sections || []).filter((s: SectionMark) => !key || s.artifact_key === key));
        }
      } catch (e) { if (!cancelled) setErr(String(e)); }
    })();
    return () => { cancelled = true; };
  }, [req]);

  const persist = useCallback(async (next: SectionMark[]) => {
    if (!req) return next;
    setBusy(true);
    try {
      const r = await apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(req.ecoId)}/runs/${encodeURIComponent(req.runId)}/sections`,
        { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ sections: next }) });
      if (r.ok) { const fb = await r.json(); const srv = (fb.sections || []).filter((s: SectionMark) => !artifactKey || s.artifact_key === artifactKey); setSections(srv); return srv; }
    } finally { setBusy(false); }
    return next;
  }, [req, artifactKey]);

  // Click-to-mark: attach a capture-phase listener to the iframe document.
  useEffect(() => {
    const ifr = iframeRef.current;
    const doc = ifr?.contentDocument;
    if (!doc || !doc.body) return;
    doc.body.style.cursor = markMode ? "crosshair" : "";
    if (!markMode) return;
    const onClick = (ev: Event) => {
      ev.preventDefault(); ev.stopPropagation();
      const el = ev.target as HTMLElement;
      if (!el || el === doc.body) return;
      // Walk up to a block-ish container for a more meaningful section.
      let node: HTMLElement = el;
      for (let i = 0; i < 3 && node.parentElement && (node.innerText || "").trim().length < 30; i++) node = node.parentElement;
      const built = buildAnchor(doc, node);
      node.style.outline = "2px solid #a855f7";
      node.style.outlineOffset = "2px";
      setDraft(built); setDraftScore(0); setDraftFeedback("");
    };
    doc.addEventListener("click", onClick, true);
    return () => { doc.removeEventListener("click", onClick, true); if (doc.body) doc.body.style.cursor = ""; };
  }, [markMode, html]);

  const saveDraft = async () => {
    if (!draft) return;
    const mark: SectionMark = { artifact_key: artifactKey, anchor: draft.anchor, excerpt: draft.excerpt, score: draftScore || null, feedback: draftFeedback };
    await persist([...sections, mark]);
    setDraft(null); setDraftScore(0); setDraftFeedback("");
  };

  const removeMark = async (idx: number) => { await persist(sections.filter((_, i) => i !== idx)); };

  const promoteSection = async (id?: string) => {
    if (!req || !id) return;
    setBusy(true);
    try {
      await apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(req.ecoId)}/runs/${encodeURIComponent(req.runId)}/promote`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scope: "section", section_id: id }) });
    } finally { setBusy(false); }
  };

  const scrollToMark = (a: Anchor) => {
    const doc = iframeRef.current?.contentDocument;
    if (!doc) return;
    const el = locate(doc, a);
    if (el) { el.scrollIntoView({ behavior: "smooth", block: "center" }); el.style.outline = "2px solid #a855f7"; el.style.outlineOffset = "2px"; }
  };

  if (!req) return null;

  return (
    <div className="fixed inset-0 z-[56] flex items-center justify-center p-6" data-presenter-chrome>
      <div className="absolute inset-0 bg-black/50" onClick={onClose} />
      <div className="relative w-[min(1200px,94vw)] h-[88vh] bg-tessera-card border border-tessera-border rounded-2xl shadow-2xl overflow-hidden flex flex-col">
        <div className="flex items-center justify-between px-4 py-2.5 border-b border-tessera-border shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-tessera-accent text-sm">📄</span>
            <span className="text-sm font-semibold truncate">{title}</span>
            <span className="text-[10px] uppercase tracking-widest text-gray-500 ml-2">live deliverable · in-app</span>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setMarkMode((v) => !v)}
              className={`text-[11px] px-2.5 py-1 rounded-md border ${markMode ? "bg-purple-500/20 border-purple-500/40 text-purple-200" : "border-tessera-border text-gray-300 hover:border-tessera-accent/50"}`}
              title="Click sections of the deliverable to rate + comment on them"
            >{markMode ? "Marking… (click a section)" : "Mark & rate sections"}</button>
            <button onClick={onClose} className="text-gray-400 hover:text-white text-sm px-2" title="Close">✕</button>
          </div>
        </div>
        <div className="flex-1 min-h-0 flex">
          {/* Deliverable */}
          <div className="flex-1 min-w-0 bg-white relative">
            {html != null ? (
              <iframe
                ref={iframeRef}
                data-artifact-frame
                title={title}
                sandbox="allow-same-origin"
                srcDoc={html}
                onLoad={(e) => {
                  // Sandboxed srcdoc: handle links from the parent so internal anchors
                  // scroll (instead of blanking about:srcdoc) and external links open a tab.
                  const doc = (e.currentTarget as HTMLIFrameElement).contentDocument;
                  if (!doc) return;
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
                      ev.preventDefault();
                    }
                  });
                }}
                className="w-full h-full border-0 bg-white"
              />
            ) : err ? (
              <div className="h-full flex items-center justify-center text-sm text-red-300 p-6 text-center">{err}</div>
            ) : (
              <div className="h-full flex items-center justify-center text-sm text-gray-400">Loading deliverable…</div>
            )}
          </div>

          {/* Section marks side panel */}
          {(markMode || sections.length > 0) && (
            <div className="w-[320px] shrink-0 border-l border-tessera-border bg-tessera-card flex flex-col">
              <div className="px-3 py-2 border-b border-tessera-border text-[11px] text-gray-400">
                Section marks {busy && <span className="text-gray-600">· saving…</span>}
              </div>
              <div className="flex-1 overflow-y-auto p-3 space-y-2">
                {draft && (
                  <div className="border border-purple-500/40 rounded-lg p-2 bg-purple-500/5">
                    <p className="text-[11px] text-gray-300 line-clamp-3 mb-1">“{draft.excerpt.slice(0, 140)}”</p>
                    <div className="flex items-center gap-1 mb-1">
                      {[1, 2, 3, 4, 5].map((n) => (
                        <button key={n} onClick={() => setDraftScore(n)}
                          className={`text-sm leading-none ${n <= draftScore ? "text-yellow-400" : "text-gray-600 hover:text-gray-400"}`}>★</button>
                      ))}
                    </div>
                    <textarea value={draftFeedback} onChange={(e) => setDraftFeedback(e.target.value)} rows={2}
                      placeholder="What about this section?" className="w-full text-[11px] bg-tessera-bg border border-tessera-border rounded-md px-2 py-1 text-gray-200 outline-none resize-y" />
                    <div className="flex items-center gap-2 mt-1">
                      <button onClick={saveDraft} disabled={busy} className="text-[11px] px-2 py-0.5 rounded bg-tessera-gradient text-white disabled:opacity-40">Save mark</button>
                      <button onClick={() => setDraft(null)} className="text-[11px] text-gray-500 hover:text-gray-300">cancel</button>
                    </div>
                  </div>
                )}
                {sections.length === 0 && !draft && (
                  <p className="text-[11px] text-gray-600">{markMode ? "Click a section in the deliverable to mark it." : "No section marks yet."}</p>
                )}
                {sections.map((s, i) => (
                  <div key={s.id || i} className="border border-tessera-border rounded-lg p-2">
                    <div className="flex items-center justify-between mb-1">
                      <span className="text-yellow-400 text-[11px]">{s.score ? "★".repeat(s.score) : "—"}</span>
                      <div className="flex items-center gap-2">
                        <button onClick={() => scrollToMark(s.anchor)} className="text-[10px] text-gray-400 hover:text-white" title="Scroll to section">locate</button>
                        <button onClick={() => promoteSection(s.id)} disabled={busy || !s.id} className="text-[10px] text-purple-300 hover:text-purple-200" title="Promote to standing guidance">promote</button>
                        <button onClick={() => removeMark(i)} disabled={busy} className="text-[10px] text-red-400/80 hover:text-red-300">delete</button>
                      </div>
                    </div>
                    <p className="text-[10px] text-gray-500 line-clamp-2 mb-0.5">“{s.excerpt.slice(0, 120)}”</p>
                    {s.feedback && <p className="text-[11px] text-gray-300 whitespace-pre-wrap">{s.feedback}</p>}
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
