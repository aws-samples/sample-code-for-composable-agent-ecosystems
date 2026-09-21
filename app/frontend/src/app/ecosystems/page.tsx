"use client";

// Full ecosystem details page. Static-export-safe: this is a single static route
// that reads the ecosystem id from the `?id=` query string on the client (path-based
// dynamic routes can't be pre-rendered for arbitrary runtime ids under output:"export").
//
// This page is also the reason the grid card no longer fetches sharing per-card:
// sharing (and every other detail) loads HERE, once, on explicit navigation — killing
// the ecosystems-grid N+1 that fired one /sharing request per owned card.

import { useEffect, useMemo, useState } from "react";
import Link from "next/link";
import { useAuth } from "@/components/auth-provider";
import { DagMap } from "@/components/dag-map";
import { SharingDialog } from "@/components/sharing-dialog";
import { PastRunsModal } from "@/components/past-runs-modal";
import { TranscriptModal } from "@/components/transcript-modal";
import { TesseraSpinner } from "@/components/tessera-spinner";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
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
}

interface SharingEntry {
  policyId: string;
  principal_type: string;
  principal_id: string;
  permission: string;
  email?: string;
}

const STATUS_COLOR: Record<string, string> = {
  SUCCEEDED: "text-green-400 bg-green-500/10 border-green-500/20",
  RUNNING: "text-blue-300 bg-blue-500/10 border-blue-500/20",
  FAILED: "text-red-300 bg-red-500/10 border-red-500/20",
  TIMED_OUT: "text-red-300 bg-red-500/10 border-red-500/20",
  ABORTED: "text-gray-400 bg-gray-500/10 border-gray-500/20",
};

function fmtDate(s: string | null): string {
  if (!s) return "—";
  try { return new Date(s).toLocaleString(); } catch { return s; }
}
function fmtDuration(sec: number | null): string {
  if (sec == null) return "";
  if (sec < 60) return `${Math.round(sec)}s`;
  const m = Math.floor(sec / 60), r = Math.round(sec % 60);
  return `${m}m ${r}s`;
}

