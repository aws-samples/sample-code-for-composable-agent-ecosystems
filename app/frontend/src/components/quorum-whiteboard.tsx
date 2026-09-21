"use client";

/**
 * QuorumWhiteboard — the shared board for the Quorum archetype (docs/spec-quorum-whiteboard.md).
 *
 * Renders WHITEBOARD_* ops on an Excalidraw surface, animated like a human hand drawing
 * (progressive strokes, corner hesitation, ease-in/out, handwriting) rather than popping in.
 * Reuses the engine proven in temp/whiteboard-demo. The submitter (rule 4) can draw; an agent's
 * request to erase another author's mark (rule 3) surfaces an approve/deny prompt; an agent can
 * ask the submitter to draw (rule 5) via a banner. This component is loaded ssr:false by the room.
 */
import { useEffect, useRef, useState } from "react";
import { Excalidraw, convertToExcalidrawElements } from "@excalidraw/excalidraw";
import "@excalidraw/excalidraw/index.css";

export interface WBOp { op_id: string; author: string; kind: string; data: any; refs?: string[] }
interface EraseReq { op_id: string; by: string; reason?: string; owner?: string }

interface Props {
  ops: WBOp[];
  erased: string[];
  pending: EraseReq[];
  request?: { by: string; text: string } | null;
  nameFor: (id: string) => string;
  colorFor: (id: string) => string;
  onDraw: (ops: WBOp[]) => void;
  onEraseConsent: (opId: string, approve: boolean) => void;
  onDrawingChange?: (active: boolean) => void;
}

const sleep = (ms: number) => new Promise<void>((r) => setTimeout(r, ms));
const SPEED = 1.0; // >1 slower

