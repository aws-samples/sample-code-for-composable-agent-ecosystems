"use client";

import { useState } from "react";
import { EcosystemInputForm } from "../ecosystem-input-form";
import { BizEco, describeEco } from "./business-grid";

/**
 * Branded single-eco business intake (docs/spec-business-view.md §12.1). Collects the
 * eco's declared inputs (reusing EcosystemInputForm) + optional context, and launches —
 * entirely within the business chrome (never the technical ecosystems view).
 */
export function BusinessIntake({ eco, onLaunch, onCancel }: {
  eco: BizEco & { inputs?: Array<{ id: string; type: string; label: string; accept?: string[]; required?: boolean; options?: string[]; default?: string }> };
  onLaunch: (mission: string, values: Record<string, string>) => void;
  onCancel: () => void;
}) {
  const d = describeEco(eco);
  const inputs = (eco.inputs || []) as any[];
  const [mission, setMission] = useState("");
  const [launching, setLaunching] = useState(false);

  const launch = (values: Record<string, string>) => {
    if (launching) return;
    setLaunching(true);
    onLaunch(mission.trim() || `Run: ${d.title}`, values);
  };

  return (
    <div className="max-w-3xl mx-auto px-6 py-8">
      <button onClick={onCancel} className="text-[12px] text-gray-400 hover:text-white mb-4">◂ Back</button>
      <div className="rounded-2xl border border-tessera-border overflow-hidden">
        <div className="relative p-6 border-b border-tessera-border" style={{ background: "linear-gradient(180deg, rgba(139,92,246,0.10), transparent)" }}>
          <div className="absolute inset-x-0 top-0 h-[3px] bg-tessera-gradient" />
          <div className="text-[11px] font-bold uppercase tracking-[0.14em] text-tessera-accent">New run</div>
          <h2 className="text-[22px] font-extrabold text-white mt-1">{d.title}</h2>
          {d.hero && <div className="text-[12.5px] text-purple-200 mt-2"><span className="text-tessera-accent">✦</span> You&apos;ll get: {d.hero}</div>}
        </div>
        <div className="p-6 space-y-4">
          <div>
            <label className="block text-sm text-gray-200 mb-1.5">What would you like assessed?</label>
            <textarea
              value={mission}
              onChange={(e) => setMission(e.target.value)}
              rows={4}
              placeholder="Describe the decision / initiative — or add context for the run."
              className="w-full text-[13px] rounded-lg bg-tessera-bg border border-tessera-border px-3 py-2 text-gray-100 placeholder:text-gray-500 focus:outline-none focus:border-tessera-accent/50"
            />
          </div>

          {inputs.length > 0 ? (
            <EcosystemInputForm inputs={inputs} onSubmit={launch} loading={launching} />
          ) : (
            <button
              onClick={() => launch({})}
              disabled={launching || !mission.trim()}
              className="px-6 py-3 text-[14px] font-extrabold text-white rounded-xl bg-tessera-gradient shadow-[0_6px_20px_rgba(139,92,246,0.35)] hover:opacity-95 disabled:opacity-40 transition"
            >
              {launching ? "Starting…" : "Start a run →"}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
