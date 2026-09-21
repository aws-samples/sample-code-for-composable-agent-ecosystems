"use client";

import { useState, useEffect, useRef, useCallback } from "react";

export interface Stat { label?: string; value: string; }
export interface Beat {
  narration: string;
  headline?: string;
  bullets?: string[];
  stats?: Stat[];
  duration_s?: number;
  actions?: Record<string, unknown>[];
}
export interface Plan { title?: string; audience?: string; minutes?: number; focus?: string; beats: Beat[]; }
export interface Highlight { target: string; note?: string; }

const norm = (s: string) => (s || "").toLowerCase().replace(/\s+/g, " ").trim();

/** Search roots for anchoring. CRITICAL: when the outcome/deliverable overlay is open,
 *  search ONLY inside that iframe. The same text usually also exists in the run/agent panels
 *  sitting UNDERNEATH the overlay, so searching the top document would spotlight the hidden
 *  layer behind the deliverable (wrong element, confusing). Only when no artifact overlay is
 *  open do we anchor against the top document (grid, run panels, etc). */
function searchRoots(): Document[] {
  const iframeDocs: Document[] = [];
  document.querySelectorAll("iframe[data-artifact-frame]").forEach((f) => {
    const r = (f as HTMLElement).getBoundingClientRect();
    if (r.width < 4 || r.height < 4) return;                 // not visible / not laid out
    try { const d = (f as HTMLIFrameElement).contentDocument; if (d && d.body) iframeDocs.push(d); } catch { /* cross-origin */ }
  });
  return iframeDocs.length ? iframeDocs : [document];
}

/** Bounding rect of an element in TOP-window coordinates (adds the iframe offset when the
 *  element lives inside an in-app artifact iframe). */
function rectInTop(el: Element): { left: number; top: number; width: number; height: number } {
  const r = el.getBoundingClientRect();
  const win = el.ownerDocument?.defaultView;
  if (win && win.frameElement) {
    const fr = (win.frameElement as HTMLElement).getBoundingClientRect();
    return { left: r.left + fr.left, top: r.top + fr.top, width: r.width, height: r.height };
  }
  return { left: r.left, top: r.top, width: r.width, height: r.height };
}

/** Tags that represent a whole "point" the audience reads as one unit. */
const BLOCK_TAGS = new Set(["P","LI","TR","BLOCKQUOTE","H1","H2","H3","H4","H5","H6","TD","TH","DD","DT","FIGCAPTION","PRE"]);
const INLINE_TAGS = new Set(["STRONG","EM","SPAN","CODE","A","B","I","MARK","SMALL","U","SUP","SUB","LABEL"]);

/** The matcher deliberately locks onto the SHORTEST element containing the snippet, which is
 *  often an inner <strong>/<span>/<td>/<code> — so the spotlight hugs a fragment, not the point.
 *  Promote the match to its nearest block-level ancestor (the sentence / list item / row /
 *  heading) so the callout covers the whole point. Guarded so we never blow up to a huge
 *  container. */
function promoteToBlock(el: Element): Element {
  if (BLOCK_TAGS.has(el.tagName)) return el;
  const vh = el.ownerDocument?.defaultView?.innerHeight || window.innerHeight;
  let cur: Element | null = el.parentElement;
  while (cur && cur.tagName !== "BODY" && cur.tagName !== "HTML") {
    if (BLOCK_TAGS.has(cur.tagName)) {
      const h = cur.getBoundingClientRect().height;
      return h <= vh * 0.6 ? cur : el;   // don't promote to something enormous
    }
    cur = cur.parentElement;
  }
  // No semantic block ancestor (e.g. div-card layouts): if we matched a bare inline
  // fragment, widen one level so the box isn't hugging a single word.
  if (INLINE_TAGS.has(el.tagName) && el.parentElement) {
    const p = el.parentElement;
    if (p.getBoundingClientRect().height <= vh * 0.6) return p;
  }
  return el;
}

/** Robustly find the DOM element that best matches a target snippet, using token + figure
 *  scoring so it tolerates paraphrase / markdown wrapping. Searches the top document AND any
 *  in-app artifact iframe, so we can spotlight sections inside the real HTML deliverable. */
