"use client";

import { useEffect, useMemo, useState } from "react";
import type { BizEco } from "./business-grid";

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

const prettify = (id: string, name?: string) =>
  (name && name.trim()) || id.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());
const fmt = (s?: string) => { if (!s) return ""; try { return new Date(s).toLocaleString(); } catch { return s; } };

interface RunRow { name?: string; execName?: string; displayName?: string; tags?: string[]; status?: string; startDate?: string; }
interface Detail { mission?: string; files?: string[]; verdict?: string; loading?: boolean; }

/**
 * "Your results" — business run history (docs/spec-business-view.md §12.2).
 * Editable auto-suggested name + tags (searchable), inputs (text+files) and outputs
 * on expand, Open → the interactive Studio report. All per-eco/generic.
 */
export function BusinessHistory({ ecosystems, defaultEcoId, onOpen }: {
  ecosystems: BizEco[];
  defaultEcoId?: string | null;
  onOpen: (ecoId: string, runId: string) => void;
}) {
  const options = useMemo(() => (ecosystems || [])
    .map((e) => ({ id: e.id, label: prettify(e.id, e.name) }))
    .sort((a, b) => a.label.localeCompare(b.label)), [ecosystems]);
  const [ecoId] = useState<string>(defaultEcoId || options[0]?.id || "");
  const [runs, setRuns] = useState<RunRow[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [expanded, setExpanded] = useState<string | null>(null);
  const [details, setDetails] = useState<Record<string, Detail>>({});
  const [editing, setEditing] = useState<string | null>(null);
  const [draftName, setDraftName] = useState("");
  const [draftTags, setDraftTags] = useState("");
  const [savingMeta, setSavingMeta] = useState(false);

  useEffect(() => {
    if (!ecoId) return;
    let active = true;
    setLoading(true); setError(null); setRuns([]); setExpanded(null);
    (async () => {
      try {
        const r = await apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(ecoId)}/runs`);
        const d = await r.json().catch(() => ({}));
        const list: RunRow[] = Array.isArray(d) ? d : (d.runs || d.executions || []);
        if (active) setRuns(list);
      } catch (e) {
        if (active) setError(`Couldn't load results: ${e}`);
      } finally { if (active) setLoading(false); }
    })();
    return () => { active = false; };
  }, [ecoId]);

  const runName = (run: RunRow) => run.displayName || `${prettify(ecoId)} · ${fmt(run.startDate) || "run"}`;

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return runs;
    return runs.filter((run) => {
      const hay = [runName(run), (run.execName || run.name || ""), ...(run.tags || [])].join(" ").toLowerCase();
      return hay.includes(q);
    });
  }, [runs, query, ecoId]);

  const loadDetail = async (rid: string) => {
    if (details[rid]) return;
    setDetails((m) => ({ ...m, [rid]: { loading: true } }));
    try {
      const r = await apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(ecoId)}/runs/${encodeURIComponent(rid)}`);
      const d = await r.json().catch(() => ({}));
      const inp = d?.execution?.input || {};
      const mission = inp.mission_display || inp.mission || "";
      const files = (inp.attachments || []).map((a: any) => a?.name).filter(Boolean);
      const verdict = d?.outcome?.recommendation || d?.decision?.outcome || "";
      setDetails((m) => ({ ...m, [rid]: { mission, files, verdict } }));
    } catch {
      setDetails((m) => ({ ...m, [rid]: {} }));
    }
  };

  // Open an uploaded INPUT file — presign on demand (URLs expire) then open inline.
  const openAttachment = async (rid: string, name: string) => {
    try {
      const r = await apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(ecoId)}/runs/${encodeURIComponent(rid)}/attachment?name=${encodeURIComponent(name)}`);
      const d = await r.json().catch(() => ({}));
      if (d.url) window.open(d.url, "_blank", "noopener");
      else alert("Couldn't open that file.");
    } catch { alert("Couldn't open that file."); }
  };

  const toggle = (rid: string) => {
    const next = expanded === rid ? null : rid;
    setExpanded(next);
    if (next) {
      loadDetail(rid);
      const run = runs.find((r) => (r.execName || r.name) === rid);
      setEditing(null);
      setDraftName(run?.displayName || "");
      setDraftTags((run?.tags || []).join(", "));
    }
  };

  const startEdit = (rid: string) => {
    const run = runs.find((r) => (r.execName || r.name) === rid);
    const det = details[rid];
    // auto-suggest a name from the mission if none set yet
    const suggest = run?.displayName || (det?.mission ? det.mission.replace(/\s+/g, " ").trim().slice(0, 60) : "");
    setDraftName(suggest);
    setDraftTags((run?.tags || []).join(", "));
    setEditing(rid);
  };

  const saveMeta = async (rid: string) => {
    setSavingMeta(true);
    const tags = draftTags.split(",").map((t) => t.trim().toLowerCase()).filter(Boolean);
    try {
      const r = await apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(ecoId)}/runs/${encodeURIComponent(rid)}/meta`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ name: draftName, tags }),
      });
      const d = await r.json().catch(() => ({}));
      if (d.ok) {
        setRuns((rs) => rs.map((run) => ((run.execName || run.name) === rid ? { ...run, displayName: d.name, tags: d.tags } : run)));
        setEditing(null);
      }
    } catch { /* ignore */ }
    finally { setSavingMeta(false); }
  };

  const badge = (st?: string) => {
    const s = (st || "").toUpperCase();
    if (s.includes("SUCCEED")) return "text-green-300 border-green-500/40 bg-green-500/10";
    if (s.includes("FAIL") || s.includes("ABORT")) return "text-red-300 border-red-500/40 bg-red-500/10";
    if (s.includes("RUN")) return "text-amber-300 border-amber-500/40 bg-amber-500/10";
    return "text-gray-300 border-gray-600/40 bg-gray-700/20";
  };

  return (
    <div className="max-w-5xl mx-auto p-6">
      <div className="mb-4">
        <h1 className="text-2xl font-bold text-white">Your results</h1>
        <p className="text-sm text-gray-400 mt-1">Name, tag and search your runs. Open one to read, ask, and spotlight the report.</p>
      </div>

      <input
        value={query}
        onChange={(e) => setQuery(e.target.value)}
        placeholder="Search results by name or tag…"
        className="w-full mb-4 text-[13px] rounded-lg bg-tessera-card border border-tessera-border px-3 py-2 text-gray-100 placeholder:text-gray-500 focus:outline-none focus:border-tessera-accent/50"
      />

      {loading && <div className="text-sm text-gray-500">Loading results…</div>}
      {error && <div className="text-sm text-red-400">{error}</div>}
      {!loading && !error && filtered.length === 0 && (
        <div className="text-sm text-gray-500">{runs.length ? "No results match your search." : "No results yet — start a run from Solutions."}</div>
      )}

      <div className="space-y-2">
        {filtered.map((run, i) => {
          const rid = run.execName || run.name || "";
          const det = details[rid];
          const isOpen = expanded === rid;
          return (
            <div key={rid || i} className="rounded-xl border border-tessera-border bg-tessera-card overflow-hidden">
              <div className="flex items-center justify-between gap-3 px-4 py-3">
                <button onClick={() => rid && toggle(rid)} className="flex-1 min-w-0 text-left">
                  <div className="text-sm font-semibold text-white truncate">{runName(run)}</div>
                  <div className="flex items-center gap-2 mt-1 flex-wrap">
                    <span className="text-[11px] text-gray-500">{fmt(run.startDate)}</span>
                    {(run.tags || []).map((t) => (
                      <span key={t} className="text-[10px] px-1.5 py-0.5 rounded-full bg-tessera-accent/10 border border-tessera-accent/30 text-purple-200">#{t}</span>
                    ))}
                  </div>
                </button>
                <div className="flex items-center gap-2 shrink-0">
                  {run.status && <span className={`text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded border ${badge(run.status)}`}>{run.status}</span>}
                  <button onClick={() => rid && onOpen(ecoId, rid)} disabled={!rid}
                    className="px-3 py-1.5 text-[12px] font-semibold rounded-lg bg-tessera-accent/20 text-tessera-accent border border-tessera-accent/40 hover:bg-tessera-accent/30 disabled:opacity-40 transition">Open →</button>
                  <button onClick={() => rid && toggle(rid)} className="text-gray-400 hover:text-white text-sm w-6 text-center">{isOpen ? "▾" : "▸"}</button>
                </div>
              </div>

              {isOpen && (
                <div className="border-t border-tessera-border px-4 py-3 bg-tessera-bg/40 space-y-3">
                  {det?.loading && <div className="text-[12px] text-gray-500">Loading run details…</div>}
                  {det && !det.loading && (
                    <>
                      <div>
                        <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500 mb-1">Inputs</div>
                        {det.mission ? <div className="text-[12px] text-gray-300 whitespace-pre-wrap leading-relaxed">{det.mission}</div>
                          : <div className="text-[12px] text-gray-500">No mission text recorded.</div>}
                        {(det.files || []).length > 0 && (
                          <div className="flex flex-wrap gap-1.5 mt-2">
                            {det.files!.map((f) => (
                              <button key={f} onClick={() => openAttachment(rid, f)}
                                title="Open file"
                                className="text-[10px] px-2 py-0.5 rounded bg-gray-700/40 border border-tessera-border text-gray-300 hover:text-white hover:border-tessera-accent/60 hover:bg-tessera-accent/10 transition cursor-pointer">📎 {f}</button>
                            ))}
                          </div>
                        )}
                      </div>
                      <div>
                        <div className="text-[10px] font-bold uppercase tracking-wider text-gray-500 mb-1">Output</div>
                        <div className="flex items-center gap-2">
                          {det.verdict && <span className="text-[11px] font-semibold text-tessera-accent">{det.verdict}</span>}
                          <button onClick={() => onOpen(ecoId, rid)} className="text-[12px] text-tessera-accent hover:underline">Open the interactive report →</button>
                        </div>
                      </div>
                    </>
                  )}

                  {editing === rid ? (
                    <div className="space-y-2 pt-1">
                      <input value={draftName} onChange={(e) => setDraftName(e.target.value)} placeholder="Run name"
                        className="w-full text-[12px] rounded bg-tessera-card border border-tessera-border px-2.5 py-1.5 text-gray-100" />
                      <input value={draftTags} onChange={(e) => setDraftTags(e.target.value)} placeholder="tags, comma, separated"
                        className="w-full text-[12px] rounded bg-tessera-card border border-tessera-border px-2.5 py-1.5 text-gray-100" />
                      <div className="flex gap-2">
                        <button onClick={() => saveMeta(rid)} disabled={savingMeta}
                          className="px-3 py-1 text-[11px] font-semibold rounded bg-tessera-accent/20 text-tessera-accent border border-tessera-accent/40 hover:bg-tessera-accent/30 disabled:opacity-40">{savingMeta ? "Saving…" : "Save"}</button>
                        <button onClick={() => setEditing(null)} className="px-3 py-1 text-[11px] text-gray-400 hover:text-white">Cancel</button>
                      </div>
                    </div>
                  ) : (
                    <button onClick={() => startEdit(rid)} className="text-[11px] text-gray-400 hover:text-tessera-accent">✎ Name &amp; tags</button>
                  )}
                </div>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
