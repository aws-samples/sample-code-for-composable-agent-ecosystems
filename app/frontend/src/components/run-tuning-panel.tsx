"use client";

// Run Telemetry widget (spec-run-tuning §5/§6.3) — a floating, dockable panel
// (chat-window style) showing the run's cost/latency/model telemetry from the
// manifest, plus a 1–5 quality rating that feeds the cross-run recommender.
//
// Design: a small launcher pill is ALWAYS available in the run view (bottom-right),
// so it never competes with the DAG/agent output for vertical space (panel-fatigue
// fix) yet can be opened any time — including mid-run. While the run is live and the
// drawer is open it auto-refreshes so you can watch cost/tokens accrue. Rating is
// enabled once the run is terminal.
import { useEffect, useState, useCallback, useRef } from "react";
import { getApiUrl } from "@/lib/runtime-config";

const API_URL_FALLBACK = process.env.NEXT_PUBLIC_API_URL || "";
const apiBase = () => getApiUrl() || API_URL_FALLBACK;

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

interface Manifest {
  status?: string;
  metrics?: { wall_clock_s?: number; cost_estimate_usd?: number; cost_is_partial?: boolean;
              totals?: { tokens_in?: number; tokens_out?: number };
              per_agent?: { agent_id: string; model?: string; duration_s?: number; tokens_in?: number; tokens_out?: number; cost_usd?: number }[] };
  run_config?: { model_profile?: string; model_overrides?: { by_type?: Record<string, string> } };
  human_rating?: { score?: number; note?: string } | null;
}

function fmtUSD(n?: number) { return n == null ? "—" : `$${n.toFixed(n < 0.01 ? 5 : 4)}`; }
function fmtDur(s?: number) { if (s == null) return "—"; const m = Math.floor(s / 60); return m ? `${m}m ${Math.round(s % 60)}s` : `${Math.round(s)}s`; }