function anchorElement(target: string): Element | null {
  const n = norm(target);
  const words = n.split(" ").filter((w) => w.replace(/[^a-z0-9]/g, "").length >= 4);
  const figures = n.match(/\d[\d.,:§%\/-]*\d|\d/g) || [];
  const keys = Array.from(new Set([...words, ...figures]));
  if (!keys.length) return null;

  let best: Element | null = null;
  let bestScore = 0;
  let bestLen = Infinity;
  for (const root of searchRoots()) {
    const inIframe = root !== document;
    const els = Array.from(root.querySelectorAll("p,li,td,th,h1,h2,h3,h4,h5,blockquote,strong,em,code,span,div"));
    for (const el of els) {
      if ((el as HTMLElement).offsetParent === null) continue; // hidden
      if (el.children.length > 8) continue; // prefer leaf-ish blocks
      if (!inIframe && el.closest("[data-presenter-chrome]")) continue; // top-doc chrome only
      const txt = norm(el.textContent || "");
      if (!txt) continue;
      let hits = 0;
      for (const k of keys) if (txt.includes(k)) hits++;
      if (!hits) continue;
      const frac = hits / keys.length;
      const hasFigure = figures.length > 0 && figures.some((f) => txt.includes(f));
      if (frac < 0.5 && !hasFigure) continue;
      if (frac > bestScore || (frac === bestScore && txt.length < bestLen)) {
        best = el;
        bestScore = frac;
        bestLen = txt.length;
      }
    }
  }
  return best ? promoteToBlock(best) : null;
}

/** Fallback focus when there is no text target: the open HTML deliverable, else the run
 *  panel, else <main>. */
function fallbackFocus(): Element | null {
  const frame = document.querySelector("iframe[data-artifact-frame]");
  if (frame && (frame as HTMLElement).offsetParent !== null) return frame;
  const dialog = document.querySelector('[role="dialog"], [data-run-modal]');
  if (dialog && (dialog as HTMLElement).offsetParent !== null) return dialog;
  return document.querySelector("main");
}

/** The stage: dims the whole app and spotlights only the focused region (the anchored finding,
 *  or the active content panel), with an optional callout. Runs for the whole presentation. */
export function PresentationOverlay({ active, highlight }: { active: boolean; highlight: Highlight | null }) {
  const [rect, setRect] = useState<{ left: number; top: number; width: number; height: number } | null>(null);

  useEffect(() => {
    if (!active) { setRect(null); return; }
    let cancelled = false;
    let locked: Element | null = null;
    let tries = 0;
    setRect(null);

    const show = (el: Element, scroll: boolean) => {
      if (cancelled) return;
      locked = el;
      if (scroll) el.scrollIntoView({ behavior: "smooth", block: "center" });
      setTimeout(() => { if (!cancelled) setRect(rectInTop(el)); }, scroll ? 360 : 0);
    };

    // Try to lock onto the anchored finding; if not found yet, keep trying while the
    // opened run streams in, then fall back to the active panel. Lock ONCE (no flicker).
    const tick = () => {
      if (cancelled || locked) return;
      const tEl = highlight?.target ? anchorElement(highlight.target) : null;
      if (tEl) { mo.disconnect(); show(tEl, true); return; }
      if (highlight?.target && tries < 24) { tries += 1; setTimeout(tick, 300); return; } // await content
      const fb = fallbackFocus();
      if (fb) { show(fb, false); }            // don't scroll for a whole-panel fallback
      else if (tries++ < 16) setTimeout(tick, 300);
    };

    // Re-anchor only until we lock, so late-arriving run content still gets caught.
    const mo = new MutationObserver(() => {
      if (cancelled || locked || !highlight?.target) return;
      const el = anchorElement(highlight.target);
      if (el) { mo.disconnect(); show(el, true); }
    });
    mo.observe(document.body, { childList: true, subtree: true, characterData: true });
    tick();

    // Track the LOCKED element's position only (rAF-debounced) — no re-resolving.
    let raf = 0;
    const reposition = () => {
      cancelAnimationFrame(raf);
      raf = requestAnimationFrame(() => { if (!cancelled && locked) setRect(rectInTop(locked)); });
    };
    window.addEventListener("scroll", reposition, true);
    window.addEventListener("resize", reposition);
    return () => {
      cancelled = true;
      mo.disconnect();
      cancelAnimationFrame(raf);
      window.removeEventListener("scroll", reposition, true);
      window.removeEventListener("resize", reposition);
    };
  }, [active, highlight]);

  if (!active) return null;

  // No focus resolved yet → plain full-screen dim so the app recedes behind the slide.
  if (!rect) {
    return <div className="fixed inset-0 z-[58] bg-black/70 pointer-events-none" data-presenter-chrome />;
  }

  const pad = 14;
  const vw = window.innerWidth, vh = window.innerHeight;
  const box = {
    left: Math.max(0, rect.left - pad),
    top: Math.max(0, rect.top - pad),
    width: Math.min(vw, rect.width + pad * 2),
    height: Math.min(vh, rect.height + pad * 2),
  };
  const below = box.top + box.height + 150 < vh;
  return (
    <div className="fixed inset-0 z-[58] pointer-events-none" data-presenter-chrome>
      {/* Spotlight: transparent hole with a huge shadow that dims everything else. */}
      <div
        style={{
          position: "fixed",
          left: box.left, top: box.top, width: box.width, height: box.height,
          borderRadius: 10,
          boxShadow: "0 0 0 9999px rgba(6,4,20,0.82)",
          outline: "2px solid rgba(167,139,250,0.85)",
          outlineOffset: 2,
          transition: "all 0.35s cubic-bezier(.2,.8,.2,1)",
        }}
      />
      {highlight?.note && (
        <div
          style={{
            position: "fixed",
            left: Math.min(box.left, vw - 340),
            top: below ? box.top + box.height + 14 : Math.max(12, box.top - 120),
            maxWidth: 320,
          }}
          className="bg-tessera-card/95 backdrop-blur border border-tessera-accent/70 rounded-xl shadow-2xl p-3 text-[13px] leading-snug text-gray-50"
        >
          <span className="text-tessera-accent mr-1">✨</span>
          {highlight.note}
        </div>
      )}
    </div>
  );
}

