/**
 * Presentation transforms — declarative, deterministic DOM transforms applied over the
 * EXISTING deliverable, stacked *under* the Studio's CSS-override layer.
 *
 * Two transform families:
 *  - `tabs`    — structural re-presentation: group <h2> sections into tabs (moves existing
 *                nodes; never authors text).
 *  - `diagram` — a content-derived VISUALISATION: the design agent generates a self-contained
 *                inline SVG (any chart type — heatmap, bar, matrix, timeline, …) from a
 *                section's data; the engine inserts it (or replaces the section's table) at
 *                the anchor. The SVG marks data-bearing labels with class "tx-data"; a fidelity
 *                check verifies every such label/value is present in the source section, so the
 *                diagram can VISUALISE but never FABRICATE.
 *
 * The engine touches ONLY the anchor's section (insert a figure, optionally replace its table)
 * — it never edits prose, so the rest of the document is unchanged by construction.
 */

export type DiagramParams = { id?: string; anchor: string; mode?: "after" | "before" | "replace"; svg: string; title?: string };
export type Transform =
  | { type: "tabs"; params?: { headingSelector?: string } }
  | { type: "diagram"; params: DiagramParams }
  | { type: "section"; params: SectionParams }
  | { type: string; params?: Record<string, unknown> };

const SCAFFOLD = "data-tx-scaffold";
const TXID = "data-tx-id";          // stable identity for anything the studio can address

const slug = (s: string) =>
  (s || "").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "").slice(0, 40) || "item";

/** Give every chart already in the document a stable id (base-1, base-2, …) so it can be
 *  addressed — replaced or removed — just like one the studio added. Idempotent. */
export function ensureBaseIds(doc: Document): void {
  let n = 0;
  Array.from(doc.querySelectorAll("figure, .svgwrap, svg")).forEach((el) => {
    // only tag the outermost chart host, and only once
    if (el.closest(`[${TXID}]`)) return;
    const host = (el.closest("figure, .svgwrap") || el) as HTMLElement;
    if (host.hasAttribute(TXID)) return;
    host.setAttribute(TXID, `base-${++n}`);
  });
}
const STYLE_ID = "tx-style";

function contentRoot(doc: Document): HTMLElement {
  const first = doc.querySelector("h2") || doc.querySelector("h1");
  return (first?.parentElement as HTMLElement) || doc.body;
}

function injectCss(doc: Document) {
  if (doc.getElementById(STYLE_ID)) return;
  const st = doc.createElement("style");
  st.id = STYLE_ID;
  st.setAttribute(SCAFFOLD, "1"); // CSS text must not count toward any content check
  st.textContent = [
    ".tx-tabbar{display:flex;flex-wrap:wrap;gap:6px;border-bottom:2px solid #e5e7eb;margin:8px 0 20px;padding:0}",
    ".tx-tab{appearance:none;border:0;background:transparent;padding:9px 15px;font:600 13px/1.2 inherit;color:#6b7280;cursor:pointer;border-bottom:2px solid transparent;margin-bottom:-2px;border-radius:8px 8px 0 0}",
    ".tx-tab:hover{color:#111827;background:#f3f4f6}",
    ".tx-tab.tx-active{color:#7c3aed;border-bottom-color:#7c3aed;background:#faf5ff}",
    "figure[data-tx-diagram]{margin:18px 0;padding:0;max-width:100%}",
    "figure[data-tx-diagram] svg{max-width:100%;height:auto}",
    "figure[data-tx-diagram] figcaption{font:600 12px/1.3 inherit;color:#6b7280;margin-top:6px}",
    "section[data-tx-section]{margin:26px 0 0;padding-top:8px}",
    "section[data-tx-section] table{width:100%;border-collapse:collapse;font-size:12.5px}",
    "section[data-tx-section] td,section[data-tx-section] th{border:1px solid #e5e7eb;padding:6px 9px;vertical-align:top;text-align:left}",
    "section[data-tx-section] th{background:#f8fafc;font-weight:700}",
    "section[data-tx-section] .tx-gterm,section[data-tx-section] td:first-child{white-space:nowrap;font-weight:600}",
    "section[data-tx-section] p{color:#475467}",
  ].join("\n");
  (doc.head || doc.body).appendChild(st);
}

