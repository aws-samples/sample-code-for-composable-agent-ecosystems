// ScorecardPanel — the Ecosystem Evaluation Harness demo surface
// (docs/spec-ecosystem-evaluation.md §9.1). Renders a scorecard artifact:
//   - overall gate badge (block/warn/off + allow/refuse)
//   - each metric as MEDIAN ± BAND vs threshold (noise-aware, §6.2)
//   - per-case pass/fail grid + per-issue miss-rate (primary signal, §6.2)
//   - judge explanations (MRM evidence, §6.1)
//   - lineage {version, config_hash, golden_set_version, judge_model, timestamp}
//
// Eco-AGNOSTIC: the component renders whatever generic metrics/cases the
// scorecard carries; it hard-codes no domain vocabulary. Pure presentational —
// feed it a `scorecard` (and optional `gate`) object.

"use client";

import { useState } from "react";

export interface AggregatedMetric {
  metric_id: string;
  scorer: string;
  median: number;
  band: number;
  empirical_band?: number;
  n?: number;
  miss_rate?: number;
  threshold?: number | null;
  passes?: boolean;
  display?: string;
}

export interface Scorecard {
  lineage?: {
    ecosystem_id?: string;
    ecosystem_version?: string;
    config_hash?: string;
    golden_set_version?: string;
    judge_model?: string | null;
    timestamp?: string;
  };
  gate?: string;
  metrics?: AggregatedMetric[];
  per_case?: Record<string, Record<string, Record<string, any>>>;
  case_miss_rate?: Record<string, { missed: number; runs: number; miss_rate: number; display: string }>;
  judge_explanations?: Record<string, string>;
  trajectory?: Record<string, any>;
  notes?: string[];
}

export interface GateOutcome {
  gate: string;
  allow_deploy: boolean;
  failures?: Array<Record<string, any>>;
  regressions?: Array<Record<string, any>>;
  summary?: string;
}

function pct(v: number): string {
  return `${(v * 100).toFixed(1)}%`;
}

function metricPasses(m: AggregatedMetric): boolean | null {
  if (m.passes !== undefined) return m.passes;
  if (m.threshold == null) return null;
  return m.median + m.band >= m.threshold;
}

