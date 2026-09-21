"use client";

// /solo — the single-ecosystem "ecosystem export" experience (FULL PARITY).
//
// This route is LOCKED to ONE ecosystem (its id + endpoints come from the injected
// window.__TESSERA_SOLO__ config, resolved via runtime-config, or NEXT_PUBLIC_* env)
// and REUSES the platform's existing run chain VERBATIM: <MissionConsole> to shape the
// mission, useWebSocket().launch() to start the run, then <BusinessRun> wrapping
// <RunView> (via <RunTabContent>) plus <GateDecisionDialog> for pending quality gates —
// styled exactly like the platform's business run.
//
// It deliberately imports NONE of the platform's proprietary surfaces (BuilderView /
// CatalogBrowserView / ToolCatalogView / admin / optimizer / the ecosystem grid) so
// Next code-splits them out of the /solo bundle. RunView's platform-only telemetry +
// optimizer panels are gated off here via showPlatformPanels={false} and are
// lazy-loaded in run-view.tsx, so they never enter this bundle.

import { useEffect, useMemo, useState } from "react";
import { useAuth } from "@/components/auth-provider";
import { getIdToken } from "@/lib/auth";
import { useWebSocket } from "@/hooks/use-websocket";
import { RunTabContent } from "@/components/run-tab-content";
import { BusinessRun } from "@/components/business/business-run";
import { RunView } from "@/components/run-view";
import { GateDecisionDialog } from "@/components/gate-decision-dialog";
import { MissionConsole, type MissionEco } from "@/components/mission-console";
import type { RunTab } from "@/hooks/use-run-tabs";
import { soloConfig, getApiUrl, getWsUrl } from "@/lib/runtime-config";

const prettyLabel = (id: string) =>
  id.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

async function authFetch(url: string, init?: RequestInit): Promise<Response> {
  const token = await getIdToken();
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(url, { ...init, headers });
}

