"use client";

import { AGUIEvent } from "@/lib/ag-ui-types";

type DagStatus = "pending" | "running" | "complete" | "failed" | "skipped";
type NodeKind = "agent" | "checkpoint" | "challenger" | "report";

interface DagNode {
  id: string; name: string; swarm: string; depends_on: string[]; kind: NodeKind;
  okind?: string; desc?: string;
}

interface DagMapProps {
  config: any;
  events: AGUIEvent[];
  focusedAgent: string | null;
  onFocusAgent: (id: string) => void;
}

// ── Agent-TYPE taxonomy: distinct look per role ──────────────────────────────
type AgentType = "research" | "synthesis" | "compose" | "drafter" | "checkpoint" | "challenger" | "report" | "agent";
const TYPE_META: Record<AgentType, { label: string; bar: string; chipBg: string; chipText: string; icon: string }> = {
  research:   { label: "research",   bar: "#2dd4bf", chipBg: "bg-teal-500/15",    chipText: "text-teal-300",    icon: "🔎" },
  synthesis:  { label: "synthesis",  bar: "#38bdf8", chipBg: "bg-sky-500/15",     chipText: "text-sky-300",     icon: "∑" },
  compose:    { label: "composer",   bar: "#a78bfa", chipBg: "bg-violet-500/15",  chipText: "text-violet-300",  icon: "✎" },
  drafter:    { label: "drafter",    bar: "#f472b6", chipBg: "bg-pink-500/15",    chipText: "text-pink-300",    icon: "📄" },
  checkpoint: { label: "checkpoint", bar: "#c084fc", chipBg: "bg-purple-500/15",  chipText: "text-purple-300",  icon: "◆" },
  challenger: { label: "challenger", bar: "#fb7185", chipBg: "bg-rose-500/15",    chipText: "text-rose-300",    icon: "⚔" },
  report:     { label: "report",     bar: "#818cf8", chipBg: "bg-indigo-500/15",  chipText: "text-indigo-300",  icon: "▤" },
  agent:      { label: "agent",      bar: "#94a3b8", chipBg: "bg-slate-500/15",   chipText: "text-slate-300",   icon: "●" },
};

function typeOf(n: DagNode): AgentType {
  if (n.kind === "checkpoint") return "checkpoint";
  if (n.kind === "challenger") return "challenger";
  if (n.kind === "report") return "report";
  const o = (n.okind || "").toLowerCase();
  const s = (n.swarm || "").toLowerCase();
  if (o === "drafter" || s.includes("output") || s.includes("draft")) return "drafter";
  if (s.includes("compos")) return "compose";
  if (s.includes("synth")) return "synthesis";
  if (s.includes("research") || (n.depends_on.length === 0)) return "research";
  return "agent";
}

const PLATFORM_DESC: Record<string, string> = {
  checkpoint: "Fail-fast quality gate reviewing this layer's claims; below threshold it pauses for a human.",
  challenger: "Adversarial reviewer that red-teams the pipeline's output before the report is composed.",
  report: "Faithfully synthesizes all agents' findings and the challenger's verdict into the final report.",
};

function shortDesc(a: any, kind: NodeKind): string {
  if (kind !== "agent") return PLATFORM_DESC[kind] || "";
  let s = String(a?.description || a?.prompt || "").replace(/\s+/g, " ").trim();
  if (!s) return "";
  const m = s.match(/contribution:\s*(.*)/i);
  if (m) s = m[1].trim();
  const cut = s.search(/\.\s/);
  if (cut > 24 && cut < 170) s = s.slice(0, cut);
  return s.length > 150 ? s.slice(0, 147) + "…" : s;
}

const NODE_W = 186, NODE_H = 58, H_GAP = 206, V_GAP = 112, PAD = 18;