export function ScorecardPanel({ scorecard, gate }: { scorecard: Scorecard | null; gate?: GateOutcome | null }) {
  const [expanded, setExpanded] = useState(true);
  if (!scorecard) return null;

  const lin = scorecard.lineage || {};
  const metrics = scorecard.metrics || [];
  const missRate = scorecard.case_miss_rate || {};
  const g = gate || null;

  const gateColor = g
    ? g.allow_deploy
      ? "#065f46"
      : "#b91c1c"
    : "#334155";
  const gateLabel = g
    ? `${(g.gate || "").toUpperCase()} — ${g.allow_deploy ? "DEPLOY ALLOWED" : "DEPLOY REFUSED"}`
    : `GATE: ${(scorecard.gate || "").toUpperCase()}`;

  return (
    <div style={{ border: "1px solid #e5e7eb", borderRadius: 8, background: "#fff", margin: "12px 0", overflow: "hidden" }}>
      <div
        onClick={() => setExpanded((e) => !e)}
        style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "10px 14px", background: "#0b2545", color: "#fff", cursor: "pointer" }}
      >
        <strong style={{ fontSize: 14 }}>Evaluation Scorecard</strong>
        <span style={{ display: "flex", gap: 10, alignItems: "center" }}>
          <span style={{ background: gateColor, color: "#fff", borderRadius: 4, padding: "2px 8px", fontSize: 11, fontWeight: 700 }}>{gateLabel}</span>
          <span style={{ fontSize: 12, opacity: 0.7 }}>{expanded ? "▾" : "▸"}</span>
        </span>
      </div>

      {expanded && (
        <div style={{ padding: 14 }}>
          {g?.summary && (
            <p style={{ margin: "0 0 10px", fontSize: 13, color: g.allow_deploy ? "#065f46" : "#b91c1c" }}>{g.summary}</p>
          )}

          {/* Metrics: median ± band vs threshold (noise-aware) */}
          <table style={{ width: "100%", borderCollapse: "collapse", fontSize: 13 }}>
            <thead>
              <tr style={{ textAlign: "left", borderBottom: "2px solid #cbd5e0" }}>
                <th style={{ padding: "6px 8px" }}>Metric</th>
                <th style={{ padding: "6px 8px" }}>Scorer</th>
                <th style={{ padding: "6px 8px" }}>Median ± band</th>
                <th style={{ padding: "6px 8px" }}>Threshold</th>
                <th style={{ padding: "6px 8px" }}>Status</th>
              </tr>
            </thead>
            <tbody>
              {metrics.map((m) => {
                const p = metricPasses(m);
                return (
                  <tr key={m.metric_id} style={{ borderBottom: "1px solid #eef2f7" }}>
                    <td style={{ padding: "6px 8px", fontFamily: "monospace" }}>{m.metric_id}</td>
                    <td style={{ padding: "6px 8px", color: "#64748b" }}>{m.scorer}</td>
                    <td style={{ padding: "6px 8px" }}>{m.display || `${m.median.toFixed(3)} ±${m.band.toFixed(3)}`}</td>
                    <td style={{ padding: "6px 8px" }}>{m.threshold == null ? "—" : m.threshold}</td>
                    <td style={{ padding: "6px 8px" }}>
                      {p == null ? (
                        <span style={{ color: "#64748b" }}>info</span>
                      ) : p ? (
                        <span style={{ color: "#065f46", fontWeight: 700 }}>PASS</span>
                      ) : (
                        <span style={{ color: "#b91c1c", fontWeight: 700 }}>FAIL</span>
                      )}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>

          {/* Per-issue miss-rate — the primary, most stable regression signal */}
          {Object.keys(missRate).length > 0 && (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "#334155", marginBottom: 6 }}>
                Per-case miss-rate (times the case failed / replicates)
              </div>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8 }}>
                {Object.entries(missRate).map(([name, r]) => (
                  <span
                    key={name}
                    title={`${name}: missed ${r.display}`}
                    style={{
                      fontSize: 12,
                      padding: "3px 8px",
                      borderRadius: 4,
                      background: r.miss_rate === 0 ? "#d1fae5" : r.miss_rate < 0.5 ? "#fef3c7" : "#fca5a5",
                      color: "#0b2545",
                    }}
                  >
                    {name}: {r.display}
                  </span>
                ))}
              </div>
            </div>
          )}

          {/* Judge explanations (MRM evidence) */}
          {scorecard.judge_explanations && Object.keys(scorecard.judge_explanations).length > 0 && (
            <div style={{ marginTop: 14 }}>
              <div style={{ fontSize: 12, fontWeight: 700, color: "#334155", marginBottom: 6 }}>Judge explanations</div>
              {Object.entries(scorecard.judge_explanations).map(([mid, ex]) => (
                <div key={mid} style={{ fontSize: 12, marginBottom: 4 }}>
                  <span style={{ fontFamily: "monospace", color: "#64748b" }}>{mid}: </span>
                  {ex}
                </div>
              ))}
            </div>
          )}

          {/* Trajectory panel */}
          {scorecard.trajectory && Object.keys(scorecard.trajectory).length > 0 && (
            <div style={{ marginTop: 14, fontSize: 12, color: "#334155" }}>
              <span style={{ fontWeight: 700 }}>Trajectory: </span>
              {scorecard.trajectory.tool_calls_total != null && (
                <span>{scorecard.trajectory.tool_calls_total} tool calls; </span>
              )}
              {Array.isArray(scorecard.trajectory.path_validity_scores) && (
                <span>path-validity samples: {scorecard.trajectory.path_validity_scores.length}</span>
              )}
            </div>
          )}

          {/* Lineage (provenance / MRM sign-off) */}
          <div style={{ marginTop: 14, paddingTop: 10, borderTop: "1px dashed #cbd5e0", fontSize: 11, color: "#64748b" }}>
            <span>version: {lin.ecosystem_version || "—"}</span>
            {"  ·  "}
            <span>config: {(lin.config_hash || "").slice(0, 10) || "—"}</span>
            {"  ·  "}
            <span>golden set: {lin.golden_set_version || "—"}</span>
            {"  ·  "}
            <span>judge: {lin.judge_model || "—"}</span>
            {"  ·  "}
            <span>{lin.timestamp || ""}</span>
          </div>
        </div>
      )}
    </div>
  );
}
