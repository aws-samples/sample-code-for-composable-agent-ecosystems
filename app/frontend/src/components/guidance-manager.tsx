"use client";

// Guidance Manager (spec-quality-feedback-loop §7) — manage the persistent,
// operator-curated "standing guidance" for an ecosystem. These entries are
// injected into input_context at launch (websocket on_launch) so they steer
// EVERY future run. Entries come from promoted run/section feedback or are
// added manually here; each can be edited, enabled/disabled, or deleted.
import { useCallback, useEffect, useState } from "react";

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

export interface GuidanceEntry {
  id: string;
  text: string;
  enabled: boolean;
  source?: { type?: string; session_id?: string; section_id?: string };
  created_at?: string;
  updated_at?: string;
}

function sourceLabel(e: GuidanceEntry): string {
  const t = e.source?.type;
  if (t === "run") return "from run feedback";
  if (t === "section") return "from section feedback";
  return "manual";
}

export function GuidanceManager({ ecosystemId, onClose }: { ecosystemId: string; onClose?: () => void }) {
  const [entries, setEntries] = useState<GuidanceEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [newText, setNewText] = useState("");
  const [editing, setEditing] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string>("");

  const base = `${API_URL}/api/ecosystems/${encodeURIComponent(ecosystemId)}/guidance`;

  const load = useCallback(async () => {
    setLoading(true);
    try {
      const r = await apiFetch(base);
      if (r.ok) { const d = await r.json(); setEntries(d.entries || []); }
    } catch { /* ignore */ } finally { setLoading(false); }
  }, [base]);

  useEffect(() => { load(); }, [load]);

  const add = async () => {
    const text = newText.trim();
    if (!text) return;
    setBusy("add");
    try {
      const r = await apiFetch(base, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ text }) });
      if (r.ok) { setNewText(""); await load(); }
    } finally { setBusy(""); }
  };

  const patch = async (id: string, body: Record<string, unknown>) => {
    setBusy(id);
    try {
      const r = await apiFetch(`${base}/${id}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      if (r.ok) { setEditing((s) => { const n = { ...s }; delete n[id]; return n; }); await load(); }
    } finally { setBusy(""); }
  };

  const remove = async (id: string) => {
    setBusy(id);
    try {
      const r = await apiFetch(`${base}/${id}`, { method: "DELETE" });
      if (r.ok) await load();
    } finally { setBusy(""); }
  };

  const enabledCount = entries.filter((e) => e.enabled).length;

  return (
    <div className="bg-tessera-card border border-tessera-border rounded-xl p-4">
      <div className="flex items-center justify-between mb-2">
        <div className="flex items-center gap-2">
          <span className="text-tessera-accent">★</span>
          <span className="font-semibold text-gray-200 text-sm">Standing guidance</span>
          <span className="text-[10px] text-gray-500">{enabledCount} active · applied to every run</span>
        </div>
        {onClose && <button onClick={onClose} className="text-gray-500 hover:text-white text-sm">✕</button>}
      </div>
      <p className="text-[11px] text-gray-500 mb-3">
        Lessons promoted from run/section feedback (or added here). Enabled entries are injected into every future run of this ecosystem.
      </p>

      <div className="flex items-start gap-2 mb-3">
        <textarea
          value={newText}
          onChange={(e) => setNewText(e.target.value)}
          placeholder="Add guidance — e.g. 'Always model the reduced insurance need at retirement, in dollars.'"
          rows={2}
          className="flex-1 text-[11px] bg-tessera-bg border border-tessera-border rounded-lg px-2 py-1.5 text-gray-200 placeholder:text-gray-600 focus:border-tessera-accent/50 outline-none resize-y"
        />
        <button onClick={add} disabled={busy === "add" || !newText.trim()}
          className="text-[11px] px-2.5 py-1.5 rounded-md bg-tessera-gradient text-white disabled:opacity-40">Add</button>
      </div>

      {loading ? (
        <p className="text-[11px] text-gray-500">Loading guidance…</p>
      ) : entries.length === 0 ? (
        <p className="text-[11px] text-gray-600">No guidance yet. Rate a run and promote feedback, or add one above.</p>
      ) : (
        <ul className="space-y-2">
          {entries.map((e) => (
            <li key={e.id} className={`border rounded-lg p-2 ${e.enabled ? "border-tessera-border" : "border-tessera-border/40 opacity-60"}`}>
              {editing[e.id] !== undefined ? (
                <div className="flex items-start gap-2">
                  <textarea value={editing[e.id]} onChange={(ev) => setEditing((s) => ({ ...s, [e.id]: ev.target.value }))}
                    rows={2} className="flex-1 text-[11px] bg-tessera-bg border border-tessera-border rounded-md px-2 py-1 text-gray-200 outline-none resize-y" />
                  <button onClick={() => patch(e.id, { text: editing[e.id] })} disabled={busy === e.id}
                    className="text-[11px] text-green-400 hover:text-green-300">save</button>
                  <button onClick={() => setEditing((s) => { const n = { ...s }; delete n[e.id]; return n; })}
                    className="text-[11px] text-gray-500 hover:text-gray-300">cancel</button>
                </div>
              ) : (
                <div className="flex items-start gap-2">
                  <label className="mt-0.5 cursor-pointer" title={e.enabled ? "Enabled — disable" : "Disabled — enable"}>
                    <input type="checkbox" checked={e.enabled} disabled={busy === e.id}
                      onChange={() => patch(e.id, { enabled: !e.enabled })} className="accent-purple-500" />
                  </label>
                  <div className="flex-1">
                    <p className="text-[12px] text-gray-200 whitespace-pre-wrap">{e.text}</p>
                    <span className="text-[10px] text-gray-500">{sourceLabel(e)}</span>
                  </div>
                  <button onClick={() => setEditing((s) => ({ ...s, [e.id]: e.text }))} className="text-[11px] text-gray-400 hover:text-white">edit</button>
                  <button onClick={() => remove(e.id)} disabled={busy === e.id} className="text-[11px] text-red-400/80 hover:text-red-300">delete</button>
                </div>
              )}
            </li>
          ))}
        </ul>
      )}
    </div>
  );
}