export default function EcosystemDetailsPage() {
  const { user } = useAuth();
  const [id, setId] = useState<string | null>(null);
  const [config, setConfig] = useState<any>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState<string | null>(null);

  const [sharing, setSharing] = useState<SharingEntry[] | null>(null);
  const [runs, setRuns] = useState<RunSummary[] | null>(null);
  const [showSharing, setShowSharing] = useState(false);
  const [showBuildTranscript, setShowBuildTranscript] = useState(false);
  const [runsModal, setRunsModal] = useState<{ open: boolean; initial?: string }>({ open: false });
  const [focusedAgent, setFocusedAgent] = useState<string | null>(null);

  // Read the id from the query string on mount (client-only; no useSearchParams so we
  // don't need a Suspense boundary under static export).
  useEffect(() => {
    if (typeof window === "undefined") return;
    const qId = new URLSearchParams(window.location.search).get("id");
    setId(qId);
    if (!qId) { setErr("No ecosystem specified."); setLoading(false); }
  }, []);

  // Load the full config.
  useEffect(() => {
    if (!id) return;
    let stop = false;
    setLoading(true); setErr(null);
    apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(id)}`)
      .then((r) => {
        if (r.status === 404) throw new Error("Ecosystem not found.");
        if (!r.ok) throw new Error(`Failed to load (${r.status}).`);
        return r.json();
      })
      .then((c) => { if (!stop) setConfig(c); })
      .catch((e) => { if (!stop) setErr(e?.message || String(e)); })
      .finally(() => { if (!stop) setLoading(false); });
    return () => { stop = true; };
  }, [id]);

  const eco = config?.ecosystem || {};
  const owner: string = config?.owner || "";
  const isPlatform = !!config?.platform;
  const agents: any[] = Array.isArray(config?.agents) ? config.agents : [];
  const inputs: any[] = Array.isArray(eco?.inputs) ? eco.inputs : [];
  const outcome: any = eco?.outcome || config?.outcome || {};
  const tags: string[] = Array.isArray(config?.tags) ? config.tags : [];
  const domains: string[] = Array.isArray(config?.domains) ? config.domains : [];
  const isOwner = !!user && !!owner && owner === user.userId;

  // Owner-only sharing fetch — ONE call, only on this page, only for owners.
  useEffect(() => {
    if (!id || !isOwner) { setSharing(null); return; }
    let stop = false;
    apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(id)}/sharing`)
      .then((r) => (r.ok ? r.json() : []))
      .then((d) => { if (!stop) setSharing(Array.isArray(d) ? d : []); })
      .catch(() => { if (!stop) setSharing([]); });
    return () => { stop = true; };
  }, [id, isOwner]);

  // Recent run history.
  useEffect(() => {
    if (!id) return;
    let stop = false;
    apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(id)}/runs`)
      .then((r) => (r.ok ? r.json() : { executions: [] }))
      .then((d) => { if (!stop) setRuns(Array.isArray(d?.executions) ? d.executions : []); })
      .catch(() => { if (!stop) setRuns([]); });
    return () => { stop = true; };
  }, [id]);

  const name = eco?.name || id || "Ecosystem";

  const section = "bg-tessera-card border border-tessera-border rounded-xl p-4";
  const chip = "text-[10px] px-1.5 py-0.5 rounded-full border";

  return (
    <div className="min-h-screen bg-tessera-bg text-gray-200">
      <div className="max-w-5xl mx-auto p-6">
        <Link href="/" className="text-xs text-gray-400 hover:text-tessera-accent">← Ecosystems</Link>

        {loading ? (
          <div className="flex items-center gap-2 mt-10 text-sm text-gray-400"><TesseraSpinner /> Loading ecosystem…</div>
        ) : err ? (
          <div className="mt-8 bg-red-500/10 border border-red-500/30 rounded-xl p-4 text-sm text-red-300">{err}</div>
        ) : (
          <>
            {/* Header */}
            <div className="mt-3 flex items-start justify-between gap-4 flex-wrap">
              <div className="min-w-0">
                <h1 className="text-2xl font-bold text-white">{name}</h1>
                <div className="flex items-center gap-1.5 flex-wrap mt-2">
                  <span className={`${chip} bg-tessera-accent/10 text-tessera-accent border-tessera-accent/20`}>{config?.strategy || "orchestrated"}</span>
                  {domains.map((d) => <span key={d} className={`${chip} bg-purple-500/10 text-purple-300 border-purple-500/20`}>🗂 {d}</span>)}
                  {isPlatform && <span className={`${chip} bg-amber-500/15 text-amber-300 border-amber-500/30 font-semibold`}>🔒 SYSTEM</span>}
                  <span className="text-xs text-gray-500">{agents.length} agents</span>
                  {owner && <span className="text-xs text-gray-600">· owner {isOwner ? "you" : owner}</span>}
                </div>
              </div>
              <div className="flex items-center gap-2">
                <Link href={`/?eco=${encodeURIComponent(id || "")}&action=run`} className="px-3 py-2 text-sm rounded-lg bg-tessera-accent/20 text-tessera-accent hover:bg-tessera-accent/30">▶ Run</Link>
                {!isPlatform && (isOwner) && (
                  <Link href={`/?eco=${encodeURIComponent(id || "")}&action=edit`} className="px-3 py-2 text-sm rounded-lg bg-gray-700/50 text-gray-200 hover:bg-gray-700 border border-tessera-border">✎ Edit</Link>
                )}
                {isOwner && (
                  <button onClick={() => setShowSharing(true)} className="px-3 py-2 text-sm rounded-lg bg-gray-700/50 text-gray-200 hover:bg-gray-700 border border-tessera-border">🔗 Share</button>
                )}
                {config?._build_transcript?.messages?.length > 0 && (
                  <button onClick={() => setShowBuildTranscript(true)} className="px-3 py-2 text-sm rounded-lg bg-tessera-accent/15 text-tessera-accent border border-tessera-accent/30 hover:bg-tessera-accent/25">💬 Build transcript</button>
                )}
              </div>
            </div>

            {eco?.description && <p className="mt-4 text-sm text-gray-400 leading-relaxed">{eco.description}</p>}

            <div className="grid grid-cols-1 lg:grid-cols-2 gap-3 mt-5">
              {/* Inputs */}
              <div className={section}>
                <h2 className="text-sm font-semibold text-white mb-2">Inputs</h2>
                {inputs.length === 0 ? <p className="text-xs text-gray-500">No declared inputs.</p> : (
                  <ul className="space-y-1.5">
                    {inputs.map((inp, i) => {
                      const label = typeof inp === "string" ? inp : (inp?.name || inp?.label || inp?.key || JSON.stringify(inp));
                      const desc = typeof inp === "object" ? (inp?.description || inp?.desc) : "";
                      return <li key={i} className="text-xs"><span className="text-gray-200">{label}</span>{desc ? <span className="text-gray-500"> — {desc}</span> : null}</li>;
                    })}
                  </ul>
                )}
                {tags.length > 0 && (
                  <div className="mt-3 pt-3 border-t border-tessera-border">
                    <div className="text-[10px] text-gray-500 mb-1">Tags</div>
                    <div className="flex flex-wrap gap-1">{tags.map((t) => <span key={t} className={`${chip} bg-gray-700/40 text-gray-300 border-gray-600`}>{t}</span>)}</div>
                  </div>
                )}
              </div>

              {/* Deliverable & Provenance (audience) */}
              {(outcome?.kind || (outcome?.artifacts || []).length) ? (
                <div className={section}>
                  <h2 className="text-sm font-semibold text-white mb-2">Deliverable &amp; Provenance</h2>
                  <div className="text-xs text-gray-300 mb-2">
                    {outcome.kind === "file_artifact" ? "Produces downloadable deliverable(s):" : "Report only (no downloadable artifact)."}
                  </div>
                  {(outcome.artifacts || []).length > 0 && (
                    <ul className="space-y-1 mb-3">
                      {(outcome.artifacts || []).map((a: any, i: number) => (
                        <li key={i} className="text-xs text-gray-300"><span className="font-mono text-gray-200">{a.save_as || a.id}</span> <span className="text-gray-500">({a.format}) — {a.download_label || "Download"}</span></li>
                      ))}
                    </ul>
                  )}
                  <div className="mt-1 pt-2 border-t border-tessera-border">
                    <div className="text-[10px] text-gray-500 mb-1">Audience</div>
                    {outcome.audience ? (
                      <div className="text-xs">
                        <span className="text-tessera-accent font-medium">{outcome.audience}</span>
                        <p className="text-[10px] text-gray-500 mt-1">This deliverable renders audience-appropriate provenance (Authority · Evidence · Confidence) with a platform-verified Assurance &amp; Provenance trail.</p>
                      </div>
                    ) : (
                      <p className="text-[10px] text-gray-500">Not set — no audience-specific provenance layer. Set an audience (in the builder) for decision / assessment / report deliverables.</p>
                    )}
                  </div>
                </div>
              ) : null}

              {/* Agents */}
              <div className={section}>
                <h2 className="text-sm font-semibold text-white mb-2">Agents ({agents.length})</h2>
                {agents.length === 0 ? <p className="text-xs text-gray-500">No agents.</p> : (
                  <ul className="space-y-2 max-h-72 overflow-y-auto">
                    {agents.map((a, i) => (
                      <li key={a?.id || a?.name || i} className="text-xs border-b border-tessera-border/50 pb-1.5 last:border-0">
                        <div className="text-gray-200 font-medium">{a?.name || a?.id || `agent ${i + 1}`}</div>
                        {a?.role && <div className="text-gray-500">{a.role}</div>}
                        <div className="flex flex-wrap gap-1 mt-0.5">
                          {a?.model && <span className={`${chip} bg-gray-700/40 text-gray-400 border-gray-600`}>{a.model}</span>}
                          {Array.isArray(a?.tools) && a.tools.length > 0 && <span className={`${chip} bg-gray-700/40 text-gray-400 border-gray-600`}>🔧 {a.tools.length} tools</span>}
                          {Array.isArray(a?.depends_on) && a.depends_on.length > 0 && <span className={`${chip} bg-gray-700/40 text-gray-400 border-gray-600`}>← {a.depends_on.join(", ")}</span>}
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </div>
            </div>

            {/* DAG preview */}
            <div className={`${section} mt-3`}>
              <h2 className="text-sm font-semibold text-white mb-2">Workflow</h2>
              <div className="overflow-x-auto">
                <DagMap config={config} events={[]} focusedAgent={focusedAgent} onFocusAgent={(a) => setFocusedAgent((p) => (p === a ? null : a))} />
              </div>
            </div>

            {/* Sharing (owner only) */}
            {isOwner && (
              <div className={`${section} mt-3`}>
                <div className="flex items-center justify-between mb-2">
                  <h2 className="text-sm font-semibold text-white">Sharing</h2>
                  <button onClick={() => setShowSharing(true)} className="text-xs text-tessera-accent hover:underline">Manage</button>
                </div>
                {sharing == null ? <p className="text-xs text-gray-500">Loading…</p>
                  : sharing.length === 0 ? <p className="text-xs text-gray-500">Not shared with anyone.</p>
                  : (
                    <ul className="space-y-1">
                      {sharing.map((s) => (
                        <li key={s.policyId} className="text-xs flex items-center gap-2">
                          <span className="text-gray-200">{s.email || s.principal_id}</span>
                          {s.principal_type === "group" && <span className="text-[9px] text-gray-500">(group)</span>}
                          <span className="text-[9px] text-tessera-accent">({s.permission})</span>
                        </li>
                      ))}
                    </ul>
                  )}
              </div>
            )}

            {/* Run history */}
            <div className={`${section} mt-3`}>
              <div className="flex items-center justify-between mb-2">
                <h2 className="text-sm font-semibold text-white">Run history</h2>
                {runs && runs.length > 0 && <button onClick={() => setRunsModal({ open: true })} className="text-xs text-tessera-accent hover:underline">View all</button>}
              </div>
              {runs == null ? <p className="text-xs text-gray-500">Loading…</p>
                : runs.length === 0 ? <p className="text-xs text-gray-500">No runs yet.</p>
                : (
                  <ul className="space-y-1">
                    {runs.slice(0, 8).map((r) => (
                      <li key={r.executionArn || r.name}>
                        <button onClick={() => setRunsModal({ open: true, initial: r.name })} className="w-full text-left flex items-center gap-2 text-xs py-1 hover:bg-gray-800/40 rounded px-1">
                          <span className={`${chip} ${STATUS_COLOR[r.status] || "text-gray-400 bg-gray-500/10 border-gray-500/20"}`}>{r.status}</span>
                          <span className="text-gray-300 truncate flex-1">{r.name}</span>
                          <span className="text-gray-600">{fmtDate(r.startDate)}</span>
                          {r.duration_s != null && <span className="text-gray-600">{fmtDuration(r.duration_s)}</span>}
                        </button>
                      </li>
                    ))}
                  </ul>
                )}
            </div>
          </>
        )}
      </div>

      {showSharing && id && (
        <SharingDialog ecosystemId={id} ecosystemName={name} onClose={() => {
          setShowSharing(false);
          // Refresh the inline sharing list after managing.
          if (isOwner) apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(id)}/sharing`).then((r) => (r.ok ? r.json() : [])).then((d) => setSharing(Array.isArray(d) ? d : [])).catch(() => {});
        }} />
      )}
      {runsModal.open && id && (
        <PastRunsModal ecosystemId={id} ecosystemName={name} initialRunName={runsModal.initial} onClose={() => setRunsModal({ open: false })} />
      )}
      {showBuildTranscript && config?._build_transcript?.messages && (
        <TranscriptModal
          title="How this ecosystem was built"
          subtitle={name}
          messages={config._build_transcript.messages}
          reconstructed={config._build_transcript.reconstructed}
          onClose={() => setShowBuildTranscript(false)}
        />
      )}
    </div>
  );
}