export function DagMap({ config, events, focusedAgent, onFocusAgent }: DagMapProps) {
  const agents: DagNode[] =
    (config?.agents || []).map((a: any) => ({
      id: a.id,
      name: a.name || a.id,
      swarm: a.swarm || "",
      depends_on: a.depends_on || [],
      kind: "agent" as NodeKind,
      okind: a.output_kind,
      desc: shortDesc(a, "agent"),
    }));

  // Group USER agents into execution layers (same topological algorithm the compiler uses).
  const userLayers: DagNode[][] = [];
  {
    const placed = new Set<string>();
    let remaining = [...agents];
    while (remaining.length > 0) {
      const layer = remaining.filter((a) => a.depends_on.every((d) => placed.has(d)));
      if (layer.length === 0) { userLayers.push(remaining); break; }
      userLayers.push(layer);
      layer.forEach((a) => placed.add(a.id));
      remaining = remaining.filter((a) => !placed.has(a.id));
    }
  }

  // Mirror the compiler's deploy-time platform injection.
  const quality = config?.quality || {};
  const rawChallenger = quality.challenger ?? true;
  const challengerMode = rawChallenger === true ? "final" : rawChallenger === false ? "off" : String(rawChallenger).toLowerCase();
  const reportEnabled = config?.report !== false;

  const layers: DagNode[][] = [];
  userLayers.forEach((layer, li) => {
    layers.push(layer);
    if (challengerMode === "per_level" && layer.length > 0) {
      layers.push([{ id: `checkpoint_${li}`, name: `Checkpoint L${li}`, swarm: "", depends_on: [], kind: "checkpoint", desc: PLATFORM_DESC.checkpoint }]);
    }
  });
  if (challengerMode === "final") layers.push([{ id: "challenger_agent", name: "Challenger", swarm: "", depends_on: [], kind: "challenger", desc: PLATFORM_DESC.challenger }]);
  if (reportEnabled) layers.push([{ id: "report_agent", name: "Report", swarm: "", depends_on: [], kind: "report", desc: PLATFORM_DESC.report }]);

  const allNodes: DagNode[] = layers.flat();

  const statuses = new Map<string, DagStatus>();
  allNodes.forEach((a) => statuses.set(a.id, "pending"));
  events.forEach((e) => {
    if (e.type === "STATE_DELTA" && e.agent && (e as any).status) {
      const s = (e as any).status as string;
      if (["pending", "running", "complete", "failed", "skipped"].includes(s)) statuses.set(e.agent as string, s as DagStatus);
    }
  });

  const groundingResults = new Map<string, boolean>();
  events.forEach((e) => {
    if (e.type === "STATE_DELTA" && e.agent && (e as any).grounding) groundingResults.set(e.agent as string, (e as any).grounding.grounded);
  });
  type GateResult = "passed" | "warning" | "awaiting" | "failed";
  const gateResults = new Map<string, { gate: string; status: GateResult }>();
  events.forEach((e) => {
    if (e.type !== "STATE_DELTA" || !e.agent) return;
    const s = (e as any).status;
    if (s === "warning" && (e as any).gate) gateResults.set(e.agent as string, { gate: (e as any).gate, status: "warning" });
    else if (s === "gate_decision_required") gateResults.set(e.agent as string, { gate: (e as any).gate_decision_required?.gate_type || "gate", status: "awaiting" });
    else if (s === "error" && ((e as any).error || "").includes("Gate")) gateResults.set(e.agent as string, { gate: "gate", status: "failed" });
  });

  const dotColor = (status: DagStatus) =>
    status === "running" ? "bg-blue-500 animate-pulse" :
    status === "complete" ? "bg-green-500" :
    status === "failed" ? "bg-red-500 animate-pulse" :
    status === "skipped" ? "bg-gray-700" : "bg-gray-600";

  if (agents.length === 0) return <div className="text-gray-500 text-xs p-4">No agents</div>;

  // ── Layout: layers as ROWS (top→bottom), agents spread horizontally per row, centered ──
  const maxCols = Math.max(1, ...layers.map((l) => l.length));
  const pos = new Map<string, { x: number; y: number }>();
  layers.forEach((layer, li) => {
    const startX = PAD + (maxCols - layer.length) * H_GAP / 2;
    layer.forEach((n, ni) => pos.set(n.id, { x: startX + ni * H_GAP, y: PAD + li * V_GAP }));
  });
  const width = PAD * 2 + maxCols * H_GAP - (H_GAP - NODE_W);
  const height = PAD * 2 + (layers.length - 1) * V_GAP + NODE_H;

  // Edges: node ← its deps (real dependency graph, so parallel branches + skips look non-linear).
  // Platform nodes (empty deps) chain from the whole previous layer.
  type Edge = { x1: number; y1: number; x2: number; y2: number; s: DagStatus; t: DagStatus };
  // Edges connect ONLY adjacent rendered layers, so flow always passes THROUGH a
  // checkpoint/gate instead of bypassing it. Within a step we still use the real
  // depends_on (so parallel fan-in / fan-out looks non-linear); when a node's deps
  // aren't in the immediately-previous layer (a gate was inserted between), it
  // funnels from the whole previous layer.
  const edges: Edge[] = [];
  layers.forEach((layer, li) => {
    if (li === 0) return;
    const prev = layers[li - 1];
    const prevIds = new Set(prev.map((n) => n.id));
    layer.forEach((node) => {
      const tp = pos.get(node.id); if (!tp) return;
      const deps = node.depends_on.filter((d) => prevIds.has(d));
      const sources = deps.length ? deps : prev.map((n) => n.id);
      sources.forEach((sid) => {
        const sp = pos.get(sid); if (!sp) return;
        edges.push({
          x1: sp.x + NODE_W / 2, y1: sp.y + NODE_H, x2: tp.x + NODE_W / 2, y2: tp.y,
          s: statuses.get(sid) || "pending", t: statuses.get(node.id) || "pending",
        });
      });
    });
  });
  const edgeStroke = (e: Edge) =>
    e.s === "failed" || e.t === "failed" ? "#ef4444" :
    e.t === "running" ? "#a78bfa" :
    e.s === "complete" ? "#475569" : "#2b303b";

  // Progress
  const completeCount = allNodes.filter((a) => statuses.get(a.id) === "complete").length;
  const failedCount = allNodes.filter((a) => statuses.get(a.id) === "failed").length;
  const totalCount = allNodes.length;
  let activeLayer = layers.length;
  for (let i = 0; i < layers.length; i++) { if (layers[i].some((a) => statuses.get(a.id) !== "complete")) { activeLayer = i + 1; break; } }
  const showProgress = completeCount > 0 || failedCount > 0 || allNodes.some((a) => statuses.get(a.id) === "running");

  return (
    <div className="h-full flex flex-col p-3">
      <div className="flex items-center justify-between mb-2 shrink-0">
        <h4 className="text-[10px] text-gray-500 uppercase tracking-wider font-bold">Execution Graph</h4>
        {showProgress && (
          <span className="text-[10px] font-mono tabular-nums text-gray-400">
            <span className="text-tessera-accent font-bold">{completeCount}</span>
            <span className="text-gray-600">/{totalCount}</span>
            {failedCount > 0 && <span className="text-red-400 ml-1">· {failedCount}✕</span>}
            {layers.length > 1 && <span className="text-gray-500 ml-1">· L{activeLayer}/{layers.length}</span>}
          </span>
        )}
      </div>

      {/* Scrollable graph canvas */}
      <div className="flex-1 min-h-0 overflow-auto">
        <div className="relative mx-auto" style={{ width, height, minWidth: "100%" }}>
          {/* edges */}
          <svg className="absolute inset-0 pointer-events-none" width={width} height={height}>
            <defs>
              <marker id="dag-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto">
                <path d="M0,0 L7,3.5 L0,7" fill="#475569" />
              </marker>
            </defs>
            {edges.map((e, i) => {
              const my = (e.y1 + e.y2) / 2;
              const active = e.t === "running";
              const stroke = edgeStroke(e);
              return (
                <path key={i} d={`M ${e.x1} ${e.y1} C ${e.x1} ${my}, ${e.x2} ${my}, ${e.x2} ${e.y2}`}
                  fill="none" stroke={stroke} strokeWidth={active ? 2 : 1.4}
                  strokeDasharray={active ? "4 3" : e.s === "failed" || e.t === "failed" ? "5 3" : undefined}
                  markerEnd="url(#dag-arrow)">
                  {active && <animate attributeName="stroke-dashoffset" from="0" to="-14" dur="0.8s" repeatCount="indefinite" />}
                </path>
              );
            })}
          </svg>

          {/* nodes */}
          {layers.map((layer) => layer.map((node) => {
            const p = pos.get(node.id)!;
            const status = statuses.get(node.id) || "pending";
            const t = typeOf(node);
            const meta = TYPE_META[t];
            const isFocused = focusedAgent === node.id;
            const gate = gateResults.get(node.id);
            return (
              <button
                key={node.id}
                onClick={() => onFocusAgent(node.id)}
                title={`${node.name} · ${meta.label} · ${status}${node.desc ? "\n\n" + node.desc : ""}`}
                style={{ left: p.x, top: p.y, width: NODE_W, height: NODE_H }}
                className={`absolute text-left rounded-lg border bg-tessera-card/95 backdrop-blur-sm overflow-hidden transition-all ${
                  isFocused ? "border-white/70 ring-1 ring-white/30 shadow-lg" :
                  status === "failed" ? "border-red-500/60" :
                  status === "running" ? "border-blue-500/50 shadow-md shadow-blue-500/20" :
                  "border-tessera-border hover:border-gray-500"
                }`}
              >
                <span className="absolute left-0 top-0 bottom-0 w-[3px]" style={{ background: meta.bar }} />
                <div className="pl-2.5 pr-2 py-1.5 h-full flex flex-col justify-center">
                  <div className="flex items-center gap-1.5">
                    <span className={`w-2 h-2 rounded-full shrink-0 ${dotColor(status)}`} />
                    <span className={`text-[10.5px] font-semibold truncate ${status === "failed" ? "text-red-200" : status === "running" ? "text-white" : "text-gray-200"}`}>
                      {node.name}
                    </span>
                    <span className="ml-auto flex items-center gap-0.5 shrink-0">
                      {groundingResults.has(node.id) && (
                        <span className={groundingResults.get(node.id) ? "text-green-400 text-[8px]" : "text-yellow-400 text-[8px]"}>{groundingResults.get(node.id) ? "✓" : "⚠"}</span>
                      )}
                      {gate && <span className={`text-[8px] ${gate.status === "awaiting" ? "text-purple-400 animate-pulse" : gate.status === "warning" ? "text-amber-400" : gate.status === "failed" ? "text-red-400" : "text-green-400"}`}>◆</span>}
                    </span>
                  </div>
                  <div className="flex items-center gap-1 mt-0.5">
                    <span className={`text-[7.5px] px-1 rounded ${meta.chipBg} ${meta.chipText} shrink-0`}>{meta.label}</span>
                    <span className="text-[8px] text-gray-500 truncate">{node.desc || node.swarm}</span>
                  </div>
                </div>
              </button>
            );
          }))}
        </div>
      </div>

      {/* legend — status + type */}
      <div className="mt-2 pt-2 border-t border-gray-800 flex flex-wrap gap-x-3 gap-y-1 shrink-0">
        <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-blue-500 animate-pulse" /><span className="text-[7px] text-gray-500">running</span></span>
        <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-green-500" /><span className="text-[7px] text-gray-500">complete</span></span>
        <span className="flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-red-500" /><span className="text-[7px] text-gray-500">failed</span></span>
        <span className="text-gray-700 text-[7px]">·</span>
        {(["research", "synthesis", "compose", "drafter", "challenger", "report"] as AgentType[]).map((t) => (
          <span key={t} className="flex items-center gap-1"><span className="w-2 h-[3px] rounded-full" style={{ background: TYPE_META[t].bar }} /><span className="text-[7px] text-gray-500">{TYPE_META[t].label}</span></span>
        ))}
      </div>
    </div>
  );
}