export default function QuorumWhiteboard({ ops, erased, pending, request, nameFor, colorFor, onDraw, onEraseConsent, onDrawingChange }: Props) {
  const [api, setApi] = useState<any>(null);
  const canvasRef = useRef<HTMLDivElement>(null);
  const drawnRef = useRef<Set<string>>(new Set());     // op_ids already animated
  const committedRef = useRef<any[]>([]);              // finished excalidraw elements
  const penRef = useRef<any>(null);
  const penTplRef = useRef<any>(null);
  const queueRef = useRef<WBOp[]>([]);
  const runningRef = useRef(false);
  const [drawMode, setDrawMode] = useState(false);
  const baseCountRef = useRef(0);                      // element count that is "board" (not submitter's WIP)

  const conv1 = (skel: any) => convertToExcalidrawElements([skel] as any)[0] as any;
  const bbox = (pts: number[][]) => {
    let a = 1e9, b = 1e9, c = -1e9, d = -1e9;
    for (const [x, y] of pts) { a = Math.min(a, x); b = Math.min(b, y); c = Math.max(c, x); d = Math.max(d, y); }
    return { w: Math.max(1, c - a), h: Math.max(1, d - b) };
  };
  const rectCorners = (x: number, y: number, w: number, h: number) =>
    [[x, y], [x + w, y], [x + w, y + h], [x, y + h], [x, y]];


  /**
   * SILHOUETTES. The server declares what each node IS (store / actor / risk / impact / control /
   * service) and that only ever changed the colour, so a datastore, a person and a risk were three
   * identical rectangles. These are the shapes people actually draw on a board, returned as stroke
   * paths so they animate with the author's own hand. No new model affordance: the kind was already
   * being sent and thrown away.
   */
  const shapePaths = (shape: string, x: number, y: number, w: number, h: number): number[][][] => {
    const cx = x + w / 2, cy = y + h / 2;
    switch (shape) {
      case "diamond":                       // a risk / a decision point
        return [[[cx, y], [x + w, cy], [cx, y + h], [x, cy], [cx, y]]];
      case "cylinder": {                    // a datastore, drawn as a can
        const lip = Math.min(12, h * 0.22);
        return [
          [[x, y + lip], [x, y + h - lip], [cx, y + h], [x + w, y + h - lip], [x + w, y + lip]],
          [[x, y + lip], [cx, y], [x + w, y + lip], [cx, y + lip * 2], [x, y + lip]],
        ];
      }
      case "person": {                      // an actor — head, body, arms, legs beside the label
        const r = Math.min(11, h * 0.2), hx = x + r + 4, hy = y + r + 2;
        return [
          [[hx + r, hy], [hx + r * 0.4, hy + r * 0.9], [hx - r * 0.6, hy + r * 0.5],
           [hx - r * 0.5, hy - r * 0.6], [hx + r * 0.5, hy - r * 0.7], [hx + r, hy]],
          [[hx, hy + r], [hx, hy + r + 18]],
          [[hx - 11, hy + r + 6], [hx + 11, hy + r + 4]],
          [[hx, hy + r + 18], [hx - 9, hy + r + 30]],
          [[hx, hy + r + 18], [hx + 9, hy + r + 31]],
        ];
      }
      case "burst": {                       // an impact — a jagged explosion
        const pts: number[][] = [];
        const spikes = 11;
        for (let i = 0; i <= spikes * 2; i++) {
          const a = (Math.PI * 2 * i) / (spikes * 2) - Math.PI / 2;
          const rr = i % 2 === 0 ? 1 : 0.68;
          pts.push([cx + Math.cos(a) * (w / 2) * rr, cy + Math.sin(a) * (h / 2) * rr]);
        }
        return [pts];
      }
      case "shield":                        // a control
        return [[[x + 4, y], [x + w - 4, y], [x + w - 4, y + h * 0.55],
                 [cx, y + h], [x + 4, y + h * 0.55], [x + 4, y]]];
      case "rounded": {                     // a service
        const r = Math.min(14, h * 0.3);
        return [[[x + r, y], [x + w - r, y], [x + w, y + r], [x + w, y + h - r],
                 [x + w - r, y + h], [x + r, y + h], [x, y + h - r], [x, y + r], [x + r, y]]];
      }
      default:
        return [rectCorners(x, y, w, h)];
    }
  };

/**
 * A per-author HAND. Tremor, thickness, speed, slant and corner behaviour were global and random, so
 * every member drew identically — the board looked like one person had done all of it, which is exactly
 * what it was before authors had pens. Seeded from the persona id, so a member's hand is consistent for
 * the whole meeting and recognisable between members without needing a label.
 */
type Hand = {
  tremor: number;      // perpendicular wobble, px
  width: number;       // stroke thickness multiplier
  speed: number;       // pacing multiplier — some people draw fast and scrappy
  overshoot: number;   // px past a corner before turning back (nobody stops exactly on the corner)
  gap: number;         // px of unclosed corner on a rectangle
  slant: number;       // text baseline drift per character
  roughness: number;   // excalidraw sketchiness
};

function handFor(author: string): Hand {
  // deterministic 32-bit hash of the persona id — same hand every render, every session
  let h = 2166136261;
  for (let i = 0; i < (author || "").length; i++) {
    h ^= (author || "").charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  const u = (n: number) => (((h >>> (n * 4)) & 0xff) / 255);
  return {
    tremor:    0.7 + u(0) * 1.9,      // 0.7 .. 2.6
    width:     0.8 + u(1) * 0.9,      // 0.8 .. 1.7
    speed:     0.7 + u(2) * 0.8,      // 0.7 .. 1.5
    overshoot: u(3) * 4.5,            // 0 .. 4.5
    gap:       u(4) * 5.0,            // 0 .. 5
    slant:     (u(5) - 0.5) * 0.5,    // -0.25 .. 0.25 px/char
    roughness: 1.0 + u(6) * 1.2,      // 1.0 .. 2.2
  };
}

  const render = () => {
    if (!api) return;
    api.updateScene({ elements: [...committedRef.current, ...(penRef.current ? [penRef.current] : [])] });
  };
  const movePen = (px: number, py: number, color: string) => {
    if (!penTplRef.current) penTplRef.current = conv1({ type: "ellipse", x: 0, y: 0, width: 14, height: 14, backgroundColor: "#111", strokeColor: "#111", fillStyle: "solid" });
    penRef.current = { ...penTplRef.current, x: px - 7, y: py - 7, strokeColor: color, backgroundColor: color, seed: penTplRef.current.seed };
  };

  async function stroke(corners: number[][], color: string, type = "line", width = 2.5,
                        style?: string, hand: Hand = handFor("")) {
    const pts: number[][] = [corners[0]];
    const cornerIdx = new Set<number>();
    for (let i = 1; i < corners.length; i++) {
      const [x0, y0] = corners[i - 1], [x1, y1] = corners[i];
      const dist = Math.hypot(x1 - x0, y1 - y0), n = Math.max(1, Math.round(dist / 10));
      const ux = dist ? (x1 - x0) / dist : 0, uy = dist ? (y1 - y0) / dist : 0;
      for (let k = 1; k <= n; k++) {
        const t = k / n;
        const j = k < n ? (Math.random() * 2 - 1) * hand.tremor : 0;   // this author's tremor
        pts.push([x0 + (x1 - x0) * t - uy * j, y0 + (y1 - y0) * t + ux * j]);
      }
      cornerIdx.add(pts.length - 1);
    }
    const ox = pts[0][0], oy = pts[0][1];
    const rel = pts.map(([x, y]) => [x - ox, y - oy]);
    // W5: a dashed line reads "optional/async", a wavy one "unreliable" — meaning carried by the
    // line itself rather than only by a label.
    const tpl = conv1({ type, x: ox, y: oy, points: rel, strokeColor: color,
                        strokeWidth: width * hand.width,
                        roughness: style === "wavy" ? 2.4 : hand.roughness,
                        strokeStyle: style === "dashed" ? "dashed" : "solid" });
    movePen(ox, oy, color); render(); await sleep(160 * SPEED);
    const cidx = [...cornerIdx].sort((a, b) => a - b);
    let ci = 0, segFrom = 0, segTo = cidx[0] ?? rel.length - 1;
    let active: any = null;
    for (let k = 2; k <= rel.length; k++) {
      const sub = rel.slice(0, k), bb = bbox(sub);
      active = { ...tpl, points: sub, width: bb.w, height: bb.h, seed: tpl.seed };
      committedRef.current = [...committedRef.current.filter((e) => e.id !== tpl.id), active];
      movePen(ox + sub[k - 1][0], oy + sub[k - 1][1], color); render();
      const idx = k - 1;
      if (idx > segTo && ci < cidx.length - 1) { segFrom = segTo; segTo = cidx[++ci]; }
      const t = segTo > segFrom ? (idx - segFrom) / (segTo - segFrom) : 0.5;
      const ease = 1.7 - 1.0 * Math.sin(Math.PI * Math.min(1, Math.max(0, t)));
      await sleep((28 * ease + Math.random() * 10) * SPEED / hand.speed);
      if (k % 12 === 0) await sleep((60 + Math.random() * 60) * SPEED);   // occasional mid-stroke hesitation
      if (cornerIdx.has(idx)) await sleep((130 + Math.random() * 90) * SPEED / hand.speed);
    }
    await sleep(100 * SPEED);
  }

  async function typeText(text: string, x: number, y: number, color: string, size = 16,
                          hand: Hand = handFor("")) {
    // Baseline drift: handwriting on a vertical surface wanders off level, consistently per person.
    const tpl = conv1({ type: "text", x, y: y + hand.slant * Math.min(text.length, 40), text,
                        fontSize: size, strokeColor: color, textAlign: "left", fontFamily: 1 });
    for (let i = 1; i <= text.length; i++) {
      const active = { ...tpl, text: text.slice(0, i), originalText: text.slice(0, i), seed: tpl.seed };
      committedRef.current = [...committedRef.current.filter((e) => e.id !== tpl.id), active];
      render();
      const ch = text[i - 1]; let d = 55 + Math.random() * 50;
      if (ch === " ") d += 45; if (ch === "\n") d += 170;
      await sleep(d * SPEED / hand.speed);
    }
    await sleep(110 * SPEED);
  }

  async function animateOp(op: WBOp) {
    // An explicit colour carries MEANING (red = risk/impact, green = control, purple = store) and
    // must win over the per-author tint; fall back to the author's colour when unset.
    const color = (op.data && (op.data as any).color) || colorFor(op.author);
    const d = op.data || {};
    const hand = handFor(op.author || "");
    if (op.kind === "box") {
      const shape = String((d as any).shape || "rect");
      const paths = shapePaths(shape, d.x || 0, d.y || 0, d.w || 160, d.h || 60);
      for (let pi = 0; pi < paths.length; pi++) {
        const path = paths[pi];
        // A hand-drawn outline does not close: the pen overshoots the last corner and stops short of
        // where it started. Nobody produces four exact right angles on a vertical surface. Only the
        // closed outlines get that treatment — a stick figure's limbs are already open strokes.
        const closed = path.length > 2 &&
          Math.abs(path[0][0] - path[path.length - 1][0]) < 1 &&
          Math.abs(path[0][1] - path[path.length - 1][1]) < 1;
        const pts = closed ? path.slice(0, path.length - 1) : path.slice();
        if (closed) pts.push([path[0][0] + hand.gap, path[0][1] - hand.overshoot * 0.4]);
        await stroke(pts, color, "line", 2.5, undefined, hand);
      }
      if (d.label) {
        // The label is written AFTER the shape, and long text overflows rather than being wrapped to
        // fit — a person writes until they run out of box.
        const lines = String(d.label).split("\n");
        const inset = shape === "person" ? 34 : shape === "diamond" ? 26 : 12;
        await typeText(String(d.label), (d.x || 0) + inset,
                       (d.y || 0) + (d.h || 60) / 2 - lines.length * 10, color, 15, hand);
      }
    } else if (op.kind === "arrow") {
      // Routed edges arrive as a polyline (orthogonal, laid out server-side to minimise crossings);
      // older ops only carry from/to, so keep drawing those with a slight hand-drawn bow.
      const pts = Array.isArray(d.points) && d.points.length >= 2 ? (d.points as number[][]) : null;
      if (pts) {
        await stroke(pts, d.color || "#868e96", "arrow", d.width || 2, d.style, hand);
      } else {
        const from = d.from || [0, 0], to = d.to || [100, 0];
        const mid = [(from[0] + to[0]) / 2 + (Math.random() * 8 - 4), (from[1] + to[1]) / 2];
        await stroke([from, mid, to], "#868e96", "arrow", 2, undefined, hand);
      }
      if (d.label) await typeText(String(d.label), ((d.from || d.points?.[0] || [0, 0])[0]) + 8,
                                  ((d.from || d.points?.[0] || [0, 0])[1]) - 16, color, 13, hand);
    } else if (op.kind === "text") {
      await typeText(String(d.text || ""), d.x || 0, d.y || 0, color, d.size || 15, hand);
    } else if (op.kind === "stroke") {
      // freehand emphasis (a circle round the punchline, an underline, a strike-through)
      const p = (d.points || []) as number[][];
      if (p.length >= 2) await stroke(p, color, "line", d.width || 2.5, d.style, hand);
    }
    penRef.current = null; render();
    baseCountRef.current = committedRef.current.length;
  }

  // fit camera to all known ops (once, roughly)
  const fitCamera = () => {
    if (!api || !ops.length) return;
    let a = 1e9, b = 1e9, c = -1e9, d = -1e9;
    for (const o of ops) {
      const g = o.data || {};
      const xs = g.kind === "arrow" || o.kind === "arrow" ? [g.from?.[0], g.to?.[0]] : [g.x, (g.x || 0) + (g.w || 160)];
      const ys = o.kind === "arrow" ? [g.from?.[1], g.to?.[1]] : [g.y, (g.y || 0) + (g.h || 60)];
      for (const x of xs) if (typeof x === "number") { a = Math.min(a, x); c = Math.max(c, x); }
      for (const y of ys) if (typeof y === "number") { b = Math.min(b, y); d = Math.max(d, y); }
    }
    if (a > c || b > d) { a = 0; b = 0; c = 960; d = 640; }
    const el = canvasRef.current; const w = el?.clientWidth || 900, h = el?.clientHeight || 560;
    const dw = Math.max(200, c - a + 80), dh = Math.max(160, d - b + 80);
    const z = Math.min(w / dw, h / dh) * 0.9;
    const cx = a + (c - a) / 2, cy = b + (d - b) / 2;
    api.updateScene({ appState: { scrollX: w / (2 * z) - cx, scrollY: h / (2 * z) - cy, zoom: { value: z } } });
  };

  // drain the animation queue
  async function drain() {
    if (runningRef.current || !api) return;
    runningRef.current = true;
    onDrawingChange?.(true);
    try {
      while (queueRef.current.length) {
        const op = queueRef.current.shift()!;
        if (drawnRef.current.has(op.op_id)) continue;
        drawnRef.current.add(op.op_id);
        fitCamera();
        await animateOp(op);
      }
    } finally { runningRef.current = false; onDrawingChange?.(false); }
  }

  // enqueue any not-yet-drawn ops when props change
  useEffect(() => {
    if (!api) return;
    const fresh = ops.filter((o) => !drawnRef.current.has(o.op_id));
    if (fresh.length) { queueRef.current.push(...fresh); drain(); }
    // apply erasures (fade already-committed shapes out)
    if (erased.length) {
      committedRef.current = committedRef.current.filter((e) => {
        const oid = (e as any).__opId; return !(oid && erased.includes(oid));
      });
      render();
    }
  }, [ops, erased, api]); // eslint-disable-line react-hooks/exhaustive-deps

  // submitter drawing: send new elements to the board as ops
  const sendMyDrawing = () => {
    if (!api) return;
    const els = api.getSceneElements().filter((e: any) => !e.isDeleted);
    const extra = els.slice(baseCountRef.current);
    const newOps: WBOp[] = [];
    for (const e of extra) {
      if (e.type === "rectangle") newOps.push({ op_id: "", author: "human", kind: "box", data: { x: e.x, y: e.y, w: e.width, h: e.height } });
      else if (e.type === "arrow" || e.type === "line") { const pts = e.points || []; const last = pts[pts.length - 1] || [0, 0]; newOps.push({ op_id: "", author: "human", kind: e.type === "arrow" ? "arrow" : "stroke", data: e.type === "arrow" ? { from: [e.x, e.y], to: [e.x + (last[0] || 0), e.y + (last[1] || 0)] } : { points: pts.map((p: number[]) => [e.x + p[0], e.y + p[1]]) } }); }
      else if (e.type === "text") newOps.push({ op_id: "", author: "human", kind: "text", data: { x: e.x, y: e.y, text: e.text } });
      else if (e.type === "freedraw") newOps.push({ op_id: "", author: "human", kind: "stroke", data: { points: (e.points || []).map((p: number[]) => [e.x + p[0], e.y + p[1]]) } });
    }
    if (newOps.length) onDraw(newOps);
    setDrawMode(false);
  };

  return (
    <div className="wb-frame relative w-full h-full rounded-[14px] p-4 pb-8"
         style={{ background: "linear-gradient(180deg,#e9edf2,#c3cad3 60%,#aab2bd)", boxShadow: "0 18px 48px rgba(0,0,0,.45), inset 0 1px 0 rgba(255,255,255,.7)" }}>
      <div className="absolute -top-2 left-6 z-10 text-[11px] font-bold text-white px-2.5 py-1 rounded-md" style={{ background: "#ff5d5d", boxShadow: "0 3px 8px rgba(0,0,0,.3)" }}>WHITEBOARD</div>

      {/* ask-submitter banner (rule 5) */}
      {request && (
        <div className="absolute top-2 right-4 z-20 max-w-[60%] text-[12px] bg-amber-100 border border-amber-300 text-amber-900 px-3 py-1.5 rounded-lg shadow">
          <b>{nameFor(request.by)}</b> asks you to draw: {request.text}
        </div>
      )}

      {/* erase-consent prompts (rule 3) */}
      {pending.map((r) => (
        <div key={r.op_id} className="absolute top-10 right-4 z-20 max-w-[60%] text-[12px] bg-white border border-red-300 text-gray-800 px-3 py-1.5 rounded-lg shadow">
          <b>{nameFor(r.by)}</b> asks to erase {nameFor(r.owner || "")}&apos;s mark{r.reason ? ` (“${r.reason}”)` : ""}. We usually keep others&apos; work.
          <div className="mt-1 flex gap-2">
            <button onClick={() => onEraseConsent(r.op_id, true)} className="px-2 py-0.5 rounded bg-red-500 text-white">Allow erase</button>
            <button onClick={() => onEraseConsent(r.op_id, false)} className="px-2 py-0.5 rounded bg-gray-200">Keep it</button>
          </div>
        </div>
      ))}

      {/* the white surface */}
      <div className="relative w-full h-full rounded-md overflow-hidden bg-white" style={{ boxShadow: "inset 0 2px 10px rgba(0,0,0,.12), inset 0 0 0 1px #d8dde3" }}>
        <div ref={canvasRef} className={`wb-surface w-full h-full ${drawMode ? "wb-draw" : "wb-view"}`}>
          <Excalidraw
            viewModeEnabled={!drawMode}
            excalidrawAPI={(a: any) => setApi(a)}
            initialData={{ appState: { viewBackgroundColor: "#ffffff" } as any }}
          />
        </div>
      </div>

      {/* submitter controls (rule 4) */}
      <div className="absolute bottom-1.5 left-1/2 -translate-x-1/2 z-20 flex items-center gap-2">
        {!drawMode ? (
          <button onClick={() => { setDrawMode(true); baseCountRef.current = api?.getSceneElements().filter((e: any) => !e.isDeleted).length || 0; }}
                  className="text-[11px] px-2.5 py-1 rounded bg-gray-800 text-white shadow">✏️ Draw</button>
        ) : (
          <>
            <button onClick={sendMyDrawing} className="text-[11px] px-2.5 py-1 rounded bg-emerald-600 text-white shadow">Add to board</button>
            <button onClick={() => setDrawMode(false)} className="text-[11px] px-2.5 py-1 rounded bg-gray-200 shadow">Cancel</button>
          </>
        )}
      </div>

      {/* hide Excalidraw chrome in view mode so it reads as a bare board */}
      <style>{`
        .wb-view .layer-ui__wrapper{display:none !important}
        .wb-surface .App-bottom-bar,.wb-surface .scroll-back-to-content{display:none !important}
      `}</style>
    </div>
  );
}