/** Plays a beat plan: drives the app, shows a visual slide per beat, timer + pause/step. */
export function PresentationPlayer({ plan, onAction, onExit, suspended }:
  { plan: Plan; onAction: (a: Record<string, unknown>) => void; onExit: () => void; suspended?: boolean }) {
  const [idx, setIdx] = useState(-1); // -1 = title card
  const [paused, setPaused] = useState(false);
  const [remaining, setRemaining] = useState(0);
  const timer = useRef<ReturnType<typeof setInterval> | null>(null);
  const beats = plan.beats || [];

  const runBeatActions = useCallback((b: Beat) => {
    // Clear any previous spotlight so the stage refocuses cleanly for this beat.
    onAction({ action: "clear_highlight" });
    (b.actions || []).forEach((a, i) => setTimeout(() => onAction(a), 450 + i * 900));
  }, [onAction]);

  const firedFor = useRef(-999); // ensure a beat's actions fire EXACTLY once (re-renders re-run this effect)
  useEffect(() => {
    if (idx < 0 || idx >= beats.length) return;
    if (firedFor.current === idx) return; // already fired this beat — don't relaunch runs on every render
    firedFor.current = idx;
    const b = beats[idx];
    runBeatActions(b);
    setRemaining(Math.max(5, Math.round(b.duration_s || 12)));
  }, [idx, beats, runBeatActions]);

  useEffect(() => {
    if (idx < 0 || paused || suspended || idx >= beats.length) return;
    timer.current = setInterval(() => {
      setRemaining((r) => { if (r <= 1) { setIdx((i) => i + 1); return 0; } return r - 1; });
    }, 1000);
    return () => { if (timer.current) clearInterval(timer.current); };
  }, [idx, paused, suspended, beats.length]);

  const done = idx >= beats.length;
  const totalS = beats.reduce((s, b) => s + Math.round(b.duration_s || 12), 0);
  const go = (n: number) => setIdx(Math.max(-1, Math.min(beats.length, n)));
  const beat = idx >= 0 && !done ? beats[idx] : null;

  // Title card
  if (idx === -1) {
    return (
      <div className="fixed inset-0 z-[80] bg-black/85 backdrop-blur-sm flex items-center justify-center" data-presenter-chrome>
        <div className="bg-tessera-card border border-tessera-border rounded-2xl p-10 max-w-xl text-center shadow-2xl">
          <div className="text-[10px] uppercase tracking-[0.25em] text-tessera-accent mb-3">● Live presentation</div>
          <h1 className="text-3xl font-bold mb-3 leading-tight">{plan.title || "Tessera"}</h1>
          <p className="text-sm text-gray-400 mb-1">{plan.audience}{plan.focus ? ` · focus: ${plan.focus}` : ""}</p>
          <p className="text-[11px] text-gray-500 mb-7">{beats.length} beats · ~{Math.round(totalS / 60)} min · drives the real app live</p>
          <div className="flex gap-3 justify-center">
            <button onClick={() => go(0)} className="px-6 py-2.5 bg-tessera-gradient rounded-lg text-sm font-semibold hover:scale-105 transition">▶ Start</button>
            <button onClick={onExit} className="px-5 py-2.5 border border-tessera-border rounded-lg text-sm text-gray-400 hover:text-white">Cancel</button>
          </div>
        </div>
      </div>
    );
  }

  return (
    <div className="fixed inset-x-0 bottom-0 z-[80] pointer-events-none" data-presenter-chrome>
      {/* Visual slide + controls (lower band, lit above the dimmed stage) */}
      <div className="pointer-events-auto mx-auto max-w-4xl m-4 rounded-2xl border border-tessera-accent/40 bg-tessera-card/95 backdrop-blur-md shadow-2xl shadow-black/60 overflow-hidden">
        <div className="h-1 bg-tessera-gradient" style={{ width: `${done ? 100 : ((idx + 1) / beats.length) * 100}%`, transition: "width .4s" }} />
        <div className="p-5">
          {done ? (
            <div className="flex items-center justify-between gap-4">
              <div>
                <h2 className="text-xl font-bold">That's the tour.</h2>
                <p className="text-sm text-gray-400 mt-1">Ask me anything — pause and type into the concierge.</p>
              </div>
              <button onClick={onExit} className="px-4 py-2 rounded-lg bg-tessera-gradient text-sm font-semibold">Done</button>
            </div>
          ) : (
            <>
              {beat?.headline && (
                <h2 className="text-3xl font-extrabold leading-tight bg-tessera-gradient bg-clip-text text-transparent">
                  {beat.headline}
                </h2>
              )}
              {beat?.narration && (
                <p
                  className="mt-2 text-[13px] leading-snug text-gray-400 overflow-hidden"
                  style={{ display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical" }}
                  title={beat.narration}
                >
                  {beat.narration}
                </p>
              )}
              <div className="mt-3 flex items-end justify-between gap-6">
                {beat?.bullets && beat.bullets.length > 0 && (
                  <ul className="space-y-2 flex-1 min-w-0">
                    {beat.bullets.map((b, i) => (
                      <li key={i} className="flex items-start gap-2.5 text-[17px] font-medium text-gray-50">
                        <span className="text-tessera-accent text-lg leading-6">›</span>
                        <span>{b}</span>
                      </li>
                    ))}
                  </ul>
                )}
                {beat?.stats && beat.stats.length > 0 && (
                  <div className="flex gap-2.5 shrink-0">
                    {beat.stats.slice(0, 3).map((s, i) => (
                      <div key={i} className="text-center px-4 py-3 rounded-xl bg-tessera-accent/10 border border-tessera-accent/25 min-w-[76px]">
                        <div className="text-2xl font-extrabold text-tessera-accent leading-none">{s.value}</div>
                        {s.label && <div className="text-[9px] uppercase tracking-wider text-gray-400 mt-1.5">{s.label}</div>}
                      </div>
                    ))}
                  </div>
                )}
              </div>
            </>
          )}

          {/* Controls */}
          <div className="mt-3 flex items-center justify-between">
            <span className="text-[10px] uppercase tracking-widest text-gray-500">
              {done ? "complete" : `Beat ${idx + 1} / ${beats.length}`}
            </span>
            <div className="flex items-center gap-2">
              {!done && <span className="text-[11px] text-gray-500 tabular-nums w-9 text-right">{remaining}s</span>}
              {!done && <button onClick={() => go(idx - 1)} className="px-2.5 py-1.5 text-xs rounded-lg bg-white/5 hover:bg-white/10" title="Previous">⏮</button>}
              {!done && <button onClick={() => setPaused((p) => !p)} className="px-2.5 py-1.5 text-xs rounded-lg bg-white/5 hover:bg-white/10" title={paused ? "Resume" : "Pause"}>{paused ? "▶" : "⏸"}</button>}
              {!done && <button onClick={() => go(idx + 1)} className="px-2.5 py-1.5 text-xs rounded-lg bg-white/5 hover:bg-white/10" title="Next">⏭</button>}
              <button onClick={onExit} className="px-2.5 py-1.5 text-xs rounded-lg bg-white/5 hover:bg-white/10 text-gray-400" title="End presentation">✕</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