// ── tabs ─────────────────────────────────────────────────────────────────────
function applyTabs(doc: Document, params?: { headingSelector?: string }) {
  const root = contentRoot(doc);
  const sel = params?.headingSelector || "h2";
  const heads = Array.from(root.children).filter((n) => (n as HTMLElement).matches?.(sel)) as HTMLElement[];
  if (heads.length < 2) return;

  const panels: { label: string; panel: HTMLElement }[] = [];
  heads.forEach((h, i) => {
    const panel = doc.createElement("section");
    panel.className = "tx-tab-panel";
    const nextHead = heads[i + 1] || null;
    const toMove: ChildNode[] = [];
    let node: ChildNode | null = h;
    while (node && node !== nextHead) { toMove.push(node); node = node.nextSibling; }
    toMove.forEach((n) => panel.appendChild(n));
    panels.push({ label: (h.textContent || `Section ${i + 1}`).trim(), panel });
  });

  const wrap = doc.createElement("div");
  wrap.className = "tx-tabs";
  const bar = doc.createElement("div");
  bar.className = "tx-tabbar";
  bar.setAttribute(SCAFFOLD, "1");
  panels.forEach((p, i) => {
    const b = doc.createElement("button");
    b.type = "button";
    b.className = "tx-tab" + (i === 0 ? " tx-active" : "");
    b.textContent = p.label;
    b.setAttribute(SCAFFOLD, "1");
    b.setAttribute("data-idx", String(i));
    bar.appendChild(b);
    p.panel.style.display = i === 0 ? "" : "none";
  });
  wrap.appendChild(bar);
  panels.forEach((p) => wrap.appendChild(p.panel));
  root.appendChild(wrap);

  bar.addEventListener("click", (e) => {
    const t = e.target as HTMLElement;
    if (!t || t.className.indexOf("tx-tab") < 0) return;
    const idx = +(t.getAttribute("data-idx") || "0");
    Array.from(bar.children).forEach((c, i) => (c as HTMLElement).classList.toggle("tx-active", i === idx));
    Array.from(wrap.querySelectorAll<HTMLElement>(".tx-tab-panel")).forEach((pan, i) => (pan.style.display = i === idx ? "" : "none"));
  });
}

// ── diagram ──────────────────────────────────────────────────────────────────
const _norm = (s: string) => (s || "").toLowerCase().replace(/\s+/g, " ").trim();

/** Locate a section by anchor text (matches an h1/h2/h3), returning the heading,
 *  its node range (until the next same-or-higher heading), its first table, and any
 *  existing figure/svg inside it (the node a new diagram should SUPERSEDE). */
function resolveSection(doc: Document, anchor: string): { heading: HTMLElement; range: Element[]; firstTable: HTMLTableElement | null; existingFigure: Element | null } | null {
  const a = _norm(anchor);
  if (!a) return null;
  const heads = Array.from(doc.querySelectorAll<HTMLElement>("h1,h2,h3,h4"));
  const heading = heads.find((h) => { const t = _norm(h.textContent || ""); return t && (t.includes(a) || a.includes(t)); });
  if (!heading) return null;
  const level = +heading.tagName[1];
  const range: Element[] = [heading];
  let firstTable: HTMLTableElement | null = null;
  let existingFigure: Element | null = null;
  let n = heading.nextElementSibling;
  while (n) {
    if (/^H[1-6]$/.test(n.tagName) && +n.tagName[1] <= level) break;
    range.push(n);
    if (!firstTable) firstTable = (n.tagName === "TABLE" ? (n as HTMLTableElement) : n.querySelector("table"));
    if (!existingFigure) {
      // a wrapped figure/chart container, or a bare svg — whichever comes first
      existingFigure = n.matches?.("figure,[data-tx-diagram],.svgwrap") ? n
        : (n.querySelector("figure,[data-tx-diagram],.svgwrap") || (n.tagName === "SVG" ? n : n.querySelector("svg")));
    }
    n = n.nextElementSibling;
  }
  return { heading, range, firstTable, existingFigure };
}

const sectionText = (sec: { range: Element[] }) => sec.range.map((e) => e.textContent || "").join(" ");

/** Elements never allowed in agent-generated SVG (script execution / HTML smuggling). */
const _SVG_FORBIDDEN_TAGS = new Set(["script", "foreignobject", "iframe", "object", "embed", "audio", "video"]);

/** Strip anything executable/external from agent-generated SVG (defense-in-depth; the
 *  report iframe is already script-sandboxed).
 *
 *  Parse the markup into a real DOM and walk the tree removing forbidden elements,
 *  every `on*` event-handler attribute, and any href/xlink:href whose scheme isn't
 *  http/https. A DOM walk (rather than regex tag-stripping) can't be defeated by
 *  malformed, nested, or overlapping tags like `<scr<script>ipt>`, which is exactly the
 *  weakness in the previous regex approach. */
