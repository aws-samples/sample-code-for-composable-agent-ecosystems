"use client";

import { useEffect, useState } from "react";
import ReactMarkdown from "react-markdown";
import { ToolChip } from "./a2ui-renderer";
import { RunChat } from "./run-chat";
import { OutcomePanel } from "./outcome-panel";
import { RunTuningPanel } from "./run-tuning-panel";
import { GuidanceManager } from "./guidance-manager";
import { A2UISurfaceRenderer } from "./a2ui-surface";
import { DagCatalogPanel } from "./dag-catalog-panel";
import { ResizableSplit } from "./resizable-split";
import { ResumeDialog } from "./resume-dialog";
import { TranscriptModal } from "./transcript-modal";
import { ReplayPlayer } from "./replay-player";
import { TesseraSpinner } from "./tessera-spinner";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  // Authenticate using AWS Amplify session management
  const { fetchAuthSession } = await import("aws-amplify/auth");
  let token = "";
  try {
    const session = await fetchAuthSession({ forceRefresh: false });
    token = session.tokens?.idToken?.toString() || "";
  } catch {}
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(url, { ...init, headers });
}

interface RunSummary {
  name: string;
  status: string;
  startDate: string;
  stopDate: string | null;
  duration_s: number | null;
  executionArn: string;
  displayName?: string;      // operator-set run name (runs-api /meta)
  tags?: string[];           // operator-set tags
}

interface AgentSnapshot {
  agent_id: string;
  structured: { findings?: string; open_questions?: string };
  output_preview: string;
  output_size: number;
  output: string;
  tool_results: any[];
  output_s3_uri: string;
}

interface FailedAgent {
  agent_id: string | null;
  state_name: string | null;
  event_type: string;
  error: string;
  cause: string;
}

interface RunDetail {
  execution: {
    name: string;
    status: string;          // SUCCEEDED | FAILED | TIMED_OUT | ABORTED | RUNNING — ABORTED is an AWS Step Functions API value (not changeable)
    error?: string;
    cause?: string;
    redriveStatus?: string;  // REDRIVABLE | NOT_REDRIVABLE
    redriveCount?: number;
    redriveStatusReason?: string;
    startDate: string;
    stopDate: string | null;
    input?: Record<string, any>;
  };
  agents: AgentSnapshot[];
  failed_agents?: FailedAgent[];
  skipped_agents?: string[];
  outcome?: import("./outcome-panel").Outcome;
  mission_transcript?: { messages: { role: string; content: string }[]; reconstructed: boolean } | null;
}

