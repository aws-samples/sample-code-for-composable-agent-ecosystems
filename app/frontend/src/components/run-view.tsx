"use client";

// RunView — the technical run surface (agent activity strip + focused-agent
// output + execution DAG + outcome + run chat). Extracted VERBATIM from
// app/page.tsx so it can be reused by both the platform page and the
// single-ecosystem /solo export route.
//
// Behaviour is identical to the in-page version. The only additions are:
//   - self-contained API_URL + apiFetch (previously module-level in page.tsx),
//     resolved at RUNTIME via getApiUrl() so the exported /solo build talks to
//     the eco's own endpoints (window.__TESSERA_SOLO__), not a build-time URL.
//   - an optional `showPlatformPanels` prop (default TRUE) that gates the
//     platform-only telemetry/optimizer panels + the redrive banner. The /solo
//     export passes it FALSE so those panels (and their non-exported backend
//     routes) never load there. The optimizer + tuning panels are lazy-loaded
//     via next/dynamic so they are code-split OUT of any bundle that never
//     renders them (e.g. /solo).

import { useState, useEffect } from "react";
import dynamic from "next/dynamic";
import { AGUIEvent } from "@/lib/ag-ui-types";
import type { RunStatus, ConnectionState } from "@/hooks/use-ag-ui";
import { expandWithPlatformAgents } from "@/lib/platform-agents";
import { DagCatalogPanel } from "@/components/dag-catalog-panel";
import { ResizableSplit } from "@/components/resizable-split";
import { A2UIRenderer } from "@/components/a2ui-renderer";
import { TesseraSpinner } from "@/components/tessera-spinner";
import { OutcomePanel } from "@/components/outcome-panel";
import { RunChat } from "@/components/run-chat";
import { ScorecardPanel, type Scorecard, type GateOutcome } from "@/components/scorecard-panel";
import { getApiUrl } from "@/lib/runtime-config";

// Platform-only panels — lazy so they code-split out of routes that never
// render them (the single-ecosystem /solo export sets showPlatformPanels=false).
const RunTuningPanel = dynamic(
  () => import("@/components/run-tuning-panel").then((m) => m.RunTuningPanel),
  { ssr: false },
);
const OptimizerApplyPanel = dynamic(
  () => import("@/components/optimizer-apply-panel").then((m) => m.OptimizerApplyPanel),
  { ssr: false },
);

// Authenticated fetch — adds Authorization header from Amplify session.
async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const { fetchAuthSession } = await import("aws-amplify/auth");
  let token = "";
  try {
    const session = await fetchAuthSession({ forceRefresh: false });
    token = session.tokens?.idToken?.toString() || "";
  } catch { /* proceed without token */ }
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(url, { ...init, headers });
}