export function sanitizeSvg(svg: string): string {
  const input = svg || "";
  // DOMParser isn't available during SSR; this runs client-side (called from DOM
  // transforms), but guard so a non-browser context fails closed (empty string).
  if (typeof DOMParser === "undefined") return "";

  const doc = new DOMParser().parseFromString(input, "image/svg+xml");
  const root = doc.querySelector("svg");
  if (!root || doc.querySelector("parsererror")) return "";

  const walk = (el: Element) => {
    // Depth-first over a static copy, since we mutate the tree as we go.
    for (const child of Array.from(el.children)) {
      if (_SVG_FORBIDDEN_TAGS.has(child.tagName.toLowerCase())) {
        child.remove();
        continue;
      }
      for (const attr of Array.from(child.attributes)) {
        const name = attr.name.toLowerCase();
        if (name.startsWith("on")) {
          child.removeAttribute(attr.name);
          continue;
        }
        if (name === "href" || name === "xlink:href") {
          const scheme = attr.value.trim().toLowerCase();
          if (!/^(https?:|#|\/|mailto:)/.test(scheme)) child.removeAttribute(attr.name);
        }
      }
      walk(child);
    }
  };
  walk(root);

  return new XMLSerializer().serializeToString(root);
}

function applyDiagram(doc: Document, p: DiagramParams) {
  if (!p || !p.svg) return;
  const id = p.id || slug(p.title || p.anchor);
  const fig = doc.createElement("figure");
  fig.setAttribute("data-tx-diagram", "1");
  fig.setAttribute(TXID, id);
  fig.innerHTML = sanitizeSvg(p.svg);
  if (p.title) {
    const cap = doc.createElement("figcaption");
    cap.setAttribute(SCAFFOLD, "1");           // caption is our chrome
    cap.textContent = p.title;
    fig.appendChild(cap);
  }

  // 1) IDENTITY FIRST — if this chart already exists (same id), update it IN PLACE. This is
  //    what makes "change that diagram" impossible to duplicate, no matter which mode or
  //    anchor wording the agent used, and it leaves any OTHER chart in the section alone.
  const existing = doc.querySelector(`[${TXID}="${CSS.escape(id)}"]`);
  if (existing?.parentNode) { existing.replaceWith(fig); return; }

  const sec = resolveSection(doc, p.anchor);
  if (!sec) return;                            // anchor not found → skip (target validation)

  // 2) An explicit `replace` supersedes the chart already in that section (the agent asked
  //    to swap it), but only when it has no id of its own to update.
  if ((p.mode || "after") === "replace") {
    const victim = sec.existingFigure || sec.firstTable;
    if (victim?.parentNode) {
      const host = (victim.matches("figure,[data-tx-diagram],.svgwrap")
        ? victim : (victim.closest("figure,[data-tx-diagram],.svgwrap") || victim)) as Element;
      host.replaceWith(fig);
      return;
    }
  }
  // 3) Otherwise ADD it — a section may hold several charts.
  if (p.mode === "before" && sec.heading.parentNode) { sec.heading.parentNode.insertBefore(fig, sec.heading); return; }
  sec.heading.parentNode?.insertBefore(fig, sec.heading.nextSibling);
}

/** REMOVE — delete content from the document (a section's chart, its first table, or the
 *  whole section). Safe because saves are flattened copies: the run's original artifact is
 *  never overwritten. Idempotent — deleting an already-deleted node is a no-op. */
function applyRemove(doc: Document, p: { id?: string; anchor?: string; target?: string }) {
  // Precise form: delete the element with this id (base-2, conf-mix, …). This is what makes
  // "remove THAT chart" exact instead of "whichever one I find first".
  if (p?.id) {
    const el = doc.querySelector(`[${TXID}="${CSS.escape(String(p.id))}"]`);
    if (el) { el.remove(); return; }
  }
  const target = String(p?.target || "chart").toLowerCase();
  const anchor = String(p?.anchor || "");
  if (target === "tabs") {                       // undo a tab layout
    doc.querySelectorAll(".tx-tabbar").forEach((n) => n.remove());
    doc.querySelectorAll<HTMLElement>(".tx-tab-panel").forEach((n) => { n.style.display = ""; });
    return;
  }
  if (!anchor) return;
  const sec = resolveSection(doc, anchor);
  if (!sec) return;
  if (target === "section") {
    sec.range.forEach((n) => n.remove());
    return;
  }
  if (target === "table") { sec.firstTable?.remove(); return; }
  // default: the section's chart
  const fig = sec.existingFigure;
  if (fig) (fig.matches("figure,[data-tx-diagram],.svgwrap") ? fig : (fig.closest("figure,[data-tx-diagram],.svgwrap") || fig)).remove();
}

// ── section (additive, grounded) ──────────────────────────────────────────────
/** SECTION — an ADDITIVE block derived from the document: glossary, index of findings,
 *  summary table, contents, checklist, … It never alters or reinterprets existing text
 *  (we only insert), and its markup is sanitised. The block is marked scaffold so content
 *  checks treat the ORIGINAL document as the source of truth. */
const SECTION_OK_TAGS = new Set(["SECTION","DIV","H2","H3","H4","P","UL","OL","LI","TABLE","THEAD","TBODY",
  "TR","TH","TD","STRONG","B","EM","I","CODE","SPAN","BR","DL","DT","DD","SMALL","A","CAPTION","HR"]);

function sanitizeFragment(doc: Document, html: string): DocumentFragment {
  const tpl = doc.createElement("div");
  tpl.innerHTML = html || "";
  const walk = (el: Element) => {
    for (const child of Array.from(el.children)) {
      if (!SECTION_OK_TAGS.has(child.tagName)) { child.remove(); continue; }
      for (const attr of Array.from(child.attributes)) {
        const n = attr.name.toLowerCase();
        const okAttr = n === "class" || (child.tagName === "A" && (n === "href" || n === "target" || n === "rel"));
        if (!okAttr || (n === "href" && /^\s*javascript:/i.test(attr.value))) child.removeAttribute(attr.name);
      }
      walk(child);
    }
  };
  walk(tpl);
  const frag = doc.createDocumentFragment();
  while (tpl.firstChild) frag.appendChild(tpl.firstChild);
  return frag;
}

export type SectionParams = { title?: string; html: string; mode?: "end" | "after" | "before"; anchor?: string; id?: string };

function applySection(doc: Document, p: SectionParams) {
  if (!p?.html) return;
  const key = p.id || p.title || "";
  const sec = doc.createElement("section");
  sec.setAttribute("data-tx-section", key || "1");
  sec.setAttribute(TXID, key ? `sec-${slug(key)}` : "sec-1");
  // update in place when this section already exists (same id) — never a second copy
  const prev = doc.querySelector(`[${TXID}="${CSS.escape(key ? `sec-${slug(key)}` : "sec-1")}"]`);
  sec.setAttribute(SCAFFOLD, "1");
  if (p.title) {
    const h = doc.createElement("h2");
    h.textContent = p.title;
    sec.appendChild(h);
  }
  sec.appendChild(sanitizeFragment(doc, p.html));
  if (prev?.parentNode) { prev.replaceWith(sec); return; }
  const mode = p.mode || "end";
  if ((mode === "after" || mode === "before") && p.anchor) {
    const at = resolveSection(doc, p.anchor);
    if (at?.heading?.parentNode) {
      if (mode === "before") at.heading.parentNode.insertBefore(sec, at.heading);
      else {
        const last = at.range[at.range.length - 1];
        last.parentNode?.insertBefore(sec, last.nextSibling);
      }
      return;
    }
  }
  (doc.body || doc.documentElement).appendChild(sec);
}

// ── apply + checks ─────────────────────────────────────────────────────────────
export function applyTransforms(doc: Document, transforms: Transform[]): void {
  if (!doc || !transforms?.length) return;
  injectCss(doc);
  ensureBaseIds(doc);
  // Deletions run FIRST. Applied in list order, a remove appended after an insert would
  // delete the chart just added (it sits first in the section) and leave the old one.
  const ordered = [...transforms.filter((t) => t.type === "remove"),
                   ...transforms.filter((t) => t.type !== "remove")];
  for (const t of ordered) {
    if (t.type === "tabs") applyTabs(doc, (t as any).params);
    else if (t.type === "diagram") applyDiagram(doc, (t as any).params);
    else if (t.type === "section" || t.type === "glossary") applySection(doc, (t as any).params);
    else if (t.type === "remove") applyRemove(doc, (t as any).params || {});
    // Unknown transform types are ignored (forward-compatible).
  }
}

/** Normalized visible text with scaffold + diagrams removed. */
export function normalizedText(root: HTMLElement | Document): string {
  const el = (root as Document).body || (root as HTMLElement);
  const clone = el.cloneNode(true) as HTMLElement;
  Array.from(clone.querySelectorAll(`[${SCAFFOLD}],[data-tx-diagram]`)).forEach((n) => n.remove());
  return (clone.textContent || "").replace(/\s+/g, " ").trim();
}

function tokenize(text: string): Set<string> {
  const t = (text || "").toLowerCase();
  const words = t.match(/[a-z]{4,}/g) || [];
  const nums = t.match(/\d[\d.,]*\d|\d/g) || [];
  // keep multi-char numbers as data; single small ints are treated as axis ticks (ignored on check)
  return new Set([...words, ...nums]);
}

/** Extract the DATA-bearing text from an SVG. Prefers elements the agent marked with
 *  class "tx-data"; falls back to all SVG text (looser). */
function svgDataText(svg: string): string {
  try {
    const box = document.implementation.createHTMLDocument("").createElement("div");
    box.innerHTML = svg;
    const data = box.querySelectorAll(".tx-data");
    if (data.length) return Array.from(data).map((e) => e.textContent || "").join(" ");
    return box.textContent || "";
  } catch {
    return (svg || "").replace(/<[^>]+>/g, " ");
  }
}

/**
 * Fidelity / content guard. For `diagram` transforms, every DATA label/value in the SVG
 * must appear in the source section (no fabrication). `tabs` carry no data risk.
 * Returns { ok, unmatched, note }.
 */
export function verifyInvariant(baseHtml: string, transforms: Transform[]): { ok: boolean; unmatched: string[]; note: string } {
  const diagrams = (transforms || []).filter((t) => t.type === "diagram") as { type: "diagram"; params: DiagramParams }[];
  if (!diagrams.length) return { ok: true, unmatched: [], note: "" };
  const doc = new DOMParser().parseFromString(baseHtml, "text/html");
  const unmatched: string[] = [];
  for (const d of diagrams) {
    const p = d.params || ({} as DiagramParams);
    const sec = resolveSection(doc, p.anchor || "");
    const src = tokenize(sec ? sectionText(sec) : (doc.body.textContent || ""));
    for (const tok of tokenize(svgDataText(p.svg || ""))) {
      // ignore short numeric axis ticks (1-2 digits) — treat only words + longer numbers as data
      if (/^\d{1,2}$/.test(tok)) continue;
      if (!src.has(tok)) unmatched.push(tok);
    }
  }
  const uniq = Array.from(new Set(unmatched));
  return {
    ok: uniq.length === 0,
    unmatched: uniq,
    note: uniq.length
      ? `${uniq.length} diagram label/value(s) not found in the section — review: ${uniq.slice(0, 8).join(", ")}`
      : "All diagram data is sourced from the section — verified.",
  };
}

/** Compact DOM outline for the design agent: headings + table signatures, so it can
 *  target existing sections and read their data to build a grounded diagram. */
export function domOutline(html: string): string {
  const doc = new DOMParser().parseFromString(html, "text/html");
  ensureBaseIds(doc);
  const lines: string[] = [];
  const headingOf = (el: Element) => {
    let n: Element | null = el;
    while (n) {
      let p: Element | null = n.previousElementSibling;
      while (p) { if (/^H[1-4]$/.test(p.tagName)) return (p.textContent || "").trim().slice(0, 60); p = p.previousElementSibling; }
      n = n.parentElement;
    }
    return "";
  };
  doc.querySelectorAll("h1,h2,h3").forEach((h) => lines.push(`${h.tagName} ${(h.textContent || "").trim().slice(0, 80)}`));
  // Existing charts/sections ARE addressable by id — even after a save, because the id is
  // baked into the markup. The agent uses these to change or delete a specific one.
  doc.querySelectorAll(`[${TXID}]`).forEach((el) => {
    const kind = el.hasAttribute("data-tx-section") ? "SECTION" : "CHART";
    lines.push(`${kind} id="${el.getAttribute(TXID)}" in "${headingOf(el)}"`);
  });
  doc.querySelectorAll("table").forEach((t, i) => {
    const hs = Array.from(t.querySelectorAll("tr:first-child th, tr:first-child td")).map((c) => (c.textContent || "").trim().slice(0, 24));
    lines.push(`TABLE#${i} rows=${t.querySelectorAll("tr").length} cols=[${hs.join(" | ")}]`);
  });
  return lines.join("\n");
}