export function PastRunsModal({
  ecosystemId,
  ecosystemName,
  onClose,
  onRedriveStarted,
  onResumeStarted,
  onOptimizeStarted,
  initialRunName,
}: {
  ecosystemId: string;
  ecosystemName: string;
  onClose: () => void;
  /** When set (e.g. from the concierge "open this run"), the modal opens focused on
   *  this specific run instead of auto-picking the most recent succeeded one. */
  initialRunName?: string;
  /** Called after a successful redrive POST. The execution name is the same
   *  as the original (redrive resumes in-place); the parent should pivot to
   *  the live RunView and start tracking the resumed execution. */
  onRedriveStarted?: (execName: string) => void;
  /** Called after a successful resume POST. Receives the NEW session id (resume
   *  starts a fresh execution); the parent pivots to the live RunView tracking it. */
  onResumeStarted?: (newSessionId: string) => void;
  /** Called after a successful optimizer trigger. Receives the started
   *  optimizer session_id and the target ecosystem id (so the parent
   *  can persist the mapping for the apply-patch panel later). */
  onOptimizeStarted?: (optimizerSessionId: string, targetEcoId: string) => void;
}) {
  const [runs, setRuns] = useState<RunSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string | null>(initialRunName || null);
  const [detail, setDetail] = useState<RunDetail | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const [selectedAgent, setSelectedAgent] = useState<string | null>(null);
  const [redriving, setRedriving] = useState(false);
  const [redriveMsg, setRedriveMsg] = useState<string | null>(null);
  const [optimizing, setOptimizing] = useState(false);
  const [optimizeMsg, setOptimizeMsg] = useState<string | null>(null);
  const [ecoConfig, setEcoConfig] = useState<any>(null);
  const [resumeOpen, setResumeOpen] = useState(false);
  const [showTranscript, setShowTranscript] = useState(false);
  // Run naming / tagging — same runs-api `/meta` the business view uses, so a name set in
  // either place shows up in both. Tags are stored but kept OUT of the dropdown label.
  const [metaEdit, setMetaEdit] = useState(false);
  const [metaName, setMetaName] = useState("");
  const [metaTags, setMetaTags] = useState("");
  const [metaSaving, setMetaSaving] = useState(false);
  const [replayOpen, setReplayOpen] = useState(false);
  const [showGuidance, setShowGuidance] = useState(false);

  // Load ecosystem config (for DAG topology)
  useEffect(() => {
    apiFetch(`${API_URL}/api/ecosystems/${ecosystemId}`).then(r => r.json()).then(setEcoConfig).catch(() => setEcoConfig(null));
  }, [ecosystemId]);

  // Load run list once; auto-pick most recent
  useEffect(() => {
    let cancelled = false;
    setLoading(true);
    apiFetch(`${API_URL}/api/ecosystems/${ecosystemId}/runs`)
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        if (data.error) { setError(String(data.error)); return; }
        setRuns(data.executions || []);
        const list = data.executions || [];
        if (initialRunName && list.some((r: RunSummary) => r.name === initialRunName)) {
          setSelected(initialRunName);
        } else {
          const succ = list.find((r: RunSummary) => r.status === "SUCCEEDED");
          const first = list[0];
          if (succ) setSelected(succ.name);
          else if (first) setSelected(first.name);
        }
      })
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setLoading(false));
    return () => { cancelled = true; };
  }, [ecosystemId]);

  // Load run detail when selected changes
  useEffect(() => {
    if (!selected) { setDetail(null); setSelectedAgent(null); return; }
    let cancelled = false;
    setDetailLoading(true);
    setRedriveMsg(null);
    apiFetch(`${API_URL}/api/ecosystems/${ecosystemId}/runs/${selected}`)
      .then((r) => r.json())
      .then((data) => {
        if (cancelled) return;
        if (data.error) { setError(String(data.error)); return; }
        setDetail(data);
        const report = (data.agents || []).find((a: AgentSnapshot) => isReportAgent(a.agent_id));
        const fallback = (data.agents || [])[(data.agents || []).length - 1];
        // For FAILED runs, focus the failed agent so the user lands on the source of the failure.
        const failedAid = (data.failed_agents && data.failed_agents[0]?.agent_id) || null;
        if (data.execution?.status && isFailedStatus(data.execution.status) && failedAid) {
          setSelectedAgent(failedAid);
        } else {
          setSelectedAgent(report?.agent_id || fallback?.agent_id || null);
        }
      })
      .catch((e) => !cancelled && setError(String(e)))
      .finally(() => !cancelled && setDetailLoading(false));
    return () => { cancelled = true; };
  }, [ecosystemId, selected]);

  // Build DagMap config from ecosystem config (preferred) or fall back to flat list.
  const dagConfig = (() => {
    if (ecoConfig?.agents && Array.isArray(ecoConfig.agents)) return ecoConfig;
    if (!detail) return { agents: [] };
    return {
      agents: detail.agents.map((a) => ({
        id: a.agent_id, name: a.agent_id, swarm: "", depends_on: [],
      })),
    };
  })();

  // Status events for DagMap — accurate per-agent state, not the old "everything complete" fudge.
  // - succeeded agents (in detail.agents): "complete"
  // - failed agents (in failed_agents): "failed"
  // - skipped agents (in skipped_agents): "skipped"
  // - everyone else: "pending" (DagMap default)
  const dagEvents = (() => {
    if (!detail) return [];
    const evs: any[] = [];
    detail.agents.forEach((a) => evs.push({ type: "STATE_DELTA", agent: a.agent_id, status: "complete" }));
    (detail.failed_agents || []).forEach((fa) => {
      if (fa.agent_id) evs.push({ type: "STATE_DELTA", agent: fa.agent_id, status: "failed" });
    });
    (detail.skipped_agents || []).forEach((aid) => {
      evs.push({ type: "STATE_DELTA", agent: aid, status: "skipped" });
    });
    return evs;
  })();

  // Full event stream for the run's Events viewer (RunChat). The runs-api detail already
  // carries each completed agent's full output text, so synthesize the same TEXT_MESSAGE_
  // CONTENT + STATE_DELTA events the live run produces. Without this the viewer was passed
  // an empty array and showed "Waiting for agent output..." forever for a past/rehydrated run.
  const viewerEvents = (() => {
    if (!detail) return [];
    const evs: any[] = [...dagEvents];
    (detail.agents || []).forEach((a) => {
      if (a.agent_id && a.output) {
        evs.push({ type: "TEXT_MESSAGE_CONTENT", agent: a.agent_id, messageId: `${a.agent_id}-out`, delta: a.output });
      }
    });
    return evs;
  })();

  // In-place redrive (native SFN): re-runs the failed task + downstream in the SAME execution.
  const doRedrive = async (): Promise<boolean> => {
    const res = await apiFetch(
      `${API_URL}/api/ecosystems/${ecosystemId}/runs/${selected}/redrive`,
      { method: "POST" },
    );
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      setRedriveMsg("Retry initiated (redrive) — switching to live view…");
      // Hand control back to the parent — it pivots to the live RunView and begins SFN
      // polling so the user sees real progress instead of a stale failure screen.
      if (onRedriveStarted && selected) setTimeout(() => onRedriveStarted(selected), 600);
      return true;
    }
    setRedriveMsg(`Redrive rejected: ${data.error || "unknown reason"} (${data.redriveStatus || data.executionStatus || ""})`);
    return false;
  };

  // Resume fallback: start a NEW run reusing completed upstream, re-running from the
  // failed agent onward. Used when SFN redrive isn't available (old run, redrive limit, etc.).
  const doResumeFromFailure = async (failedAgentId: string): Promise<boolean> => {
    const res = await apiFetch(
      `${API_URL}/api/ecosystems/${ecosystemId}/runs/${selected}/resume`,
      {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ resume_from: failedAgentId, guidance: "" }),
      },
    );
    const data = await res.json().catch(() => ({}));
    if (res.ok && data.session_id) {
      setRedriveMsg(`Retry initiated (resume from ${failedAgentId}) — switching to live view…`);
      if (onResumeStarted) setTimeout(() => onResumeStarted(data.session_id), 700);
      return true;
    }
    setRedriveMsg(`Retry failed: ${data.error || `unable to resume (${res.status})`}`);
    return false;
  };

  // Unified Retry: prefer native redrive (cheap, in-place); if the run isn't redrivable,
  // fall back to resume-from-the-failed-agent (works for old/exhausted runs). Falls back
  // only when we know which agent failed — the resume path needs a target node.
  const handleRetry = async () => {
    if (!selected || !detail) return;
    setRedriving(true);
    setRedriveMsg(null);
    try {
      if (redrivable) {
        const ok = await doRedrive();
        if (ok) return;
        // Redrive was reported redrivable but the POST was rejected — try resume if we can.
      }
      const failedId = (detail.failed_agents || [])[0]?.agent_id;
      if (failedId) {
        await doResumeFromFailure(failedId);
      } else if (!redrivable) {
        setRedriveMsg(
          `This run can't be retried automatically: ${detail.execution.redriveStatusReason || "not redrivable"}, ` +
          `and the failed step couldn't be identified for a resume. Use “Resume from …” in the graph to pick a step.`,
        );
      }
    } catch (e) {
      setRedriveMsg(`Retry request failed: ${e}`);
    } finally {
      setRedriving(false);
    }
  };

  // Phase 5d: trigger the platform-optimizer against THIS ecosystem.
  // Disabled if fewer than 2 prior runs exist (the optimizer Lambda also
  // refuses, but doing it client-side avoids a wasted API round-trip).
  const handleOptimize = async () => {
    setOptimizing(true);
    setOptimizeMsg(null);
    try {
      const res = await apiFetch(`${API_URL}/api/optimize-ecosystem`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target_ecosystem_id: ecosystemId,
          max_runs: 5,
        }),
      });
      const data = await res.json();
      if (res.ok && data.session_id) {
        setOptimizeMsg(
          `Optimizer started — analysing ${data.telemetry?.runs_loaded ?? "?"} runs across ${data.telemetry?.agents_in_target ?? "?"} agents…`
        );
        if (onOptimizeStarted) {
          // Brief delay so the user sees the success message before nav.
          setTimeout(() => onOptimizeStarted(data.session_id, ecosystemId), 700);
        }
      } else {
        setOptimizeMsg(`Optimize rejected: ${data.error || "unknown reason"}${data.detail ? " — " + data.detail : ""}`);
      }
    } catch (e) {
      setOptimizeMsg(`Optimize request failed: ${e}`);
    } finally {
      setOptimizing(false);
    }
  };

  const status = detail?.execution?.status;
  const failed = status ? isFailedStatus(status) : false;
  const redrivable = detail?.execution?.redriveStatus === "REDRIVABLE";
  const failedAgentObj = (detail?.failed_agents || [])[0];
  // Retry is possible when either native redrive is available, or we know which agent
  // failed (so we can resume from it). Otherwise the user must pick a step manually.
  const canRetry = redrivable || !!failedAgentObj?.agent_id;

  const runLabel = (r: RunSummary) =>
    (r.displayName?.trim() || r.name.replace(`${ecosystemId}-`, ""));
  const selectedRun = runs.find((r) => r.name === selected) || null;

  const saveMeta = async () => {
    if (!selected) return;
    setMetaSaving(true);
    const tags = metaTags.split(",").map((t) => t.trim().toLowerCase()).filter(Boolean);
    try {
      const r = await apiFetch(
        `${API_URL}/api/ecosystems/${ecosystemId}/runs/${encodeURIComponent(selected)}/meta`,
        { method: "POST", headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ name: metaName, tags }) });
      const d = await r.json().catch(() => ({}));
      if (d.ok) {
        setRuns((rs) => rs.map((x) => (x.name === selected ? { ...x, displayName: d.name, tags: d.tags } : x)));
        setMetaEdit(false);
      }
    } catch { /* ignore */ }
    finally { setMetaSaving(false); }
  };

  return (
    <div className="h-[calc(100vh-3rem)] flex flex-col">
      {/* Header — sticky */}
      <div className="bg-tessera-card border border-tessera-border rounded-xl p-4 mb-3 flex items-center gap-3 shrink-0">
        <button onClick={onClose} className="text-xs text-gray-400 hover:text-white">← Back to ecosystems</button>
        <div className="h-4 w-px bg-tessera-border" />
        <div className="flex-1">
          <h2 className="text-base font-bold leading-tight">Past runs · {ecosystemName}</h2>
          <p className="text-[10px] text-gray-500">{runs.length} recent execution{runs.length === 1 ? "" : "s"}</p>
        </div>
        <select
          value={selected || ""}
          onChange={(e) => setSelected(e.target.value || null)}
          disabled={loading || runs.length === 0}
          className="bg-gray-900 border border-tessera-border rounded px-3 py-2 text-xs text-white font-mono min-w-[420px]"
        >
          <option value="">{loading ? "Loading…" : runs.length === 0 ? "No past runs yet" : "Select a run…"}</option>
          {runs.map((r) => (
            <option key={r.name} value={r.name}>
              {r.status === "SUCCEEDED" ? "✓" : r.status === "RUNNING" ? "•" : "✕"} {runLabel(r)}
              {"  ·  "}{formatDate(r.startDate)} · {formatDuration(r.duration_s)}
            </option>
          ))}
        </select>
        {selectedRun && !metaEdit && (
          <button
            onClick={() => { setMetaName(selectedRun.displayName || ""); setMetaTags((selectedRun.tags || []).join(", ")); setMetaEdit(true); }}
            title="Name and tag this run so it's identifiable in the list"
            className="px-2.5 py-2 text-xs rounded bg-gray-700/40 text-gray-300 border border-tessera-border hover:bg-gray-700/60 whitespace-nowrap"
          >✎ Name</button>
        )}
        <button
          onClick={handleOptimize}
          disabled={optimizing || runs.length < 1}
          title={
            runs.length < 1
              ? "Need at least 1 prior run to optimize"
              : "Profile the recent runs and propose quality / time / cost improvements"
          }
          className="px-3 py-2 text-xs font-semibold rounded bg-amber-500/15 text-amber-300 border border-amber-500/30 hover:bg-amber-500/25 hover:border-amber-500/50 disabled:opacity-30 disabled:cursor-not-allowed transition whitespace-nowrap"
        >
          {optimizing ? "⚡ Starting…" : "⚡ Optimize"}
        </button>
        <button
          onClick={() => setShowTranscript(true)}
          disabled={!detail?.mission_transcript?.messages?.length}
          title={detail?.mission_transcript?.messages?.length ? "View the mission conversation that launched this run" : "No mission transcript for this run"}
          className="px-3 py-2 text-xs font-semibold rounded bg-tessera-accent/15 text-tessera-accent border border-tessera-accent/30 hover:bg-tessera-accent/25 hover:border-tessera-accent/50 disabled:opacity-30 disabled:cursor-not-allowed transition whitespace-nowrap"
        >
          💬 Transcript
        </button>
        <button
          onClick={() => setReplayOpen(true)}
          disabled={!selected}
          title={selected ? "Cinematic replay: build → mission → run → deliverable" : "Select a run to replay"}
          className="px-3 py-2 text-xs font-semibold rounded bg-purple-500/15 text-purple-300 border border-purple-500/30 hover:bg-purple-500/25 hover:border-purple-500/50 disabled:opacity-30 disabled:cursor-not-allowed transition whitespace-nowrap"
        >
          🎬 Replay
        </button>
        <button
          onClick={() => setShowGuidance((v) => !v)}
          title="Manage standing guidance applied to every run of this ecosystem"
          className={`px-3 py-2 text-xs font-semibold rounded border transition whitespace-nowrap ${showGuidance ? "bg-purple-500/25 text-purple-200 border-purple-500/50" : "bg-purple-500/15 text-purple-300 border-purple-500/30 hover:bg-purple-500/25 hover:border-purple-500/50"}`}
        >
          ★ Guidance
        </button>
        {selected && detail && (
          <OutcomePanel sessionId={selected} outcome={detail.outcome || null} running={detail.execution?.status === "RUNNING"} compact />
        )}
      </div>
      {metaEdit && selectedRun && (
        <div className="bg-tessera-card border border-tessera-border rounded-xl px-4 py-3 mb-3 flex items-center gap-2 shrink-0">
          <span className="text-[10px] uppercase tracking-wider text-gray-500 shrink-0">Run name</span>
          <input value={metaName} onChange={(e) => setMetaName(e.target.value)}
            placeholder={selectedRun.name.replace(`${ecosystemId}-`, "")}
            className="flex-1 bg-gray-900 border border-tessera-border rounded px-2.5 py-1.5 text-xs text-white placeholder-gray-600" />
          <span className="text-[10px] uppercase tracking-wider text-gray-500 shrink-0">Tags</span>
          <input value={metaTags} onChange={(e) => setMetaTags(e.target.value)}
            placeholder="comma, separated"
            className="w-[220px] bg-gray-900 border border-tessera-border rounded px-2.5 py-1.5 text-xs text-white placeholder-gray-600" />
          <button onClick={saveMeta} disabled={metaSaving}
            className="px-3 py-1.5 text-xs font-semibold rounded bg-tessera-accent/20 text-tessera-accent border border-tessera-accent/40 hover:bg-tessera-accent/30 disabled:opacity-40">
            {metaSaving ? "Saving…" : "Save"}</button>
          <button onClick={() => setMetaEdit(false)} className="px-2 py-1.5 text-xs text-gray-400 hover:text-white">Cancel</button>
        </div>
      )}

      {showGuidance && (
        <div className="mb-3 shrink-0">
          <GuidanceManager ecosystemId={ecosystemId} onClose={() => setShowGuidance(false)} />
        </div>
      )}

      {optimizeMsg && (
        <div className="mb-3 text-xs text-amber-200 bg-amber-500/10 border border-amber-500/30 rounded p-2 shrink-0">
          {optimizeMsg}
        </div>
      )}

      {error && <div className="mb-3 text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded p-2 shrink-0">{error}</div>}

      {/* Failure banner — only when run failed. Shows error name, cause, and redrive button. */}
      {failed && detail && (
        <div className="mb-3 bg-red-500/10 border border-red-500/30 rounded-xl p-3 shrink-0">
          <div className="flex items-start gap-3">
            <div className="shrink-0">
              <span className="inline-block px-2 py-0.5 text-[10px] font-bold uppercase tracking-wider bg-red-500/30 text-red-300 border border-red-500/50 rounded">
                {status}
              </span>
            </div>
            <div className="flex-1 min-w-0">
              <div className="text-xs font-semibold text-red-200 mb-0.5">
                {failedAgentObj?.agent_id
                  ? <>Failure in <span className="font-mono">{failedAgentObj.agent_id}</span></>
                  : "Execution failed"}
                {failedAgentObj?.error && <span className="ml-2 text-red-400/80">· {failedAgentObj.error}</span>}
              </div>
              <div className="text-[10px] text-red-300/90 font-mono whitespace-pre-wrap break-words max-h-32 overflow-y-auto">
                {extractErrorMessage(failedAgentObj?.cause) || detail.execution.error || detail.execution.cause || "No error message available."}
              </div>
              {(detail.execution.redriveCount ?? 0) > 0 && (
                <div className="text-[10px] text-red-400/70 mt-1">Redrive count: {detail.execution.redriveCount}</div>
              )}
              {detail.execution.redriveStatusReason && (
                <div className="text-[10px] text-red-400/70 mt-1">Reason: {detail.execution.redriveStatusReason}</div>
              )}
            </div>
            <div className="shrink-0">
              <button
                onClick={handleRetry}
                disabled={!canRetry || redriving}
                title={
                  !canRetry
                    ? `Can't retry automatically: ${detail.execution.redriveStatusReason || detail.execution.redriveStatus || "not redrivable"} and the failed step is unknown. Use “Resume from …” in the graph to pick a step.`
                    : redrivable
                    ? "Retry: redrive the failed task in place — re-runs only the failed step + downstream"
                    : `Retry: start a new run that reuses completed steps and re-runs from ${failedAgentObj?.agent_id} onward`
                }
                className={`px-3 py-2 text-xs font-semibold rounded border transition ${
                  !canRetry
                    ? "border-gray-700 bg-gray-900 text-gray-500 cursor-not-allowed"
                    : redriving
                    ? "border-tessera-accent/40 bg-tessera-accent/10 text-tessera-accent/60 cursor-wait"
                    : "border-tessera-accent/60 bg-tessera-accent/20 text-tessera-accent hover:bg-tessera-accent/30"
                }`}
              >
                {redriving ? "Retrying…" : canRetry ? "↻ Retry" : "Can't retry"}
              </button>
            </div>
          </div>
          {redriveMsg && (
            <div className={`mt-2 text-[11px] ${redriveMsg.startsWith("Retry initiated") ? "text-green-400" : "text-red-300"}`}>
              {redriveMsg}
            </div>
          )}
        </div>
      )}

      {/* Telemetry (cost / time / per-agent models) — above the main split. */}
      {selected && detail && (
        <RunTuningPanel ecosystemId={ecosystemId} execName={selected} inline />
      )}

      {/* Outcome now lives as a button in the header row (opens a modal). */}

      {/* Main — resizable: execution graph (left) | agent detail (right, bigger by default). */}
      <ResizableSplit
        className="flex-1"
        storageKey="tessera.historySplit"
        defaultRightPct={58}
        right={
          <div className="h-full bg-tessera-card border border-tessera-border rounded-xl overflow-hidden flex flex-col min-h-0 min-w-0">
            <div className="flex-1 overflow-y-auto p-4 min-h-0">
              {!selected && (
                <div className="text-xs text-gray-500 text-center mt-12">Pick a run from the dropdown above.</div>
              )}
              {selected && detailLoading && <div className="flex items-center gap-2 text-xs text-gray-500"><TesseraSpinner size={16} /> Loading run…</div>}
              {selected && detail && detail.agents.length === 0 && !failed && (
                <div className="text-xs text-gray-500">No agent outputs found in S3 (run may have failed before completing Layer 0).</div>
              )}
              {/* Failed-agent panel — when a failed agent is selected */}
              {selected && detail && selectedAgent && failedAgentObj?.agent_id === selectedAgent && (
                <div className="space-y-3">
                  <div className="flex items-center gap-2 border-l-2 border-red-500 pl-3">
                    <span className="font-mono text-base text-red-300 font-bold">{selectedAgent}</span>
                    <span className="text-[10px] px-2 py-0.5 rounded bg-red-500/20 text-red-300 border border-red-500/30 uppercase tracking-wide">failed</span>
                    <span className="text-[10px] text-red-400/70">{failedAgentObj.error}</span>
                  </div>
                  <div>
                    <div className="text-[10px] text-gray-500 mb-1">FAILURE DETAILS</div>
                    <div className="bg-gray-950 p-3 rounded border border-red-500/20 font-mono text-[11px] text-red-200/90 whitespace-pre-wrap break-words max-h-96 overflow-y-auto">
                      {failedAgentObj.cause || "No cause data available."}
                    </div>
                  </div>
                </div>
              )}
              {/* Successful-agent panel — when a successful agent is selected */}
              {selected && detail && selectedAgent && failedAgentObj?.agent_id !== selectedAgent && (() => {
                const a = detail.agents.find((x) => x.agent_id === selectedAgent);
                if (!a) return <div className="text-xs text-gray-500">Agent not found in this run (it may not have run, or it failed). Pick another from the graph.</div>;
                return <AgentDetail agent={a} mission={detail.execution.input?.mission_display || detail.execution.input?.mission} attachments={detail.execution.input?.attachments} description={agentBlurb((ecoConfig?.agents || []).find((x: any) => x.id === a.agent_id))} onOpenFile={async (name) => {
                  try {
                    const r = await apiFetch(`${API_URL}/api/ecosystems/${ecosystemId}/runs/${selected}/attachment?name=${encodeURIComponent(name)}`);
                    const d = await r.json().catch(() => ({}));
                    if (d.url) window.open(d.url, "_blank", "noopener"); else alert("Couldn't open that file.");
                  } catch { alert("Couldn't open that file."); }
                }} />;
              })()}
            </div>
          </div>
        }
        left={
          <DagCatalogPanel
            dagProps={{ config: dagConfig, events: dagEvents, focusedAgent: selectedAgent, onFocusAgent: setSelectedAgent }}
            domains={ecoConfig?.domains}
            flowFooter={
              selected && selectedAgent && (dagConfig.agents?.length ?? 0) > 0 ? (
                <button
                  onClick={() => setResumeOpen(true)}
                  title={`Start a new run that reuses upstream and re-runs from ${selectedAgent} onward (works for failed OR succeeded runs)`}
                  className="m-2 px-2 py-1.5 text-[11px] font-semibold rounded bg-purple-500/20 text-purple-200 border border-purple-500/40 hover:bg-purple-500/30 transition shrink-0"
                >↻ Resume from {selectedAgent.length > 16 ? selectedAgent.slice(0, 15) + "…" : selectedAgent}</button>
              ) : null
            }
          />
        }
      />

      {resumeOpen && selected && selectedAgent && (
        <ResumeDialog
          ecoId={ecosystemId}
          sessionId={selected}
          agents={(dagConfig.agents || []).map((a: any) => ({ id: a.id, name: a.name, depends_on: a.depends_on || [] }))}
          selectedAgentId={selectedAgent}
          onClose={() => setResumeOpen(false)}
          onResumeStarted={(newSession) => { setResumeOpen(false); onResumeStarted?.(newSession); }}
        />
      )}

      {showTranscript && detail?.mission_transcript?.messages && (
        <TranscriptModal
          title="Mission conversation"
          subtitle={selected ? selected.replace(`${ecosystemId}-`, "") : undefined}
          messages={detail.mission_transcript.messages}
          reconstructed={detail.mission_transcript.reconstructed}
          onClose={() => setShowTranscript(false)}
        />
      )}
      {replayOpen && selected && (
        <ReplayPlayer ecosystemId={ecosystemId} runName={selected} onClose={() => setReplayOpen(false)} />
      )}

      {/* Telemetry + outcome now render ABOVE the main split; the bottom
          agent-cards ribbon was removed — the execution graph is the navigator. */}

      {/* Floating chat widget — uses the same chat Lambda as the live RunView.
          Backend is generic: works against any session_id whose execution
          history + outputs are still in S3 (Standard SFN keeps history 90 days). */}
      {selected && detail && (() => {
        // A report Studio (which hosts Ask) exists when the run produced an HTML
        // deliverable — the same condition the Beautify button uses. In that case
        // the floating widget drops Chat (Events only) so Ask has one home.
        const o = detail.outcome;
        const hasStudio = !!o && (o.declared_artifacts || []).some((a) =>
          (o.produced_artifacts || []).includes(a.id) &&
          ((a.format || "").toLowerCase() === "html" || a.save_as.toLowerCase().endsWith(".html")));
        return <RunChat sessionId={selected} events={viewerEvents} hideChat={hasStudio} />;
      })()}
    </div>
  );
}

