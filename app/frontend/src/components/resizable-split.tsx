"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Horizontal two-pane split with a draggable divider.
 * `rightPct` is the right pane's width as a % of the container (persisted per storageKey).
 * The right pane is the DAG on the run/history screens and is bigger by default.
 */
export function ResizableSplit({
  left,
  right,
  defaultRightPct = 60,
  min = 28,
  max = 78,
  storageKey,
  className = "",
}: {
  left: React.ReactNode;
  right: React.ReactNode;
  defaultRightPct?: number;
  min?: number;
  max?: number;
  storageKey?: string;
  className?: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const [rightPct, setRightPct] = useState<number>(() => {
    if (typeof window !== "undefined" && storageKey) {
      const raw = window.localStorage.getItem(storageKey);
      const n = raw ? parseFloat(raw) : NaN;
      if (!Number.isNaN(n) && n >= min && n <= max) return n;
    }
    return defaultRightPct;
  });
  const dragging = useRef(false);

  const onMove = useCallback((clientX: number) => {
    const el = containerRef.current;
    if (!el) return;
    const rect = el.getBoundingClientRect();
    const fromRight = ((rect.right - clientX) / rect.width) * 100;
    const clamped = Math.max(min, Math.min(max, fromRight));
    setRightPct(clamped);
  }, [min, max]);

  useEffect(() => {
    const move = (e: MouseEvent) => { if (dragging.current) { e.preventDefault(); onMove(e.clientX); } };
    const up = () => {
      if (!dragging.current) return;
      dragging.current = false;
      document.body.style.userSelect = "";
      document.body.style.cursor = "";
      if (storageKey) { try { window.localStorage.setItem(storageKey, String(rightPct)); } catch { /* ignore */ } }
    };
    window.addEventListener("mousemove", move);
    window.addEventListener("mouseup", up);
    return () => { window.removeEventListener("mousemove", move); window.removeEventListener("mouseup", up); };
  }, [onMove, rightPct, storageKey]);

  const startDrag = (e: React.MouseEvent) => {
    e.preventDefault();
    dragging.current = true;
    document.body.style.userSelect = "none";
    document.body.style.cursor = "col-resize";
  };

  return (
    <div ref={containerRef} className={`flex min-h-0 min-w-0 ${className}`}>
      <div className="min-h-0 min-w-0 overflow-hidden" style={{ width: `calc(${100 - rightPct}% - 6px)` }}>
        {left}
      </div>
      <div
        role="separator"
        aria-orientation="vertical"
        onMouseDown={startDrag}
        onDoubleClick={() => setRightPct(defaultRightPct)}
        title="Drag to resize · double-click to reset"
        className="group w-3 shrink-0 flex items-center justify-center cursor-col-resize"
      >
        <div className="w-[3px] h-full rounded-full bg-tessera-border group-hover:bg-tessera-accent/60 transition-colors" />
      </div>
      <div className="min-h-0 min-w-0 overflow-hidden" style={{ width: `calc(${rightPct}% - 6px)` }}>
        {right}
      </div>
    </div>
  );
}
