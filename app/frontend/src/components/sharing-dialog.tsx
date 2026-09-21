"use client";

import { useState, useEffect } from "react";

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

interface SharingEntry {
  policyId: string;
  principal_type: "user" | "group";
  principal_id: string;
  permission: string;
  email?: string;
}

const PERM_COLORS: Record<string, string> = {
  run: "bg-green-500/20 text-green-300",
  edit: "bg-blue-500/20 text-blue-300",
  full: "bg-purple-500/20 text-purple-300",
};

export function SharingDialog({ ecosystemId, ecosystemName, onClose }: { ecosystemId: string; ecosystemName: string; onClose: () => void }) {
  const [entries, setEntries] = useState<SharingEntry[]>([]);
  const [loading, setLoading] = useState(true);
  const [principalType, setPrincipalType] = useState<"user" | "group">("user");
  const [principalId, setPrincipalId] = useState("");
  const [permission, setPermission] = useState<"run" | "edit" | "full">("run");
  const [saving, setSaving] = useState(false);
  const [users, setUsers] = useState<{ sub: string; email: string }[]>([]);
  const [groups, setGroups] = useState<{ name: string }[]>([]);

  const load = () => {
    setLoading(true);
    apiFetch(`${API_URL}/api/ecosystems/${ecosystemId}/sharing`)
      .then(r => r.json())
      .then(data => { if (Array.isArray(data)) setEntries(data); })
      .catch(() => {})
      .finally(() => setLoading(false));
  };

  useEffect(() => {
    load();
    // Fetch users and groups for dropdowns
    apiFetch(`${API_URL}/api/admin/users?all=true`).then(r => r.json()).then(data => { if (Array.isArray(data)) setUsers(data); }).catch(() => {});
    apiFetch(`${API_URL}/api/admin/groups`).then(r => r.json()).then(data => { if (Array.isArray(data)) setGroups(data); }).catch(() => {});
  }, [ecosystemId]);

  const handleAdd = async () => {
    if (!principalId.trim()) return;
    setSaving(true);
    await apiFetch(`${API_URL}/api/ecosystems/${ecosystemId}/sharing`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ principal_type: principalType, principal_id: principalId.trim(), permission }),
    });
    setPrincipalId("");
    setSaving(false);
    load();
  };

  const handleRevoke = async (policyId: string) => {
    await apiFetch(`${API_URL}/api/ecosystems/${ecosystemId}/sharing/${policyId}`, { method: "DELETE" });
    load();
  };

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div className="bg-tessera-card border border-tessera-border rounded-xl p-6 w-[500px] max-h-[80vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold">Share — {ecosystemName}</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-lg">✕</button>
        </div>

        {/* Current access list */}
        <div className="mb-4">
          <h4 className="text-xs text-gray-400 uppercase tracking-wider mb-2">Current Access</h4>
          {loading ? (
            <p className="text-sm text-gray-500">Loading...</p>
          ) : entries.length === 0 ? (
            <p className="text-sm text-gray-500">No sharing policies. Only the owner has access.</p>
          ) : (
            <div className="space-y-1.5">
              {entries.map(e => (
                <div key={e.policyId} className="flex items-center justify-between bg-gray-800/50 rounded px-3 py-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-sm">{e.principal_type === "group" ? "👥" : "👤"}</span>
                    <span className="text-sm text-white truncate">{e.email || e.principal_id}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${PERM_COLORS[e.permission] || "bg-gray-700 text-gray-300"}`}>{e.permission}</span>
                  </div>
                  <button onClick={() => handleRevoke(e.policyId)} className="text-red-400 hover:text-red-300 shrink-0 ml-2" title="Revoke">✕</button>
                </div>
              ))}
            </div>
          )}
        </div>

        {/* Add form */}
        <div className="border-t border-tessera-border pt-4">
          <h4 className="text-xs text-gray-400 uppercase tracking-wider mb-2">Grant Access</h4>
          <div className="space-y-3">
            <div className="flex gap-3">
              <label className="flex items-center gap-1.5 text-xs text-gray-300">
                <input type="radio" name="ptype" checked={principalType === "user"} onChange={() => { setPrincipalType("user"); setPrincipalId(""); }} />
                User
              </label>
              <label className="flex items-center gap-1.5 text-xs text-gray-300">
                <input type="radio" name="ptype" checked={principalType === "group"} onChange={() => { setPrincipalType("group"); setPrincipalId(""); }} />
                Group
              </label>
            </div>
            {principalType === "user" ? (
              <select value={principalId} onChange={e => setPrincipalId(e.target.value)} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white">
                <option value="">Select a user…</option>
                {users.map(u => <option key={u.sub} value={u.sub}>{u.email}</option>)}
              </select>
            ) : (
              <select value={principalId} onChange={e => setPrincipalId(e.target.value)} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white">
                <option value="">Select a group…</option>
                {groups.map(g => <option key={g.name} value={g.name}>{g.name}</option>)}
              </select>
            )}
            <select value={permission} onChange={e => setPermission(e.target.value as any)} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white">
              <option value="run">Run only</option>
              <option value="edit">Edit (run + edit + manage files)</option>
              <option value="full">Full access</option>
            </select>
            <button onClick={handleAdd} disabled={saving || !principalId.trim()} className="w-full py-2 bg-tessera-accent/20 text-tessera-accent rounded text-sm font-medium hover:bg-tessera-accent/30 disabled:opacity-40">
              {saving ? "Granting..." : "+ Grant Access"}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

export function AccessSummary({ ecosystemId, onManage }: { ecosystemId: string; onManage: () => void }) {
  const [entries, setEntries] = useState<SharingEntry[]>([]);

  useEffect(() => {
    apiFetch(`${API_URL}/api/ecosystems/${ecosystemId}/sharing`)
      .then(r => r.json())
      .then(data => { if (Array.isArray(data)) setEntries(data); })
      .catch(() => {});
  }, [ecosystemId]);

  if (entries.length === 0) return null;

  return (
    <div className="mt-2 text-xs text-gray-400">
      <span className="text-gray-500">Shared with:</span>{" "}
      {entries.slice(0, 3).map(e => (
        <span key={e.policyId} className="inline-flex items-center gap-1 mr-2">
          <span className="text-gray-300">{e.email || e.principal_id}</span>
          <span className="text-[9px] text-tessera-accent">({e.permission})</span>
        </span>
      ))}
      {entries.length > 3 && <span className="text-gray-500">+{entries.length - 3} more</span>}
      <span role="link" tabIndex={0} onClick={(e) => { e.stopPropagation(); onManage(); }} onKeyDown={(e) => { if (e.key === 'Enter') { e.stopPropagation(); onManage(); } }} className="ml-2 text-tessera-accent hover:underline cursor-pointer">Manage</span>
    </div>
  );
}
