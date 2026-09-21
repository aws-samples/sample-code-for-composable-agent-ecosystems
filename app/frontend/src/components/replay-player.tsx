"use client";

// Cinematic run replay. Choreographs a past run into four scenes from captured/
// reconstructed data:
//   ① Building the ecosystem   — the builder conversation, typed out
//   ② Shaping the mission      — the mission conversation, typed out
//   ③ Running the mission      — the DAG lights up agent-by-agent (topological order),
//                                driven by synthetic STATE_DELTA events fed to DagMap
//   ④ The deliverable          — the run outcome revealed
// Reuses DagMap + OutcomePanel. Read-only; no run is re-executed.

import { useEffect, useMemo, useState } from "react";
import { DagMap } from "./dag-map";
import { OutcomePanel } from "./outcome-panel";
import { TesseraSpinner } from "./tessera-spinner";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

async function apiFetch(url: string): Promise<Response> {
  const { fetchAuthSession } = await import("aws-amplify/auth");
  let token = "";
  try { token = (await fetchAuthSession({ forceRefresh: false })).tokens?.idToken?.toString() || ""; } catch {}
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(url, { headers });
}

type SceneKey = "builder" | "mission" | "execution" | "deliverable";
type Msg = { role: string; content: string };

// Topological order of agents (mirrors DagMap's layering) so the replay lights up
// nodes in a valid execution order.
function topoOrder(agents: any[]): any[] {
  const placed = new Set<string>();
  const order: any[] = [];
  let remaining = agents.map((a) => ({ ...a, depends_on: a.depends_on || [] }));
  let guard = 0;
  while (remaining.length && guard++ < 100) {
    const layer = remaining.filter((a) => a.depends_on.every((d: string) => placed.has(d)));
    if (!layer.length) { order.push(...remaining); break; }
    layer.forEach((a) => { placed.add(a.id); order.push(a); });
    remaining = remaining.filter((a) => !placed.has(a.id));
  }
  return order;
}

