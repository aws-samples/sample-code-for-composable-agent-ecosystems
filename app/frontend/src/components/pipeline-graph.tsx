"use client";

import { useMemo } from "react";
import ReactFlow, { Node, Edge, Position, MarkerType, Handle, Background, Controls } from "reactflow";
import "reactflow/dist/style.css";

// A pipeline node. `kind` drives the detail drawer; `meta` carries descriptor data.
export type PNode = {
  id: string; title: string; sub?: string; icon?: string;
  count?: number | null; color: string; planned?: boolean;
  kind: "source" | "machinery" | "entity"; meta?: any;
  status?: string; delta?: string;   // live refresh status: pending|running|done|skipped|aborted|failed
};
export type PEdge = { from: string; to: string };

const ROW = 150;   // vertical spacing between layers (top-down)
const COL = 240;   // horizontal spacing within a layer

function CardNode({ data }: any) {
  const c: string = data.planned ? "#64748b" : data.color;
  const sel: boolean = !!data.__selected;
  const st: string = data.status || "";
  const stColor = st === "running" ? "#f59e0b" : (st === "done" ? "#22c55e" : (st === "aborted" || st === "failed") ? "#ef4444" : st === "pending" ? "#64748b" : "");
  const border = stColor || `${c}${sel ? "" : "66"}`;
  return (
    <div style={{
      position: "relative", minWidth: 168, maxWidth: 208,
      background: data.planned ? "linear-gradient(160deg,#1a2233,#121826)" : "linear-gradient(160deg,#1f2a3d,#131b2b)",
      border: `${sel || stColor ? 2 : 1}px ${data.planned ? "dashed" : "solid"} ${border}`,
      borderRadius: ".7rem", padding: ".68rem .78rem", cursor: "pointer",
      boxShadow: st === "running" ? `0 0 0 3px #f59e0b33, 0 8px 22px rgba(0,0,0,.55)` : sel ? `0 0 0 3px ${c}33, 0 8px 22px rgba(0,0,0,.55)` : "0 5px 15px rgba(0,0,0,.4)",
      animation: st === "running" ? "pipepulse 1.1s ease-in-out infinite" : undefined,
    }}>
      <style>{`@keyframes pipepulse{0%,100%{box-shadow:0 0 0 2px #f59e0b22,0 8px 22px rgba(0,0,0,.5)}50%{box-shadow:0 0 0 5px #f59e0b44,0 8px 22px rgba(0,0,0,.5)}}`}</style>
      <Handle type="target" position={Position.Top} style={{ opacity: 0, top: -2 }} />
      <div style={{ display: "flex", alignItems: "center", gap: ".45rem" }}>
        <span style={{ fontSize: ".95rem", lineHeight: 1 }}>{data.icon}</span>
        <span style={{ fontSize: ".58rem", fontWeight: 800, letterSpacing: ".05em", textTransform: "uppercase", color: c }}>{data.sub}</span>
        {stColor && <span style={{ marginLeft: "auto", width: 8, height: 8, borderRadius: 99, background: stColor, boxShadow: `0 0 7px ${stColor}` }} />}
      </div>
      <div style={{ fontSize: ".9rem", fontWeight: 700, color: "#e2e8f0", marginTop: ".28rem", wordBreak: "break-word", lineHeight: 1.15 }}>{data.title}</div>
      {data.planned && <span style={{ display: "inline-block", marginTop: ".4rem", fontSize: ".6rem", fontWeight: 700, color: "#f59e0b", background: "#f59e0b1a", border: "1px solid #f59e0b55", borderRadius: 9999, padding: ".05rem .5rem" }}>planned</span>}
      {!data.planned && data.count != null && (
        <div style={{ display: "flex", alignItems: "center", gap: ".35rem", marginTop: ".45rem" }}>
          <span style={{ width: 7, height: 7, borderRadius: 99, background: c, boxShadow: `0 0 8px ${c}` }} />
          <b style={{ color: "#e2e8f0", fontSize: ".82rem" }}>{Number(data.count).toLocaleString()}</b>
          <span style={{ fontSize: ".62rem", color: "#64748b" }}>records</span>
        </div>
      )}
      {data.delta && <div style={{ marginTop: ".35rem", fontSize: ".62rem", fontWeight: 600, color: stColor || "#94a3b8" }}>{data.delta}</div>}
      <Handle type="source" position={Position.Bottom} style={{ opacity: 0, bottom: -2 }} />
    </div>
  );
}

const nodeTypes = { card: CardNode };

export function PipelineGraph({ layers, edges, height = 340, onSelect, selectedId }: {
  layers: PNode[][]; edges: PEdge[]; height?: number;
  onSelect?: (n: PNode | null) => void; selectedId?: string;
}) {
  const flat = useMemo(() => layers.flat(), [layers]);
  const nodes: Node[] = useMemo(() => {
    const out: Node[] = [];
    layers.forEach((layer, li) => {
      const span = (layer.length - 1) * COL;
      layer.forEach((n, ai) => {
        out.push({
          id: n.id, type: "card",
          data: { ...n, __selected: n.id === selectedId },
          position: { x: ai * COL - span / 2, y: li * ROW },
          sourcePosition: Position.Bottom, targetPosition: Position.Top,
          draggable: false,
        });
      });
    });
    return out;
  }, [layers, selectedId]);

  const rfEdges: Edge[] = useMemo(() => edges.map((e, i) => ({
    id: `${e.from}->${e.to}-${i}`, source: e.from, target: e.to, animated: true,
    markerEnd: { type: MarkerType.ArrowClosed, color: "#64748b", width: 16, height: 16 },
    style: { stroke: "#64748b", strokeWidth: 1.6 },
  })), [edges]);

  return (
    <div style={{ height, width: "100%" }}>
      <ReactFlow
        nodes={nodes} edges={rfEdges} nodeTypes={nodeTypes}
        fitView fitViewOptions={{ padding: 0.15 }}
        minZoom={0.2} maxZoom={1.5}
        proOptions={{ hideAttribution: true }}
        nodesDraggable={false} nodesConnectable={false} elementsSelectable
        onNodeClick={(_, n) => onSelect?.(flat.find((x) => x.id === n.id) || null)}
        onPaneClick={() => onSelect?.(null)}
        style={{ background: "transparent" }}
      >
        <Background color="#1e293b" gap={22} size={1} />
        <Controls showInteractive={false} position="bottom-right" />
      </ReactFlow>
    </div>
  );
}
