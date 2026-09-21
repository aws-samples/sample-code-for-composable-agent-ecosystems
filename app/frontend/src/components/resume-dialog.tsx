"use client";

import { useMemo, useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "";

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

export interface ResumeAgent {
  id: string;
  name?: string;
  depends_on?: string[];
}

/**
 * Client-side mirror of the compiler's compute_layers (depends_on → execution
 * layers). Used only for the reuse/re-run PREVIEW; the backend recomputes the
 * authoritative split from the deployed ASL when the resume actually runs.
 */
function computeLayers(agents: ResumeAgent[]): ResumeAgent[][] {
  const placed = new Set<string>();
  const layers: ResumeAgent[][] = [];
  let remaining = [...agents];
  let guard = 0;
  while (remaining.length && guard++ < 100) {
    let layer = remaining.filter((a) => (a.depends_on || []).every((d) => placed.has(d)));
    if (layer.length === 0) layer = remaining; // dependency deadlock fallback
    layers.push(layer);
    layer.forEach((a) => placed.add(a.id));
    remaining = remaining.filter((a) => !placed.has(a.id));
  }
  return layers;
}

/**
 * Resume a DAG from a selected node — a NEW run that reuses every completed
 * upstream layer of the prior run and re-executes from the chosen node onward.
 * Gate-style: shows what's reused vs re-run, and (like a quality gate) lets the
 * operator provide corrective guidance that's injected into the re-run agents.
 */
export function ResumeDialog({
  ecoId,
  sessionId,
  agents,
  selectedAgentId,
  onClose,
  onResumeStarted,
}: {
  ecoId: string;
  sessionId: string;
  agents: ResumeAgent[];
  selectedAgentId: string;
  onClose: () => void;
  onResumeStarted: (newSessionId: string) => void;
}) {
  const [guidance, setGuidance] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { reused, rerun, selectedName } = useMemo(() => {
    const layers = computeLayers(agents);
    let selLayer = layers.findIndex((l) => l.some((a) => a.id === selectedAgentId));
    if (selLayer < 0) selLayer = 0;
    const reused: ResumeAgent[] = [];
    const rerun: ResumeAgent[] = [];
    layers.forEach((l, i) => (i < selLayer ? reused : rerun).push(...l));
    const sel = agents.find((a) => a.id === selectedAgentId);
    return { reused, rerun, selectedName: sel?.name || selectedAgentId };
  }, [agents, selectedAgentId]);

  const confirm = async () => {
    setSubmitting(true);
    setError(null);
    try {
      const res = await apiFetch(
        `${API_URL}/api/ecosystems/${encodeURIComponent(ecoId)}/runs/${encodeURIComponent(sessionId)}/resume`,
        {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ resume_from: selectedAgentId, guidance: guidance.trim() }),
        },
      );
      const data = await res.json().catch(() => ({}));
      if (res.ok && data.session_id) {
        setResult(`Resuming from ${selectedName} — reusing ${data.reused_agents?.length ?? reused.length} step(s), re-running ${data.rerun_agents?.length ?? rerun.length}…`);
        setTimeout(() => onResumeStarted(data.session_id), 700);
      } else {
        setError(data.error || `Resume failed (${res.status})${data.missing ? ` — missing: ${data.missing.join(", ")}` : ""}`);
        setSubmitting(false);
      }
    } catch (e) {
      setError(`Request failed: ${e}`);
      setSubmitting(false);
    }
  };

  const tag = (a: ResumeAgent) => a.name || a.id;

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-[60]" onClick={onClose}>
      <div className="bg-tessera-card border border-purple-500/40 rounded-xl w-[600px] max-w-[92vw] max-h-[85vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center gap-2 px-5 py-3 border-b border-tessera-border">
          <span className="text-purple-400">↻</span>
          <h3 className="text-sm font-bold text-purple-200">Resume from <span className="text-white">{selectedName}</span></h3>
        </div>

        <div className="flex-1 overflow-y-auto px-5 py-3 space-y-3">
          <p className="text-xs text-gray-400">
            Starts a <strong className="text-gray-200">new run</strong> that reuses this run&apos;s completed upstream steps and
            re-executes from <strong className="text-gray-200">{selectedName}</strong> onward. You only pay for the re-run steps.
            Checkpoints after this point will re-evaluate.
          </p>

          <div className="grid grid-cols-2 gap-3">
            <div className="rounded border border-green-500/30 bg-green-500/5 p-2.5">
              <div className="text-[11px] font-bold text-green-300 mb-1">✓ Reused ({reused.length})</div>
              <div className="text-[10px] text-gray-400 max-h-[120px] overflow-y-auto leading-relaxed">
                {reused.length ? reused.map((a) => <div key={a.id} className="truncate">{tag(a)}</div>) : <span className="italic">none — full re-run</span>}
              </div>
            </div>
            <div className="rounded border border-amber-500/30 bg-amber-500/5 p-2.5">
              <div className="text-[11px] font-bold text-amber-300 mb-1">↻ Re-run ({rerun.length})</div>
              <div className="text-[10px] text-gray-400 max-h-[120px] overflow-y-auto leading-relaxed">
                {rerun.map((a) => <div key={a.id} className={`truncate ${a.id === selectedAgentId ? "text-amber-200 font-semibold" : ""}`}>{tag(a)}</div>)}
              </div>
            </div>
          </div>

          <div>
            <label className="text-[11px] font-semibold text-gray-300">Guidance for the re-run <span className="text-gray-500 font-normal">(optional — like a gate)</span></label>
            <textarea
              value={guidance}
              onChange={(e) => setGuidance(e.target.value)}
              disabled={submitting || !!result}
              rows={3}
              placeholder="e.g. The deck truncated last time — build it as a real slide file. Tighten Act 2 to 10 slides."
              className="mt-1 w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white placeholder-gray-600 disabled:opacity-40"
            />
            <p className="text-[10px] text-gray-500 mt-1">Injected into every re-run agent as authoritative operator guidance (carried alongside any prior gate decisions).</p>
          </div>

          {error && <div className="text-[11px] text-red-300 bg-red-500/10 border border-red-500/30 rounded p-2">{error}</div>}
          {result && <div className="text-[11px] text-purple-200 bg-purple-500/10 border border-purple-500/30 rounded p-2">{result}</div>}
        </div>

        <div className="flex gap-2 px-5 py-3 border-t border-tessera-border">
          <button onClick={onClose} disabled={submitting || !!result} className="px-3 py-2 text-xs rounded border border-tessera-border text-gray-300 hover:bg-white/5 disabled:opacity-30">Cancel</button>
          <button onClick={confirm} disabled={submitting || !!result} className="ml-auto px-3 py-2 text-xs font-semibold rounded bg-purple-500/25 text-purple-100 border border-purple-500/50 hover:bg-purple-500/35 disabled:opacity-30">
            {submitting ? "Starting…" : `↻ Resume from ${selectedName}`}
          </button>
        </div>
      </div>
    </div>
  );
}
