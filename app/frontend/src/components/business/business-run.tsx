"use client";

import { useState, ReactNode } from "react";

/**
 * Business run view (docs/spec-business-view.md §4.3): a plain-language progress /
 * outcome surface that hides the DAG. The technical run view is available under
 * "Show the work". Generic — no eco-specific content.
 */
export function BusinessRun({
  ecoLabel, sessionId, isRunning, runStatus, onOpenReport, onYourResults, children,
}: {
  ecoLabel: string;
  sessionId: string;
  isRunning: boolean;
  runStatus?: string;
  onOpenReport: () => void;
  onYourResults: () => void;
  children?: ReactNode; // the technical run view (revealed under "Show the work")
}) {
  const [showWork, setShowWork] = useState(false);
  const status = (runStatus || "").toUpperCase();
  const failed = status.includes("FAIL") || status.includes("ABORT") || status.includes("TIMED");
  const done = !isRunning && (status.includes("SUCCEED") || (!!runStatus && !failed));

  const stages = ["Reading your inputs", "Working through the analysis", "Reviewing & challenging", "Preparing your result"];

  return (
    // Outer wraps at the page width so the "show the work" panel (the DAG / live agent
    // activity) can use it; the status summary card below stays at reading width.
    <div className="w-full max-w-[104rem] mx-auto p-2">
      <div className="max-w-3xl mx-auto rounded-2xl border border-tessera-border bg-tessera-card p-6 relative overflow-hidden">
        <div className="absolute inset-x-0 top-0 h-[3px] bg-tessera-gradient opacity-90" />

        {isRunning && (
          <>
            <div className="flex items-center gap-3">
              <span className="inline-block w-3 h-3 rounded-full bg-tessera-accent animate-pulse" />
              <div className="text-lg font-bold text-white">Your {ecoLabel} run is in progress</div>
            </div>
            <p className="text-sm text-gray-400 mt-1">We&apos;re analysing your inputs and preparing your result. You can leave this page — it&apos;ll appear in <button onClick={onYourResults} className="text-tessera-accent hover:underline">Your results</button> when it&apos;s done.</p>
            <div className="mt-5 space-y-2">
              {stages.map((s, i) => (
                <div key={s} className="flex items-center gap-3">
                  <span className={`w-2 h-2 rounded-full ${i === 0 ? "bg-tessera-accent animate-pulse" : "bg-gray-600"}`} />
                  <span className={`text-[13px] ${i === 0 ? "text-gray-200" : "text-gray-500"}`}>{s}</span>
                </div>
              ))}
            </div>
          </>
        )}

        {done && (
          <>
            <div className="flex items-center gap-3">
              <span className="text-green-400 text-lg">✓</span>
              <div className="text-lg font-bold text-white">Your result is ready</div>
            </div>
            <p className="text-sm text-gray-400 mt-1">Open the report to read it, ask the agents about it, and spotlight the key findings.</p>
            <div className="flex items-center gap-3 mt-5">
              <button onClick={onOpenReport} className="px-4 py-2 text-[13px] font-bold text-white rounded-lg bg-tessera-gradient shadow-[0_4px_14px_rgba(139,92,246,0.3)] hover:opacity-95 transition">Open your report →</button>
              <button onClick={onYourResults} className="px-3 py-2 text-[13px] font-semibold rounded-lg border border-tessera-border text-gray-300 hover:text-white hover:border-gray-400 transition">Your results</button>
            </div>
          </>
        )}

        {failed && !isRunning && (
          <>
            <div className="flex items-center gap-3">
              <span className="text-red-400 text-lg">⚠</span>
              <div className="text-lg font-bold text-white">This run didn&apos;t complete</div>
            </div>
            <p className="text-sm text-gray-400 mt-1">Something interrupted it. Use &ldquo;Show the work&rdquo; for details, or start again from Solutions.</p>
          </>
        )}

        <div className="mt-5 pt-4 border-t border-tessera-border">
          <button onClick={() => setShowWork((s) => !s)} className="text-[12px] text-gray-400 hover:text-tessera-accent">
            {showWork ? "▾ Hide the work" : "▸ Show the work (agents, live activity)"}
          </button>
        </div>
      </div>

      {showWork && <div className="mt-4">{children}</div>}
    </div>
  );
}
