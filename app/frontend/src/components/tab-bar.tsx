"use client";

import { RunTab, TabStatus } from "@/hooks/use-run-tabs";

const STATUS_DOT: Record<TabStatus, string> = {
  running: "bg-green-400 animate-pulse",
  succeeded: "bg-tessera-accent",
  failed: "bg-red-400",
  idle: "bg-gray-500",
};

export function TabBar({
  tabs,
  activeTabId,
  onSwitch,
  onClose,
}: {
  tabs: RunTab[];
  activeTabId: string | null;
  onSwitch: (tabId: string) => void;
  onClose: (tabId: string) => void;
}) {
  if (tabs.length === 0) return null;

  return (
    <div className="flex items-center gap-1 px-3 py-1.5 border-b border-tessera-border bg-tessera-surface overflow-x-auto shrink-0">
      {tabs.map((tab) => {
        const isActive = tab.id === activeTabId;
        return (
          <button
            key={tab.id}
            onClick={() => onSwitch(tab.id)}
            className={`flex items-center gap-2 px-3 py-1.5 rounded text-xs transition-all whitespace-nowrap ${
              isActive
                ? "bg-tessera-card border border-tessera-border text-white"
                : "text-gray-400 hover:text-gray-200 hover:bg-white/5"
            }`}
          >
            <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${STATUS_DOT[tab.status]}`} />
            <span className="font-medium truncate max-w-[120px]">{tab.ecosystemName}</span>
            <span
              onClick={(e) => { e.stopPropagation(); onClose(tab.id); }}
              className="text-gray-500 hover:text-red-400 ml-1 text-[10px]"
              title="Close tab"
            >
              ✕
            </span>
          </button>
        );
      })}
    </div>
  );
}