export function RunTuningPanel({ ecosystemId, execName, inline = false }: { ecosystemId: string; execName: string; inline?: boolean }) {
  const [m, setM] = useState<Manifest | null>(null);
  const [rating, setRating] = useState<number>(0);
  const [savedRating, setSavedRating] = useState<number>(0);
  const [open, setOpen] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  // spec-quality-feedback-loop: per-run freeform feedback + promote-to-guidance.
  const [feedback, setFeedback] = useState<string>("");
  const [savedFeedback, setSavedFeedback] = useState<string>("");
  const [fbState, setFbState] = useState<"idle" | "saving" | "saved" | "promoting" | "promoted" | "error">("idle");

  const load = useCallback(async () => {
    setRefreshing(true);
    try {
      const r = await apiFetch(`${apiBase()}/api/ecosystems/${ecosystemId}/runs/${encodeURIComponent(execName)}/manifest`);
      if (r.ok) {
        const data: Manifest = await r.json();
        setM(data);
        const hr = data.human_rating?.score || 0;
        setRating(hr); setSavedRating(hr);
      }
      // Feedback sidecar is separate from the telemetry manifest.
      const fr = await apiFetch(`${apiBase()}/api/ecosystems/${ecosystemId}/runs/${encodeURIComponent(execName)}/feedback`);
      if (fr.ok) {
        const fb = await fr.json();
        setFeedback(fb.run_feedback || "");
        setSavedFeedback(fb.run_feedback || "");
      }
    } catch { /* ignore */ } finally { setRefreshing(false); }
  }, [ecosystemId, execName]);

  useEffect(() => { load(); }, [load]);

  const saveFeedback = async () => {
    setFbState("saving");
    try {
      const r = await apiFetch(`${apiBase()}/api/ecosystems/${ecosystemId}/runs/${encodeURIComponent(execName)}/feedback`,
        { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ run_feedback: feedback }) });
      if (r.ok) { setSavedFeedback(feedback); setFbState("saved"); }
      else setFbState("error");
    } catch { setFbState("error"); }
  };

  const promoteRun = async () => {
    setFbState("promoting");
    try {
      // Persist first so the server promotes the latest text.
      if (feedback !== savedFeedback) await saveFeedback();
      const r = await apiFetch(`${apiBase()}/api/ecosystems/${ecosystemId}/runs/${encodeURIComponent(execName)}/promote`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ scope: "run" }) });
      setFbState(r.ok ? "promoted" : "error");
    } catch { setFbState("error"); }
  };

  // Live refresh: while the drawer/inline view is open and the run is still going
  // (or status not yet known), poll every 12s so cost/tokens/durations accrue.
  const isRunning = m?.status === "RUNNING" || m?.status === undefined;
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  useEffect(() => {
    if ((open || inline) && isRunning) {
      pollRef.current = setInterval(load, 12000);
      return () => { if (pollRef.current) clearInterval(pollRef.current); };
    }
  }, [open, inline, isRunning, load]);

  const submitRating = async (score: number) => {
    setRating(score);
    try {
      const r = await apiFetch(`${apiBase()}/api/ecosystems/${ecosystemId}/runs/${encodeURIComponent(execName)}/rating`,
        { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ score }) });
      if (r.ok) setSavedRating(score);
    } catch { /* ignore */ }
  };

  if (!ecosystemId || !execName) return null;
  const mt = m?.metrics;
  const profile = m?.run_config?.model_profile;
  const byType = m?.run_config?.model_overrides?.by_type || {};
  const terminal = !!m && m.status !== "RUNNING" && m.status !== undefined;

  // Collapsed (default): minimized launcher you click to restore.
  if (!open) {
    // History modal: an inline chip (with a quick glance of metrics) in the flow.
    if (inline) {
      return (
        <button
          onClick={() => { setOpen(true); load(); }}
          className="flex items-center gap-2 bg-tessera-card border border-tessera-border hover:border-tessera-accent/50 rounded-full px-3 py-1.5 text-xs mt-3"
          title="Run telemetry (cost / time / models) — click to open"
        >
          <span className="text-tessera-accent">📊</span>
          <span className="font-semibold text-gray-200">Telemetry</span>
          {mt && <span className="text-gray-400">⏱ {fmtDur(mt.wall_clock_s)} · {fmtUSD(mt.cost_estimate_usd)}{mt.cost_is_partial ? "*" : ""}</span>}
        </button>
      );
    }
    // Run window: a circular blob matching the chat launcher's LOOK (icon only,
    // no text) docked just LEFT of the chat blob (chat is bottom-5 right-5, w-12)
    // so they sit side-by-side with no overlap. Kept consistent with the 💬 blob.
    return (
      <button
        onClick={() => { setOpen(true); load(); }}
        className="fixed bottom-5 right-20 w-12 h-12 bg-tessera-gradient rounded-full shadow-lg shadow-purple-500/30 flex items-center justify-center text-lg hover:scale-105 transition z-50"
        title="Run telemetry (cost / time / models)"
      >📊</button>
    );
  }

  // Shared body — used by both the floating drawer and the inline (History) variant.
  const body = (
    <div className="overflow-y-auto px-4 py-3">
      {!mt ? (
        <p className="text-xs text-gray-500">Collecting telemetry…</p>
      ) : (
        <>
          <div className="flex items-center gap-3 text-xs mb-3 flex-wrap">
            <span className="text-gray-300">⏱ {fmtDur(mt.wall_clock_s)}</span>
            <span className="text-gray-300">💲 {fmtUSD(mt.cost_estimate_usd)}{mt.cost_is_partial ? "*" : ""}</span>
            {(mt.totals?.tokens_in || mt.totals?.tokens_out) ? (
              <span className="text-gray-500">{(mt.totals?.tokens_in || 0).toLocaleString()}→{(mt.totals?.tokens_out || 0).toLocaleString()} tok</span>
            ) : <span className="text-gray-600">tokens n/a</span>}
            {profile && <span className="text-[10px] bg-purple-500/10 text-purple-300 border border-purple-500/20 px-1.5 py-0.5 rounded-full">{profile}</span>}
            {Object.entries(byType).map(([k, v]) => <span key={k} className="text-[10px] text-gray-400 border border-tessera-border px-1.5 py-0.5 rounded-full">{k}:{v}</span>)}
          </div>

          <div className="flex items-center gap-2 mb-3">
            <span className="text-[11px] text-gray-400">Rate this run:</span>
            {[1, 2, 3, 4, 5].map(n => (
              <button key={n} disabled={!terminal} onClick={() => submitRating(n)} title={terminal ? `${n}/5` : "available when the run finishes"}
                className={`text-base leading-none disabled:opacity-30 ${n <= rating ? "text-yellow-400" : "text-gray-600 hover:text-gray-400"}`}>★</button>
            ))}
            {savedRating > 0 && <span className="text-[10px] text-green-400 ml-1">saved · feeds the optimizer</span>}
            {!terminal && <span className="text-[10px] text-gray-600 ml-1">(after run completes)</span>}
          </div>

          {/* spec-quality-feedback-loop: freeform feedback on this run + promote to
              standing guidance that steers every future run of this ecosystem. */}
          <div className="mb-3">
            <div className="flex items-center justify-between mb-1">
              <span className="text-[11px] text-gray-400">Feedback on this run</span>
              {feedback !== savedFeedback && <span className="text-[10px] text-amber-400">unsaved</span>}
              {fbState === "saved" && feedback === savedFeedback && <span className="text-[10px] text-green-400">saved</span>}
              {fbState === "promoted" && <span className="text-[10px] text-purple-300">✓ added to guidance</span>}
              {fbState === "error" && <span className="text-[10px] text-red-400">error — retry</span>}
            </div>
            <textarea
              value={feedback}
              onChange={(e) => { setFeedback(e.target.value); if (fbState !== "idle") setFbState("idle"); }}
              placeholder="What was good/bad? e.g. 'Quantify insurance premium drag in dollars, don't just flag over-cover.'"
              rows={3}
              className="w-full text-[11px] bg-tessera-bg border border-tessera-border rounded-lg px-2 py-1.5 text-gray-200 placeholder:text-gray-600 focus:border-tessera-accent/50 outline-none resize-y"
            />
            <div className="flex items-center gap-2 mt-1.5">
              <button
                onClick={saveFeedback}
                disabled={fbState === "saving" || feedback === savedFeedback}
                className="text-[11px] px-2.5 py-1 rounded-md border border-tessera-border text-gray-300 hover:border-tessera-accent/50 disabled:opacity-40"
              >{fbState === "saving" ? "Saving…" : "Save feedback"}</button>
              <button
                onClick={promoteRun}
                disabled={fbState === "promoting" || !feedback.trim()}
                title="Turn this into standing guidance applied to every future run of this ecosystem"
                className="text-[11px] px-2.5 py-1 rounded-md bg-purple-500/15 border border-purple-500/30 text-purple-200 hover:bg-purple-500/25 disabled:opacity-40"
              >{fbState === "promoting" ? "Promoting…" : "★ Promote to guidance"}</button>
            </div>
          </div>

          {mt.per_agent && mt.per_agent.length > 0 && (
            <table className="w-full text-[11px]">
              <thead><tr className="text-gray-500 text-left"><th className="font-normal">agent</th><th className="font-normal">model</th><th className="font-normal text-right">time</th><th className="font-normal text-right">tokens</th><th className="font-normal text-right">cost</th></tr></thead>
              <tbody>
                {mt.per_agent.map(a => (
                  <tr key={a.agent_id} className="text-gray-300 border-t border-tessera-border/50">
                    <td className="py-0.5">{a.agent_id}</td>
                    <td className="text-gray-400">{(a.model || "").replace(/^us\.anthropic\.claude-/, "").replace(/-\d{8}.*$/, "") || "—"}</td>
                    <td className="text-right">{fmtDur(a.duration_s)}</td>
                    <td className="text-right text-gray-500">{((a.tokens_in || 0) + (a.tokens_out || 0)).toLocaleString() || "—"}</td>
                    <td className="text-right">{fmtUSD(a.cost_usd)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          )}
          {mt.cost_is_partial && <p className="text-[10px] text-amber-400 mt-1">* cost is a lower bound — some agents reported tokens with no price.</p>}
        </>
      )}
    </div>
  );

  const headerBar = (
    <div className="flex items-center justify-between px-4 py-2.5 border-b border-tessera-border">
      <div className="flex items-center gap-2 text-xs">
        <span className="text-tessera-accent">📊</span>
        <span className="font-semibold text-gray-200">Run telemetry</span>
        {isRunning ? <span className="text-[10px] text-green-300 flex items-center gap-1"><span className="w-1.5 h-1.5 rounded-full bg-green-400 blink" /> live</span>
                   : <span className="text-[10px] text-gray-500">{m?.status?.toLowerCase()}</span>}
      </div>
      <div className="flex items-center gap-2">
        <button onClick={load} disabled={refreshing} className="text-[11px] text-gray-400 hover:text-white disabled:opacity-40" title="Refresh">⟳</button>
        <button onClick={() => setOpen(false)} className="text-gray-500 hover:text-white text-sm" title={inline ? "Minimize" : "Close"}>{inline ? "▾" : "✕"}</button>
      </div>
    </div>
  );

  // Inline variant — embedded in the History detail pane (not floating).
  if (inline) {
    return (
      <div className="bg-tessera-card border border-tessera-border rounded-xl mt-3 max-h-[40vh] flex flex-col">
        {headerBar}
        {body}
      </div>
    );
  }

  // Floating expanded drawer (bottom-right), refreshes live while running.
  return (
    <div className="fixed bottom-20 right-6 z-40 w-[440px] max-w-[92vw] max-h-[72vh] flex flex-col bg-tessera-card border border-tessera-accent/40 rounded-xl shadow-2xl shadow-black/50">
      {headerBar}
      {body}
    </div>
  );
}
