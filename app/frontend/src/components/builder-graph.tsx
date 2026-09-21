"use client";

import { useCallback, useMemo } from "react";
import ReactFlow, { Node, Edge, Position, MarkerType, useNodesState, useEdgesState, addEdge, Connection } from "reactflow";
import "reactflow/dist/style.css";

interface BuilderGraphProps {
  agents: { id: string; name: string; depends_on: string[]; swarm: string }[];
  onUpdateDeps: (agentId: string, depends_on: string[]) => void;
}

export function BuilderGraph({ agents, onUpdateDeps }: BuilderGraphProps) {
  // Defensive normalisation: depends_on must be an array. The suggest LLM occasionally
  // emits scalars or nulls; we coerce here so a malformed agent doesn't crash render.
  const normAgents = useMemo(() => agents.map(a => ({
    ...a,
    depends_on: Array.isArray(a.depends_on) ? a.depends_on : (a.depends_on == null || a.depends_on === "" ? [] : [a.depends_on]),
  })), [agents]);

  // Build layers for initial layout
  const initialLayout = useMemo(() => {
    const layers: typeof normAgents[] = [];
    const placed = new Set<string>();
    let remaining = [...normAgents];
    while (remaining.length > 0) {
      const layer = remaining.filter((a) => a.depends_on.every((d) => placed.has(d)));
      if (layer.length === 0) { layers.push(remaining); break; }
      layers.push(layer);
      layer.forEach((a) => placed.add(a.id));
      remaining = remaining.filter((a) => !placed.has(a.id));
    }
    return layers;
  }, [normAgents]);

  const initialNodes: Node[] = useMemo(() => {
    const nodes: Node[] = [];
    initialLayout.forEach((layer, li) => {
      layer.forEach((agent, ai) => {
        nodes.push({
          id: agent.id,
          data: { label: agent.name || agent.id },
          position: { x: ai * 180 + 50, y: li * 100 + 50 },
          sourcePosition: Position.Bottom,
          targetPosition: Position.Top,
          style: {
            background: "#1a1f35",
            color: "#e2e8f0",
            border: "1px solid #2a2f45",
            borderRadius: "8px",
            padding: "8px 12px",
            fontSize: "11px",
            fontWeight: 600,
          },
        });
      });
    });
    return nodes;
  }, [initialLayout]);

  const initialEdges: Edge[] = useMemo(() => {
    const edges: Edge[] = [];
    normAgents.forEach((agent) => {
      agent.depends_on.forEach((dep) => {
        if (normAgents.some(a => a.id === dep)) {
          edges.push({
            id: `${dep}-${agent.id}`,
            source: dep,
            target: agent.id,
            markerEnd: { type: MarkerType.ArrowClosed, color: "#8b5cf6" },
            style: { stroke: "#8b5cf6", strokeWidth: 1.5 },
            animated: true,
          });
        }
      });
    });
    return edges;
  }, [normAgents]);

  const [nodes, setNodes, onNodesChange] = useNodesState(initialNodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(initialEdges);

  const onConnect = useCallback((connection: Connection) => {
    if (!connection.source || !connection.target) return;
    setEdges((eds) => addEdge({
      ...connection,
      markerEnd: { type: MarkerType.ArrowClosed, color: "#8b5cf6" },
      style: { stroke: "#8b5cf6", strokeWidth: 1.5 },
      animated: true,
    }, eds));
    const target = normAgents.find(a => a.id === connection.target);
    if (target && !target.depends_on.includes(connection.source)) {
      onUpdateDeps(connection.target, [...target.depends_on, connection.source]);
    }
  }, [normAgents, onUpdateDeps, setEdges]);

  const onEdgeDelete = useCallback((deletedEdges: Edge[]) => {
    deletedEdges.forEach(edge => {
      const target = normAgents.find(a => a.id === edge.target);
      if (target) {
        onUpdateDeps(edge.target, target.depends_on.filter(d => d !== edge.source));
      }
    });
  }, [normAgents, onUpdateDeps]);

  if (normAgents.length === 0) return <div className="flex items-center justify-center h-full text-gray-500 text-xs">Add agents to see the graph</div>;

  return (
    <div className="h-full w-full" style={{ background: "#0a0e1a" }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        onNodesChange={onNodesChange}
        onEdgesChange={onEdgesChange}
        onConnect={onConnect}
        onEdgesDelete={onEdgeDelete}
        fitView
        proOptions={{ hideAttribution: true }}
        style={{ background: "#0a0e1a" }}
      />
      <div className="absolute bottom-2 left-2 text-[8px] text-gray-600">Drag to move • Connect nodes to set dependencies • Delete edges to remove</div>
    </div>
  );
}
