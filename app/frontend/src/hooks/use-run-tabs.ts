"use client";

import { useCallback, useState } from "react";

export type TabStatus = "running" | "succeeded" | "failed" | "idle";

export interface RunTab {
  id: string;           // unique tab ID (= session_id from SFN)
  ecosystemId: string;
  ecosystemName: string;
  sessionId: string;    // Step Functions run name
  status: TabStatus;
  createdAt: number;
}

const STORAGE_KEY = "tessera.runTabs";
const ACTIVE_KEY = "tessera.activeTab";

function loadTabs(): RunTab[] {
  if (typeof window === "undefined") return [];
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? JSON.parse(raw) : [];
  } catch { return []; }
}

function saveTabs(tabs: RunTab[]) {
  if (typeof window === "undefined") return;
  window.localStorage.setItem(STORAGE_KEY, JSON.stringify(tabs));
}

function loadActiveId(): string | null {
  if (typeof window === "undefined") return null;
  return window.localStorage.getItem(ACTIVE_KEY) || null;
}

function saveActiveId(id: string | null) {
  if (typeof window === "undefined") return;
  if (id) window.localStorage.setItem(ACTIVE_KEY, id);
  else window.localStorage.removeItem(ACTIVE_KEY);
}

export function useRunTabs() {
  const [tabs, setTabs] = useState<RunTab[]>(loadTabs);
  const [activeTabId, setActiveTabId] = useState<string | null>(loadActiveId);

  const addTab = useCallback((tab: RunTab) => {
    setTabs((prev) => {
      // Don't duplicate
      if (prev.some((t) => t.id === tab.id)) {
        const updated = prev.map((t) => t.id === tab.id ? { ...t, ...tab } : t);
        saveTabs(updated);
        return updated;
      }
      const updated = [...prev, tab];
      saveTabs(updated);
      return updated;
    });
    setActiveTabId(tab.id);
    saveActiveId(tab.id);
  }, []);

  const removeTab = useCallback((tabId: string) => {
    setTabs((prev) => {
      const updated = prev.filter((t) => t.id !== tabId);
      saveTabs(updated);
      return updated;
    });
    setActiveTabId((current) => {
      if (current === tabId) {
        // Switch to the most recent remaining tab
        const remaining = tabs.filter((t) => t.id !== tabId);
        const next = remaining.length > 0 ? remaining[remaining.length - 1].id : null;
        saveActiveId(next);
        return next;
      }
      return current;
    });
  }, [tabs]);

  const switchTab = useCallback((tabId: string) => {
    setActiveTabId(tabId);
    saveActiveId(tabId);
  }, []);

  const updateTabStatus = useCallback((tabId: string, status: TabStatus) => {
    setTabs((prev) => {
      const updated = prev.map((t) => t.id === tabId ? { ...t, status } : t);
      saveTabs(updated);
      return updated;
    });
  }, []);

  const activeTab = tabs.find((t) => t.id === activeTabId) || null;

  return {
    tabs,
    activeTab,
    activeTabId,
    addTab,
    removeTab,
    switchTab,
    updateTabStatus,
  };
}
