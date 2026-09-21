"use client";

import { AGUIEvent } from "@/lib/ag-ui-types";

interface SharedStatePanelProps {
  state: Record<string, unknown>;
  events: AGUIEvent[];
}

interface AgentState {
  agent: string;
  status: string;
  swarm?: string;
}

export function SharedStatePanel({ state, events }: SharedStatePanelProps) {
  // Derive agent states from STATE_DELTA events
  const agentStates = new Map<string, AgentState>();
  events
    .filter((e) => e.type === "STATE_DELTA" && e.agent)
    .forEach((e) => {
      agentStates.set(e.agent as string, {
        agent: e.agent as string,
        status: (e as any).status || "unknown",
        swarm: e.swarm as string,
      });
    });

  // Count stats
  const redSuccess = events.filter((e) => e.type === "TOOL_CALL_END" && e.swarm === "red" && (e as any).result?.success).length;
  const redAttempts = events.filter((e) => e.type === "TOOL_CALL_END" && e.swarm === "red").length;
  const blueActions = events.filter((e) => e.type === "TOOL_CALL_END" && e.swarm === "blue").length;

  const productionAgents = [
    { name: "Customer Bot", status: state.customer_bot_status as string || "nominal" },
    { name: "Data Pipeline", status: state.data_pipeline_status as string || "nominal" },
    { name: "Code Review", status: state.code_review_status as string || "nominal" },
  ];

  const statusIcon = (status: string) => {
    if (status === "compromised") return "🔴";
    if (status === "hardened") return "🟢";
    if (status === "isolated") return "🟡";
    return "⚪";
  };

  return (
    <div>
      <h3 className="text-xs font-semibold text-gray-500 uppercase mb-4">Battlefield</h3>

      {/* Production Agents */}
      <div className="mb-4">
        <div className="text-xs text-gray-400 mb-2">Production Agents</div>
        <div className="space-y-2">
          {productionAgents.map((agent) => (
            <div key={agent.name} className="flex items-center justify-between text-sm">
              <span>{statusIcon(agent.status)} {agent.name}</span>
              <span className="text-xs text-gray-500">{agent.status}</span>
            </div>
          ))}
        </div>
      </div>

      {/* Stats */}
      <div className="space-y-3 border-t border-battlefield-border pt-3">
        <div>
          <div className="text-xs text-gray-400">Red Success Rate</div>
          <div className="text-lg font-bold text-red-400">
            {redAttempts > 0 ? Math.round((redSuccess / redAttempts) * 100) : 0}%
          </div>
        </div>
        <div>
          <div className="text-xs text-gray-400">Blue Actions</div>
          <div className="text-lg font-bold text-blue-400">{blueActions}</div>
        </div>
        <div>
          <div className="text-xs text-gray-400">Defense Version</div>
          <div className="text-lg font-bold text-green-400">v{(state.defense_version as number) || 1}</div>
        </div>
      </div>

      {/* Active Agents */}
      <div className="border-t border-battlefield-border pt-3 mt-3">
        <div className="text-xs text-gray-400 mb-2">Active Agents</div>
        <div className="space-y-1">
          {Array.from(agentStates.values()).map((a) => (
            <div key={a.agent} className="flex items-center gap-2 text-xs">
              <span className={`w-2 h-2 rounded-full ${a.status === "running" ? "bg-green-500 animate-pulse" : "bg-gray-600"}`} aria-label={a.status === "running" ? "Running" : "Idle"} role="status" />
              <span className={a.swarm === "red" ? "text-red-300" : "text-blue-300"}>{a.agent}</span>
              <span className="text-gray-600 ml-auto">{a.status}</span>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