export default function SoloPage() {
  const { user } = useAuth();
  // Resolve the ecosystem + endpoints once (runtime config → build-time env fallback).
  const [cfg] = useState(() => ({ apiUrl: getApiUrl(), wsUrl: getWsUrl(), ecoId: soloConfig().ecoId || process.env.NEXT_PUBLIC_SOLO_ECO || "" }));
  const { apiUrl, wsUrl, ecoId } = cfg;

  const [ecoConfig, setEcoConfig] = useState<any>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  // Persist the active run's sessionId (per eco) so a page REFRESH returns to the run
  // view instead of dropping back to the "Start a run" mission form. Hydrated from
  // sessionStorage on mount; cleared on "New run".
  const _sidKey = `tessera.solo.sid.${ecoId}`;
  const [sessionId, setSessionIdState] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    try { return window.sessionStorage.getItem(_sidKey); } catch { return null; }
  });
  const setSessionId = (sid: string | null) => {
    setSessionIdState(sid);
    try {
      if (sid) window.sessionStorage.setItem(_sidKey, sid);
      else window.sessionStorage.removeItem(_sidKey);
    } catch {}
  };
  const [launching, setLaunching] = useState(false);
  const [runs, setRuns] = useState<Array<{ name: string; status: string; startDate?: string; duration_s?: number | null }>>([]);
  const [showHistory, setShowHistory] = useState(false);

  const { subscribe, launch } = useWebSocket({ url: wsUrl });

  // Load the single ecosystem's config (the ONLY allowed catalog/config route).
  useEffect(() => {
    if (!ecoId) { setLoadError("No ecosystem configured for this deployment."); return; }
    authFetch(`${apiUrl}/api/ecosystems/${ecoId}`)
      .then((r) => r.json())
      .then((cfg) => setEcoConfig(cfg))
      .catch(() => setLoadError("Couldn't load the ecosystem configuration."));
  }, [apiUrl, ecoId]);

  // Run history — list past + in-flight executions (runs-api). Polled so the current
  // run and any others surface without a manual refresh.
  const loadRuns = useMemo(() => async () => {
    if (!ecoId) return;
    try {
      const r = await authFetch(`${apiUrl}/api/ecosystems/${ecoId}/runs`);
      const d = await r.json();
      if (Array.isArray(d?.executions)) setRuns(d.executions);
    } catch { /* non-fatal */ }
  }, [apiUrl, ecoId]);
  useEffect(() => {
    void loadRuns();
    const t = setInterval(loadRuns, 8000);
    return () => clearInterval(t);
  }, [loadRuns]);

  const eco: MissionEco = useMemo(() => {
    const e = ecoConfig?.ecosystem || {};
    return {
      id: ecoId,
      name: e.name || prettyLabel(ecoId),
      description: e.description || "",
      inputs: e.inputs || ecoConfig?.inputs || [],
      outcome: e.outcome || ecoConfig?.outcome || null,
    };
  }, [ecoConfig, ecoId]);

  const onConverged = async (
    mission: string,
    inputs: Record<string, string>,
    attachments: Array<{ name: string; ref: string }>,
    transcript: Array<{ role: string; content: string }>,
  ) => {
    setLaunching(true);
    setLoadError(null);
    try {
      const sid = await launch(ecoId, mission, inputs, user?.userId, false, {}, attachments, transcript);
      setSessionId(sid);
      setShowHistory(false);
      setTimeout(loadRuns, 1500);
    } catch (e: any) {
      setLoadError(e?.message || "Launch failed — please try again.");
    } finally {
      setLaunching(false);
    }
  };

  const openReport = async (sid: string) => {
    try {
      const res = await authFetch(`${apiUrl}/api/runs/${encodeURIComponent(sid)}/bundle`);
      const data = await res.json();
      if (data?.download_url) window.open(data.download_url, "_blank");
    } catch { /* ignore — per-artifact downloads remain available in the run view */ }
  };

  const runTab: RunTab | null = sessionId
    ? { id: sessionId, ecosystemId: ecoId, ecosystemName: eco.name, sessionId, status: "running", createdAt: Date.now() }
    : null;

  return (
    <main className="min-h-screen flex flex-col bg-tessera-bg text-gray-100">
      {/* Header band — sticky so it stays visible while the page scrolls. */}
      <header className="sticky top-0 z-30 border-b border-tessera-border bg-tessera-card/80 backdrop-blur">
        <div className="w-full max-w-[104rem] mx-auto px-6 py-4 flex items-center gap-3">
          {/* Tessera hexagon mark + "by Tessera" — co-brands the ecosystem as the product,
              Tessera as the platform (kept small so the ecosystem name stays primary). */}
          <span className="text-tessera-accent text-lg font-bold" title="Powered by Tessera">⬡</span>
          <div className="min-w-0 flex-1">
            <div className="flex items-baseline gap-2 min-w-0">
              <div className="text-base font-bold text-white truncate">{eco.name}</div>
              <span className="text-[10px] uppercase tracking-[0.14em] text-gray-500 shrink-0">by Tessera</span>
            </div>
            {eco.description && <div className="text-xs text-gray-400 truncate">{eco.description}</div>}
          </div>
          <button
            onClick={() => { setSessionId(null); setShowHistory(false); }}
            className="text-[12px] font-semibold rounded-lg px-3 py-1.5 border border-tessera-border bg-tessera-card text-gray-300 hover:text-white hover:border-gray-400 transition"
          >＋ New run</button>
          <button
            onClick={() => { setShowHistory((s) => !s); void loadRuns(); }}
            className={`text-[12px] font-semibold rounded-lg px-3 py-1.5 border transition ${showHistory ? "border-tessera-accent/40 bg-tessera-accent/15 text-tessera-accent" : "border-tessera-border bg-tessera-card text-gray-300 hover:text-white hover:border-gray-400"}`}
          >History{runs.length ? ` (${runs.length})` : ""}</button>
        </div>
      </header>

      <div className="flex-1 w-full max-w-[104rem] mx-auto px-6 py-6">
        {loadError && (
          <div className="mb-4 px-4 py-3 rounded-lg border border-red-500/30 bg-red-500/10 text-sm text-red-300">{loadError}</div>
        )}

        {/* Run history — past + in-flight executions. Reuses the allowed /runs route; opening
            a run views it through the SAME RunView (no separate run UI). */}
        {showHistory && (
          <div className="rounded-2xl border border-tessera-border bg-tessera-card p-4 mb-4">
            <div className="text-[11px] uppercase tracking-[0.14em] text-tessera-accent font-bold mb-3">Runs</div>
            {runs.length === 0 ? (
              <div className="text-sm text-gray-500">No runs yet.</div>
            ) : (
              <div className="space-y-1">
                {runs.map((r) => {
                  const st = (r.status || "").toUpperCase();
                  const color = st === "RUNNING" ? "text-yellow-300 border-yellow-500/40"
                    : st === "SUCCEEDED" ? "text-green-300 border-green-500/40"
                    : "text-red-300 border-red-500/40";
                  return (
                    <button
                      key={r.name}
                      onClick={() => { setSessionId(r.name); setShowHistory(false); }}
                      className={`w-full text-left flex items-center gap-3 text-[12.5px] py-1.5 px-2 rounded hover:bg-tessera-bg/60 ${r.name === sessionId ? "bg-tessera-bg/60" : ""}`}
                    >
                      <span className={`text-[10px] px-2 py-0.5 rounded-full border ${color}`}>{st || "—"}</span>
                      <span className="text-gray-200 truncate flex-1">{r.name}</span>
                      {r.startDate && <span className="text-gray-500">{new Date(r.startDate).toLocaleString()}</span>}
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        )}

        {/* Mission capture (before a run is launched). Kept at a comfortable reading width
            (the run view below uses the full page width). */}
        {!sessionId && !showHistory && (
          <div className="max-w-3xl mx-auto rounded-2xl border border-tessera-border bg-tessera-card p-6 relative overflow-hidden">
            <div className="absolute inset-x-0 top-0 h-[3px] bg-tessera-gradient opacity-90" />
            <h1 className="text-lg font-bold text-white mb-1">Start a run</h1>
            <p className="text-sm text-gray-400 mb-4">Describe what you want, attach any documents, and we&apos;ll shape the mission before launching.</p>
            {ecoConfig ? (
              <MissionConsole eco={eco} apiUrl={apiUrl} wsUrl={wsUrl} onConverged={onConverged} />
            ) : (
              !loadError && <div className="text-sm text-gray-500">Loading ecosystem…</div>
            )}
            {launching && <div className="mt-3 text-sm text-tessera-accent animate-pulse">Launching your run…</div>}
          </div>
        )}

        {/* Live/past run — reuse the exact platform run chain. */}
        {sessionId && runTab && !showHistory && (
          <RunTabContent tab={runTab} subscribe={subscribe} onStatusChange={() => {}}>
            {(runProps) => (
              <>
                <BusinessRun
                  ecoLabel={eco.name}
                  sessionId={runProps.sessionId}
                  isRunning={runProps.isRunning}
                  runStatus={runProps.runStatus as any}
                  onOpenReport={() => openReport(runProps.sessionId)}
                  onYourResults={() => setSessionId(null)}
                >
                  <RunView
                    ecosystem={ecoId}
                    events={runProps.events}
                    sharedState={{}}
                    isRunning={runProps.isRunning}
                    runStatus={runProps.runStatus as any}
                    connectionState={runProps.connectionState as any}
                    inFlightPhase={runProps.inFlightPhase}
                    sessionId={runProps.sessionId}
                    outcome={runProps.outcome}
                    showPlatformPanels={false}
                  />
                </BusinessRun>

                {/* Ask-mode quality gate — reuse the platform dialog. */}
                {runProps.pendingGates.length > 0 && (
                  <GateDecisionDialog
                    key={runProps.pendingGates[0].task_token}
                    decision={runProps.pendingGates[0]}
                    onResolved={() => {}}
                  />
                )}

                <div className="mt-4 text-center">
                  <button
                    onClick={() => setSessionId(null)}
                    className="text-xs text-gray-400 hover:text-tessera-accent"
                  >
                    ← Start another run
                  </button>
                </div>
              </>
            )}
          </RunTabContent>
        )}
      </div>

      {/* Platform attribution — discreet, keeps the ecosystem as the star while crediting
          the platform. Pinned below the flex-1 content (bottom of the viewport when the
          page is short, below the fold when it's tall) + sticky so it stays in view. */}
      <footer className="sticky bottom-0 z-20 border-t border-tessera-border bg-tessera-card/80 backdrop-blur">
        <div className="w-full max-w-[104rem] mx-auto px-6 py-3 flex items-center gap-2 text-[11px] text-gray-500">
          <span className="text-tessera-accent">⬡</span>
          <span>Powered by <span className="font-semibold text-gray-400">Tessera</span> · Amazon Bedrock</span>
        </div>
      </footer>
    </main>
  );
}