export function RunView({ ecosystem, events, sharedState, isRunning, runStatus, connectionState, inFlightPhase, sessionId: sessionIdProp, outcome, showPlatformPanels = true }: { ecosystem: string | null; events: AGUIEvent[]; sharedState: Record<string, unknown>; isRunning: boolean; runStatus: RunStatus; connectionState: ConnectionState; inFlightPhase: { grounding: string[]; external: string[] }; sessionId: string | null; outcome: any | null; showPlatformPanels?: boolean }) {
  const API_URL = getApiUrl();
  const [ecoConfig, setEcoConfig] = useState<any>(null);
  const [focusedAgent, setFocusedAgent] = useState<string | null>(null);
  // Auto-follow the streaming agent by default; a manual selection pins the view
  // (stops the focus from being hijacked mid-run). Resumed via the "Follow live" toggle.
  const [followLive, setFollowLive] = useState(true);

  const sessionId = sessionIdProp || (events.find(e => e.type === "RUN_STARTED") as any)?.session_id as string || "";

  useEffect(() => {
    if (!ecosystem) return;
    let cancelled = false;
    // Load the DAG topology, retrying a few times. Under several concurrent runs the first
    // fetch can fail/time out; without a retry the DAG stays empty ("No agents") forever.
    const load = async (attempt = 0) => {
      try {
        const r = await apiFetch(`${API_URL}/api/ecosystems/${ecosystem}`);
        const cfg = await r.json();
        if (!cancelled && cfg && !cfg.error) { setEcoConfig(cfg); return; }
        throw new Error("empty/err config");
      } catch {
        if (!cancelled && attempt < 4) setTimeout(() => load(attempt + 1), 1500 * (attempt + 1));
      }
    };
    load();
    return () => { cancelled = true; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [ecosystem]);

  // Evaluation scorecard (docs/spec-ecosystem-evaluation.md §9.1). Best-effort:
  // fetch the latest scorecard for this eco; if the harness has never run (or the
  // route is absent), the panel simply does not render. Never blocks the run UI.
  const [scorecard, setScorecard] = useState<Scorecard | null>(null);
  const [scoreGate, setScoreGate] = useState<GateOutcome | null>(null);
  useEffect(() => {
    if (!ecosystem || !showPlatformPanels) return;
    apiFetch(`${API_URL}/api/ecosystems/${ecosystem}?artifact=scorecard`)
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (!d) return;
        setScorecard(d.scorecard || d);
        setScoreGate(d.gate || null);
      })
      .catch(() => {});
  }, [ecosystem, showPlatformPanels]);

  // Auto-focus currently running agent — only while following live (a manual
  // selection sets followLive=false so the view stays put for reading).
  useEffect(() => {
    if (!followLive) return;
    const running = events.filter(e => e.type === "STATE_DELTA" && (e as any).status === "running").map(e => e.agent as string);
    if (running.length > 0) setFocusedAgent(running[running.length - 1]);
  }, [events, followLive]);

  // Progress + currently-running derivation. Drives the prominent header
  // indicators: "X of Y agents · Layer K" and "▶ running: <agent_id>".
  // Computed from the latest STATE_DELTA per agent so it reflects real-time
  // state from either WS events or polling-synthesized events.
  const latestAgentStatus = (() => {
    const m = new Map<string, string>();
    for (const e of events) {
      if (e.type === "STATE_DELTA" && (e as any).agent && (e as any).status) {
        m.set((e as any).agent, (e as any).status);
      }
    }
    return m;
  })();

  // Run-view agent list. ecoConfig from get-ecosystem is now RAW (user agents
  // only); expand with the platform Challenger/Report for the summaries +
  // activity strip + focused-agent lookup. (DagMap is fed the raw ecoConfig and
  // injects platform nodes itself — see expandWithPlatformAgents docs.)
  const agents: any[] = expandWithPlatformAgents(ecoConfig);
  const focusedEvents = focusedAgent ? events.filter(e => e.agent === focusedAgent) : [];
  const focusedConfig = agents.find(a => a.id === focusedAgent);

  const agentSummaries = agents.map(a => {
    const agentEvents = events.filter(e => e.agent === a.id && e.type === "TEXT_MESSAGE_CONTENT");
    // Accumulate streaming deltas (AG-UI native) — fall back to legacy `.content` if present
    const lastContent = agentEvents
      .map(e => ((e as any).delta ?? (e as any).content ?? ""))
      .join("");
    const status = events.filter(e => e.type === "STATE_DELTA" && e.agent === a.id && (e as any).status).map(e => (e as any).status).pop() || "pending";
    return { ...a, lastContent, status };
  });

  // Manual selection pins the view (stops auto-follow hijacking it mid-run).
  const pickAgent = (id: string | null) => { setFocusedAgent(id); setFollowLive(false); };

  return (
    <div className="flex-1 min-h-0 flex flex-col">
      {/* Header */}
      <div className="flex items-center justify-between mb-3 bg-tessera-card glow-border rounded-xl px-5 py-2.5">
        <div className="flex items-center gap-3 min-w-0">
          <h2 className="text-base font-bold shrink-0">{ecoConfig?.ecosystem?.name || ecosystem}</h2>
          <span className="text-xs text-gray-500 truncate hidden xl:inline">{ecoConfig?.ecosystem?.description || ""}</span>
        </div>
        <div className="flex items-center gap-3">
          {runStatus === "running" && (
            <div className="flex items-center gap-2 bg-green-500/15 border border-green-500/40 px-4 py-1.5 rounded-full shadow-lg shadow-green-500/10">
              <span className="w-2.5 h-2.5 rounded-full bg-green-400 blink" />
              <span className="text-sm text-green-300 font-bold tracking-wide">LIVE</span>
            </div>
          )}
          {runStatus === "succeeded" && (
            <span className="text-sm bg-tessera-accent/15 text-tessera-accent px-4 py-1.5 rounded-full border border-tessera-accent/40 font-bold tracking-wide">SUCCEEDED</span>
          )}
          {runStatus === "failed" && (
            <span className="text-sm bg-red-500/20 text-red-300 px-4 py-1.5 rounded-full border-2 border-red-500/60 font-bold tracking-wide animate-pulse">FAILED</span>
          )}
          {/* Connection state — distinguishes live event stream vs polling fallback vs offline.
              Critical UX signal during long runs: when API Gateway WS idle-times out at 10 min,
              keepalive holds it open OR polling takes over — without this pill, users couldn't
              tell which mode they were in and assumed the run was stuck. */}
          {(runStatus === "running" || runStatus === "idle") && connectionState !== "live" && (
            <span
              title={
                connectionState === "polling"
                  ? "WebSocket dropped; SFN status polling active (5s refresh). Run continues normally; UI will update on each tick."
                  : "Disconnected — no live channel and no active polling"
              }
              className={`text-[9px] px-1.5 py-0.5 rounded font-mono lowercase tracking-tight ${
                connectionState === "polling"
                  ? "bg-amber-500/8 text-amber-400/70"
                  : "bg-gray-700/30 text-gray-500"
              }`}
            >
              {connectionState === "polling" ? "◌ polling" : "○ offline"}
            </span>
          )}
        </div>
      </div>

      {/* spec-run-tuning: per-run cost/latency/model telemetry + quality rating.
          Shows once a run is selected; the panel self-hides until the manifest
          has metrics. The rating feeds the cross-run optimizer.
          Platform-only — hidden on single-ecosystem exports (showPlatformPanels). */}
      {showPlatformPanels && ecosystem && sessionId && (
        <RunTuningPanel ecosystemId={ecosystem} execName={sessionId} />
      )}

      {/* Phase 5d: optimizer apply-patch panel. Renders only when the
          active ecosystem is platform-optimizer AND the run has completed.
          Reads the optimizer→target mapping from localStorage to pre-fill
          the target_eco_id. Platform-only (showPlatformPanels). */}
      {showPlatformPanels && ecosystem === "platform-optimizer" && (
        <OptimizerApplyPanel
          optimizerSessionId={sessionId || null}
          defaultTargetEcoId={(() => {
            try {
              if (typeof window === "undefined") return undefined;
              const raw = window.localStorage.getItem("tessera.optimizerTargets") || "{}";
              const map = JSON.parse(raw);
              return (sessionId && map[sessionId]) || undefined;
            } catch {
              return undefined;
            }
          })()}
          isComplete={runStatus === "succeeded"}
        />
      )}

      {/* Phase 5f.4: Outcome panel — renders for any ecosystem with declared
          artifacts. The panel itself returns null when status === "none" or
          declared_artifacts is empty, so it doesn't add visual noise to
          ecosystems that didn't opt into the outcome model. */}
      {showPlatformPanels && runStatus === "failed" && sessionId && ecosystem && (
        <div className="mb-3 bg-red-500/10 border border-red-500/30 rounded-xl px-4 py-2.5 flex items-center justify-between gap-4 shrink-0">
          <div className="flex items-center gap-2 min-w-0">
            <span className="text-red-300 text-base">✕</span>
            <div className="min-w-0">
              <div className="text-sm font-semibold text-red-200">Run failed</div>
              <div className="text-[11px] text-red-300/70 truncate">Retry from the failed state without re-running completed agents.</div>
            </div>
          </div>
          <button
            onClick={async () => {
              try {
                const res = await apiFetch(`${API_URL}/api/ecosystems/${ecosystem}/runs/${sessionId}/redrive`, { method: "POST" });
                if (res.ok) {
                  // The SFN polling will pick up the redriven execution automatically
                  window.location.reload();
                }
              } catch {}
            }}
            className="px-3 py-1.5 text-xs font-semibold rounded bg-red-500/20 text-red-200 border border-red-500/40 hover:bg-red-500/30 transition whitespace-nowrap"
          >
            ↻ Retry from failure
          </button>
        </div>
      )}

      {/* Advisory-warning indicator only. NOTE: we intentionally do NOT show an
          "awaiting decision" count here — that was derived from gate_decision_required
          events, which accumulate in the events array and never clear on resolve, so it
          showed a stale, ever-growing count. The authoritative pending-gate signal is the
          gate dialog (rendered from polled runProps.pendingGates). Warnings are advisory
          checkpoints that proceeded — informational, fine to tally for the run. */}
      {(() => {
        const warnings = events.filter(e => e.type === "STATE_DELTA" && (e as any).status === "warning").length;
        if (warnings === 0) return null;
        return (
          <div className="mb-3 flex items-center gap-3 px-4 py-2 rounded-lg border text-xs bg-amber-500/10 border-amber-500/30">
            <span className="text-amber-300">🛡</span>
            <span className="text-amber-300">{warnings} advisory warning{warnings > 1 ? "s" : ""} this run</span>
          </div>
        );
      })()}

      <OutcomePanel sessionId={sessionId || null} outcome={outcome} running={isRunning} />

      {/* Evaluation scorecard (spec §9.1) — renders only when a scorecard exists. */}
      <ScorecardPanel scorecard={scorecard} gate={scoreGate} />

      {/* Currently-running line — concrete signal of which agent is in flight.
          Especially valuable for long-running synthesizers (e.g. valuation_synthesizer
          at 280s) where the rest of the UI looks idle. The pulsing arrow + agent
          id + click-to-focus action makes it impossible to miss "yes, work is
          happening, here's what". */}
      {/* Activity strip — ALWAYS visible while runStatus=running so the
          screen never looks dead. Shows the most useful "what is happening
          right now" signal in priority order:
            1. Agents currently in flight (single or parallel)
            2. Internal grounding (Bedrock Guardrails) for recently-completed agents
            3. External grounding (web verification) for recently-completed agents
            4. Generic "transitioning between layers" fallback
          With this, even between layers when only grounding tasks are running,
          the user sees concrete activity instead of a frozen screen. */}
      {runStatus === "running" && (() => {
        const runningAgents = agents.filter(a => latestAgentStatus.get(a.id) === "running");
        const grounding = inFlightPhase?.grounding || [];
        const external = inFlightPhase?.external || [];
        // Priority: agents > internal grounding > external grounding > transitioning
        let label: string;
        let items: string[] = [];
        let clickable = false;
        if (runningAgents.length > 0) {
          label = runningAgents.length === 1 ? "Currently running:" : `${runningAgents.length} agents running in parallel:`;
          items = runningAgents.map(a => a.id);
          clickable = true;
        } else if (grounding.length > 0) {
          label = grounding.length === 1
            ? "Verifying findings (internal grounding) for:"
            : `Verifying findings (internal grounding) for ${grounding.length} agents:`;
          items = grounding;
          clickable = true;
        } else if (external.length > 0) {
          label = external.length === 1
            ? "External grounding (web verification) for:"
            : `External grounding (web verification) for ${external.length} agents:`;
          items = external;
          clickable = true;
        } else {
          label = "Transitioning between layers — preparing next stage…";
          items = [];
          clickable = false;
        }
        return (
          <div className="mb-3 flex items-center gap-3 px-5 py-2 bg-green-500/5 border border-green-500/20 rounded-lg">
            <TesseraSpinner size={20} />
            <span className="text-xs text-gray-400 shrink-0">{label}</span>
            <div className="flex gap-2 flex-wrap min-w-0">
              {items.map(id => clickable ? (
                <button
                  key={id}
                  onClick={() => pickAgent(id)}
                  className="text-xs font-mono font-bold text-green-300 hover:text-green-200 underline-offset-2 hover:underline truncate"
                >
                  {id}
                </button>
              ) : (
                <span key={id} className="text-xs font-mono text-green-300/80 truncate">{id}</span>
              ))}
            </div>
          </div>
        );
      })()}

      {/* Main layout — resizable: focused output (left) | execution graph (right, bigger by default) */}
      <ResizableSplit
        className="flex-1"
        storageKey="tessera.runSplit"
        // Equal columns by default (DAG left | details right). User-resizable + persisted.
        defaultRightPct={50}
        right={
          <div className="h-full bg-tessera-card glow-border rounded-xl p-4 overflow-y-auto min-h-0">
            {focusedAgent ? (
              <>
                <div className="flex items-center gap-2 mb-1 sticky top-0 bg-tessera-card pb-2 z-10">
                  <span className={`w-2.5 h-2.5 rounded-full ${
                    agentSummaries.find(a => a.id === focusedAgent)?.status === "running" ? "bg-blue-500 animate-pulse" :
                    agentSummaries.find(a => a.id === focusedAgent)?.status === "complete" ? "bg-green-600" :
                    agentSummaries.find(a => a.id === focusedAgent)?.status === "failed" ? "bg-red-500 animate-pulse" :
                    "bg-gray-600"
                  }`} aria-label={`Agent status: ${agentSummaries.find(a => a.id === focusedAgent)?.status || "pending"}`} />
                  <span className="text-sm font-bold text-white">{focusedConfig?.name || focusedAgent}</span>
                  <span className="text-[10px] text-gray-500 ml-2">{focusedConfig?.swarm || ""}</span>
                  <div className="ml-auto flex items-center gap-2">
                    {runStatus === "running" && (followLive
                      ? <span className="text-[10px] text-green-400 flex items-center gap-1" title="Focus auto-follows the streaming agent"><span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />Following live</span>
                      : <button onClick={() => setFollowLive(true)} title="Resume auto-following the currently streaming agent" className="text-[10px] text-tessera-accent hover:text-white flex items-center gap-1">↪ Follow live</button>
                    )}
                    <button onClick={() => { setFocusedAgent(null); setFollowLive(true); }} className="text-[10px] text-gray-500 hover:text-white">Show All</button>
                  </div>
                </div>
                {(() => {
                  const d = agentBlurb(focusedConfig);
                  return d ? <p className="text-[11px] text-gray-400 mb-3 leading-snug">{d}</p> : null;
                })()}
                <A2UIRenderer events={focusedEvents} />
              </>
            ) : (
              <>
                <div className="flex items-center gap-2 mb-3 sticky top-0 bg-tessera-card pb-2 z-10">
                  <span className="text-xs font-bold text-tessera-accent uppercase tracking-wider">All Agent Output</span>
                  <span className="text-[10px] text-gray-500">· click a node in the graph to focus one agent</span>
                </div>
                <A2UIRenderer events={events} />
              </>
            )}
          </div>
        }
        left={
          <DagCatalogPanel
            className="glow-border"
            dagProps={{ config: ecoConfig, events, focusedAgent, onFocusAgent: pickAgent }}
            domains={ecoConfig?.domains}
          />
        }
      />

      {/* Floating chat widget with events tab */}
      <RunChat sessionId={sessionId} events={events} />
    </div>
  );
}

/** Short, human-readable blurb for an agent — from its description or the first
 *  sentence of its prompt (used in the focused-agent header + DAG nodes). */
function agentBlurb(a: any): string {
  if (!a) return "";
  let s = String(a.description || a.prompt || "").replace(/\s+/g, " ").trim();
  if (!s) return "";
  const m = s.match(/contribution:\s*(.*)/i);
  if (m) s = m[1].trim();
  const c = s.search(/\.\s/);
  if (c > 24 && c < 180) s = s.slice(0, c);
  return s.length > 220 ? s.slice(0, 217) + "…" : s;
}
