"use client";

import { createContext, useContext, useEffect, useState, ReactNode } from "react";

export type ViewMode = "business" | "technical";

const ViewModeContext = createContext<{
  viewMode: ViewMode;
  setViewMode: (m: ViewMode) => void;
}>({ viewMode: "business", setViewMode: () => {} });

/**
 * Global Business ⇄ Technical view mode (docs/spec-business-view.md).
 * Persisted per browser; default Business. Changes which components render,
 * never which data is fetched — the technical view stays byte-identical.
 */
export function ViewModeProvider({ children }: { children: ReactNode }) {
  const [viewMode, setViewModeState] = useState<ViewMode>(() => {
    if (typeof window === "undefined") return "business";
    try {
      const saved = window.localStorage.getItem("tessera.viewMode");
      if (saved === "business" || saved === "technical") return saved;
    } catch {}
    return "business";
  });

  const setViewMode = (m: ViewMode) => {
    setViewModeState(m);
    try { window.localStorage.setItem("tessera.viewMode", m); } catch {}
  };

  // Reflect the mode on <html> so global styles can branch if needed.
  useEffect(() => {
    try { document.documentElement.setAttribute("data-view-mode", viewMode); } catch {}
  }, [viewMode]);

  return (
    <ViewModeContext.Provider value={{ viewMode, setViewMode }}>
      {children}
    </ViewModeContext.Provider>
  );
}

export function useViewMode() {
  return useContext(ViewModeContext);
}

/** Two-segment switch for the top bar. */
export function ViewModeToggle() {
  const { viewMode, setViewMode } = useViewMode();
  return (
    <div className="inline-flex items-center rounded-lg border border-tessera-border bg-tessera-card p-0.5 text-[11px] font-semibold">
      {(["business", "technical"] as ViewMode[]).map((m) => (
        <button
          key={m}
          onClick={() => setViewMode(m)}
          title={m === "business" ? "Business view — outcomes, no internals" : "Technical view — DAG, agents, telemetry"}
          className={`px-2.5 py-1 rounded-md transition capitalize ${
            viewMode === m
              ? "bg-tessera-accent/20 text-tessera-accent"
              : "text-gray-400 hover:text-gray-200"
          }`}
        >
          {m}
        </button>
      ))}
    </div>
  );
}