export function ReplayPlayer({ ecosystemId, runName, onClose, autoClose }: { ecosystemId: string; runName: string; onClose: () => void; autoClose?: boolean }) {
  const [config, setConfig] = useState<any>(null);
  const [detail, setDetail] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const [sceneI, setSceneI] = useState(0);
  const [step, setStep] = useState(0);
  const [playing, setPlaying] = useState(true);
  const [speed, setSpeed] = useState(1);

  useEffect(() => {
    let stop = false;
    Promise.all([
      apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(ecosystemId)}`).then((r) => r.json()),
      apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(ecosystemId)}/runs/${encodeURIComponent(runName)}`).then((r) => r.json()),
    ]).then(([cfg, det]) => {
      if (stop) return;
      setConfig(cfg); setDetail(det); setLoading(false);
    }).catch((e) => { if (!stop) { setErr(String(e)); setLoading(false); } });
    return () => { stop = true; };
  }, [ecosystemId, runName]);

  const builderMsgs: Msg[] = config?._build_transcript?.messages || [];
  const missionMsgs: Msg[] = detail?.mission_transcript?.messages || [];
  const builderRecon = !!config?._build_transcript?.reconstructed;
  const missionRecon = !!detail?.mission_transcript?.reconstructed;
  const execOrder = useMemo(() => topoOrder(config?.agents || []), [config]);

  const failedIds = useMemo(() => new Set((detail?.failed_agents || []).map((f: any) => f.agent_id)), [detail]);
  const skippedIds = useMemo(() => new Set(detail?.skipped_agents || []), [detail]);
  const previewFor = (id: string) => (detail?.agents || []).find((a: any) => a.agent_id === id)?.output_preview || "";

  // Assemble the active scenes (skip empty transcripts; execution + deliverable always).
  const scenes = useMemo(() => {
    const s: SceneKey[] = [];
    if (builderMsgs.length) s.push("builder");
    if (missionMsgs.length) s.push("mission");
    if (execOrder.length) s.push("execution");
    s.push("deliverable");
    return s;
  }, [builderMsgs.length, missionMsgs.length, execOrder.length]);

  const scene: SceneKey | undefined = scenes[sceneI];
  const total =
    scene === "builder" ? builderMsgs.length :
    scene === "mission" ? missionMsgs.length :
    scene === "execution" ? execOrder.length : 0;
  const sceneDone = scene === "deliverable" || step >= total;

  // Auto-advance: step within a scene, then to the next scene. Deliverable is terminal.
  useEffect(() => {
    if (loading || !playing || !scene) return;
    if (!sceneDone) {
      const base = scene === "execution" ? 1000 : 2000;
      const t = setTimeout(() => setStep((s) => s + 1), base / speed);
      return () => clearTimeout(t);
    }
    if (sceneI < scenes.length - 1) {
      const t = setTimeout(() => { setSceneI((i) => i + 1); setStep(0); }, 1400 / speed);
      return () => clearTimeout(t);
    }
    // Final scene (deliverable) done: when auto-driven (presenter/concierge), dwell then
    // close so the show continues. Standalone replay lingers (autoClose off).
    if (autoClose) {
      const t = setTimeout(onClose, 9000 / speed);
      return () => clearTimeout(t);
    }
  }, [loading, playing, scene, sceneDone, step, sceneI, scenes.length, speed, autoClose, onClose]);

  // Synthetic events for the execution DAG: completed agents + the one "running".
  const execEvents = useMemo(() => {
    if (scene !== "execution" && !(scenes.includes("execution") && sceneI > scenes.indexOf("execution"))) {
      // Before execution scene: no events. After it (deliverable): show all complete.
      if (scene === "deliverable" && scenes.includes("execution")) {
        return execOrder.map((a) => ({ type: "STATE_DELTA", agent: a.id, status: failedIds.has(a.id) ? "failed" : skippedIds.has(a.id) ? "skipped" : "complete" }));
      }
      return [];
    }
    const evs: any[] = [];
    for (let i = 0; i < Math.min(step, execOrder.length); i++) {
      const a = execOrder[i];
      evs.push({ type: "STATE_DELTA", agent: a.id, status: failedIds.has(a.id) ? "failed" : skippedIds.has(a.id) ? "skipped" : "complete" });
    }
    if (step < execOrder.length) evs.push({ type: "STATE_DELTA", agent: execOrder[step].id, status: "running" });
    return evs;
  }, [scene, step, execOrder, failedIds, skippedIds, scenes, sceneI]);

  const sceneMeta: Record<SceneKey, { n: string; title: string }> = {
    builder: { n: "①", title: "Building the ecosystem" },
    mission: { n: "②", title: "Shaping the mission" },
    execution: { n: "③", title: "Running the mission" },
    deliverable: { n: "④", title: "The deliverable" },
  };

  const restart = () => { setSceneI(0); setStep(0); setPlaying(true); };

  return (
    <div className="fixed inset-0 bg-black/85 z-[90] flex flex-col" onClick={onClose}>
      <div className="flex-1 flex flex-col max-w-5xl w-full mx-auto p-6 min-h-0" onClick={(e) => e.stopPropagation()}>
        {/* Header */}
        <div className="flex items-center justify-between mb-3">
          <div>
            <div className="text-xs text-tessera-accent uppercase tracking-widest">Replay</div>
            <h2 className="text-lg font-bold text-white">{config?.ecosystem?.name || ecosystemId}</h2>
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-xl">✕</button>
        </div>

        {/* Scene progress */}
        <div className="flex items-center gap-2 mb-4">
          {scenes.map((s, i) => (
            <div key={s} className={`flex-1 h-1 rounded-full transition-all ${i < sceneI ? "bg-tessera-accent" : i === sceneI ? "bg-tessera-accent/60" : "bg-gray-700"}`} />
          ))}
        </div>

        {loading ? (
          <div className="flex-1 flex items-center justify-center text-gray-400"><TesseraSpinner /> <span className="ml-2">Loading replay…</span></div>
        ) : err ? (
          <div className="flex-1 flex items-center justify-center text-red-300 text-sm">{err}</div>
        ) : (
          <>
            {scene && (
              <div className="mb-3">
                <span className="text-tessera-accent text-lg mr-2">{sceneMeta[scene].n}</span>
                <span className="text-white font-semibold">{sceneMeta[scene].title}</span>
                {((scene === "builder" && builderRecon) || (scene === "mission" && missionRecon)) && (
                  <span className="ml-2 text-[9px] uppercase tracking-wide px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/30">✨ Reconstructed</span>
                )}
              </div>
            )}

            <div className="flex-1 min-h-0 overflow-y-auto bg-tessera-card border border-tessera-border rounded-xl p-4">
              {/* Chat scenes */}
              {(scene === "builder" || scene === "mission") && (
                <div className="space-y-3">
                  {(scene === "builder" ? builderMsgs : missionMsgs).slice(0, Math.max(step, 1)).map((m, i) => (
                    <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                      <div className={`max-w-[80%] rounded-lg px-3 py-2 text-xs leading-relaxed whitespace-pre-wrap ${m.role === "user" ? "bg-tessera-accent/15 border border-tessera-accent/25 text-gray-100" : "bg-gray-800/60 border border-tessera-border text-gray-300"}`}>
                        <div className="text-[9px] uppercase tracking-wide text-gray-500 mb-1">{m.role === "user" ? "You" : "Assistant"}</div>
                        {m.content}
                      </div>
                    </div>
                  ))}
                </div>
              )}

              {/* Execution scene */}
              {scene === "execution" && (
                <div>
                  <div className="overflow-x-auto"><DagMap config={config} events={execEvents} focusedAgent={execOrder[Math.min(step, execOrder.length - 1)]?.id || null} onFocusAgent={() => {}} /></div>
                  {step < execOrder.length && (
                    <div className="mt-3 border-t border-tessera-border pt-3">
                      <div className="text-xs text-blue-300 font-medium mb-1">▶ {execOrder[step]?.name || execOrder[step]?.id}</div>
                      <p className="text-[11px] text-gray-400 line-clamp-3">{previewFor(execOrder[step]?.id) || "…"}</p>
                    </div>
                  )}
                </div>
              )}

              {/* Deliverable scene */}
              {scene === "deliverable" && (
                <OutcomePanel outcome={detail?.outcome} sessionId={runName} />
              )}
            </div>

            {/* Controls */}
            <div className="flex items-center justify-center gap-3 mt-4">
              <button onClick={() => { setStep(0); setSceneI((i) => Math.max(0, i - 1)); }} className="text-gray-400 hover:text-white text-sm px-2" title="Previous scene">⏮</button>
              <button onClick={() => setPlaying((p) => !p)} className="px-4 py-2 rounded-lg bg-tessera-accent/20 text-tessera-accent hover:bg-tessera-accent/30 text-sm font-semibold">{playing ? "⏸ Pause" : "▶ Play"}</button>
              <button onClick={() => { if (sceneI < scenes.length - 1) { setSceneI((i) => i + 1); setStep(0); } else { setStep(total); } }} className="text-gray-400 hover:text-white text-sm px-2" title="Next scene">⏭</button>
              <button onClick={() => setSpeed((s) => (s === 1 ? 2 : s === 2 ? 4 : 1))} className="text-gray-400 hover:text-white text-xs border border-tessera-border rounded px-2 py-1" title="Speed">{speed}×</button>
              {sceneI === scenes.length - 1 && sceneDone && (
                <button onClick={restart} className="text-gray-400 hover:text-white text-xs border border-tessera-border rounded px-2 py-1">↺ Replay</button>
              )}
            </div>
          </>
        )}
      </div>
    </div>
  );
}
