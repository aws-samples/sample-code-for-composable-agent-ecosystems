"use client";

import { useEffect, useMemo, useRef, useState } from "react";
import { BizEco, describeEco } from "./business-grid";

/**
 * Solution picker — the single way a business user chooses which solution they're in.
 * A small searchable modal, not a browse page: with dozens of ecosystems a card wall is
 * noise, and most ecos carry no category/description to make cards meaningful.
 *
 * Generic: lists whatever the API returns, minus `platform: true` ecosystems (platform
 * machinery, not user-facing solutions). No eco-specific logic.
 */
export function SolutionPicker({ ecosystems, currentId, required, onPick, onClose }: {
  ecosystems: BizEco[];
  currentId?: string | null;
  /** when true the modal cannot be dismissed — a solution must be chosen to continue */
  required?: boolean;
  onPick: (ecoId: string) => void;
  onClose?: () => void;
}) {
  const [q, setQ] = useState("");
  const [idx, setIdx] = useState(0);
  const inputRef = useRef<HTMLInputElement>(null);
  const listRef = useRef<HTMLDivElement>(null);

  useEffect(() => { inputRef.current?.focus(); }, []);

  const rows = useMemo(() => {
    const items = (ecosystems || [])
      .filter((e) => !(e as any).platform)
      .map((e) => ({ eco: e, d: describeEco(e) }))
      .sort((a, b) => a.d.title.localeCompare(b.d.title));
    const query = q.trim().toLowerCase();
    if (!query) return items;
    return items.filter(({ eco, d }) =>
      [d.title, eco.id, d.tagline, ...(eco.tags || [])].join(" ").toLowerCase().includes(query));
  }, [ecosystems, q]);

  useEffect(() => { setIdx(0); }, [q]);

  const choose = (id?: string) => { if (id) onPick(id); };

  const onKey = (e: React.KeyboardEvent) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setIdx((i) => Math.min(rows.length - 1, i + 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setIdx((i) => Math.max(0, i - 1)); }
    else if (e.key === "Enter") { e.preventDefault(); choose(rows[idx]?.eco.id); }
    else if (e.key === "Escape" && !required) { e.preventDefault(); onClose?.(); }
  };

  useEffect(() => {
    listRef.current?.querySelector<HTMLElement>(`[data-i="${idx}"]`)?.scrollIntoView({ block: "nearest" });
  }, [idx]);

  return (
    <div className="fixed inset-0 z-[70] bg-black/70 backdrop-blur-sm flex items-start justify-center pt-[12vh] px-4"
      onClick={() => { if (!required) onClose?.(); }}>
      <div className="w-full max-w-[560px] rounded-2xl border border-tessera-border bg-tessera-card shadow-2xl overflow-hidden"
        onClick={(e) => e.stopPropagation()} onKeyDown={onKey}>
        <div className="px-4 pt-4 pb-3 border-b border-tessera-border">
          <div className="text-[15px] font-bold text-white">Choose a solution</div>
          <div className="text-[12px] text-gray-400 mt-0.5">
            {required ? "Pick the solution you want to work in." : "Switch to another solution."}
          </div>
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search solutions…"
            className="mt-3 w-full text-[13px] rounded-lg bg-tessera-bg border border-tessera-border px-3 py-2 text-gray-100 placeholder:text-gray-500 focus:outline-none focus:border-tessera-accent/60"
          />
        </div>

        <div ref={listRef} className="max-h-[52vh] overflow-y-auto py-1">
          {rows.length === 0 && (
            <div className="px-4 py-6 text-[12.5px] text-gray-500">No solutions match “{q}”.</div>
          )}
          {rows.map(({ eco, d }, i) => (
            <button
              key={eco.id}
              data-i={i}
              onMouseEnter={() => setIdx(i)}
              onClick={() => choose(eco.id)}
              className={`w-full text-left px-4 py-2.5 flex items-start gap-3 transition ${
                i === idx ? "bg-tessera-accent/12" : "hover:bg-white/[0.03]"
              }`}
            >
              <span className="text-[16px] leading-6 shrink-0">{d.icon}</span>
              <span className="min-w-0 flex-1">
                <span className="block text-[13.5px] font-semibold text-white truncate">
                  {d.title}
                  {eco.id === currentId && <span className="ml-2 text-[10px] font-bold uppercase tracking-wider text-tessera-accent">current</span>}
                </span>
                {d.tagline && <span className="block text-[11.5px] text-gray-500 truncate">{d.tagline}</span>}
              </span>
              <span className={`text-[11px] shrink-0 mt-0.5 ${i === idx ? "text-tessera-accent" : "text-transparent"}`}>↵</span>
            </button>
          ))}
        </div>

        <div className="px-4 py-2.5 border-t border-tessera-border flex items-center gap-3 text-[10.5px] text-gray-500">
          <span>↑↓ to move · ↵ to open{required ? "" : " · esc to close"}</span>
          <span className="flex-1" />
          <span>{rows.length} solution{rows.length === 1 ? "" : "s"}</span>
        </div>
      </div>
    </div>
  );
}