function AgentDetail({ agent: a, mission, attachments, description, onOpenFile }: { agent: AgentSnapshot; mission?: string; attachments?: Array<{ name: string }>; description?: string; onOpenFile?: (name: string) => void }) {
  const surfaces = extractSurfaces(a.tool_results);
  const isReport = isReportAgent(a.agent_id);
  return (
    <div className="space-y-4">
      {mission && (
        <div className="text-[11px] text-gray-300 italic bg-gray-900/40 border border-tessera-border rounded p-2">
          Mission: "{mission}"
        </div>
      )}
      {attachments && attachments.length > 0 && (
        <div className="text-[11px] text-gray-400 bg-gray-900/40 border border-tessera-border rounded p-2">
          <span className="text-gray-500">Uploaded files:</span>{" "}
          {attachments.map((f, i) => (
            <button key={i} type="button" onClick={() => onOpenFile?.(f.name)} title="Open file"
              className="inline-flex items-center gap-1 mr-2 text-gray-300 hover:text-white hover:underline cursor-pointer"><span>📎</span>{f.name}</button>
          ))}
        </div>
      )}
      <div className={`flex items-center gap-2 ${isReport ? "border-l-2 border-tessera-accent pl-3" : ""}`}>
        <span className={`font-mono ${isReport ? "text-base text-tessera-accent font-bold" : "text-sm text-gray-200"}`}>{a.agent_id}</span>
        {isReport && <span className="text-[10px] px-2 py-0.5 rounded bg-tessera-accent/20 text-tessera-accent border border-tessera-accent/30 uppercase tracking-wide">final report</span>}
        <span className="text-[10px] text-gray-500">{a.output_size.toLocaleString()}c · {a.tool_results.length} tool calls{surfaces.length ? ` · ${surfaces.length} ui surfaces` : ""}</span>
      </div>
      {description && <p className="text-[11px] text-gray-400 leading-snug -mt-1">{description}</p>}
      {a.tool_results.length > 0 && (
        <div>
          <div className="text-[10px] text-gray-500 mb-1">TOOL CALLS</div>
          <div className="space-y-1">
            {a.tool_results.map((tr, i) => (
              <ToolChip
                key={i}
                tool={{
                  id: `${a.agent_id}-${i}`,
                  name: tr.name || "tool",
                  args: typeof tr.args === "string" ? tr.args : JSON.stringify(tr.args || {}),
                  result: tr.result,
                  status: "complete",
                }}
              />
            ))}
          </div>
        </div>
      )}
      {surfaces.length > 0 && (
        <div>
          <div className="text-[10px] text-gray-500 mb-1">GENERATED UI</div>
          <div className="space-y-3">
            {surfaces.map((s, i) => (
              <div key={i} className="bg-gray-950 border border-tessera-border rounded p-2">
                <A2UISurfaceRenderer surface={s} />
              </div>
            ))}
          </div>
        </div>
      )}
      {a.output && (
        <div>
          <div className="text-[10px] text-gray-500 mb-1">FULL OUTPUT</div>
          <div className={`bg-gray-950 p-4 rounded border ${isReport ? "border-tessera-accent/40" : "border-tessera-border"} prose prose-invert max-w-none ${isReport ? "prose-base" : "prose-sm"} prose-headings:text-tessera-accent prose-strong:text-white prose-code:text-amber-300 prose-code:bg-amber-500/10 prose-code:px-1 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-a:text-tessera-accent prose-li:my-0.5 prose-pre:bg-black/40 prose-pre:text-[11px]`}>
            <ReactMarkdown>{a.output}</ReactMarkdown>
          </div>
        </div>
      )}
      {a.structured?.findings && (
        <div>
          <div className="text-[10px] text-gray-500 mb-1">STRUCTURED FINDINGS</div>
          <div className="bg-gray-950 p-3 rounded border border-tessera-border prose prose-invert prose-sm max-w-none prose-headings:text-amber-300 prose-strong:text-amber-200 prose-code:text-amber-300 prose-code:bg-amber-500/10 prose-code:px-1 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-li:my-0.5">
            <ReactMarkdown>{a.structured.findings}</ReactMarkdown>
          </div>
        </div>
      )}
      {a.structured?.open_questions && (
        <div>
          <div className="text-[10px] text-gray-500 mb-1">OPEN QUESTIONS</div>
          <div className="bg-gray-950 p-3 rounded border border-tessera-border prose prose-invert prose-sm max-w-none prose-headings:text-blue-300 prose-strong:text-blue-200 prose-code:text-blue-300 prose-code:bg-blue-500/10 prose-code:px-1 prose-code:rounded prose-code:before:content-none prose-code:after:content-none prose-li:my-0.5">
            <ReactMarkdown>{a.structured.open_questions}</ReactMarkdown>
          </div>
        </div>
      )}
    </div>
  );
}

