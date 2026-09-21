// Platform-agent display helper.
//
// The compiler (save-ecosystem) injects platform agents at deploy time — the
// final Report, the Challenger (or per-level checkpoints). They are NOT in the
// saved YAML and are no longer injected by the get-ecosystem API. The DAG
// (dag-map) mirrors them for rendering on its own. This helper exists for the
// OTHER run-view consumers that list agents from the config — the per-agent
// summaries, the "currently running" activity strip, and the focused-agent
// lookup — so the Challenger and Report still appear there.
//
// IMPORTANT: do NOT pass the result of this into <DagMap/> — DagMap injects the
// platform nodes itself from the raw config, so feeding it an already-expanded
// list would render duplicate Report/Challenger nodes (the original bug).
//
// This intentionally mirrors the exact set the get-ecosystem Lambda used to add
// (Challenger when enabled + Report), so run-view behaviour is unchanged.

export interface DisplayAgent {
  id: string;
  name: string;
  swarm: string;
  depends_on: string[];
  tools?: unknown[];
  prompt?: string;
  [k: string]: unknown;
}

export function expandWithPlatformAgents(config: any): DisplayAgent[] {
  const agents: DisplayAgent[] = [...((config?.agents as DisplayAgent[]) || [])];
  if (!config) return agents;

  // report defaults to true (matches the compiler's config.get("report", True)).
  const reportEnabled = config.report !== false;
  if (!reportEnabled) return agents;

  // Defensive: if a caller already passed an expanded list, don't double up.
  if (agents.some((a) => a.id === "report_agent")) return agents;

  const dependedOn = new Set<string>();
  agents.forEach((a) => (a.depends_on || []).forEach((d) => dependedOn.add(d)));
  let leafIds = agents.map((a) => a.id).filter((id) => !dependedOn.has(id));
  if (leafIds.length === 0 && agents.length > 0) leafIds = [agents[agents.length - 1].id];

  // challenger defaults to true (matches quality.get("challenger", True)); any
  // truthy value (incl. "per_level"/"final") enables it.
  const quality = config.quality || {};
  const challengerEnabled = quality.challenger === undefined ? true : !!quality.challenger;

  if (challengerEnabled) {
    agents.push({ id: "challenger_agent", name: "Challenger", swarm: "platform", depends_on: leafIds, tools: [], prompt: "Platform challenger agent" });
    agents.push({ id: "report_agent", name: "Report", swarm: "platform", depends_on: [...leafIds, "challenger_agent"], tools: [], prompt: "Platform report agent" });
  } else {
    agents.push({ id: "report_agent", name: "Report", swarm: "platform", depends_on: leafIds, tools: [], prompt: "Platform report agent" });
  }
  return agents;
}
