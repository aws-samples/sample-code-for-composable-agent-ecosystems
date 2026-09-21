"use client";

import { useEffect, useState } from "react";
import { BizEco, describeEco } from "./business-grid";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";
async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const { fetchAuthSession } = await import("aws-amplify/auth");
  let token = "";
  try { token = (await fetchAuthSession({ forceRefresh: false })).tokens?.idToken?.toString() || ""; } catch {}
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(url, { ...init, headers });
}
const fmt = (s?: string) => { if (!s) return ""; try { return new Date(s).toLocaleString(); } catch { return s; } };
const badge = (st?: string) => {
  const s = (st || "").toUpperCase();
  if (s.includes("SUCCEED")) return "text-green-300 border-green-500/40 bg-green-500/10";
  if (s.includes("FAIL") || s.includes("ABORT")) return "text-red-300 border-red-500/40 bg-red-500/10";
  if (s.includes("RUN")) return "text-amber-300 border-amber-500/40 bg-amber-500/10";
  return "text-gray-300 border-gray-600/40 bg-gray-700/20";
};

/** Branded single-eco business home: a hero to start a run + recent results. */
export function BusinessHome({ eco, onStart, onOpen, onSeeAll }: {
  eco: BizEco;
  onStart: () => void;
  onOpen: (runId: string) => void;
  onSeeAll: () => void;
}) {
  const d = describeEco(eco);
  const [recent, setRecent] = useState<any[]>([]);
  useEffect(() => {
    let active = true;
    (async () => {
      try {
        const r = await apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(eco.id)}/runs`);
        const j = await r.json().catch(() => ({}));
        const list = Array.isArray(j) ? j : (j.executions || j.runs || []);
        if (active) setRecent(list.slice(0, 4));
      } catch { /* ignore */ }
    })();
    return () => { active = false; };
  }, [eco.id]);

  return (
    <div className="max-w-3xl mx-auto px-6 py-8">
      <div className="relative overflow-hidden rounded-2xl border border-tessera-border p-8"
        style={{ background: "linear-gradient(180deg, rgba(139,92,246,0.10), rgba(139,92,246,0.02))" }}>
        <div className="absolute inset-x-0 top-0 h-[4px] bg-tessera-gradient" />
        <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-tessera-accent">Start a new run</div>
        <h2 className="text-[26px] font-extrabold text-white mt-2 leading-tight">{d.title}</h2>
        <p className="text-[14px] text-gray-300 mt-1.5 max-w-xl leading-relaxed">{d.tagline}</p>
        {d.hero && (
          <div className="inline-flex items-center gap-2 mt-4 text-[12.5px] text-purple-200 bg-tessera-accent/10 border border-tessera-accent/20 rounded-lg px-3 py-2">
            <span className="text-tessera-accent">✦</span> You&apos;ll get: {d.hero}
          </div>
        )}
        <div className="flex items-center gap-3 mt-6">
          <button onClick={onStart} className="px-6 py-3 text-[14px] font-extrabold text-white rounded-xl bg-tessera-gradient shadow-[0_6px_20px_rgba(139,92,246,0.35)] hover:opacity-95 transition">Start a run →</button>
          {d.time && <span className="text-[12px] text-gray-500">⏱ {d.time}</span>}
        </div>
      </div>

      <div className="flex items-center justify-between mt-9 mb-3">
        <div className="text-[12px] font-bold uppercase tracking-wider text-gray-400">Recent results</div>
        <button onClick={onSeeAll} className="text-[12px] text-tessera-accent hover:underline">See all →</button>
      </div>
      {recent.length === 0 && <div className="text-[13px] text-gray-500">No results yet — start your first run above.</div>}
      <div className="space-y-2.5">
        {recent.map((run, i) => {
          const rid = run.execName || run.name || "";
          return (
            <div key={rid || i} className="flex items-center justify-between gap-3 rounded-xl border border-tessera-border bg-tessera-card px-4 py-3">
              <div className="min-w-0">
                <div className="text-[14px] font-semibold text-white truncate">{run.displayName || `${d.title} · ${fmt(run.startDate)}`}</div>
                <div className="flex items-center gap-2 mt-1 flex-wrap">
                  <span className="text-[11px] text-gray-500">{fmt(run.startDate)}</span>
                  {(run.tags || []).map((t: string) => <span key={t} className="text-[10px] px-1.5 py-0.5 rounded-full bg-tessera-accent/10 border border-tessera-accent/30 text-purple-200">#{t}</span>)}
                </div>
              </div>
              <div className="flex items-center gap-2.5 shrink-0">
                {run.status && <span className={`text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded border ${badge(run.status)}`}>{run.status}</span>}
                <button onClick={() => rid && onOpen(rid)} className="px-3 py-1.5 text-[12px] font-semibold rounded-lg bg-tessera-accent/20 text-tessera-accent border border-tessera-accent/40 hover:bg-tessera-accent/30 transition">Open →</button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