function isReportAgent(id: string): boolean {
  return id === "report_agent" || id === "report-agent" || /report/i.test(id);
}

/** Short blurb for an agent — from description or first sentence of its prompt. */
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

function isFailedStatus(s: string): boolean {
  return s === "FAILED" || s === "TIMED_OUT" || s === "ABORTED"; // AWS Step Functions API status values
}

function reportFirstSort(a: AgentSnapshot, b: AgentSnapshot): number {
  const aR = isReportAgent(a.agent_id) ? 0 : 1;
  const bR = isReportAgent(b.agent_id) ? 0 : 1;
  return aR - bR;
}

function formatDate(iso: string): string {
  if (!iso) return "—";
  try {
    const d = new Date(iso);
    return d.toLocaleString(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
  } catch {
    return iso;
  }
}

function formatDuration(s: number | null): string {
  if (s == null) return "—";
  if (s < 60) return `${s.toFixed(0)}s`;
  const m = Math.floor(s / 60);
  const r = Math.floor(s % 60);
  return `${m}m ${r}s`;
}

function extractErrorMessage(cause: string | undefined | null): string {
  // SFN cause is usually a JSON-encoded Lambda error payload. Pull out the
  // human-readable message if possible; otherwise return raw.
  if (!cause) return "";
  try {
    const parsed = JSON.parse(cause);
    return parsed.errorMessage || parsed.message || cause;
  } catch {
    return cause;
  }
}

function extractSurfaces(toolResults: any[]): any[] {
  const out: any[] = [];
  for (const tr of toolResults || []) {
    if (tr?.name !== "generateUI") continue;
    let raw = tr.args;
    if (typeof raw === "string") {
      try { raw = JSON.parse(raw); } catch { continue; }
    }
    const surfaceStr = raw?.surface_json;
    if (typeof surfaceStr !== "string") continue;
    try {
      const surface = JSON.parse(surfaceStr);
      if (surface && typeof surface === "object" && Array.isArray(surface.components)) {
        out.push(surface);
      }
    } catch { /* ignore */ }
  }
  return out;
}
