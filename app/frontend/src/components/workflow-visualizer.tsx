"use client";

import { AGUIEvent } from "@/lib/ag-ui-types";

interface WorkflowVisualizerProps {
  events: AGUIEvent[];
  config?: any;
}

export function WorkflowVisualizer({ events, config }: WorkflowVisualizerProps) {
  // Derive agents and their deps from config
  const agents: { id: string; name: string; swarm: string; depends_on: string[] }[] =
    (config?.agents || []).map((a: any) => ({
      id: a.id,
      name: a.name || a.id,
      swarm: a.swarm || "",
      depends_on: a.depends_on || [],
    }));

  // Derive status from events
  const statuses = new Map<string, "pending" | "running" | "complete">();
  agents.forEach((a) => statuses.set(a.id, "pending"));
  events.forEach((e) => {
    if (e.type === "STATE_DELTA" && e.agent && (e as any).status) {
      statuses.set(e.agent as string, (e as any).status);
    }
  });

  // Swarm colors
  const swarms = [...new Set(agents.map((a) => a.swarm).filter(Boolean))];
  const swarmColors: Record<string, string> = {};
  const palette = ["red", "blue", "green", "yellow", "purple", "orange"];
  swarms.forEach((s, i) => { swarmColors[s] = palette[i % palette.length]; });

  const statusDot = (status: string, swarm: string) => {
    const color = swarmColors[swarm] || "gray";
    if (status === "running") return `bg-${color}-500 animate-pulse`;
    if (status === "complete") return `bg-${color}-700`;
    return "bg-gray-700";
  };

  const statusBorder = (status: string, swarm: string) => {
    const color = swarmColors[swarm] || "gray";
    if (status === "running") return `border-${color}-500`;
    if (status === "complete") return `border-${color}-600`;
    return "border-gray-700";
  };

  if (agents.length === 0) return null;

  // Group into layers by deps satisfied
  const layers: typeof agents[] = [];
  const placed = new Set<string>();
  let remaining = [...agents];
  while (remaining.length > 0) {
    const layer = remaining.filter((a) => a.depends_on.every((d) => placed.has(d)));
    if (layer.length === 0) { layers.push(remaining); break; }
    layers.push(layer);
    layer.forEach((a) => placed.add(a.id));
    remaining = remaining.filter((a) => !placed.has(a.id));
  }

  return (
    <div className="flex items-center gap-2 overflow-x-auto py-1">
      {layers.map((layer, li) => (
        <div key={li} className="flex items-center gap-1">
          {li > 0 && <span className="text-gray-600 text-[9px] mx-1">→</span>}
          <div className={`flex ${layer.length > 1 ? "flex-col" : ""} gap-0.5`}>
            {layer.map((agent) => {
              const status = statuses.get(agent.id) || "pending";
              return (
                <div key={agent.id} className={`flex items-center gap-1 border rounded px-1.5 py-0.5 ${statusBorder(status, agent.swarm)}`} title={`${agent.name}: ${status}`}>
                  <div className={`w-2 h-2 rounded-full ${statusDot(status, agent.swarm)}`} aria-label={status || "pending"} role="status" />
                  <span className="text-[8px] text-gray-400 whitespace-nowrap">{agent.name.length > 12 ? agent.name.slice(0, 10) + "…" : agent.name}</span>
                </div>
              );
            })}
          </div>
        </div>
      ))}
    </div>
  );
}
