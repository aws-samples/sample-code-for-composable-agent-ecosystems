"use client";

// EvaluationDashboard — the top-level "Evaluation" section reachable from the
// left nav. Flow: pick an ecosystem → a polished dashboard shows
//   (a) the latest eval SCORECARD (reuses ScorecardPanel)
//   (b) the ecosystem's `evaluation:` PARAMETERS (metrics + gate + replicates + online)
//   (c) the curated GOLDEN SET (name, archetype, inputs, expected)
//   (d) a "Run a fresh eval" button that launches an eval run and polls to completion.
//
// Eco-AGNOSTIC: renders whatever generic metrics/cases the eco carries — no
// domain vocabulary. All endpoints already exist except the golden_set artifact
// branch (added to lambdas/get-ecosystem/handler.py).

import { useState, useEffect, useCallback, useRef } from "react";
import { ScorecardPanel, type Scorecard, type GateOutcome } from "@/components/scorecard-panel";
import { useBackStack } from "@/hooks/use-back-stack";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

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

// Generic scorer descriptors — domain-blind. Mirrors the deploy-wizard's
// EvaluationStep so the badge + legend styling is identical across the app.
const SCORER_INFO: Record<string, { label: string; color: string; desc: string }> = {
  categorical:       { label: "accuracy",     color: "bg-sky-500/15 text-sky-300 border-sky-500/30",             desc: "exact match on a labelled output field" },
  numeric_tolerance: { label: "numeric",      color: "bg-teal-500/15 text-teal-300 border-teal-500/30",          desc: "value within a tolerance" },
  set_recall:        { label: "coverage",     color: "bg-indigo-500/15 text-indigo-300 border-indigo-500/30",    desc: "required points are present" },
  citation_resolves: { label: "faithfulness", color: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30", desc: "every claim traces to real evidence" },
  abstention:        { label: "abstention",   color: "bg-amber-500/15 text-amber-300 border-amber-500/30",       desc: "correctly holds/refers when evidence is thin" },
  bedrock_judge:     { label: "LLM judge",    color: "bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-500/30", desc: "independent model scores narrative quality" },
};
const scorerBadge = (s: string) => SCORER_INFO[s] || { label: s, color: "bg-gray-500/15 text-gray-300 border-gray-500/30", desc: "" };

interface EcoLite { id: string; name: string; description?: string; agents?: number; domain?: string; strategy?: string }

interface EvalMetric {
  id: string;
  scorer?: string;
  field?: string;
  builtin?: string[];
  custom_rubric?: string | Record<string, unknown>;
  expected_value?: string | number | boolean;
}
interface EvalBlock {
  replicates?: number;
  run_mode?: string;
  trajectory?: boolean;
  metrics?: EvalMetric[];
  thresholds?: Record<string, number>;
  gate?: string;
  online?: { enabled?: boolean; baseline_sample_rate?: number; always_eval_when?: unknown[] };
}
interface GoldenCase {
  name?: string;
  mission?: string;
  inputs?: Record<string, unknown>;
  expected?: Record<string, unknown>;
  labels?: { archetype?: string; reviewed?: boolean; [k: string]: unknown };
}

export function EvaluationDashboard() {
  const [ecosystems, setEcosystems] = useState<EcoLite[]>([]);
  const [loading, setLoading] = useState(true);
  const [selected, setSelected] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const { pushStep } = useBackStack();

  useEffect(() => {
    apiFetch(`${API_URL}/api/ecosystems`)
      .then((r) => r.json())
      .then((d) => setEcosystems(Array.isArray(d) ? d : (d.ecosystems || [])))
      .catch(() => setEcosystems([]))
      .finally(() => setLoading(false));
  }, []);

  // Register a Back step when an ecosystem's evaluation is opened, so browser Back returns
  // to the evaluation list (not out of the whole Evaluation view). Disposer runs when
  // `selected` clears (in-app "← Back") or on unmount, keeping browser history in sync.
  useEffect(() => {
    if (!selected) return;
    const dispose = pushStep(() => setSelected(null));
    return dispose;
  }, [selected, pushStep]);

  const selectedEco = ecosystems.find((e) => e.id === selected) || null;

  if (selected) {
    return (
      <EvaluationDetail
        ecoId={selected}
        ecoName={selectedEco?.name || selected}
        onBack={() => setSelected(null)}
      />
    );
  }

  const filtered = ecosystems.filter((e) => {
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (!terms.length) return true;
    const hay = [e.name, e.description, e.domain, e.strategy].join(" ").toLowerCase();
    return terms.every((t) => hay.includes(t));
  });

  return (
    <div>
      <div className="mb-1 flex items-center gap-2">
        <span className="text-2xl">🎯</span>
        <h2 className="text-2xl font-bold">Evaluation</h2>
      </div>
      <p className="text-gray-500 text-sm mb-5">Choose an ecosystem to view its evaluation suite, golden set, and latest scorecard — or run a fresh evaluation.</p>

      {loading ? (
        <div className="flex flex-col items-center justify-center py-16 gap-3">
          <div className="animate-spin rounded-full h-8 w-8 border-2 border-gray-700 border-t-tessera-accent" />
          <p className="text-xs text-gray-500">Loading ecosystems…</p>
        </div>
      ) : (
        <>
          <input
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Search ecosystems…"
            className="w-full md:w-80 bg-tessera-card border border-tessera-border rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 mb-4"
          />
          {filtered.length === 0 ? (
            <div className="bg-tessera-card border border-tessera-border rounded-xl p-6 text-center text-sm text-gray-500">No ecosystems match your search.</div>
          ) : (
            <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
              {filtered.map((eco) => (
                <button
                  key={eco.id}
                  onClick={() => setSelected(eco.id)}
                  className="bg-tessera-card rounded-xl text-left p-4 border border-tessera-border hover:border-tessera-accent hover:shadow-lg hover:shadow-tessera-accent/10 transition group"
                >
                  <div className="flex items-start justify-between mb-2">
                    <span className="text-xs bg-tessera-accent/10 text-tessera-accent px-2 py-0.5 rounded-full">{eco.strategy || "ecosystem"}</span>
                    {typeof eco.agents === "number" && <span className="text-xs text-gray-600">{eco.agents} agents</span>}
                  </div>
                  <h3 className="font-semibold text-white text-sm mb-1">{eco.name}</h3>
                  <p className="text-xs text-gray-400 leading-relaxed line-clamp-2">{eco.description}</p>
                  <span className="inline-block mt-2 text-[11px] text-tessera-accent group-hover:underline">View evaluation →</span>
                </button>
              ))}
            </div>
          )}
        </>
      )}
    </div>
  );
}

function SectionCard({ title, subtitle, accessory, children }: { title: string; subtitle?: string; accessory?: React.ReactNode; children: React.ReactNode }) {
  return (
    <div className="bg-tessera-card border border-tessera-border rounded-xl overflow-hidden">
      <div className="flex items-center justify-between px-5 py-3.5 border-b border-tessera-border bg-white/[0.02]">
        <div>
          <h3 className="text-sm font-bold text-white">{title}</h3>
          {subtitle && <p className="text-[11px] text-gray-500 mt-0.5">{subtitle}</p>}
        </div>
        {accessory}
      </div>
      <div className="p-5">{children}</div>
    </div>
  );
}

function EmptyState({ icon, title, hint }: { icon: string; title: string; hint?: string }) {
  return (
    <div className="text-center py-8">
      <div className="text-3xl mb-2 opacity-70">{icon}</div>
      <p className="text-sm text-gray-400">{title}</p>
      {hint && <p className="text-xs text-gray-600 mt-1">{hint}</p>}
    </div>
  );
}

function EvaluationDetail({ ecoId, ecoName, onBack }: { ecoId: string; ecoName: string; onBack: () => void }) {
  const [config, setConfig] = useState<Record<string, unknown> | null>(null);
  const [scorecard, setScorecard] = useState<Scorecard | null>(null);
  const [scoreGate, setScoreGate] = useState<GateOutcome | null>(null);
  const [scorecardLoading, setScorecardLoading] = useState(true);
  const [goldenCases, setGoldenCases] = useState<GoldenCase[] | null>(null);
  const [goldenVersion, setGoldenVersion] = useState<string | null>(null);
  const [goldenLoading, setGoldenLoading] = useState(true);
  const [configLoading, setConfigLoading] = useState(true);

  // Run-a-fresh-eval state
  const [runPhase, setRunPhase] = useState<"idle" | "launching" | "running" | "done" | "error">("idle");
  const [runStatus, setRunStatus] = useState<{ name?: string; status?: string } | null>(null);
  const [runError, setRunError] = useState<string | null>(null);
  // Per-run OVERRIDE controls (Quick-run). Defaults are hydrated from the eco's
  // stored evaluation params once the config loads; overrides are PER-RUN only —
  // launching with them does NOT mutate the stored evaluation block.
  const [ovrReplicates, setOvrReplicates] = useState<number>(1);
  const [ovrCases, setOvrCases] = useState<number>(1);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const loadScorecard = useCallback(async () => {
    setScorecardLoading(true);
    try {
      const r = await apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(ecoId)}?artifact=scorecard`);
      if (r.ok) {
        const d = await r.json();
        setScorecard(d.scorecard || d);
        setScoreGate(d.gate || null);
      } else {
        setScorecard(null);
        setScoreGate(null);
      }
    } catch {
      setScorecard(null);
    } finally {
      setScorecardLoading(false);
    }
  }, [ecoId]);

  useEffect(() => {
    apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(ecoId)}`)
      .then((r) => r.json())
      .then(setConfig)
      .catch(() => setConfig(null))
      .finally(() => setConfigLoading(false));
    loadScorecard();
    apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(ecoId)}?artifact=golden_set`)
      .then((r) => (r.ok ? r.json() : { cases: [] }))
      .then((d) => { setGoldenCases(d.cases || []); setGoldenVersion(d.version || null); })
      .catch(() => setGoldenCases([]))
      .finally(() => setGoldenLoading(false));
  }, [ecoId, loadScorecard]);

  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  const evalBlock = (config?.evaluation as EvalBlock | undefined) || null;
  const hasEval = !!evalBlock && (((evalBlock.metrics || []).length > 0) || !!evalBlock.gate);
  const storedReplicates = typeof evalBlock?.replicates === "number" ? evalBlock!.replicates! : 1;
  const goldenCount = goldenCases?.length ?? 0;

  // Hydrate the override inputs from the eco's stored params once known. These
  // are the DEFAULTS the user can tweak per-run; they never write back.
  useEffect(() => {
    setOvrReplicates(storedReplicates);
  }, [storedReplicates]);
  useEffect(() => {
    if (goldenCount > 0) setOvrCases(goldenCount);
  }, [goldenCount]);

  const startPolling = useCallback(() => {
    if (pollRef.current) clearInterval(pollRef.current);
    const tick = async () => {
      try {
        const r = await apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(ecoId)}/runs/_eval/status`);
        const d = await r.json();
        setRunStatus(d.latest || null);
        if (d.scorecard_present) {
          await loadScorecard();
          setRunPhase("done");
          if (pollRef.current) clearInterval(pollRef.current);
        } else if (d.latest?.status && d.latest.status !== "RUNNING" && d.latest.status !== "SUCCEEDED") {
          setRunPhase("done");
          if (pollRef.current) clearInterval(pollRef.current);
        }
      } catch { /* transient */ }
    };
    tick();
    pollRef.current = setInterval(tick, 5000);
  }, [ecoId, loadScorecard]);

  // Launch an eval run. `overrides` is PER-RUN only (Quick-run controls) — it is
  // posted as the JSON body and never mutates the stored evaluation block. When
  // omitted / empty, the full stored config is used (backward-compatible).
  const launchEval = useCallback(async (overrides?: { replicates?: number; max_cases?: number }) => {
    setRunError(null);
    setRunPhase("launching");
    try {
      const hasOverrides = overrides && (overrides.replicates != null || overrides.max_cases != null);
      const r = await apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(ecoId)}/runs/_eval/launch`, {
        method: "POST",
        ...(hasOverrides ? { headers: { "Content-Type": "application/json" }, body: JSON.stringify(overrides) } : {}),
      });
      const d = await r.json().catch(() => ({}));
      if (!r.ok || !d.started) throw new Error(d.error || `launch failed (HTTP ${r.status})`);
      setRunPhase("running");
      startPolling();
    } catch (e) {
      setRunError(e instanceof Error ? e.message : String(e));
      setRunPhase("error");
    }
  }, [ecoId, startPolling]);

  const runFreshEval = () => launchEval();
  const runQuickEval = () => launchEval({ replicates: 1, max_cases: 1 });
  const runCustomEval = () => launchEval({ replicates: ovrReplicates, max_cases: ovrCases });

  const running = runPhase === "launching" || runPhase === "running";

  return (
    <div className="max-w-5xl mx-auto pb-10">
      {/* Header band */}
      <div className="tessera-gradient-band mb-5 rounded-xl border border-tessera-border bg-gradient-to-br from-tessera-accent/10 to-transparent p-5">
        <button onClick={onBack} className="text-xs text-gray-400 hover:text-white mb-3">← All ecosystems</button>
        <div className="flex items-center justify-between gap-4 flex-wrap">
          <div className="flex items-center gap-3 min-w-0">
            <div className="w-11 h-11 rounded-[13px] bg-tessera-accent/15 border border-tessera-accent/35 flex items-center justify-center text-[22px] shrink-0">🎯</div>
            <div className="min-w-0">
              <h2 className="text-xl font-bold text-white truncate">{ecoName}</h2>
              <p className="text-[12px] text-gray-400 font-mono truncate">{ecoId}</p>
            </div>
          </div>
          <button
            onClick={runFreshEval}
            disabled={running || !hasEval}
            title={!hasEval ? "This ecosystem has no evaluation suite yet — set one up in the Builder." : "Launch a fresh evaluation run"}
            className="shrink-0 px-4 py-2.5 bg-tessera-gradient rounded-lg text-sm font-semibold hover:opacity-90 disabled:opacity-30 disabled:cursor-not-allowed transition shadow-lg shadow-purple-500/20 flex items-center gap-2"
          >
            {running ? (
              <><span className="w-3.5 h-3.5 border-2 border-white/40 border-t-white rounded-full animate-spin" /> {runPhase === "launching" ? "Starting…" : "Running…"}</>
            ) : (
              <>▶ Run a fresh eval</>
            )}
          </button>
        </div>

        {/* Per-run override controls (Quick-run). These are PER-RUN only — they do
            NOT mutate the ecosystem's stored evaluation block. */}
        {hasEval && (
          <div className="mt-4 flex flex-wrap items-end gap-3 rounded-lg border border-tessera-border bg-gray-900/40 px-4 py-3">
            <button
              onClick={runQuickEval}
              disabled={running}
              title="Run a minimal smoke test: 1 golden case × 1 replicate = 1 pipeline run."
              className="shrink-0 px-3.5 py-2 bg-amber-500/15 text-amber-200 border border-amber-500/40 rounded-lg text-sm font-semibold hover:bg-amber-500/25 disabled:opacity-30 disabled:cursor-not-allowed transition flex items-center gap-1.5"
            >
              ⚡ Quick run (1 case × 1 replicate)
            </button>
            <div className="flex flex-col gap-1">
              <label className="text-[10px] uppercase tracking-wide text-gray-500">Replicates</label>
              <input
                type="number"
                min={1}
                max={10}
                value={ovrReplicates}
                onChange={(e) => setOvrReplicates(Math.max(1, Math.min(10, Number(e.target.value) || 1)))}
                disabled={running}
                className="w-20 bg-tessera-card border border-tessera-border rounded-md px-2 py-1.5 text-sm text-white disabled:opacity-40"
              />
            </div>
            <div className="flex flex-col gap-1">
              <label className="text-[10px] uppercase tracking-wide text-gray-500"># Golden cases</label>
              <input
                type="number"
                min={1}
                max={goldenCount > 0 ? goldenCount : 50}
                value={ovrCases}
                onChange={(e) => setOvrCases(Math.max(1, Math.min(50, Number(e.target.value) || 1)))}
                disabled={running}
                className="w-20 bg-tessera-card border border-tessera-border rounded-md px-2 py-1.5 text-sm text-white disabled:opacity-40"
              />
            </div>
            <button
              onClick={runCustomEval}
              disabled={running}
              title="Launch a custom-sized eval using the override values (per-run only)."
              className="shrink-0 px-3.5 py-2 bg-tessera-card border border-tessera-border rounded-lg text-sm font-medium text-gray-200 hover:border-tessera-accent disabled:opacity-30 disabled:cursor-not-allowed transition"
            >
              ▶ Run with these
            </button>
            <span className="text-[11px] text-gray-500 self-center">
              will run {ovrCases} case{ovrCases === 1 ? "" : "s"} × {ovrReplicates} replicate{ovrReplicates === 1 ? "" : "s"} = {ovrCases * ovrReplicates} pipeline run{ovrCases * ovrReplicates === 1 ? "" : "s"}
              <span className="text-gray-600"> · stored default: {goldenCount || "?"} × {storedReplicates} (unchanged)</span>
            </span>
          </div>
        )}
        {/* Run progress / result banner */}
        {runPhase !== "idle" && (
          <div className={`mt-4 text-xs rounded-lg px-3 py-2 border ${
            runPhase === "error" ? "bg-red-500/10 border-red-500/30 text-red-300" :
            runPhase === "done" ? "bg-emerald-500/10 border-emerald-500/30 text-emerald-300" :
            "bg-tessera-accent/10 border-tessera-accent/30 text-gray-200"
          }`}>
            {runPhase === "error" && <>⚠ {runError}</>}
            {runPhase === "launching" && <>Starting the Evaluation State Machine…</>}
            {runPhase === "running" && <>Evaluation running — scoring golden cases{runStatus?.name ? <span className="font-mono text-gray-400"> · {runStatus.name} · {runStatus.status}</span> : "…"}. This can take a few minutes; the scorecard refreshes automatically when ready.</>}
            {runPhase === "done" && (scorecard ? <>✓ Evaluation complete — the scorecard below has been refreshed.</> : <>Evaluation finished ({runStatus?.status || "unknown"}) but no scorecard was produced. Check the eval run in Step Functions.</>)}
          </div>
        )}
      </div>

      <div className="space-y-5">
        {/* (a) SCORECARD */}
        <SectionCard title="Latest scorecard" subtitle="Most recent evaluation result — median ± band vs threshold, per-case miss-rate, and deploy gate.">
          {scorecardLoading ? (
            <div className="flex items-center gap-2 text-sm text-gray-500 py-4"><span className="w-4 h-4 border-2 border-gray-700 border-t-tessera-accent rounded-full animate-spin" /> Loading scorecard…</div>
          ) : scorecard ? (
            <div className="rounded-lg overflow-hidden">
              <ScorecardPanel scorecard={scorecard} gate={scoreGate} />
            </div>
          ) : (
            <EmptyState icon="📊" title="No scorecard yet" hint={hasEval ? "Run a fresh eval above to produce the first scorecard." : "Set up an evaluation suite in the Builder first."} />
          )}
        </SectionCard>

        {/* (b) PARAMETERS */}
        <SectionCard
          title="Evaluation parameters"
          subtitle="The metrics, thresholds and gate this ecosystem is scored against."
          accessory={evalBlock ? (
            <div className="flex items-center gap-2 text-[10px]">
              {typeof evalBlock.replicates === "number" && <span className="px-2 py-0.5 rounded-full bg-gray-700/50 text-gray-300 border border-tessera-border">×{evalBlock.replicates} replicates</span>}
              {evalBlock.gate && <GatePill gate={evalBlock.gate} />}
            </div>
          ) : undefined}
        >
          {configLoading ? (
            <div className="flex items-center gap-2 text-sm text-gray-500 py-4"><span className="w-4 h-4 border-2 border-gray-700 border-t-tessera-accent rounded-full animate-spin" /> Loading parameters…</div>
          ) : !hasEval ? (
            <EmptyState icon="⚙️" title="No evaluation suite configured" hint="Open this ecosystem in the Builder → Save & Deploy → Evaluation to propose and approve a suite." />
          ) : (
            <ParametersView evalBlock={evalBlock!} />
          )}
        </SectionCard>

        {/* (c) GOLDEN SET */}
        <SectionCard
          title="Golden set"
          subtitle="The curated cases with known-correct expectations that the ecosystem is scored against."
          accessory={goldenVersion ? <span className="text-[10px] font-mono px-2 py-0.5 rounded-full bg-gray-700/50 text-gray-300 border border-tessera-border">{goldenVersion}</span> : undefined}
        >
          {goldenLoading ? (
            <div className="flex items-center gap-2 text-sm text-gray-500 py-4"><span className="w-4 h-4 border-2 border-gray-700 border-t-tessera-accent rounded-full animate-spin" /> Loading golden set…</div>
          ) : !goldenCases || goldenCases.length === 0 ? (
            <EmptyState icon="✨" title="No golden set yet" hint="Approving an evaluation suite in the Builder writes the golden cases here." />
          ) : (
            <GoldenSetView cases={goldenCases} />
          )}
        </SectionCard>
      </div>
    </div>
  );
}

function GatePill({ gate }: { gate: string }) {
  const g = String(gate).toLowerCase();
  const style = g === "block" ? "bg-red-500/15 text-red-300 border-red-500/30"
    : g === "warn" ? "bg-amber-500/15 text-amber-300 border-amber-500/30"
    : "bg-gray-500/15 text-gray-300 border-gray-500/30";
  return <span className={`px-2 py-0.5 rounded-full border font-semibold uppercase tracking-wide ${style}`}>gate: {g}</span>;
}

function ParametersView({ evalBlock }: { evalBlock: EvalBlock }) {
  const metrics = evalBlock.metrics || [];
  const thresholds = evalBlock.thresholds || {};
  const usedScorers = Array.from(new Set(metrics.map((m) => m.scorer))).filter(Boolean) as string[];

  const targetFor = (m: EvalMetric): string => {
    if (m.expected_value != null) return `= ${String(m.expected_value)}`;
    if (thresholds[m.id] != null) return `≥ ${thresholds[m.id]}`;
    return "—";
  };
  const fieldFor = (m: EvalMetric): string => {
    if (m.field) return m.field;
    if (m.builtin && m.builtin.length) return m.builtin.join(", ");
    if (m.custom_rubric) return "narrative rubric";
    return "—";
  };

  return (
    <div>
      <div className="overflow-x-auto rounded-lg border border-tessera-border">
        <table className="w-full text-sm">
          <thead>
            <tr className="bg-white/[0.03] text-[11px] text-gray-400 uppercase tracking-wide">
              <th className="text-left px-3 py-2 font-semibold">Metric</th>
              <th className="text-left px-3 py-2 font-semibold">Scorer</th>
              <th className="text-left px-3 py-2 font-semibold">Field / builtin / rubric</th>
              <th className="text-left px-3 py-2 font-semibold">Target</th>
            </tr>
          </thead>
          <tbody>
            {metrics.map((m, i) => {
              const b = scorerBadge(m.scorer || "");
              return (
                <tr key={m.id || i} className="border-t border-tessera-border">
                  <td className="px-3 py-2.5 font-mono text-gray-200">{m.id}</td>
                  <td className="px-3 py-2.5">
                    <span className={`text-[10px] px-1.5 py-0.5 rounded border font-medium ${b.color}`} title={b.desc}>{b.label}</span>
                  </td>
                  <td className="px-3 py-2.5 text-xs text-gray-400 font-mono">{fieldFor(m)}</td>
                  <td className="px-3 py-2.5 text-xs text-gray-300 font-mono">{targetFor(m)}</td>
                </tr>
              );
            })}
            {metrics.length === 0 && (
              <tr><td colSpan={4} className="px-3 py-4 text-center text-xs text-amber-400">No metrics declared in the evaluation block.</td></tr>
            )}
          </tbody>
        </table>
      </div>

      {/* Legend of scorer types in play */}
      {usedScorers.length > 0 && (
        <div className="flex flex-wrap items-center gap-1.5 mt-3">
          <span className="text-[10px] text-gray-500 uppercase tracking-wide mr-1">Scorers:</span>
          {usedScorers.map((s) => {
            const b = scorerBadge(s);
            return <span key={s} className={`text-[9px] px-1.5 py-0.5 rounded border ${b.color}`} title={b.desc}>{b.label} — {b.desc}</span>;
          })}
        </div>
      )}

      {/* Run settings + online eval */}
      <div className="grid grid-cols-1 md:grid-cols-2 gap-3 mt-4">
        <div className="bg-gray-900/40 border border-tessera-border rounded-lg p-3">
          <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-2">Run settings</div>
          <dl className="space-y-1 text-xs">
            <ParamRow k="Replicates" v={evalBlock.replicates != null ? String(evalBlock.replicates) : "—"} />
            <ParamRow k="Run mode" v={evalBlock.run_mode || "—"} />
            <ParamRow k="Trajectory" v={evalBlock.trajectory ? "on" : "off"} />
            <ParamRow k="Deploy gate" v={evalBlock.gate || "—"} />
          </dl>
        </div>
        <div className="bg-gray-900/40 border border-tessera-border rounded-lg p-3">
          <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-2">Online evaluation</div>
          {evalBlock.online ? (
            <dl className="space-y-1 text-xs">
              <ParamRow k="Enabled" v={evalBlock.online.enabled ? "yes" : "no"} />
              <ParamRow k="Baseline sample rate" v={evalBlock.online.baseline_sample_rate != null ? String(evalBlock.online.baseline_sample_rate) : "—"} />
              {Array.isArray(evalBlock.online.always_eval_when) && evalBlock.online.always_eval_when.length > 0 && (
                <div className="pt-1">
                  <div className="text-gray-500 mb-1">Always eval when</div>
                  <div className="flex flex-wrap gap-1">
                    {evalBlock.online.always_eval_when.map((c, i) => (
                      <span key={i} className="text-[10px] font-mono px-1.5 py-0.5 rounded bg-gray-800 text-gray-300 border border-tessera-border">
                        {typeof c === "string" ? c : JSON.stringify(c)}
                      </span>
                    ))}
                  </div>
                </div>
              )}
            </dl>
          ) : (
            <p className="text-xs text-gray-600">Not configured.</p>
          )}
        </div>
      </div>
    </div>
  );
}

function ParamRow({ k, v }: { k: string; v: string }) {
  return (
    <div className="flex items-center justify-between">
      <dt className="text-gray-500">{k}</dt>
      <dd className="text-gray-200 font-mono">{v}</dd>
    </div>
  );
}

const ARCHETYPE_STYLE: Record<string, string> = {
  happy_path: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30",
  edge: "bg-sky-500/15 text-sky-300 border-sky-500/30",
  adversarial: "bg-rose-500/15 text-rose-300 border-rose-500/30",
  insufficient_evidence: "bg-amber-500/15 text-amber-300 border-amber-500/30",
};

function GoldenSetView({ cases }: { cases: GoldenCase[] }) {
  return (
    <div className="grid grid-cols-1 md:grid-cols-2 gap-3">
      {cases.map((c, i) => {
        const arch = c.labels?.archetype;
        const archStyle = (arch && ARCHETYPE_STYLE[arch]) || "bg-gray-500/15 text-gray-300 border-gray-500/30";
        const inputs = c.inputs || {};
        const expected = c.expected || {};
        return (
          <div key={c.name || i} className="bg-gray-900/40 border border-tessera-border rounded-lg p-4 flex flex-col">
            <div className="flex items-center justify-between gap-2 mb-2">
              <span className="text-sm font-semibold text-white truncate">{c.name || `case-${i + 1}`}</span>
              <div className="flex items-center gap-1.5 shrink-0">
                {arch && <span className={`text-[9px] px-1.5 py-0.5 rounded border font-medium ${archStyle}`}>{arch.replace(/_/g, " ")}</span>}
                {c.labels?.reviewed
                  ? <span className="text-[9px] px-1.5 py-0.5 rounded border bg-emerald-500/10 text-emerald-300 border-emerald-500/30">reviewed</span>
                  : <span className="text-[9px] px-1.5 py-0.5 rounded border bg-gray-700/40 text-gray-400 border-tessera-border">unreviewed</span>}
              </div>
            </div>

            {c.mission && <p className="text-xs text-gray-400 mb-3 line-clamp-3">{c.mission}</p>}

            {Object.keys(inputs).length > 0 && (
              <div className="mb-2.5">
                <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">Inputs</div>
                <div className="space-y-1">
                  {Object.entries(inputs).map(([k, v]) => (
                    <div key={k} className="text-[11px] flex gap-2">
                      <span className="text-gray-500 font-mono shrink-0">{k}:</span>
                      <span className="text-gray-300 break-words">{fmtVal(v) || <span className="text-gray-600 italic">(empty)</span>}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            {Object.keys(expected).length > 0 && (
              <div className="mt-auto pt-1">
                <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-1">Expected</div>
                <div className="flex flex-wrap gap-1.5">
                  {Object.entries(expected).map(([k, v]) => (
                    <span key={k} className="text-[10px] px-1.5 py-0.5 rounded bg-tessera-accent/10 text-tessera-accent border border-tessera-accent/25 font-mono">
                      {k} = {fmtVal(v)}
                    </span>
                  ))}
                </div>
              </div>
            )}
          </div>
        );
      })}
    </div>
  );
}

function fmtVal(v: unknown): string {
  if (v == null) return "";
  if (typeof v === "boolean") return v ? "true" : "false";
  if (typeof v === "object") return JSON.stringify(v);
  return String(v);
}
