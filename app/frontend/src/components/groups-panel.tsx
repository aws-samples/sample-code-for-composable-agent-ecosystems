"use client";

import { useState, useEffect, useCallback } from "react";

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

interface Group {
  name: string;
  description: string;
  memberCount: number | string;
  created: string;
}

interface Member {
  sub: string;
  email: string;
  username: string;
}

export function GroupsPanel() {
  const [groups, setGroups] = useState<Group[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedGroup, setSelectedGroup] = useState<string | null>(null);
  const [members, setMembers] = useState<Member[]>([]);
  const [membersLoading, setMembersLoading] = useState(false);
  const [newName, setNewName] = useState("");
  const [newDesc, setNewDesc] = useState("");
  const [newMember, setNewMember] = useState("");
  const [creating, setCreating] = useState(false);

  const [allUsers, setAllUsers] = useState<{ sub: string; email: string }[]>([]);

  const loadGroups = useCallback(() => {
    setLoading(true);
    apiFetch(`${API_URL}/api/admin/groups`)
      .then(r => r.json())
      .then(data => { if (Array.isArray(data)) setGroups(data); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, []);

  useEffect(() => {
    loadGroups();
    apiFetch(`${API_URL}/api/admin/users?all=true`).then(r => r.json()).then(data => { if (Array.isArray(data)) setAllUsers(data); }).catch(() => {});
  }, [loadGroups]);

  const loadMembers = (name: string) => {
    setSelectedGroup(name);
    setMembersLoading(true);
    apiFetch(`${API_URL}/api/admin/groups/${name}/members`)
      .then(r => r.json())
      .then(data => { if (Array.isArray(data)) setMembers(data); })
      .catch(() => setMembers([]))
      .finally(() => setMembersLoading(false));
  };

  const createGroup = async () => {
    if (!newName.trim()) return;
    setCreating(true);
    await apiFetch(`${API_URL}/api/admin/groups`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: newName.trim(), description: newDesc }),
    });
    setNewName("");
    setNewDesc("");
    setCreating(false);
    loadGroups();
  };

  const deleteGroup = async (name: string) => {
    if (!confirm(`Delete group "${name}"?`)) return;
    await apiFetch(`${API_URL}/api/admin/groups/${name}`, { method: "DELETE" });
    if (selectedGroup === name) setSelectedGroup(null);
    loadGroups();
  };

  const addMember = async () => {
    if (!newMember.trim() || !selectedGroup) return;
    await apiFetch(`${API_URL}/api/admin/groups/${selectedGroup}/members`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ userId: newMember.trim() }),
    });
    setNewMember("");
    loadMembers(selectedGroup);
  };

  const removeMember = async (userId: string) => {
    if (!selectedGroup) return;
    await apiFetch(`${API_URL}/api/admin/groups/${selectedGroup}/members/${userId}`, { method: "DELETE" });
    loadMembers(selectedGroup);
  };

  return (
    <div>
      <h3 className="text-lg font-bold mb-3">Groups</h3>

      {/* Create group form */}
      <div className="flex gap-2 mb-4">
        <input value={newName} onChange={e => setNewName(e.target.value)} placeholder="Group name" className="flex-1 bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white" />
        <input value={newDesc} onChange={e => setNewDesc(e.target.value)} placeholder="Description" className="flex-1 bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white" />
        <button onClick={createGroup} disabled={creating || !newName.trim()} className="px-4 py-2 bg-tessera-accent/20 text-tessera-accent rounded text-sm disabled:opacity-40">
          {creating ? "..." : "+ Create"}
        </button>
      </div>

      {loading ? (
        <p className="text-gray-500 text-sm">Loading...</p>
      ) : groups.length === 0 ? (
        <p className="text-gray-500 text-sm">No groups yet.</p>
      ) : (
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
          {/* Groups list */}
          <div className="space-y-1.5">
            {groups.map(g => (
              <div key={g.name} className={`flex items-center justify-between rounded-lg px-3 py-2.5 border cursor-pointer transition ${selectedGroup === g.name ? "border-tessera-accent/50 bg-tessera-accent/5" : "border-tessera-border hover:border-gray-600"}`} onClick={() => loadMembers(g.name)}>
                <div>
                  <div className="text-sm font-medium text-white">{g.name}</div>
                  <div className="text-xs text-gray-500">{g.description || "No description"} · {g.memberCount} member(s)</div>
                </div>
                <button onClick={e => { e.stopPropagation(); deleteGroup(g.name); }} className="text-xs text-red-400 hover:text-red-300">Delete</button>
              </div>
            ))}
          </div>

          {/* Members panel */}
          {selectedGroup && (
            <div className="bg-tessera-card border border-tessera-border rounded-xl p-4">
              <h4 className="text-sm font-bold mb-3">Members of "{selectedGroup}"</h4>
              <div className="flex gap-2 mb-3">
                <select value={newMember} onChange={e => setNewMember(e.target.value)} className="flex-1 bg-gray-900 border border-tessera-border rounded px-3 py-1.5 text-sm text-white">
                  <option value="">Select user...</option>
                  {allUsers.filter(u => !members.some(m => m.sub === u.sub)).map(u => (
                    <option key={u.sub} value={u.sub}>{u.email}</option>
                  ))}
                </select>
                <button onClick={addMember} disabled={!newMember.trim()} className="px-3 py-1.5 bg-tessera-accent/20 text-tessera-accent rounded text-xs disabled:opacity-40">+ Add</button>
              </div>
              {membersLoading ? (
                <p className="text-gray-500 text-xs">Loading...</p>
              ) : members.length === 0 ? (
                <p className="text-gray-500 text-xs">No members.</p>
              ) : (
                <div className="space-y-1">
                  {members.map(m => (
                    <div key={m.sub} className="flex items-center justify-between bg-gray-800/50 rounded px-3 py-1.5">
                      <span className="text-sm text-gray-300">{m.email || m.username}</span>
                      <button onClick={() => removeMember(m.sub || m.username)} className="text-xs text-red-400 hover:text-red-300">Remove</button>
                    </div>
                  ))}
                </div>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  );
}
