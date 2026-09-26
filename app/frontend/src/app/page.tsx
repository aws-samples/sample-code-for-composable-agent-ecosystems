"use client";

// Services: Amazon Bedrock, AWS Lambda, Amazon Cognito, Amazon S3, AWS Step Functions, Amazon GuardDuty
import { useState, useEffect, useCallback, useMemo, useRef } from "react";
import { useAuth } from "@/components/auth-provider";
import { useAGUI } from "@/hooks/use-ag-ui";
import { useWebSocket } from "@/hooks/use-websocket";
import { useRunTabs, RunTab } from "@/hooks/use-run-tabs";
import { useClientWebSocket } from "@/hooks/use-client-ws";
import { BackStackContext, BackStackApi, useBackStack } from "@/hooks/use-back-stack";
import { AGUIEvent, StateDeltaEvent } from "@/lib/ag-ui-types";
import { RunView } from "@/components/run-view";
import { HomeView, type HomeNavTarget } from "@/components/home-view";
import { WorkflowVisualizer } from "@/components/workflow-visualizer";

import { SharedStatePanel } from "@/components/shared-state-panel";

import { DecisionModal } from "@/components/decision-modal";
import { BrowserLoginModal } from "@/components/browser-login-modal";
import { EventStream } from "@/components/event-stream";
import { TesseraLogo } from "@/components/tessera-logo";
import { ViewModeToggle, useViewMode } from "@/components/view-mode";
import { describeEco } from "@/components/business/business-grid";
import { SolutionPicker } from "@/components/business/solution-picker";
import { BusinessHome } from "@/components/business/business-home";
import { BusinessHistory } from "@/components/business/business-history";
import { BusinessRun } from "@/components/business/business-run";
import { BeautifyStudio } from "@/components/beautify-studio";
import { TesseraSpinner } from "@/components/tessera-spinner";
import { ConciergeChat } from "@/components/concierge-chat";
import { PresentationPlayer, PresentationOverlay, type Plan, type Highlight } from "@/components/presentation-player";
import { ArtifactViewer, type ArtifactReq } from "@/components/artifact-viewer";
import { GateDecisionDialog } from "@/components/gate-decision-dialog";
import { DecisionSurface } from "@/components/decision-surface";
import { QuorumAdvancedPanel, type QuorumSettings } from "@/components/quorum-advanced-panel";
import type { DecisionPoint } from "@/lib/decision";

import { BuilderGraph } from "@/components/builder-graph";
import { DeployWizard } from "@/components/deploy-wizard";
import { PastRunsModal } from "@/components/past-runs-modal";

import { TabBar } from "@/components/tab-bar";
import { RunTabContent } from "@/components/run-tab-content";
import { DeliberationRoom } from "@/components/deliberation-room";
import Link from "next/link";
import { SharingDialog } from "@/components/sharing-dialog";
import { ExportEcosystemDialog } from "@/components/export-ecosystem-dialog";
import { ReplayPlayer } from "@/components/replay-player";
import { GroupsPanel } from "@/components/groups-panel";
import { CatalogBrowser } from "@/components/catalog-browser";
import { EvaluationDashboard } from "@/components/evaluation-dashboard";
import ReactMarkdown from "react-markdown";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";
// Callers build catalog URLs as `${CATALOG_API}catalog/...` (no leading slash), so
// CATALOG_API MUST end in exactly one `/` when set — normalize here so a slash-less build
// can't produce a malformed host (`...amazonaws.comcatalog/...` → "Failed to fetch").
const CATALOG_API = (() => { const u = process.env.NEXT_PUBLIC_CATALOG_API_URL || ""; return u ? u.replace(/\/?$/, "/") : ""; })();
const WS_URL = process.env.NEXT_PUBLIC_WS_URL || (API_URL.replace(/^http/, "ws"));

// Authenticated fetch — adds Authorization header from Amplify session
async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const { fetchAuthSession } = await import("aws-amplify/auth");
  let token = "";
  try {
    const session = await fetchAuthSession({ forceRefresh: false });
    token = session.tokens?.idToken?.toString() || "";
  } catch { /* proceed without token */ }
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(url, { ...init, headers });
}

// Phase 5.1: mirror of compiler DEFAULT_TOOL_BUDGETS so the agent edit panel
// can show the default cap for each tool. Keep in sync with
// lambdas/save-ecosystem/handler.py DEFAULT_TOOL_BUDGETS.
const DEFAULT_TOOL_BUDGETS: Record<string, { leaf: number; synthesizer: number }> = {
  web_search:       { leaf: 6,  synthesizer: 0 },
  tavily_search:    { leaf: 6,  synthesizer: 0 },
  exa_search:       { leaf: 6,  synthesizer: 0 },
  // memory_read / memory_write are intentionally UNCAPPED (the inter-agent
  // handoff mechanism) — they show as "—" in the editor, matching the compiler.
  code_interpreter: { leaf: 3,  synthesizer: 5 },
  calculator:       { leaf: 10, synthesizer: 10 },
  current_time:     { leaf: 3,  synthesizer: 3 },
  generateUI:       { leaf: 5,  synthesizer: 8 },
  http_request:     { leaf: 6,  synthesizer: 2 },
};

interface Ecosystem {
  id: string;
  name: string;
  description: string;
  agents: number;
  strategy: string;
  owner?: string;
  inputs?: Array<{ id: string; type: string; label: string; accept?: string[]; required?: boolean; options?: string[]; default?: string }>;
  writes_to?: string[];
  reads_from_external?: string[];
  // System-managed ecosystems (Phase 5d) are repo-managed; users cannot
  // edit/delete and the AI builder cannot modify them. Backend
  // (list-ecosystems Lambda) sets this from the YAML's `platform: true`.
  platform?: boolean;
  domain?: string;
  domains?: string[];
  tags?: string[];
}

export default function SuperApp() {
  const { user, isAdmin, signOut, authFetch } = useAuth();
  const { viewMode } = useViewMode();
  const [ecosystems, setEcosystems] = useState<Ecosystem[]>([]);
  const [ecosystemsLoading, setEcosystemsLoading] = useState(true);

  // Multi-tab run management
  const ws = useWebSocket({ url: WS_URL });
  const { tabs, activeTab, activeTabId, addTab, removeTab, switchTab, updateTabStatus } = useRunTabs();
  const [openRunReq, setOpenRunReq] = useState<{ ecoId: string; runId: string } | null>(null);
  const [historyOpen, setHistoryOpen] = useState(false);
  const [lastOpenedRun, setLastOpenedRun] = useState<{ eco_id: string; run_id: string } | null>(null);
  const [presentation, setPresentation] = useState<Plan | null>(null);
  const [presHighlight, setPresHighlight] = useState<Highlight | null>(null);
  const [gridQueryReq, setGridQueryReq] = useState<string | null>(null);
  const [missionReq, setMissionReq] = useState<string | null>(null);
  const [builderEditReq, setBuilderEditReq] = useState<string | null>(null);
  const [replayReq, setReplayReq] = useState<{ ecoId: string; runName: string } | null>(null);
  const [builderConvo, setBuilderConvo] = useState(0);
  const [artifactReq, setArtifactReq] = useState<ArtifactReq | null>(null);
  const [spotlightSeq, setSpotlightSeq] = useState<{ target: string; note?: string }[] | null>(null);
  const [spotlightIdx, setSpotlightIdx] = useState(0);
  // profile menu — top-right account popover (email, last run, sign out)
  const [profileMenuOpen, setProfileMenuOpen] = useState(false);
  // Initial state restored from localStorage if there's an in-flight run.
  // useAGUI's trackedExec also reads from localStorage so SFN polling
  // resumes automatically. The two reads are independent but share the same
  // 'tessera.activeRun' key.
  const [selectedEcosystem, setSelectedEcosystem] = useState<string | null>(() => {
    if (typeof window === "undefined") return null;
    try {
      const saved = window.localStorage.getItem("tessera.selectedEco");
      if (saved) return saved;
      const raw = window.localStorage.getItem("tessera.activeRun");
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      return parsed?.ecosystemId || null;
    } catch { return null; }
  });
  const [sharedState, setSharedState] = useState<Record<string, unknown>>({});
  const [view, setView] = useState<"home" | "ecosystems" | "run" | "registry" | "builder" | "tools" | "help" | "admin" | "attention" | "domains" | "catalog" | "evaluation">(() => {
    if (typeof window === "undefined") return "home";
    const VALID = ["home", "ecosystems", "run", "registry", "builder", "tools", "help", "admin", "attention", "domains", "catalog", "evaluation"];
    try {
      // Restore the current view on REFRESH (same tab session) so the user stays on the
      // page they're on. We use sessionStorage — it survives a refresh but is cleared when
      // the tab/session ends, so a FRESH login (new session) still lands on Home rather
      // than a stale view. This satisfies both "login → Home" and "refresh stays put".
      const saved = window.sessionStorage.getItem("tessera.view.session");

      // Resume a run on refresh so a mid-run reload doesn't drop the user out of it — but
      // ONLY when the user was actually on the run view (or has no saved view yet, e.g. a
      // legacy/first landing). `tessera.activeRun` is intentionally kept in localStorage
      // even after a run finishes (so the run view can rehydrate a completed DAG), so its
      // mere presence must NOT hijack navigation: without the saved-view guard, every
      // refresh after any run completes would yank the user back to the run view regardless
      // of the page they were on.
      if (!saved || saved === "run") {
        const raw = window.localStorage.getItem("tessera.activeRun");
        if (raw) {
          const parsed = JSON.parse(raw);
          if (parsed?.ecosystemId && parsed?.execName) return "run";
        }
      }

      if (saved && VALID.includes(saved)) return saved as any;
    } catch { /* ignore */ }
    // Fresh session / first-time landing: the Home overview / launchpad (technical mode).
    return "home";
  });
  const [catalogDomain, setCatalogDomain] = useState<string>(() => {
    if (typeof window === "undefined") return "";
    try { return window.localStorage.getItem("tessera.catalogDomain") || ""; } catch { return ""; }
  });
  const openCatalog = (id?: string) => { setCatalogDomain(id || ""); setView("catalog"); };
  // Business interactive outcome: the report opened in the Studio (read/ask/spotlight).
  const [bizReport, setBizReport] = useState<{ sessionId: string; artifactKey: string; label: string } | null>(null);
  const [openingReport, setOpeningReport] = useState(false);   // Tessera anim while the report loads
  const [bizIntake, setBizIntake] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);           // keep the intake mounted in business mode
  // Resolve a run's produced HTML deliverable and open it in the Studio; fall back to
  // the technical run view if there isn't one. Shared by Your-results + the business run view.
  const openReport = async (ecoId: string, runId: string) => {
    setOpeningReport(true);
    try {
      const r = await authFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(ecoId)}/runs/${encodeURIComponent(runId)}`);
      const d = await r.json().catch(() => ({}));
      const arts = (d?.outcome?.declared_artifacts || []) as any[];
      const produced = (d?.outcome?.produced_artifacts || []) as string[];
      const html = arts.find((a) => produced.includes(a.id) && ((a.format || "").toLowerCase() === "html" || (a.save_as || "").toLowerCase().endsWith(".html")));
      if (html) { setBizReport({ sessionId: runId, artifactKey: html.artifact_key, label: html.download_label || html.save_as }); return; }
      setSelectedEcosystem(ecoId); setOpenRunReq({ ecoId, runId }); setView("ecosystems");
    } catch {
      setSelectedEcosystem(ecoId); setOpenRunReq({ ecoId, runId }); setView("ecosystems");
    } finally {
      setOpeningReport(false);
    }
  };
  // Once a run launches (view flips to "run"), the business intake is done.
  useEffect(() => { if (view === "run") setBizIntake(false); }, [view]);

  // "home" is the shared product launchpad — it renders in BOTH business and technical
  // modes as the common front door. Business mode navigates OUT of Home via the HomeView
  // feature cards (onNavigate → setView), since business mode has no sidebar nav.

  // Persist the current view to sessionStorage so a REFRESH stays on the same page
  // (static export = no server routing for the in-app views). sessionStorage (not
  // localStorage) means a fresh login in a new tab/session still lands on Home.
  useEffect(() => { try { window.sessionStorage.setItem("tessera.view.session", view); } catch {} }, [view]);

  // Browser Back/Forward support. The app is a state-driven SPA (navigation is a `view`
  // state var, not a route), so without this the browser history never changes and Back
  // does nothing between in-app pages. We mirror `view` into the URL hash (#builder,
  // #ecosystems, …) — safe for the static export since the hash never hits the server —
  // pushing a history entry on user navigation and restoring `view` on popstate.
  // `viewFromPopstateRef` guards against the pushState effect re-firing for a change that
  // popstate itself just made (which would otherwise defeat Forward and stack duplicates).
  // `viewRef` mirrors the current `view` so the popstate handler (registered once) always
  // sees the latest value without needing `view` in its dependency array.
  const viewRef = useRef(view);
  useEffect(() => { viewRef.current = view; }, [view]);
  // `suppressPushRef` is set by the popstate handler so the view-sync effect it triggers
  // does NOT push a new entry (which would defeat Forward and stack duplicates).
  const suppressPushRef = useRef(false);
  const historySeededRef = useRef(false);

  // In-view drill-down back-stack (see hooks/use-back-stack.ts). Each entry is a drill-down
  // level (catalog domain/record, domain detail, run detail) that Back should unwind BEFORE
  // leaving the top-level view. Steps are also tagged into browser history so a browser Back
  // pops them one at a time.
  const backStackRef = useRef<{ id: number }[]>([]);
  const backHandlersRef = useRef<Record<number, () => void>>({});
  const backStepSeqRef = useRef(0);
  const pushBackStep = useCallback((back: () => void) => {
    const id = ++backStepSeqRef.current;
    backStackRef.current.push({ id });
    backHandlersRef.current[id] = back;
    try { window.history.pushState({ __backStep: id, view: viewRef.current }, "", window.location.hash); } catch {}
    // Disposer: called when the component reverts the drill-down itself (in-app back link)
    // or unmounts. If this step is still the current top of the browser stack, step back so
    // the browser history stays in sync with the app state.
    return () => {
      const idx = backStackRef.current.findIndex((s) => s.id === id);
      if (idx === -1) return; // already unwound by a browser Back
      const wasTop = idx === backStackRef.current.length - 1;
      backStackRef.current.splice(idx, 1);
      delete backHandlersRef.current[id];
      if (wasTop && window.history.state && window.history.state.__backStep === id) {
        suppressPushRef.current = true; // the resulting popstate is app-initiated
        try { window.history.back(); } catch {}
      }
    };
  }, []);
  const backStackApi = useMemo<BackStackApi>(() => ({ pushStep: pushBackStep }), [pushBackStep]);

  // Seed the FIRST history entry once, synchronously on mount, so the current page has a
  // history.state{view} to return to and the URL carries its hash. Using replaceState here
  // (not push) means the first Back press leaves the app rather than being a dead in-app stop.
  if (typeof window !== "undefined" && !historySeededRef.current) {
    historySeededRef.current = true;
    try { window.history.replaceState({ view }, "", `#${view}`); } catch {}
  }

  // Push a new history entry on every genuine view change (not the ones popstate caused).
  useEffect(() => {
    if (typeof window === "undefined") return;
    if (suppressPushRef.current) { suppressPushRef.current = false; return; }
    // If history.state already reflects this view (e.g. the seed above just set it), don't
    // stack a duplicate.
    if ((window.history.state && window.history.state.view) === view && !window.history.state.__backStep) return;
    try { window.history.pushState({ view }, "", `#${view}`); } catch {}
  }, [view]);

  // Back/Forward: unwind an in-view drill-down step first; only if none is pending do we
  // restore the top-level view from the popped entry's state (fallback to hash).
  useEffect(() => {
    if (typeof window === "undefined") return;
    const VALID = ["home", "ecosystems", "run", "registry", "builder", "tools", "help", "admin", "attention", "domains", "catalog", "evaluation"];
    const onPop = (e: PopStateEvent) => {
      // If we still have a registered drill-down step, this Back should unwind ONE level.
      // (A browser Back off a __backStep entry lands on the entry below it; our stack top is
      // the step being left.)
      if (backStackRef.current.length > 0) {
        const step = backStackRef.current.pop()!;
        const handler = backHandlersRef.current[step.id];
        delete backHandlersRef.current[step.id];
        if (handler) { try { handler(); } catch {} }
        return; // do NOT also change the view
      }
      const fromState = e.state && typeof e.state.view === "string" ? e.state.view : "";
      const target = fromState || window.location.hash.replace(/^#/, "");
      if (VALID.includes(target) && target !== viewRef.current) {
        suppressPushRef.current = true;
        setView(target as typeof view);
      }
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  // When the top-level view changes (e.g. user clicks a different nav item), any pending
  // drill-down steps belong to the OLD view and must be cleared so they don't leak into the
  // new view's Back behavior. The browser entries are harmless (they resolve to the same
  // view); we just drop the handlers so onPop falls through to view-restore.
  useEffect(() => {
    backStackRef.current = [];
    backHandlersRef.current = {};
  }, [view]);
  useEffect(() => {
    try {
      if (selectedEcosystem) window.localStorage.setItem("tessera.selectedEco", selectedEcosystem);
      else window.localStorage.removeItem("tessera.selectedEco");
    } catch {}
  }, [selectedEcosystem]);
  useEffect(() => { try { window.localStorage.setItem("tessera.catalogDomain", catalogDomain); } catch {} }, [catalogDomain]);

  // Fetch ecosystems from real orchestrator (once — authFetch's identity changes when
  // the id token loads, which would otherwise re-fire this and double the slow call).
  const ecosystemsFetchedRef = useRef(false);
  useEffect(() => {
    if (ecosystemsFetchedRef.current) return;
    ecosystemsFetchedRef.current = true;
    authFetch(`${API_URL}/api/ecosystems`)
      .then((r) => r.json())
      .then(setEcosystems)
      .catch(() => setEcosystems([]))
      .finally(() => setEcosystemsLoading(false));
  }, [authFetch]);

  // Deep-link from the ecosystem details page: /?eco=<id>&action=run|edit.
  // page.tsx is state-driven (no router params), so we read the query once on mount,
  // trigger the matching in-app flow, then clean the URL so it doesn't re-fire.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const p = new URLSearchParams(window.location.search);
    const ecoId = p.get("eco");
    const action = p.get("action");
    if (!ecoId || !action) return;
    if (action === "run") { setView("ecosystems"); setMissionReq(ecoId); }
    else if (action === "edit") { setView("builder"); setBuilderEditReq(ecoId); }
    window.history.replaceState({}, "", window.location.pathname);
  }, []);

  const handleEvent = (event: AGUIEvent) => {
    if (event.type === "STATE_DELTA") {
      setSharedState((prev) => ({ ...prev, ...(event as StateDeltaEvent) }));
    }
  };

  const { events, isRunning, runStatus, connectionState, inFlightPhase, outcome, sessionId, pendingDecision, startRun, submitDecision, trackExecution } = useAGUI({
    url: API_URL,
    onEvent: handleEvent,
  });

  const launchEcosystem = async (id: string, mission: string, inputs?: Record<string, string>, dryRun?: boolean, runConfig?: Record<string, unknown>, attachments?: Array<{ name: string; ref: string }>, missionTranscript?: Array<{ role: string; content: string }>) => {
    setSelectedEcosystem(id);
    setSharedState({});
    setView("run");
    try {
      const sid = await ws.launch(id, mission, inputs, user?.userId, dryRun, runConfig, attachments, missionTranscript);
      const ecoName = ecosystems.find((e) => e.id === id)?.name || id;
      addTab({
        id: sid,
        ecosystemId: id,
        ecosystemName: ecoName,
        sessionId: sid,
        status: "running",
        createdAt: Date.now(),
      });
    } catch (e) {
      console.error("Launch failed:", e);
    }
  };

  const [sidebarOpen, setSidebarOpen] = useState(true);

  // Attention inbox — async human-in-the-loop queue. Poll the user-scoped
  // GET /api/attention so blocked gates/questions surface even when the user
  // isn't watching the run. Live badge in the sidebar; full list in the view.
  const [attentionItems, setAttentionItems] = useState<any[]>([]);
  // Authenticated-browser sign-in modal (browser_login gate).
  const [browserLogin, setBrowserLogin] = useState<{ sessionId: string; agentId: string; domain?: string } | null>(null);
  useEffect(() => {
    let stop = false;
    const load = async () => {
      try {
        const r = await apiFetch(`${API_URL}/api/attention`);
        if (!r.ok) return;
        const d = await r.json();
        if (!stop && Array.isArray(d.items)) setAttentionItems(d.items);
      } catch { /* ignore */ }
    };
    load();
    const t = setInterval(load, 20000);
    return () => { stop = true; clearInterval(t); };
  }, []);

  // Derived: overall run status from tabs (for sidebar indicators)
  const anyRunning = tabs.some((t) => t.status === "running");
  const tabRunStatus = activeTab?.status || (anyRunning ? "running" : runStatus);

  // Auto-adopt: if there's a tracked execution from the old system (localStorage)
  // but no tab for it, create one so the new tab UI shows it.
  // Legacy migration (ONE-SHOT): adopt a pre-tabs run from `tessera.activeRun`
  // only on first mount. Previously this re-ran every time tabs.length hit 0,
  // so closing the last tab instantly re-created it (the "can't close the tab"
  // bug). The ref guard ensures it runs at most once per page load.
  const legacyAdoptedRef = useRef(false);
  useEffect(() => {
    if (legacyAdoptedRef.current) return;
    legacyAdoptedRef.current = true;
    if (tabs.length === 0 && typeof window !== "undefined") {
      try {
        const raw = window.localStorage.getItem("tessera.activeRun");
        if (raw) {
          const parsed = JSON.parse(raw);
          if (parsed?.ecosystemId && parsed?.execName) {
            const ecoName = ecosystems.find((e) => e.id === parsed.ecosystemId)?.name || parsed.ecosystemId;
            addTab({
              id: parsed.execName,
              ecosystemId: parsed.ecosystemId,
              ecosystemName: ecoName,
              sessionId: parsed.execName,
              status: "running",
              createdAt: Date.now(),
            });
          }
        }
      } catch {}
    }
  }, [ecosystems, tabs.length, addTab]);

  // Close a tab AND consume the legacy `tessera.activeRun` key if it points at
  // this run — otherwise a page reload would re-adopt and resurrect it.
  const closeTab = useCallback((tabId: string) => {
    try {
      const raw = window.localStorage.getItem("tessera.activeRun");
      if (raw && JSON.parse(raw)?.execName === tabId) window.localStorage.removeItem("tessera.activeRun");
    } catch {}
    removeTab(tabId);
  }, [removeTab]);

  // Auto-cleanup: remove completed/failed tabs older than 5 min, cap at 8 total
  useEffect(() => {
    const cleanup = setInterval(() => {
      const now = Date.now();
      const MAX_TABS = 8;
      const EXPIRE_MS = 5 * 60 * 1000;
      const expired = tabs.filter(
        (t) => (t.status === "succeeded" || t.status === "failed") && (now - t.createdAt > EXPIRE_MS)
      );
      for (const t of expired) removeTab(t.id);
      // Cap: remove oldest completed if over limit
      if (tabs.length > MAX_TABS) {
        const completed = tabs.filter((t) => t.status !== "running").sort((a, b) => a.createdAt - b.createdAt);
        for (const t of completed.slice(0, tabs.length - MAX_TABS)) removeTab(t.id);
      }
    }, 30000);
    return () => clearInterval(cleanup);
  }, [tabs, removeTab]);

  return (
    <BackStackContext.Provider value={backStackApi}>
    <div className="h-screen flex overflow-hidden">
      {/* Sidebar (technical mode only) */}
      {viewMode === "technical" && (
      <nav className={`${sidebarOpen ? "w-60" : "w-14"} h-full shrink-0 transition-all duration-200 bg-tessera-surface border-r border-tessera-border p-3 flex flex-col`}>
        {/* Logo + collapse toggle */}
        <div className="flex items-center gap-3 mb-1">
          <button onClick={() => setSidebarOpen(!sidebarOpen)} className="shrink-0" title={sidebarOpen ? "Collapse" : "Expand"}>
            <TesseraLogo size={32} />
          </button>
          {sidebarOpen && <span className="text-lg font-bold tessera-gradient">Tessera</span>}
        </div>
        {/* bedrock attribution */}
        {sidebarOpen && <div className="text-[10px] text-gray-500 mb-6 pl-1">Powered by Amazon Bedrock</div>}
        {!sidebarOpen && <div className="h-6" title="Powered by Amazon Bedrock" />}
        {sidebarOpen && <div className="mb-4"><ViewModeToggle /></div>}

        <div className="space-y-1 flex-1">
          {/* Home — the product overview / launchpad (first landing) */}
          <NavItem icon="🏠" label="Home" collapsed={!sidebarOpen} active={view === "home"} onClick={() => setView("home")} />

          {/* Operate — the daily run loop */}
          <NavItem icon="⬡" label="Ecosystems" collapsed={!sidebarOpen} active={view === "ecosystems"} onClick={() => setView("ecosystems")} />
          <NavItem icon="▶" label={`Runs${tabs.length > 0 ? ` (${tabs.length})` : ""}`} collapsed={!sidebarOpen} active={view === "run"} onClick={() => setView("run")} />
          <NavItem icon="🔔" label={`Attention${attentionItems.length > 0 ? ` (${attentionItems.length})` : ""}`} collapsed={!sidebarOpen} active={view === "attention"} onClick={() => setView("attention")} />

          {/* Author & configure */}
          {/* Agent Registry — hidden for now (restore by uncommenting) */}
          {/* <NavItem icon="◈" label="Agent Registry" collapsed={!sidebarOpen} active={view === "registry"} onClick={() => setView("registry")} /> */}
          <NavItem icon="⚙" label="Builder" collapsed={!sidebarOpen} active={view === "builder"} onClick={() => setView("builder")} />
          <NavItem icon="🗂" label="Domains" collapsed={!sidebarOpen} active={view === "domains"} onClick={() => setView("domains")} />
          <NavItem icon="📚" label="Catalog Browser" collapsed={!sidebarOpen} active={view === "catalog"} onClick={() => openCatalog()} />
          <NavItem icon="🎯" label="Evaluation" collapsed={!sidebarOpen} active={view === "evaluation"} onClick={() => setView("evaluation")} />
          <NavItem icon="🔧" label="Tool Catalog" collapsed={!sidebarOpen} active={view === "tools"} onClick={() => setView("tools")} />

          {/* Meta */}
          <NavItem icon="?" label="Help" collapsed={!sidebarOpen} active={view === "help"} onClick={() => setView("help")} />
          {isAdmin && <NavItem icon="👤" label="Admin" collapsed={!sidebarOpen} active={view === "admin" as any} onClick={() => setView("admin" as any)} />}
        </div>

        {/* profile menu — run status moved to the top-right profile avatar (presence dot);
            "Amazon Bedrock connected" replaced by the "Powered by Amazon Bedrock" attribution
            under the logo above. Sidebar footer status lines removed. */}
      </nav>
      )}

      {/* Main Content */}
      <main className="flex-1 min-w-0 min-h-0 p-6 overflow-auto flex flex-col">
        {viewMode === "business" && (
          <div className="sticky top-0 -mx-6 -mt-6 mb-6 z-20 px-8 py-4 border-b border-tessera-border bg-[#0d1120] flex items-center gap-4">
            {selectedEcosystem ? (() => {
              const eObj = ecosystems.find((e) => e.id === selectedEcosystem) as any;
              const dd = eObj ? describeEco(eObj) : { icon: "◆", title: selectedEcosystem, tagline: "" };
              return (
                <>
                  <div className="w-11 h-11 rounded-[13px] bg-tessera-accent/15 border border-tessera-accent/35 flex items-center justify-center text-[22px]">{dd.icon}</div>
                  <div className="min-w-0">
                    <div className="text-lg font-extrabold text-white truncate">{dd.title}</div>
                    <div className="text-[12.5px] text-gray-400 truncate">{dd.tagline}</div>
                  </div>
                </>
              );
            })() : <span className="text-lg font-bold tessera-gradient">Solutions</span>}
            <div className="flex-1" />
            {selectedEcosystem && (
              <>
                <button onClick={() => setPickerOpen(true)}
                  title="Choose a different solution"
                  className="text-[12px] text-gray-400 border border-tessera-border rounded-lg px-3 py-1.5 bg-tessera-card hover:text-white hover:border-gray-400 transition">⇄ Switch solution</button>
                <div className="inline-flex rounded-lg border border-tessera-border bg-tessera-card p-0.5 text-[11px] font-semibold">
                  <button onClick={() => { setView("ecosystems"); setBizIntake(false); }} className={`px-2.5 py-1 rounded-md ${(view as any) !== "results" && view !== "run" ? "bg-tessera-accent/20 text-tessera-accent" : "text-gray-400 hover:text-gray-200"}`}>Home</button>
                  <button onClick={() => setView("results" as any)} className={`px-2.5 py-1 rounded-md ${(view as any) === "results" ? "bg-tessera-accent/20 text-tessera-accent" : "text-gray-400 hover:text-gray-200"}`}>Results</button>
                </div>
              </>
            )}
            {tabs.length > 0 && (
              <button onClick={() => setView("run")}
                className={`inline-flex items-center gap-1.5 text-[12px] font-semibold rounded-lg px-3 py-1.5 border transition ${view === "run" ? "border-tessera-accent/40 bg-tessera-accent/15 text-tessera-accent" : "border-tessera-border bg-tessera-card text-gray-300 hover:text-white hover:border-gray-400"}`}
                title="Return to your run">
                {tabs.some((t) => t.status === "running") && <span className="w-1.5 h-1.5 rounded-full bg-emerald-400 animate-pulse" />}
                Runs <span className="text-gray-500 font-normal">({tabs.length})</span>
              </button>
            )}
            {attentionItems.length > 0 && (
              <button onClick={() => setView("attention")}
                className={`relative text-[14px] leading-none rounded-lg px-2.5 py-1.5 border transition ${view === "attention" ? "border-tessera-accent/40 bg-tessera-accent/15" : "border-tessera-border bg-tessera-card hover:border-gray-400"}`}
                title="Attention needed">
                🔔
                <span className="absolute -top-1.5 -right-1.5 min-w-[16px] h-[16px] px-1 rounded-full bg-rose-500 text-white text-[10px] font-bold flex items-center justify-center">{attentionItems.length}</span>
              </button>
            )}
            <ViewModeToggle />
          </div>
        )}
        {view === "home" && (
          <HomeView
            ecosystemCount={ecosystems.length}
            runningCount={tabs.filter((t) => t.status === "running").length}
            attentionCount={attentionItems.length}
            isAdmin={isAdmin}
            onNavigate={(target: HomeNavTarget) => {
              if (target === "catalog") openCatalog();
              else setView(target);
            }}
          />
        )}
        {view === ("results" as any) && viewMode === "business" && (
          <BusinessHistory
            ecosystems={(selectedEcosystem ? ecosystems.filter((e) => e.id === selectedEcosystem) : ecosystems) as any}
            defaultEcoId={selectedEcosystem}
            onOpen={openReport}
          />
        )}
        {view === "ecosystems" && ecosystemsLoading && (
          <div className="flex flex-col items-center justify-center py-16 gap-3">
            <TesseraSpinner size={48} />
            <p className="text-xs text-gray-500">Loading ecosystems…</p>
          </div>
        )}
        {view === "ecosystems" && !ecosystemsLoading && viewMode === "business" && !missionReq && !openRunReq && !bizIntake && (
          selectedEcosystem && ecosystems.find((e) => e.id === selectedEcosystem)
            ? <BusinessHome
                eco={ecosystems.find((e) => e.id === selectedEcosystem) as any}
                onStart={() => { setBizIntake(true); setMissionReq(selectedEcosystem!); }}
                onOpen={(rid) => openReport(selectedEcosystem!, rid)}
                onSeeAll={() => setView("results" as any)}
              />
            : null
        )}
        {view === "ecosystems" && !ecosystemsLoading && !(viewMode === "business" && !missionReq && !openRunReq && !bizIntake) && (
          <EcosystemsView ecosystems={ecosystems} hideGrid={viewMode === "business"} onIntakeClose={() => setBizIntake(false)} onLaunch={launchEcosystem} isRunning={isRunning} onRunViewerChange={setHistoryOpen} openRunRequest={openRunReq} onOpenRunHandled={() => setOpenRunReq(null)} gridQueryRequest={gridQueryReq} onGridQueryHandled={() => setGridQueryReq(null)} missionRequest={missionReq} onMissionHandled={() => setMissionReq(null)} onTrackRedrive={(ecosystemId, execName) => {
            // Redrive was initiated from the past-runs modal. Switch to the
            // live run view and start polling SFN for the resumed execution.
            setSelectedEcosystem(ecosystemId);
            setView("run");
            trackExecution(ecosystemId, execName);
          }} onTrackOptimize={(optimizerSessionId, targetEcoId) => {
            // Phase 5d: optimizer was triggered from past-runs. The optimizer
            // is itself an ecosystem (platform-optimizer); switch to its
            // run-window and track the new SFN execution so the user
            // immediately sees Q/T/C analysts running.
            const optimizerEcoId = "platform-optimizer";
            // Persist the optimizer→target mapping so the apply panel
            // (rendered by RunView when the optimizer run completes) can
            // pre-fill the target ecosystem ID. Survives page reload.
            try {
              const raw = window.localStorage.getItem("tessera.optimizerTargets") || "{}";
              const map = JSON.parse(raw);
              map[optimizerSessionId] = targetEcoId;
              window.localStorage.setItem("tessera.optimizerTargets", JSON.stringify(map));
            } catch { /* ignore quota / parse errors */ }
            setSelectedEcosystem(optimizerEcoId);
            setView("run");
            trackExecution(optimizerEcoId, optimizerSessionId);
          }} />
        )}
        {view === "run" && (
          <>
            <TabBar tabs={tabs} activeTabId={activeTabId} onSwitch={switchTab} onClose={closeTab} />
            {tabs.map((tab) => (
              <div key={tab.id} style={{ display: tab.id === activeTabId ? "contents" : "none" }}>
                <RunTabContent
                  tab={tab}
                  subscribe={ws.subscribe}
                  onStatusChange={(status) => updateTabStatus(tab.id, status)}
                >
                  {(runProps) => {
                    const isQuorum = runProps.events.some((e: any) => e?.type === "RUN_STARTED" && (e.archetype === "quorum" || e.interactive));
                    if (isQuorum) return (
                      <>
                        <DeliberationRoom sessionId={runProps.sessionId} events={runProps.events} send={ws.send} />
                        {runProps.pendingGates.length > 0 && (
                          <GateDecisionDialog key={runProps.pendingGates[0].task_token} decision={runProps.pendingGates[0]} onResolved={() => {}} />
                        )}
                      </>
                    );
                    return (
                    <>
                      {viewMode === "business" ? (
                        <BusinessRun
                          ecoLabel={tab.ecosystemId.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase())}
                          sessionId={runProps.sessionId}
                          isRunning={runProps.isRunning}
                          runStatus={runProps.runStatus as any}
                          onOpenReport={() => openReport(tab.ecosystemId, runProps.sessionId)}
                          onYourResults={() => setView("results" as any)}
                        >
                          <RunView
                            ecosystem={tab.ecosystemId}
                            events={runProps.events}
                            sharedState={{}}
                            isRunning={runProps.isRunning}
                            runStatus={runProps.runStatus as any}
                            connectionState={runProps.connectionState as any}
                            inFlightPhase={runProps.inFlightPhase}
                            sessionId={runProps.sessionId}
                            outcome={runProps.outcome}
                          />
                        </BusinessRun>
                      ) : (
                      <RunView
                        ecosystem={tab.ecosystemId}
                        events={runProps.events}
                        sharedState={{}}
                        isRunning={runProps.isRunning}
                        runStatus={runProps.runStatus as any}
                        connectionState={runProps.connectionState as any}
                        inFlightPhase={runProps.inFlightPhase}
                        sessionId={runProps.sessionId}
                        outcome={runProps.outcome}
                      />
                      )}
                      {/* Show gate decision dialog from polling (reliable, doesn't depend on WS) */}
                      {runProps.pendingGates.length > 0 && (
                        <GateDecisionDialog
                          key={runProps.pendingGates[0].task_token}
                          decision={runProps.pendingGates[0]}
                          onResolved={() => {}}
                        />
                      )}
                    </>
                    );
                  }}
                </RunTabContent>
              </div>
            ))}
            {tabs.length === 0 && (
              /* Fallback: legacy single-run view (for restored runs from before tabs) */
              <RunView
                ecosystem={selectedEcosystem}
                events={events}
                sharedState={sharedState}
                isRunning={isRunning}
                runStatus={runStatus}
                connectionState={connectionState}
                inFlightPhase={inFlightPhase}
                sessionId={sessionId}
                outcome={outcome}
              />
            )}
          </>
        )}
        {view === "registry" && <RegistryView />}
        {view === "builder" && <BuilderView autoConverse={builderConvo} editEcoId={builderEditReq} onEditHandled={() => setBuilderEditReq(null)} />}
        {view === "domains" && <DomainBuilderView onBrowseCatalog={openCatalog} />}
        {view === "catalog" && <CatalogBrowserView initialDomain={catalogDomain} />}
        {view === "evaluation" && <EvaluationDashboard />}
        {view === "tools" && <ToolCatalogView />}
        {view === "help" && <HelpView />}
        {view === "admin" && <AdminView />}
        {view === "attention" && (
          <div className="max-w-3xl">
            <h2 className="text-xl font-bold mb-1">Attention</h2>
            <p className="text-xs text-gray-500 mb-5">Conversations waiting on you — quality gates, builder questions, approvals. Click one to open it.</p>
            {attentionItems.length === 0 ? (
              <div className="text-sm text-gray-500 bg-tessera-surface border border-tessera-border rounded-lg p-6 text-center">Nothing needs your attention right now.</div>
            ) : (
              <div className="space-y-2">
                {attentionItems.map((it) => {
                  const sid = String(it.session_id || "");
                  const ecoId = sid.includes("-") ? sid.slice(0, sid.lastIndexOf("-")) : sid;
                  const typeLabel = it.type === "challenger" ? "Quality gate" : it.type === "builder_question" ? "Builder question" : it.type === "design_review" ? "Design review" : it.type === "approval" ? "Approval" : it.type === "browser_login" ? "Sign-in needed" : "Needs attention";
                  const deadline = it.deadline ? new Date(Number(it.deadline) * 1000) : null;
                  const overdue = deadline ? deadline.getTime() < Date.now() : false;

                  // Authenticated-browser sign-in: two actions instead of opening
                  // the run — "Open sign-in" mints a live-view URL, "Done" saves
                  // the profile. See docs/features/browser-tool.md.
                  if (it.type === "browser_login") {
                    const dom = (it.summary || "").match(/Sign in to (\S+)/)?.[1];
                    return (
                      <div key={`${it.ownerId}#${it.itemId}`} className="w-full text-left bg-tessera-surface border border-tessera-border rounded-lg p-3">
                        <div className="flex items-center gap-2 mb-1">
                          <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30">{typeLabel}</span>
                          <span className="text-xs font-mono text-gray-400 truncate">{sid}</span>
                        </div>
                        <div className="text-sm text-gray-200 mb-2">{it.summary || "Sign in so Tessera's browser can access this site."}</div>
                        <div className="flex gap-2">
                          <button onClick={() => setBrowserLogin({ sessionId: sid, agentId: it.agent_id, domain: dom })} className="px-3 py-1.5 text-xs rounded-md bg-tessera-accent/20 text-tessera-accent border border-tessera-accent/40 hover:bg-tessera-accent/30 transition">Open sign-in</button>
                        </div>
                        <div className="text-[10px] text-gray-500 mt-1.5">Opens a live browser here — sign in (SSO/MFA fine), then Done. The paused run resumes authenticated.</div>
                      </div>
                    );
                  }
                  return (
                    <button
                      key={`${it.ownerId}#${it.itemId}`}
                      onClick={() => {
                        if (!ecoId || !sid) return;
                        setSelectedEcosystem(ecoId);
                        setView("run");
                        // Open (or focus) a run tab for this session so RunTabContent
                        // mounts, polls pending_gates, and renders the gate dialog.
                        // (trackExecution alone doesn't create a tab → no dialog.)
                        const ecoName = ecosystems.find((e) => e.id === ecoId)?.name || ecoId;
                        addTab({ id: sid, ecosystemId: ecoId, ecosystemName: ecoName, sessionId: sid, status: "running", createdAt: Date.now() });
                      }}
                      className="w-full text-left bg-tessera-surface border border-tessera-border hover:border-tessera-accent/50 rounded-lg p-3 transition"
                    >
                      <div className="flex items-center gap-2 mb-1">
                        <span className="text-[10px] uppercase tracking-wide px-1.5 py-0.5 rounded bg-purple-500/15 text-purple-300 border border-purple-500/30">{typeLabel}</span>
                        <span className="text-xs font-mono text-gray-400 truncate">{sid}</span>
                        {deadline && <span className={`ml-auto text-[10px] ${overdue ? "text-red-400" : "text-gray-500"}`}>{overdue ? "overdue" : `by ${deadline.toLocaleString()}`}</span>}
                      </div>
                      <div className="text-sm text-gray-200">{it.summary || "(no summary)"}</div>
                    </button>
                  );
                })}
              </div>
            )}
          </div>
        )}
      </main>

      {/* Decision Modal */}
      {pendingDecision && (
        <DecisionModal decision={pendingDecision} onSubmit={submitDecision} />
      )}

      {/* Authenticated-browser sign-in (browser_login gate) */}
      {browserLogin && (
        <BrowserLoginModal
          apiUrl={API_URL}
          sessionId={browserLogin.sessionId}
          agentId={browserLogin.agentId}
          domain={browserLogin.domain}
          onClose={(resolved) => {
            setBrowserLogin(null);
            if (resolved) {
              // refresh the attention inbox (the item was resolved server-side)
              apiFetch(`${API_URL}/api/attention`).then(r => r.ok ? r.json() : null).then(d => { if (d && Array.isArray(d.items)) setAttentionItems(d.items); }).catch(() => {});
            }
          }}
        />
      )}

      {/* profile menu — top-right account popover (email, last run, sign out) */}
      {user && (
        <div className="fixed top-4 right-4 z-[60]">
          <button
            onClick={() => setProfileMenuOpen((o) => !o)}
            className="relative w-9 h-9 rounded-full bg-tessera-gradient text-white text-sm font-bold flex items-center justify-center shadow-lg shadow-purple-500/30 hover:scale-105 transition"
            title={`${user.email} — ${
              tabRunStatus === "running" ? "Run in progress" :
              tabRunStatus === "failed" ? "Last run failed" :
              tabRunStatus === "succeeded" ? "Last run succeeded" :
              "No run in progress"
            }`}
            aria-label="Account menu"
          >
            {(user.email || "?").charAt(0).toUpperCase()}
            {/* profile menu — run-status presence dot (Option B) */}
            <span
              aria-hidden
              className={`absolute -bottom-0.5 -right-0.5 w-2.5 h-2.5 rounded-full border border-[#0d1120] ${
                tabRunStatus === "running" ? "bg-green-400 blink" :
                tabRunStatus === "failed" ? "bg-red-400" :
                tabRunStatus === "succeeded" ? "bg-tessera-accent" :
                "bg-gray-500"
              }`}
            />
          </button>
          {profileMenuOpen && (
            <>
              {/* click-away backdrop */}
              <div className="fixed inset-0 z-[59]" onClick={() => setProfileMenuOpen(false)} />
              <div className="absolute right-0 mt-2 w-64 bg-tessera-card border border-tessera-border rounded-xl shadow-2xl shadow-black/50 z-[61] overflow-hidden">
                <div className="px-4 py-3 border-b border-tessera-border">
                  <div className="text-[10px] uppercase tracking-wide text-gray-500 mb-0.5">Signed in as</div>
                  <div className="text-sm text-gray-200 truncate" title={user.email}>{user.email}</div>
                  {isAdmin && <div className="mt-1 inline-block text-[10px] bg-blue-500/15 text-blue-300 rounded px-1.5 py-0.5">Admin</div>}
                </div>
                <div className="px-4 py-3 border-b border-tessera-border flex items-center gap-2 text-xs">
                  <span className={`w-1.5 h-1.5 rounded-full shrink-0 ${
                    tabRunStatus === "running" ? "bg-green-400 blink" :
                    tabRunStatus === "failed" ? "bg-red-400" :
                    tabRunStatus === "succeeded" ? "bg-tessera-accent" :
                    "bg-gray-600"
                  }`} />
                  <span className="text-gray-400">{
                    tabRunStatus === "running" ? "Run in progress" :
                    tabRunStatus === "failed" ? "Last run failed" :
                    tabRunStatus === "succeeded" ? "Last run succeeded" :
                    "Idle (no active run)"
                  }</span>
                </div>
                <button
                  onClick={() => { setProfileMenuOpen(false); try { window.sessionStorage.removeItem("tessera.view.session"); } catch {} signOut(); }}
                  className="w-full text-left px-4 py-2.5 text-xs text-red-400 hover:text-red-300 hover:bg-red-500/10 transition"
                >
                  Sign out
                </button>
              </div>
            </>
          )}
        </div>
      )}

      {/* Concierge — live streaming front door (interactive eco) that can navigate the app. */}
      {user && (() => {
        const handleAct = (a: import("@/components/concierge-chat").ConciergeAction) => {
          const clearSpot = () => { setArtifactReq(null); setSpotlightSeq(null); };
          switch (a.action) {
            case "open_run":
              clearSpot();
              if (a.eco_id && a.run_id) {
                setView("ecosystems"); setSelectedEcosystem(a.eco_id);
                setOpenRunReq({ ecoId: a.eco_id, runId: a.run_id });
                setLastOpenedRun({ eco_id: a.eco_id, run_id: a.run_id });
              }
              break;
            case "show_artifact":
              setSpotlightSeq(null); setReplayReq(null);
              if (a.eco_id && a.run_id) setArtifactReq({ ecoId: a.eco_id, runId: a.run_id, artifactKey: a.artifact_key });
              break;
            case "spotlight_outcome": {
              // Direct Q&A: open the real deliverable and spotlight its sections (like the presenter).
              setReplayReq(null);
              const secs = (a.sections || []).filter((s) => s && s.target);
              if (a.eco_id && a.run_id && secs.length) {
                setArtifactReq({ ecoId: a.eco_id, runId: a.run_id, artifactKey: a.artifact_key });
                setSpotlightSeq(secs); setSpotlightIdx(0);
                setPresHighlight({ target: secs[0].target, note: secs[0].note });
              }
              break;
            }
            case "open_catalog": clearSpot(); openCatalog(a.domain || ""); break;
            case "self_model": clearSpot(); openCatalog("tessera-self"); break;
            case "filter_grid": clearSpot(); setView("ecosystems"); setGridQueryReq(a.query || ""); break;
            case "start_run": clearSpot(); if (a.eco_id) launchEcosystem(a.eco_id, a.mission || "Live demo run", undefined, true); break;  // dry-run: cheap "watch it come alive" beat
            case "open_builder": clearSpot(); setView("builder"); setBuilderConvo(Date.now()); break;
            case "open_mission": clearSpot(); if (a.eco_id) { setView("ecosystems"); setMissionReq(a.eco_id); } break;
            case "replay_run": clearSpot(); setPresHighlight(null); if (a.eco_id) {
              const eco = a.eco_id;
              if (a.run_id) setReplayReq({ ecoId: eco, runName: a.run_id });
              else authFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(eco)}/runs`).then((r) => r.json()).then((d) => {
                const ex = (d.executions || []) as Array<{ name: string; status: string }>;
                const pick = ex.find((e) => e.status === "SUCCEEDED") || ex[0];
                if (pick) setReplayReq({ ecoId: eco, runName: pick.name });
              }).catch(() => {});
            } break;
            case "highlight": if (a.target) setPresHighlight({ target: a.target, note: a.note }); break;
            case "clear_highlight": setPresHighlight(null); break;
            case "navigate": {
              clearSpot();
              const v = a.view === "runs" ? "run" : (a.view || "");
              if (["ecosystems", "run", "registry", "builder", "tools", "help", "admin", "attention", "domains", "catalog", "evaluation"].includes(v)) setView(v as typeof view);
              break;
            }
          }
        };
        return (
          <>
            {/* The Concierge is a MAIN-SCREEN assistant. In technical mode it's hidden on the
                run screen and history viewer (they have their own run-scoped RunChat, bottom-right).
                In BUSINESS mode the concierge is the primary helper and must be visible everywhere
                (it sits bottom-left, so it never collides with the run chat bottom-right). */}
            {(viewMode === "business" || (view !== "run" && !historyOpen)) && (
              <ConciergeChat context={lastOpenedRun ? { open_run: lastOpenedRun } : undefined}
                onAction={handleAct} onPlan={(p) => { setReplayReq(null); setPresentation(p as unknown as Plan); }} />
            )}            <PresentationOverlay active={!!presentation || !!artifactReq} highlight={presHighlight} />
            <ArtifactViewer req={artifactReq} onClose={() => { setArtifactReq(null); setSpotlightSeq(null); setPresHighlight(null); }} />
            {replayReq && <ReplayPlayer ecosystemId={replayReq.ecoId} runName={replayReq.runName} autoClose onClose={() => setReplayReq(null)} />}
            {/* Standalone spotlight stepper (concierge Q&A "spotlight the outcome" — not a full presentation) */}
            {spotlightSeq && !presentation && (
              <div className="fixed bottom-4 left-1/2 -translate-x-1/2 z-[82] pointer-events-auto flex items-center gap-3 bg-tessera-card/95 backdrop-blur-md border border-tessera-accent/40 rounded-full shadow-2xl px-4 py-2">
                <span className="text-tessera-accent text-sm">✨</span>
                <span className="text-xs text-gray-300 max-w-[420px] truncate">{spotlightSeq[spotlightIdx]?.note || spotlightSeq[spotlightIdx]?.target}</span>
                {spotlightSeq.length > 1 && (
                  <span className="flex items-center gap-1.5">
                    <button onClick={() => { const i = Math.max(0, spotlightIdx - 1); setSpotlightIdx(i); setPresHighlight({ target: spotlightSeq[i].target, note: spotlightSeq[i].note }); }} disabled={spotlightIdx === 0} className="px-2 py-0.5 text-xs rounded bg-white/5 hover:bg-white/10 disabled:opacity-30">‹</button>
                    <span className="text-[10px] text-gray-500 tabular-nums">{spotlightIdx + 1}/{spotlightSeq.length}</span>
                    <button onClick={() => { const i = Math.min(spotlightSeq.length - 1, spotlightIdx + 1); setSpotlightIdx(i); setPresHighlight({ target: spotlightSeq[i].target, note: spotlightSeq[i].note }); }} disabled={spotlightIdx === spotlightSeq.length - 1} className="px-2 py-0.5 text-xs rounded bg-white/5 hover:bg-white/10 disabled:opacity-30">›</button>
                  </span>
                )}
                <button onClick={() => { setArtifactReq(null); setSpotlightSeq(null); setPresHighlight(null); }} className="px-2 py-0.5 text-xs rounded bg-white/5 hover:bg-white/10 text-gray-400" title="Close">✕</button>
              </div>
            )}
            {presentation && <PresentationPlayer plan={presentation} suspended={!!replayReq}
              onAction={(a) => handleAct(a as unknown as import("@/components/concierge-chat").ConciergeAction)}
              onExit={() => { setPresentation(null); setPresHighlight(null); setArtifactReq(null); setSpotlightSeq(null); }} />}
            {/* Solution picker — the ONLY way to choose/switch the business solution. Required
                (non-dismissable) when nothing is selected yet, so the business view is never empty. */}
            {viewMode === "business" && view !== "home" && !ecosystemsLoading && ecosystems.length > 0 && (pickerOpen || !selectedEcosystem) && (
              <SolutionPicker
                ecosystems={ecosystems as any}
                currentId={selectedEcosystem}
                required={!selectedEcosystem}
                onPick={(id) => { setSelectedEcosystem(id); setPickerOpen(false); setBizIntake(false); setView("ecosystems"); }}
                onClose={() => setPickerOpen(false)}
              />
            )}
            {/* Business interactive outcome — the report opened in the Studio (read / ask / spotlight). */}
            {openingReport && !bizReport && (
              <div className="fixed inset-0 z-[84] bg-black/75 backdrop-blur-sm flex flex-col items-center justify-center gap-4">
                <TesseraSpinner size={52} />
                <div className="text-sm text-gray-300">Opening your report…</div>
              </div>
            )}
            {bizReport && (
              <BeautifyStudio
                sessionId={bizReport.sessionId}
                artifactKey={bizReport.artifactKey}
                artifactLabel={bizReport.label}
                onClose={() => setBizReport(null)}
              />
            )}
          </>
        );
      })()}
    </div>
    </BackStackContext.Provider>
  );
}

function NavItem({ icon, label, active, collapsed, onClick }: { icon: string; label: string; active: boolean; collapsed: boolean; onClick: () => void }) {
  return (
    <button
      onClick={onClick}
      title={label}
      className={`w-full text-left px-3 py-2.5 rounded-lg text-sm transition ${collapsed ? "flex items-center justify-center" : ""} ${
        active ? "bg-tessera-accent/15 text-tessera-accent border border-tessera-accent/30" : "text-gray-400 hover:text-white hover:bg-white/5"
      }`}
    >
      <span>{icon}</span>
      {!collapsed && <span className="ml-2">{label}</span>}
    </button>
  );
}

function EcosystemsView({ ecosystems, hideGrid, onIntakeClose, onLaunch, isRunning, onRunViewerChange, onTrackRedrive, onTrackOptimize, openRunRequest, onOpenRunHandled, gridQueryRequest, onGridQueryHandled, missionRequest, onMissionHandled }: { ecosystems: Ecosystem[]; hideGrid?: boolean; onIntakeClose?: () => void; onLaunch: (id: string, mission: string, inputs?: Record<string, string>, dryRun?: boolean, runConfig?: Record<string, unknown>, attachments?: Array<{ name: string; ref: string }>, missionTranscript?: Array<{ role: string; content: string }>) => void; isRunning: boolean; onRunViewerChange?: (open: boolean) => void; onTrackRedrive: (ecosystemId: string, execName: string) => void; onTrackOptimize: (optimizerSessionId: string, targetEcoId: string) => void; openRunRequest?: { ecoId: string; runId: string } | null; onOpenRunHandled?: () => void; gridQueryRequest?: string | null; onGridQueryHandled?: () => void; missionRequest?: string | null; onMissionHandled?: () => void }) {
  const { user, isAdmin } = useAuth();
  const [selected, setSelected] = useState<Ecosystem | null>(null);
  const [query, setQuery] = useState("");
  const [activeCat, setActiveCat] = useState("All");

  // Distinct DOMAINS present (for the filter chips), ordered by frequency.
  const categories = (() => {
    const counts: Record<string, number> = {};
    for (const e of ecosystems) { const c = e.domain || "Unassigned"; counts[c] = (counts[c] || 0) + 1; }
    return ["All", ...Object.keys(counts).sort((a, b) => counts[b] - counts[a])];
  })();

  // Instant client-side filter across name/description/tags/domain + agent count.
  // Multi-word queries match when EVERY word appears somewhere in the combined text
  // (AND, any order) — so "risk trading" matches "Hedge Fund Trading Desk" tagged risk,
  // rather than requiring the exact contiguous phrase.
  const filtered = ecosystems.filter((e) => {
    if (activeCat !== "All" && (e.domain || "Unassigned") !== activeCat) return false;
    const terms = query.trim().toLowerCase().split(/\s+/).filter(Boolean);
    if (terms.length === 0) return true;
    const hay = [e.name, e.description, e.domain, ...(e.domains || []), e.strategy, ...(e.tags || [])].join(" ").toLowerCase();
    return terms.every((t) => hay.includes(t));
  });
  const [modal, setModal] = useState<"run" | "files" | "history" | "sharing" | "export" | null>(null);
  const [focusRunId, setFocusRunId] = useState<string | null>(null);
  const { pushStep: pushEcoBackStep } = useBackStack();
  // Tell the parent when the history (run) viewer is open, so it can hide the
  // main-screen Concierge (the viewer has its own run-scoped RunChat).
  useEffect(() => { onRunViewerChange?.(modal === "history"); }, [modal, onRunViewerChange]);
  // Register a Back step for the full-screen sub-pages (past-runs viewer, run/mission
  // viewer) so browser Back returns to the ecosystem grid instead of leaving the whole
  // Ecosystems view. Cleanup disposes when the sub-page closes (in-app close) or unmounts.
  useEffect(() => {
    if (modal !== "history" && modal !== "run") return;
    const dispose = pushEcoBackStep(() => { setModal(null); setFocusRunId(null); });
    return dispose;
  }, [modal, pushEcoBackStep]);
  // Concierge "open this run" — do exactly what the user does: select the eco, open the
  // History (run) window, and focus the specific run.
  useEffect(() => {
    if (!openRunRequest) return;
    const eco = ecosystems.find((e) => e.id === openRunRequest.ecoId);
    if (eco) { setSelected(eco); setFocusRunId(openRunRequest.runId); setModal("history"); }
    onOpenRunHandled?.();
  }, [openRunRequest, ecosystems, onOpenRunHandled]);
  // Presenter filter_grid — set the grid search to spotlight a subset.
  useEffect(() => {
    if (gridQueryRequest == null) return;
    setActiveCat("All"); setQuery(gridQueryRequest); setModal(null);
    onGridQueryHandled?.();
  }, [gridQueryRequest, onGridQueryHandled]);
  // Presenter open_mission — select the eco and open its conversational mission capture.
  useEffect(() => {
    if (missionRequest == null) return;
    const eco = ecosystems.find((e) => e.id === missionRequest);
    if (eco) { setSelected(eco); setMissionMode("chat"); setModal("run"); }
    onMissionHandled?.();
  }, [missionRequest, ecosystems, onMissionHandled]);
  // In business (grid-hidden) mode, dismissing the intake modal must return to the
  // business home rather than an empty EcosystemsView (grid is hidden). Fire only on
  // a real run→null transition so opening the intake doesn't immediately close it.
  const prevModalRef = useRef<any>(null);
  useEffect(() => {
    if (hideGrid && prevModalRef.current === "run" && modal === null) onIntakeClose?.();
    prevModalRef.current = modal;
  }, [modal, hideGrid, onIntakeClose]);
  const [optimizing, setOptimizing] = useState(false);
  const [optimizeMsg, setOptimizeMsg] = useState<{ kind: "success" | "error"; text: string } | null>(null);

  // Phase 5d: trigger the platform-optimizer against the currently selected
  // ecosystem from the top-level action bar. First-class action — sibling
  // of Run / Files / History rather than buried in the past-runs viewer.
  const triggerOptimize = async (eco: Ecosystem) => {
    setOptimizing(true);
    setOptimizeMsg(null);
    try {
      const res = await apiFetch(`${API_URL}/api/optimize-ecosystem`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ target_ecosystem_id: eco.id, max_runs: 5 }),
      });
      const data = await res.json();
      if (res.ok && data.session_id) {
        setOptimizeMsg({
          kind: "success",
          text: `Optimizer started — analysing ${data.telemetry?.runs_loaded ?? "?"} run(s) across ${data.telemetry?.agents_in_target ?? "?"} agents…`,
        });
        // Brief delay so the user sees the success message before nav.
        setTimeout(() => onTrackOptimize(data.session_id, eco.id), 700);
      } else {
        setOptimizeMsg({
          kind: "error",
          text: `Optimize rejected: ${data.error || "unknown reason"}${data.detail ? " — " + data.detail : ""}`,
        });
      }
    } catch (e) {
      setOptimizeMsg({ kind: "error", text: `Optimize request failed: ${e}` });
    } finally {
      setOptimizing(false);
    }
  };
  const [mission, setMission] = useState("");
  const [inputValues, setInputValues] = useState<Record<string, string>>({});
  const [attachments, setAttachments] = useState<Array<{name: string; content: string}>>([]);
  const [attachError, setAttachError] = useState<string | null>(null);
  // Conversational mission capture (mirrors the builder). missionMode: "chat"
  // (default — converse to shape the mission, file-aware) or "quick" (one-shot
  // textarea escape hatch for users who know exactly what they want).
  const [missionMode, setMissionMode] = useState<"chat" | "quick">("chat");
  const [mConvo, setMConvo] = useState<{ role: "user" | "assistant"; content: string }[]>([]);
  const [mState, setMState] = useState<any>({});
  const [mQuestion, setMQuestion] = useState<{ id?: string; text: string; options?: string[] } | null>(null);
  const [mStream, setMStream] = useState("");
  const [mBusy, setMBusy] = useState(false);
  const [mConverged, setMConverged] = useState(false);
  const [mFinalMission, setMFinalMission] = useState("");
  const [mFinalInputs, setMFinalInputs] = useState<Record<string, string>>({});
  const [mJobId, setMJobId] = useState<string | null>(null);
  const [mError, setMError] = useState<string | null>(null);
  const [mInput, setMInput] = useState("");
  const mEndRef = useRef<HTMLDivElement>(null);
  const mScrollRef = useRef<HTMLDivElement>(null);
  const { clientId: mClientId, connected: mWsConnected, subscribe: mSubscribe } = useClientWebSocket(WS_URL);
  // Per-mount draft id so optional run uploads land in a run-scoped S3 prefix.
  // Use a crypto RNG (not Math.random) so the id is unpredictable.
  const [draftId] = useState(() => `draft-${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`);
  useEffect(() => {
    return mSubscribe((event: any) => {
      if (event.type === "CONVERSE_DELTA") {
        if (event.jobId !== mJobId) return;
        setMStream((s) => s + (event.text || ""));
      } else if (event.type === "CONVERSE_RESULT") {
        if (event.jobId !== mJobId) return;
        setMBusy(false); setMJobId(null);
        if (event.error) { setMError(String(event.error)); setMStream(""); return; }
        setMConvo((m) => [...m, { role: "assistant", content: event.reply || mStream || "(no reply)" }]);
        setMStream("");
        if (event.mission_state) setMState(event.mission_state);
        setMQuestion(event.next_question || null);
        setMConverged(!!event.converged);
        if (event.converged) { setMFinalMission(event.mission || ""); setMFinalInputs(event.inputs || {}); }
      }
    });
  }, [mSubscribe, mJobId, mStream]);
  useEffect(() => {
    // Scroll ONLY the messages container to its bottom — NOT scrollIntoView,
    // which bubbles up and scrolls the whole modal down, pushing the input box
    // (and Launch button) off-screen. Keeps the input pinned + visible.
    const el = mScrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [mConvo, mStream, mQuestion, mBusy]);
  const sendMissionTurn = async (text: string) => {
    const msg = text.trim();
    if (!msg || mBusy || !selected) return;
    if (!mWsConnected) { setMError("Live connection not ready — try again in a moment."); return; }
    setMError(null); setMBusy(true); setMStream(""); setMQuestion(null);
    const history = mConvo.map((m) => ({ role: m.role, content: m.content }));
    setMConvo((m) => [...m, { role: "user", content: msg }]);
    setMInput("");
    const sel: any = selected;
    const eco_context = { name: sel.name, description: sel.description || "", inputs: sel.inputs || [], outcome: sel.outcome || null };
    const att = attachments.map((a) => ({ name: a.name, ref: a.content }));
    try {
      const resp = await apiFetch(`${API_URL}/api/ecosystems/suggest`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "mission", message: msg, history, mission_state: mState, eco_context, attachments: att, clientId: mClientId }),
      });
      const data = await resp.json();
      if (data.error) throw new Error(data.error);
      if (data.status === "pending" && data.jobId) { setMJobId(data.jobId); }
      else if (data.reply !== undefined) {
        setMBusy(false);
        setMConvo((m) => [...m, { role: "assistant", content: data.reply }]);
        if (data.mission_state) setMState(data.mission_state);
        setMQuestion(data.next_question || null);
        setMConverged(!!data.converged);
        if (data.converged) { setMFinalMission(data.mission || ""); setMFinalInputs(data.inputs || {}); }
      }
    } catch (e: any) { setMBusy(false); setMError(e?.message || "Turn failed"); }
  };
  // Upload one optional run-scoped file (any ecosystem), classified server-side.
  const uploadRunAttachment = async (f: File) => {
    if (!selected) return;
    setAttachError(null);
    try {
      const res = await apiFetch(`${API_URL}/api/uploads/presign`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ filename: f.name, sessionId: draftId, type: "run", contentType: f.type || "application/octet-stream" }) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.error || !data.uploadUrl) {
        setAttachError(`Couldn't attach "${f.name}": ${data.error || `server error (HTTP ${res.status})`}`);
        return;
      }
      const put = await fetch(data.uploadUrl, { method: "PUT", body: f, headers: { "Content-Type": f.type || "application/octet-stream" } });
      if (!put.ok) {
        setAttachError(`Couldn't upload "${f.name}": storage rejected it (HTTP ${put.status}).`);
        return;
      }
      setAttachments((prev) => [...prev, { name: f.name, content: data.s3Uri }]);
    } catch (err: any) {
      // A blocked CORS preflight or network failure lands here as a TypeError.
      setAttachError(`Couldn't upload "${f.name}": ${err?.message || "network/upload error"}. Check the file type and size (max 4.5MB).`);
      console.error("Run attachment upload failed:", err);
    }
  };
  const [dryRun, setDryRun] = useState(false);
  // Advanced run config (spec-run-tuning §4) — per-run model selection, run-scoped.
  const [showAdvanced, setShowAdvanced] = useState(false);
  const [modelProfile, setModelProfile] = useState<string>("");          // "" = platform default
  const [typeOverrides, setTypeOverrides] = useState<Record<string, string>>({}); // tier -> alias
  const [rankedRecall, setRankedRecall] = useState<boolean>(true);       // M.5: relevance-ranked catalog memory recall (default on)
  // Quorum run settings chosen at launch (quorum/index.html §12). Untouched = {} = the recorded
  // defaults, so an ordinary launch is unchanged.
  const [quorumSettings, setQuorumSettings] = useState<QuorumSettings>({});
  const buildRunConfig = (): Record<string, unknown> => {
    const rc: Record<string, unknown> = {};
    if (Object.keys(quorumSettings).length) rc.quorum_settings = quorumSettings;
    if (modelProfile) rc.model_profile = modelProfile;
    const byType = Object.fromEntries(Object.entries(typeOverrides).filter(([, v]) => v));
    if (Object.keys(byType).length) rc.model_overrides = { by_type: byType };
    // Always send the boolean so it's recorded on the run (for A/B comparison).
    rc.ranked_recall = rankedRecall;
    return rc;
  };
  // Cross-run config recommendation (spec-run-tuning §6), fetched when the run modal opens.
  const [recommendation, setRecommendation] = useState<any>(null);
  const applyRecommendation = (rc: any) => {
    setModelProfile(rc?.model_profile || "");
    setTypeOverrides((rc?.model_overrides?.by_type as Record<string, string>) || {});
    setRankedRecall(rc?.ranked_recall !== false);
    setShowAdvanced(true);
  };
  useEffect(() => {
    if (modal === "run" && selected) {
      setRecommendation(null);
      apiFetch(`${API_URL}/api/ecosystems/${selected.id}/runs/recommendation`)
        .then(r => (r.ok ? r.json() : null)).then(setRecommendation).catch(() => {});
      // Reset conversational mission capture for a fresh run.
      setMissionMode("chat"); setMConvo([]); setMState({}); setMQuestion(null);
      setMStream(""); setMBusy(false); setMConverged(false); setMFinalMission("");
      setMFinalInputs({}); setMJobId(null); setMError(null); setMInput("");
      setMission(""); setInputValues({}); setAttachments([]); setAttachError(null);
      setRankedRecall(true);
    }
  }, [modal, selected]);
  const [ecoFiles, setEcoFiles] = useState<any[]>([]);
  const [uploading, setUploading] = useState(false);

  const loadEcoFiles = (ecoId: string) => {
    apiFetch(`${API_URL}/api/ecosystems/${ecoId}/files`).then(r => r.json()).then(data => { if (Array.isArray(data)) setEcoFiles(data); }).catch(() => {});
  };
  const uploadEcoFile = async (ecoId: string, file: File) => {
    setUploading(true);
    setAttachError(null);
    try {
      const res = await apiFetch(`${API_URL}/api/uploads/presign`, { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ filename: file.name, ecosystemId: ecoId, type: "ecosystem", contentType: file.type || "application/octet-stream" }) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.error || !data.uploadUrl) {
        setAttachError(`Couldn't upload "${file.name}": ${data.error || `server error (HTTP ${res.status})`}`);
        setUploading(false); return;
      }
      const put = await fetch(data.uploadUrl, { method: "PUT", body: file, headers: { "Content-Type": file.type || "application/octet-stream" } });
      if (!put.ok) { setAttachError(`Couldn't upload "${file.name}": storage rejected it (HTTP ${put.status}).`); setUploading(false); return; }
      setTimeout(() => loadEcoFiles(ecoId), 5000);
    } catch (e: any) {
      setAttachError(`Couldn't upload "${file.name}": ${e?.message || "network/upload error"}. Check the file type and size (max 4.5MB).`);
      console.error("Upload failed:", e);
    }
    setUploading(false);
  };
  const deleteEcoFile = async (ecoId: string, filename: string) => {
    if (!confirm(`Delete "${filename}"?`)) return;
    await apiFetch(`${API_URL}/api/ecosystems/${ecoId}/files/${filename}`, { method: "DELETE" });
    loadEcoFiles(ecoId);
  };
  const handleFileChange = (id: string, file: File | null) => {
    if (!file) return;
    const reader = new FileReader();
    reader.onload = (e) => setInputValues((v) => ({ ...v, [id]: e.target?.result as string }));
    reader.readAsText(file);
  };

  // History modal
  if (modal === "history" && selected) {
    return <PastRunsModal ecosystemId={selected.id} ecosystemName={selected.name} initialRunName={focusRunId || undefined} onClose={() => { setModal(null); setFocusRunId(null); }} onRedriveStarted={(execName) => { onTrackRedrive(selected.id, execName); setModal(null); }} onResumeStarted={(newSession) => { onTrackRedrive(selected.id, newSession); setModal(null); }} onOptimizeStarted={(sid, targetId) => { onTrackOptimize(sid, targetId); setModal(null); }} />;
  }

  return (
    <div>
      {!hideGrid && (<>
      {/* Sticky top region — title + action bar + search/filters stay pinned while the
          ecosystem grid scrolls, so an eco can be selected and an action taken without
          scrolling back up. Same recipe as the business-mode header: negative margins to
          bleed to <main>'s padding edges, an opaque bg so scrolled cards don't show
          through, and z above the grid. <main> is the scroll container (overflow-auto),
          so `sticky top-0` pins to its top. */}
      <div className="sticky -top-6 z-20 -mx-6 -mt-6 px-6 pt-6 pb-3 mb-4 bg-tessera-bg/95 backdrop-blur border-b border-tessera-border">
      {/* Top action bar. The right margin keeps the right-most action (Export) clear of
          the fixed top-right profile avatar (top-4 right-4, ~9x9 + shadow/hover-scale) so
          it isn't hidden behind it. */}
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-2xl font-bold mb-1">Ecosystems</h2>
          <p className="text-gray-500 text-sm">Select an ecosystem, then choose an action</p>
        </div>
        <div className="flex gap-2 mr-20">
          <button onClick={() => { if (selected) setModal("run"); }} disabled={!selected} className="px-3 py-2 text-sm rounded-lg bg-tessera-accent/20 text-tessera-accent disabled:opacity-30 disabled:cursor-not-allowed hover:bg-tessera-accent/30">▶ Run</button>
          <button onClick={() => { if (selected) { setAttachError(null); loadEcoFiles(selected.id); setModal("files"); } }} disabled={!selected} className="px-3 py-2 text-sm rounded-lg bg-gray-700/50 text-gray-300 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-gray-700">📁 Files</button>
          <button onClick={() => { if (selected) setModal("history"); }} disabled={!selected} className="px-3 py-2 text-sm rounded-lg bg-gray-700/50 text-gray-300 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-gray-700">📜 History</button>
          <button
            onClick={() => { if (selected && !optimizing) triggerOptimize(selected); }}
            disabled={!selected || optimizing || !!selected?.platform}
            title={
              !selected
                ? "Select an ecosystem first"
                : selected.platform
                ? "System-managed ecosystems cannot be optimized (they're managed by the system itself)"
                : "Profile recent runs and propose quality / time / cost improvements"
            }
            className="px-3 py-2 text-sm rounded-lg bg-amber-500/20 text-amber-300 border border-amber-500/30 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-amber-500/30 hover:border-amber-500/50 transition"
          >
            {optimizing ? "⚡ Starting…" : "⚡ Optimize"}
          </button>
          {isAdmin && (
            <button onClick={() => { if (selected) setModal("sharing"); }} disabled={!selected} className="px-3 py-2 text-sm rounded-lg bg-blue-500/20 text-blue-300 border border-blue-500/30 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-blue-500/30">🔗 Share</button>
          )}
          <button
            onClick={() => { if (selected) setModal("export"); }}
            disabled={!selected || !!selected?.platform}
            title={
              !selected
                ? "Select an ecosystem first"
                : selected.platform
                ? "System-managed ecosystems cannot be exported"
                : "Generate a self-contained Terraform package to deploy this ecosystem in your own AWS account"
            }
            className="px-3 py-2 text-sm rounded-lg bg-emerald-500/20 text-emerald-300 border border-emerald-500/30 disabled:opacity-30 disabled:cursor-not-allowed hover:bg-emerald-500/30"
          >
            ⬇ Export
          </button>
        </div>
      </div>

      {optimizeMsg && (
        <div
          className={`mb-3 text-xs rounded p-2 ${
            optimizeMsg.kind === "success"
              ? "text-amber-200 bg-amber-500/10 border border-amber-500/30"
              : "text-red-300 bg-red-500/10 border border-red-500/30"
          }`}
        >
          {optimizeMsg.text}
        </div>
      )}

      {/* Search + category filter */}
      {ecosystems.length > 0 && (
        <div className="space-y-2">
          <input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="Search ecosystems…"
            className="w-full md:w-80 bg-tessera-card border border-tessera-border rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500" />
          <div className="flex flex-wrap gap-1.5">
            {categories.map((c) => (
              <button key={c} onClick={() => setActiveCat(c)}
                className={`text-[11px] px-2.5 py-1 rounded-full border transition ${activeCat === c ? "bg-tessera-accent/20 text-tessera-accent border-tessera-accent/40" : "text-gray-400 border-tessera-border hover:border-gray-500"}`}>
                {c}
              </button>
            ))}
          </div>
        </div>
      )}
      </div>{/* end sticky top region */}

      {/* Ecosystem grid */}
      {ecosystems.length === 0 ? (
        <div className="bg-tessera-card glow-border rounded-xl p-8 text-center">
          <div className="text-4xl mb-3">⬡</div>
          <p className="text-gray-400">No ecosystems found.</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="bg-tessera-card border border-tessera-border rounded-xl p-6 text-center text-sm text-gray-500">No ecosystems match your search.</div>
      ) : (
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-3">
          {filtered.map((eco) => (
            <div key={eco.id} className={`bg-tessera-card rounded-xl text-left transition border ${selected?.id === eco.id ? "border-tessera-accent shadow-lg shadow-tessera-accent/10" : "border-tessera-border hover:border-gray-600"}`}>
              <button onClick={() => setSelected(eco)} className="w-full text-left p-4 pb-2">
                <div className="flex items-start justify-between mb-2">
                <div className="flex items-center gap-1.5 flex-wrap">
                  <span className="text-xs bg-tessera-accent/10 text-tessera-accent px-2 py-0.5 rounded-full">{eco.strategy}</span>
                  {eco.domain && eco.domain !== "Unassigned" && (
                    <span className="text-[10px] bg-purple-500/10 text-purple-300 border border-purple-500/20 px-1.5 py-0.5 rounded-full">🗂 {eco.domain}</span>
                  )}
                </div>
                <div className="flex items-center gap-1.5">
                  {eco.platform && (
                    <span
                      className="text-[9px] bg-amber-500/15 text-amber-300 border border-amber-500/30 px-1.5 py-0.5 rounded font-semibold tracking-wide"
                      title="System-managed ecosystem — not editable via UI"
                    >
                      🔒 SYSTEM
                    </span>
                  )}
                  <span className="text-xs text-gray-600">{eco.agents} agents</span>
                </div>
              </div>
              <h3 className="font-semibold text-white text-sm mb-1">{eco.name}</h3>
              <p className="text-xs text-gray-400 leading-relaxed line-clamp-2">{eco.description}</p>
              {eco.owner && eco.owner !== user?.userId && !eco.platform && (
                <span className="inline-block mt-1.5 text-[10px] bg-blue-500/10 text-blue-300 border border-blue-500/20 px-1.5 py-0.5 rounded">🔗 Shared with you</span>
              )}
              </button>
              <div className="px-4 pb-3">
                <Link href={`/ecosystems?id=${encodeURIComponent(eco.id)}`} className="text-[11px] text-tessera-accent hover:underline">Details →</Link>
              </div>
            </div>
          ))}
        </div>
      )}

      {/* Run modal */}
      </>)}
      {modal === "run" && selected && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setModal(null)}>
          <div className={`bg-tessera-card border border-tessera-border rounded-xl p-6 ${missionMode === "chat" ? "w-[860px]" : "w-[560px]"} max-h-[85vh] overflow-y-auto`} onClick={e => e.stopPropagation()}>
            <h3 className="text-lg font-bold mb-1">{selected.name}</h3>
            <p className="text-xs text-gray-500 mb-4">{selected.agents} agents • {selected.strategy}</p>
            <div className="space-y-4">
              {/* Universal optional file upload — available for EVERY ecosystem,
                  independent of declared inputs. Files are classified server-side,
                  considered in the mission chat, and passed to the run. */}
              <div>
                <label className="block text-xs text-gray-400 mb-1">Attach files <span className="text-gray-600">(optional)</span></label>
                <label className="cursor-pointer inline-block px-3 py-1.5 rounded text-xs bg-gray-700/50 text-gray-200 hover:bg-gray-700 border border-tessera-border">
                  + Add files
                  <input type="file" multiple onChange={async (e) => { const fs = e.target.files; if (!fs) return; for (let i = 0; i < fs.length; i++) await uploadRunAttachment(fs[i]); e.target.value = ""; }} className="hidden" />
                </label>
                {attachments.length > 0 && <div className="mt-1.5 space-y-0.5">{attachments.map((a, i) => <div key={i} className="flex items-center gap-1 text-[10px] text-gray-400"><span>📎</span><span className="truncate max-w-[360px]">{a.name}</span><button type="button" onClick={() => setAttachments(p => p.filter((_, j) => j !== i))} className="text-red-400 ml-1">✕</button></div>)}</div>}
                {attachError && <div className="mt-1.5 text-[11px] text-red-300 bg-red-500/10 border border-red-500/30 rounded px-2 py-1 flex items-start gap-1.5"><span>⚠</span><span className="flex-1">{attachError}</span><button type="button" onClick={() => setAttachError(null)} className="text-red-400">✕</button></div>}
                <p className="text-[10px] text-gray-600 mt-1">Any file type — they&apos;ll be considered in the mission chat and given to the run.</p>
              </div>
              {missionMode === "quick" && selected.inputs && selected.inputs.length > 0 && (
                <div className="space-y-3">
                  {selected.inputs.map((input: any) => (
                    <div key={input.id}>
                      <label className="block text-xs text-gray-400 mb-1">{input.label} {input.required && <span className="text-red-400">*</span>}</label>
                      {input.type === "file" ? (
                        <div>
                          <label className="cursor-pointer inline-block px-3 py-1.5 rounded text-xs bg-tessera-accent/20 text-tessera-accent hover:bg-tessera-accent/30">
                            {input.multiple ? "+ Add files" : "Choose file"}
                            <input type="file" accept={typeof input.accept === "string" ? input.accept : Array.isArray(input.accept) ? input.accept.join(",") : ""} multiple={!!input.multiple} onChange={async (e) => {
                              const files = e.target.files;
                              if (!files) return;
                              setAttachError(null);
                              for (let i = 0; i < files.length; i++) {
                                const f = files[i];
                                try {
                                  const res = await apiFetch(`${API_URL}/api/uploads/presign`, { method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify({ filename: f.name, ecosystemId: selected.id, type: "ecosystem", contentType: f.type || "application/octet-stream" }) });
                                  const data = await res.json().catch(() => ({}));
                                  if (!res.ok || data.error || !data.uploadUrl) { setAttachError(`Couldn't attach "${f.name}": ${data.error || `server error (HTTP ${res.status})`}`); continue; }
                                  const put = await fetch(data.uploadUrl, { method: "PUT", body: f, headers: { "Content-Type": f.type || "application/octet-stream" } });
                                  if (!put.ok) { setAttachError(`Couldn't upload "${f.name}": storage rejected it (HTTP ${put.status}).`); continue; }
                                  setAttachments(prev => input.multiple ? [...prev, {name: f.name, content: data.s3Uri}] : [{name: f.name, content: data.s3Uri}]);
                                } catch (err: any) { setAttachError(`Couldn't upload "${f.name}": ${err?.message || "network/upload error"}. Check the file type and size (max 4.5MB).`); console.error("Upload failed:", err); }
                              }
                              e.target.value = "";
                            }} className="hidden" />
                          </label>
                          {attachments.length > 0 && <div className="mt-1 space-y-0.5">{attachments.map((a,i) => <div key={i} className="flex items-center gap-1 text-[10px] text-gray-500"><span className="truncate">{a.name}</span><button type="button" onClick={() => setAttachments(p => p.filter((_,j) => j!==i))} className="text-red-400">✕</button></div>)}</div>}
                        </div>
                      ) : input.type === "select" ? (
                        <select value={inputValues[input.id] || ""} onChange={(e) => setInputValues(v => ({...v, [input.id]: e.target.value}))} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white"><option value="">Select...</option>{(input.options||[]).map((o: string) => <option key={o} value={o}>{o}</option>)}</select>
                      ) : (
                        <input type="text" value={inputValues[input.id] || ""} onChange={(e) => setInputValues(v => ({ ...v, [input.id]: e.target.value }))} placeholder={input.label} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white" />
                      )}
                    </div>
                  ))}
                </div>
              )}
              {/* Mission capture — conversational by default (mirrors the builder),
                  with a one-shot "type it myself" escape hatch. */}
              <div>
                <div className="flex items-center justify-between mb-1">
                  <label className="text-xs text-gray-400">Mission</label>
                  <button type="button" onClick={() => setMissionMode(m => m === "chat" ? "quick" : "chat")} className="text-[10px] text-gray-500 hover:text-tessera-accent transition">
                    {missionMode === "chat" ? "Type it myself →" : "← Plan it conversationally"}
                  </button>
                </div>

                {missionMode === "quick" ? (
                  <textarea value={mission} onChange={e => setMission(e.target.value)} placeholder="Describe the mission for this run..." rows={3} className="w-full bg-gray-900 border border-tessera-border rounded-lg px-4 py-3 text-sm text-white placeholder-gray-500 resize-none" />
                ) : (
                  <div className="flex gap-3 items-start">
                  <div className="flex-1 min-w-0 bg-tessera-card glow-border rounded-lg p-3">
                    <div ref={mScrollRef} className="max-h-72 overflow-y-auto space-y-2 mb-2">
                      {mConvo.length === 0 && !mStream && !mBusy && (
                        <div className="text-xs text-gray-500 text-center py-4 px-2">Tell me what you want this run to achieve{attachments.length > 0 ? ` — I can see your ${attachments.length} attached file${attachments.length > 1 ? "s" : ""}` : ""}. I&apos;ll ask a couple of quick questions, then we launch.</div>
                      )}
                      {mConvo.map((m, i) => (
                        <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                          <div className={`max-w-[85%] px-3 py-2 rounded-lg text-sm whitespace-pre-wrap ${m.role === "user" ? "bg-tessera-accent/20 text-tessera-accent" : "bg-gray-800 text-gray-200"}`}>{m.content}</div>
                        </div>
                      ))}
                      {mStream && <div className="flex justify-start"><div className="max-w-[85%] px-3 py-2 rounded-lg text-sm whitespace-pre-wrap bg-gray-800/70 text-gray-300 italic">{mStream}<span className="not-italic animate-pulse">▍</span></div></div>}
                      {mBusy && !mStream && <div className="text-xs text-gray-500 italic animate-pulse">Planning…</div>}
                      {mQuestion && !mConverged && (
                        <DecisionSurface
                          busy={mBusy}
                          decision={{ id: mQuestion.id || "q", kind: "question", title: "Run planner", prompt: mQuestion.text, options: (mQuestion.options || []).map(o => ({ id: o, label: o })), allowText: false, commit: "reversible" } as DecisionPoint}
                          onResolve={(r) => sendMissionTurn(r.optionId || r.text || "")}
                        />
                      )}
                      {mConverged && (
                        <div className="bg-tessera-accent/5 border border-tessera-accent/40 rounded-lg p-3">
                          <div className="text-[11px] font-bold text-tessera-accent uppercase tracking-wider mb-1">Mission ready</div>
                          <p className="text-xs text-gray-200 whitespace-pre-wrap">{mFinalMission}</p>
                          {Object.keys(mFinalInputs).length > 0 && <div className="mt-2 text-[10px] text-gray-400 space-y-0.5">{Object.entries(mFinalInputs).map(([k, v]) => <div key={k}><span className="text-gray-500">{k}:</span> {String(v)}</div>)}</div>}
                          <button type="button" onClick={() => setMConverged(false)} className="mt-2 text-[10px] text-gray-500 hover:text-white">Keep refining</button>
                        </div>
                      )}
                      <div ref={mEndRef} />
                    </div>
                    {!mConverged && (
                      <div className="flex gap-2 items-end">
                        <textarea value={mInput} onChange={e => setMInput(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); sendMissionTurn(mInput); } }} placeholder={mConvo.length === 0 ? "What should this run do?" : "Reply, or type your own answer…"} rows={2} className="flex-1 bg-gray-900 border border-tessera-border rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 resize-none" />
                        <button type="button" onClick={() => sendMissionTurn(mInput)} disabled={mBusy || !mInput.trim()} className="px-4 py-2 bg-tessera-gradient rounded-lg text-sm font-semibold disabled:opacity-40 transition">{mBusy ? "…" : mConvo.length === 0 ? "✨ Start" : "Send"}</button>
                      </div>
                    )}
                    {mError && <div className="mt-2 px-3 py-2 bg-red-500/10 border border-red-500/30 rounded-lg text-[11px] text-red-300">{mError}</div>}
                    {!mConverged
                      ? <p className="text-[10px] text-gray-600 mt-1.5">⌘/Ctrl+Enter to send. Launch unlocks once the mission is ready.</p>
                      : <p className="text-[10px] text-green-400/80 mt-1.5">✓ Mission ready — hit Launch below. (Use “Keep refining” to adjust.)</p>}
                  </div>
                  {/* RIGHT — live run plan (mirrors the builder's intent/assumptions panel) */}
                  <div className="w-60 shrink-0 bg-tessera-card glow-border rounded-lg p-3 max-h-[420px] overflow-y-auto">
                    <div className="text-[11px] font-bold text-tessera-accent uppercase tracking-wider mb-2">Run plan</div>
                    {mState?.goal ? <p className="text-[11px] text-gray-300 mb-2 whitespace-pre-wrap">{mState.goal}</p> : <p className="text-[11px] text-gray-600 italic mb-2">Your goal will take shape as we talk.</p>}
                    {Array.isArray(mState?.checklist) && mState.checklist.length > 0 && (
                      <div className="mb-2">
                        <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">Briefing ({mState.checklist.filter((c: any) => c?.resolved).length}/{mState.checklist.length})</div>
                        {mState.checklist.map((c: any, i: number) => (
                          <div key={c.id || i} className="text-[10px] flex items-start gap-1 mb-0.5">
                            <span className={c.resolved ? "text-green-400" : "text-gray-600"}>{c.resolved ? "✓" : "○"}</span>
                            <span className="text-gray-400"><span className={c.resolved ? "text-gray-300" : ""}>{c.label}</span>{c.resolved && c.value ? <span className="text-gray-500"> — {String(c.value)}</span> : ""}</span>
                          </div>
                        ))}
                      </div>
                    )}
                    {(() => {
                      const inps: Record<string, any> = { ...(mState?.inputs || {}), ...mFinalInputs };
                      return Object.keys(inps).length > 0 ? (
                        <div className="mb-2">
                          <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">Captured</div>
                          {Object.entries(inps).map(([k, v]) => <div key={k} className="text-[11px] text-gray-300 truncate"><span className="text-gray-500">{k}:</span> {String(v)}</div>)}
                        </div>
                      ) : null;
                    })()}
                    {Array.isArray(selected.inputs) && selected.inputs.length > 0 && (
                      <div className="mb-2">
                        <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">Required inputs</div>
                        {selected.inputs.map((inp: any) => {
                          const have = (mState?.inputs || {})[inp.id] || mFinalInputs[inp.id];
                          return <div key={inp.id} className="text-[10px] flex items-center gap-1"><span className={have ? "text-green-400" : "text-gray-600"}>{have ? "✓" : "○"}</span><span className="text-gray-400 truncate">{inp.label}{inp.required && !have ? " *" : ""}</span></div>;
                        })}
                      </div>
                    )}
                    {attachments.length > 0 && (
                      <div className="mb-2">
                        <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">Files</div>
                        {attachments.map((a, i) => <div key={i} className="text-[10px] text-gray-400 truncate">📎 {a.name}</div>)}
                      </div>
                    )}
                    {mState?.notes && <p className="text-[10px] text-gray-500 mt-1 whitespace-pre-wrap">{mState.notes}</p>}
                    <div className="mt-2 text-[10px]">{mConverged ? <span className="text-green-400">✓ Ready to launch</span> : <span className="text-gray-600">Gathering details…</span>}</div>
                  </div>
                  </div>
                )}
              </div>
              <label className="flex items-center gap-2 cursor-pointer select-none">
                <input type="checkbox" checked={dryRun} onChange={e => setDryRun(e.target.checked)} className="accent-tessera-accent" />
                <span className="text-xs text-gray-300">⚡ Dry run <span className="text-gray-500">(fast test — runs the full pipeline with minimal work; not for real results)</span></span>
              </label>

              {/* Advanced: per-run model selection (run-scoped; never edits the ecosystem) */}
              <div className="border-t border-tessera-border pt-3">
                <button type="button" onClick={() => setShowAdvanced(s => !s)} className="text-xs text-gray-400 hover:text-white flex items-center gap-1">
                  <span>{showAdvanced ? "▾" : "▸"}</span> Advanced · {selected.strategy === "quorum" ? "the room, gates, models & memory" : "models & memory"}
                  {(modelProfile || Object.values(typeOverrides).some(Boolean) || !rankedRecall || Object.keys(quorumSettings).length > 0) && <span className="text-[10px] text-tessera-accent">• customized</span>}
                </button>
                {showAdvanced && (
                  <div className="mt-2 space-y-2 bg-gray-900/60 border border-tessera-border rounded-lg p-3">
                    {/* Quorum only: how the room itself runs. Validated server-side (fail closed) and
                        recorded with the run as _run_config.json so runs can be compared. */}
                    {selected.strategy === "quorum" && (
                      <QuorumAdvancedPanel value={quorumSettings} onChange={setQuorumSettings} />
                    )}
                    <label className="flex items-center gap-2 cursor-pointer select-none border-b border-tessera-border pb-2">
                      <input type="checkbox" checked={rankedRecall} onChange={e => setRankedRecall(e.target.checked)} className="accent-tessera-accent" />
                      <span className="text-[11px] text-gray-300">🔎 Relevance-ranked memory recall <span className="text-gray-500">(default on — ranks shared-catalog dossiers by relevance and injects only the most on-point; turn OFF to compare against the raw chronological dump)</span></span>
                    </label>
                    <p className="text-[10px] text-gray-500">Applies to <strong>this run only</strong>. Opus is slower/costlier; Sonnet is fast. Leave on Default to use the ecosystem&apos;s tiering.</p>
                    {recommendation?.status === "ok" && recommendation.recommended && (
                      <div className="bg-tessera-accent/5 border border-tessera-accent/30 rounded p-2 text-[11px]">
                        <div className="text-tessera-accent font-semibold">⚡ Optimizer suggests: {recommendation.recommended.signature}</div>
                        <div className="text-gray-400 mt-0.5">
                          {recommendation.recommended_delta
                            ? <>cost {recommendation.recommended_delta.cost_pct ?? "—"}% · time {recommendation.recommended_delta.latency_pct ?? "—"}% vs <span className="text-gray-300">{recommendation.recommended_delta.vs}</span></>
                            : <>best observed config</>} · {recommendation.cohort_runs} run{recommendation.cohort_runs === 1 ? "" : "s"}
                        </div>
                        <button type="button" onClick={() => applyRecommendation(recommendation.recommended.run_config)} className="mt-1 text-tessera-accent hover:underline">Apply →</button>
                      </div>
                    )}
                    {recommendation?.status === "no_data" && (
                      <p className="text-[10px] text-gray-500">Optimizer: not enough run data yet — run a few model configs and rate them to get a recommendation.</p>
                    )}
                    {/* Model PROFILE and per-agent-type overrides are a DAG concept: they tier agent
                        types in a pipeline. A quorum is a room — it has a chair and members, not a
                        leaf/synthesizer/terminal hierarchy — and the quorum runtime reads neither
                        value, so showing them here would be a control that does nothing. Quorum model
                        choice lives in the panel above (models.chair / models.members). */}
                    {selected.strategy !== "quorum" && (<>
                    <div>
                      <label className="block text-[11px] text-gray-400 mb-1">Profile</label>
                      <select value={modelProfile} onChange={e => setModelProfile(e.target.value)} className="w-full bg-gray-900 border border-tessera-border rounded px-2 py-1.5 text-xs text-white">
                        <option value="">Default (ecosystem tiering)</option>
                        <option value="fast">Fast — Sonnet everywhere</option>
                        <option value="balanced">Balanced — Opus only on final synthesis</option>
                        <option value="thorough">Thorough — Opus on all synthesis + review</option>
                        <option value="deepseek">DeepSeek V3.2 (experimental — all roles)</option>
                      </select>
                    </div>
                    <div className="grid grid-cols-2 gap-2">
                      {([
                        ["leaf", "Research (leaf)"],
                        ["synthesizer", "Synthesis"],
                        ["terminal", "Final synthesis"],
                        ["reviewer", "Checkpoints"],
                        ["drafter", "Drafter"],
                      ] as const).map(([tier, label]) => (
                        <div key={tier}>
                          <label className="block text-[11px] text-gray-400 mb-1">{label}</label>
                          <select value={typeOverrides[tier] || ""} onChange={e => setTypeOverrides(o => ({ ...o, [tier]: e.target.value }))} className="w-full bg-gray-900 border border-tessera-border rounded px-2 py-1 text-[11px] text-white">
                            <option value="">Auto</option>
                            <option value="opus">Opus</option>
                            <option value="sonnet">Sonnet</option>
                            <option value="haiku">Haiku</option>
                          </select>
                        </div>
                      ))}
                    </div>
                    <p className="text-[10px] text-gray-500">Per-type overrides take precedence over the profile.</p>
                    </>)}
                  </div>
                )}
              </div>

              {(() => {
                const useMission = missionMode === "quick" ? mission : mFinalMission;
                const canLaunch = !isRunning && (missionMode === "quick" ? !!mission.trim() : mConverged);
                return (
                  <button
                    onClick={() => {
                      const allInputs: Record<string, string> = { ...inputValues, ...(missionMode === "chat" ? mFinalInputs : {}) };
                      onLaunch(selected.id, useMission, allInputs, dryRun, buildRunConfig(), attachments.map(a => ({ name: a.name, ref: a.content })), mConvo);
                      setModal(null); setAttachments([]);
                    }}
                    disabled={!canLaunch}
                    className="w-full py-3 bg-tessera-gradient hover:opacity-90 disabled:opacity-30 disabled:cursor-not-allowed rounded-lg text-sm font-semibold transition shadow-lg shadow-purple-500/20"
                  >
                    {isRunning ? "Running..." : (missionMode === "chat" && !mConverged) ? "Plan the mission above to launch" : dryRun ? "⚡ Launch (dry run)" : "▶ Launch"}
                  </button>
                );
              })()}
            </div>
          </div>
        </div>
      )}

      {/* Files modal */}
      {modal === "files" && selected && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setModal(null)}>
          <div className="bg-tessera-card border border-tessera-border rounded-xl p-6 w-[700px] max-h-[80vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
            <div className="flex items-center justify-between mb-4">
              <h3 className="text-lg font-bold">📁 Files — {selected.name}</h3>
              <label className="text-xs px-3 py-1.5 bg-tessera-accent/10 text-tessera-accent rounded cursor-pointer hover:bg-tessera-accent/20">
                {uploading ? "Uploading..." : "+ Upload"}
                <input type="file" className="hidden" disabled={uploading} onChange={(e) => { const f = e.target.files?.[0]; if (f) uploadEcoFile(selected.id, f); e.target.value = ""; }} />
              </label>
            </div>
            {attachError && <div className="mb-3 text-[11px] text-red-300 bg-red-500/10 border border-red-500/30 rounded px-2 py-1 flex items-start gap-1.5"><span>⚠</span><span className="flex-1">{attachError}</span><button type="button" onClick={() => setAttachError(null)} className="text-red-400">✕</button></div>}
            {ecoFiles.length > 0 ? (
              <table className="w-full text-sm">
                <thead><tr className="text-xs text-gray-400 uppercase border-b border-gray-700">
                  <th className="text-left px-3 py-2">File</th>
                  <th className="text-left px-3 py-2">Tag</th>
                  <th className="text-left px-3 py-2">Summary</th>
                  <th className="text-left px-3 py-2">Status</th>
                  <th className="text-right px-3 py-2"></th>
                </tr></thead>
                <tbody>
                  {ecoFiles.map(f => (
                    <tr key={f.filename} className="border-t border-gray-800">
                      <td className="px-3 py-2 text-gray-300 text-xs">{f.filename}</td>
                      <td className="px-3 py-2"><span className="px-1.5 py-0.5 bg-purple-900/30 text-purple-300 rounded text-xs">{f.tag}</span></td>
                      <td className="px-3 py-2 text-gray-500 text-xs">{f.summary}</td>
                      <td className="px-3 py-2">{f.status === "ready" ? <span className="text-green-400 text-xs">✓</span> : <span className="text-yellow-500 text-xs">⏳</span>}</td>
                      <td className="px-3 py-2 text-right"><button onClick={() => deleteEcoFile(selected.id, f.filename)} className="text-xs text-red-400 hover:text-red-300">Delete</button></td>
                    </tr>
                  ))}
                </tbody>
              </table>
            ) : (
              <p className="text-center text-gray-500 text-sm py-8">No files. Upload documents that agents should access on every run.</p>
            )}
          </div>
        </div>
      )}

      {/* Sharing dialog */}
      {modal === "sharing" && selected && (
        <SharingDialog ecosystemId={selected.id} ecosystemName={selected.name} onClose={() => setModal(null)} />
      )}

      {/* Export dialog */}
      {modal === "export" && selected && (
        <ExportEcosystemDialog ecosystemId={selected.id} ecosystemName={selected.name} onClose={() => setModal(null)} />
      )}
    </div>
  );
}


type Taxo = { type: string; key: string; description: string; status: string; accepted?: boolean };
type AgentCls = { id: string; disposition: string; entity_type?: string; reason?: string };
type DomainSummary = { id: string; name: string; description: string; taxonomy_count: number; version?: number; seeded_from?: string[]; ecosystems?: string[]; has_catalog?: boolean };

function DomainBuilderView({ onBrowseCatalog }: { onBrowseCatalog: (id: string) => void }) {
  const [screen, setScreen] = useState<"gallery" | "create" | "detail">("gallery");
  const [activeId, setActiveId] = useState<string | null>(null);
  const [domains, setDomains] = useState<DomainSummary[]>([]);
  const [loading, setLoading] = useState(true);
  const { pushStep } = useBackStack();

  const loadDomains = () => {
    setLoading(true);
    apiFetch(`${API_URL}/api/domains`).then(r => r.json()).then((d) => setDomains(Array.isArray(d) ? d : [])).catch(() => {}).finally(() => setLoading(false));
  };
  useEffect(() => { loadDomains(); }, []);
  // Register a Back step when a create/detail screen is open, so browser Back returns to the
  // domain gallery (not out of the Domains view). Disposer runs when `screen` returns to
  // gallery (in-app back) or on unmount, keeping browser history in sync.
  useEffect(() => {
    if (screen === "gallery") return;
    const dispose = pushStep(() => { setScreen("gallery"); });
    return dispose;
  }, [screen, pushStep]);

  if (screen === "create") return <DomainCreate onCancel={() => setScreen("gallery")} onCreated={(id) => { loadDomains(); setActiveId(id); setScreen("detail"); }} />;
  if (screen === "detail" && activeId) return <DomainDetail domainId={activeId} onBack={() => { loadDomains(); setScreen("gallery"); }} />;
  return <DomainGallery domains={domains} loading={loading} onNew={() => setScreen("create")} onOpen={(id) => { setActiveId(id); setScreen("detail"); }} onBrowseCatalog={onBrowseCatalog} />;
}

function CatalogBrowserView({ initialDomain }: { initialDomain: string }) {
  const [domain, setDomain] = useState(initialDomain || "");
  const [domains, setDomains] = useState<any[]>([]);
  const { pushStep } = useBackStack();
  useEffect(() => { setDomain(initialDomain || ""); }, [initialDomain]);
  useEffect(() => { apiFetch(`${API_URL}/api/domains`).then(r => r.json()).then((d) => { const all = Array.isArray(d) ? d : (d.domains || []); setDomains(all.filter((x: any) => x.has_catalog)); }).catch(() => {}); }, []);
  // Register a Back step when a domain is opened, so browser Back returns to the catalog
  // list (not out of the whole Catalog view). Disposer runs on cleanup — i.e. when `domain`
  // clears (in-app "← Catalogs") or on unmount — keeping the browser history in sync.
  useEffect(() => {
    if (!domain) return;
    const dispose = pushStep(() => setDomain(""));
    return dispose;
  }, [domain, pushStep]);
  if (domain) return <CatalogBrowser domainId={domain} onBack={() => setDomain("")} />;
  return (
    <div className="p-6 max-w-5xl mx-auto">
      <h1 className="text-xl font-semibold text-white mb-1">📚 Catalog Browser</h1>
      <p className="text-sm text-gray-400 mb-5">Select a domain to browse its authoritative catalog — entities plus generic (uncontextualized) methodology.</p>
      <div className="grid grid-cols-3 gap-3">
        {domains.map((d) => (
          <button key={d.id} onClick={() => setDomain(d.id)} className="border border-tessera-border bg-gray-900/40 rounded-lg p-4 text-left hover:border-tessera-accent min-h-[120px] flex flex-col">
            <div className="text-sm font-semibold text-white">{d.name || d.id}</div>
            <div className="text-xs text-gray-400 mt-1 flex-1 line-clamp-3">{d.description}</div>
            <div className="flex justify-between items-center text-[11px] mt-2"><span className="text-gray-500">🗂 {d.taxonomy_count} catalogs</span><span className="text-tessera-accent font-medium">Browse →</span></div>
          </button>
        ))}
        {domains.length === 0 && <div className="text-sm text-gray-500 col-span-3">No catalog-enabled domains yet. Add a <code>catalog</code> descriptor to a domain to make it browsable.</div>}
      </div>
    </div>
  );
}

function DomainGallery({ domains, loading, onNew, onOpen, onBrowseCatalog }: { domains: DomainSummary[]; loading: boolean; onNew: () => void; onOpen: (id: string) => void; onBrowseCatalog: (id: string) => void }) {
  return (
    <div className="p-6 max-w-5xl mx-auto">
      <h1 className="text-xl font-semibold text-white mb-1">🗂 Domains</h1>
      <p className="text-sm text-gray-400 mb-5">A domain owns a shared, entity-keyed knowledge taxonomy. Ecosystems are assigned to a domain and inherit its catalogs, so knowledge compounds across every ecosystem in the domain.</p>
      {loading ? <div className="text-sm text-gray-500">Loading…</div> : (
        <div className="grid grid-cols-3 gap-3">
          <button onClick={onNew} className="border border-dashed border-tessera-border rounded-lg p-4 text-left hover:border-tessera-accent min-h-[120px] flex flex-col justify-center">
            <div className="text-2xl text-tessera-accent">＋</div>
            <div className="text-sm font-medium text-white mt-1">New domain</div>
            <div className="text-xs text-gray-500">Describe it, then seed from an ecosystem or let the builder propose the taxonomy.</div>
          </button>
          {domains.map(d => (
            <div key={d.id} className="border border-tessera-border bg-gray-900/40 rounded-lg p-4 text-left min-h-[120px] flex flex-col hover:border-tessera-accent">
              <button onClick={() => onOpen(d.id)} className="text-left flex-1">
                <div className="text-sm font-semibold text-white">{d.name || d.id}</div>
                <div className="text-xs text-gray-400 mt-1 line-clamp-3">{d.description}</div>
                <div className="flex gap-3 text-[11px] text-gray-500 mt-2">
                  <span>🗂 {d.taxonomy_count} catalogs</span>
                  <span>⬡ {(d.ecosystems || []).length} ecosystems</span>
                </div>
              </button>
              {d.has_catalog && <button onClick={() => onBrowseCatalog(d.id)} className="mt-2 self-start text-[11px] font-medium text-tessera-accent hover:underline">📚 Browse catalog →</button>}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}

function DomainCreate({ onCancel, onCreated }: { onCancel: () => void; onCreated: (id: string) => void }) {
  const [step, setStep] = useState<"converse" | "describe" | "origin" | "review" | "ingest">("converse");
  const [name, setName] = useState("");
  const [description, setDescription] = useState("");
  const [ecos, setEcos] = useState<{ id: string; name?: string }[]>([]);
  const [seedEco, setSeedEco] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [dom, setDom] = useState<{ id: string; name: string; description: string } | null>(null);
  const [taxo, setTaxo] = useState<Taxo[]>([]);
  const [conclusions, setConclusions] = useState<string[]>([]);
  const [seededFrom, setSeededFrom] = useState<string[]>([]);
  const [creating, setCreating] = useState(false);
  // Conversational builder state (mirrors the ecosystem builder's converse dialogue).
  const [convo, setConvo] = useState<{ role: "user" | "assistant"; content: string }[]>([]);
  const [convoInput, setConvoInput] = useState("");
  const [nextQ, setNextQ] = useState<{ id?: string; text: string; options?: string[] } | null>(null);
  const [design, setDesign] = useState<any>({});
  const convoEndRef = useRef<HTMLDivElement>(null);
  useEffect(() => { convoEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" }); }, [convo, busy, nextQ]);
  const [ingest, setIngest] = useState<{ needs_ingest?: boolean; classification?: { type: string; disposition: string; why: string }[]; ingest?: { sources?: { name: string; produces?: string[]; fetch?: { transport?: string; url?: string }; normalize?: { mode?: string; builder?: string } }[] }; schema?: Record<string, unknown> } | null>(null);
  const [ingestBusy, setIngestBusy] = useState(false);
  const [starting, setStarting] = useState(false);

  const generateIngest = async (domId: string, desc: string) => {
    setIngestBusy(true); setErr(null);
    try {
      const res = await apiFetch(`${API_URL}/api/domains/seed`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ generate_ingest: true, domain: domId, description: desc, taxonomy: taxo.filter(t => t.accepted).map(({ accepted: _a, ...t }) => t) }) });
      const d = await res.json();
      if (!res.ok) throw new Error(d.error || `HTTP ${res.status}`);
      setIngest(d);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setIngestBusy(false); }
  };

  const saveAndStart = async (start: boolean) => {
    if (!dom?.id) return;
    setStarting(true); setErr(null);
    try {
      const ing = ingest?.ingest || {};
      if ((ing.sources || []).length) {
        if (!CATALOG_API) throw new Error("catalog API not configured");
        const r1 = await apiFetch(`${CATALOG_API}catalog/save-ingest`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ domain: dom.id, ingest: ing, schema: ingest?.schema }) });
        if (!r1.ok) throw new Error((await r1.json()).error || `save HTTP ${r1.status}`);
        if (start) {
          const r2 = await apiFetch(`${CATALOG_API}catalog/start-ingest`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ domain: dom.id }) });
          if (!r2.ok) throw new Error((await r2.json()).error || `start HTTP ${r2.status}`);
        }
      }
      onCreated(dom.id);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setStarting(false); }
  };

  useEffect(() => { apiFetch(`${API_URL}/api/ecosystems`).then(r => r.json()).then((d) => { const l = Array.isArray(d) ? d : []; setEcos(l); setSeedEco(p => p || (l[0]?.id ?? "")); }).catch(() => {}); }, []);

  // Conversational turn: post the message + running design_state, apply the returned turn.
  const sendDomainTurn = async (text: string) => {
    const msg = text.trim();
    if (!msg || busy) return;
    setErr(null); setBusy(true); setNextQ(null);
    const history = convo.map(m => ({ role: m.role, content: m.content }));
    setConvo(c => [...c, { role: "user", content: msg }]);
    setConvoInput("");
    try {
      const res = await apiFetch(`${API_URL}/api/domains/seed`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ mode: "converse", message: msg, history, design_state: design }) });
      const d = await res.json();
      if (!res.ok) throw new Error(d.error || `HTTP ${res.status}`);
      setConvo(c => [...c, { role: "assistant", content: d.reply || "(no reply)" }]);
      if (d.design) setDesign(d.design);
      setNextQ(d.next_question || null);
      if (d.converged) {
        const dg = d.design || {};
        setDom({ id: dg.domain?.suggested_id || "", name: dg.domain?.suggested_name || name, description: dg.domain?.description || description });
        setTaxo((dg.taxonomy || []).map((t: Taxo) => ({ ...t, accepted: true })));
        setConclusions(dg.conclusions_never_cataloged || []);
        setSeededFrom([]);
        setStep("review");
      }
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };

  const propose = async (mode: "describe" | "seed") => {
    setBusy(true); setErr(null);
    try {
      const body = mode === "describe" ? { name, description } : { ecosystem_id: seedEco };
      const res = await apiFetch(`${API_URL}/api/domains/seed`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setDom({ id: data.domain?.suggested_id || "", name: data.domain?.suggested_name || name, description: data.domain?.description || description });
      setTaxo((data.taxonomy || []).map((t: Taxo) => ({ ...t, accepted: true })));
      setSeededFrom(mode === "seed" ? [seedEco] : []);
      setStep("review");
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };

  const create = async () => {
    if (!dom?.id) return;
    setCreating(true); setErr(null);
    try {
      const payload = { id: dom.id, name: dom.name, description: dom.description, taxonomy: taxo.filter(t => t.accepted).map(({ accepted: _a, ...t }) => t), conclusions_never_cataloged: conclusions, seeded_from: seededFrom };
      const res = await apiFetch(`${API_URL}/api/domains`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(payload) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setDom({ ...dom, id: data.domain || dom.id });
      setStep("ingest");
      generateIngest(data.domain || dom.id, dom.description);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setCreating(false); }
  };

  return (
    <div className="p-6 max-w-3xl mx-auto">
      <button onClick={onCancel} className="text-xs text-gray-400 hover:text-white mb-3">← Domains</button>
      <h1 className="text-xl font-semibold text-white mb-4">New domain</h1>
      {err && <div className="bg-red-500/10 border border-red-500/40 rounded p-3 text-sm text-red-300 mb-4">{err}</div>}

      {step === "converse" && (
        <div className="space-y-3">
          <p className="text-sm text-gray-400">Describe the domain and I&apos;ll help you shape its catalog taxonomy — one question at a time. Focus on the durable, reusable facts ecosystems in this domain should share (not any single run&apos;s conclusions).</p>
          <div className="border border-tessera-border rounded-lg bg-gray-900/40 p-3 max-h-[46vh] overflow-y-auto space-y-3">
            {convo.length === 0 && <div className="text-xs text-gray-500">e.g. &ldquo;Australian personal financial advice reference knowledge — super caps &amp; thresholds, advice-law standards, insurance definitions, market indicators.&rdquo;</div>}
            {convo.map((m, i: number) => (
              <div key={i} className={m.role === "user" ? "text-right" : "text-left"}>
                <div className={`inline-block rounded-lg px-3 py-2 text-sm whitespace-pre-wrap ${m.role === "user" ? "bg-tessera-accent/20 text-white" : "bg-gray-800 text-gray-200"}`}>{m.content}</div>
              </div>
            ))}
            {busy && <div className="text-xs text-gray-500">Thinking…</div>}
            {(design?.taxonomy || []).length > 0 && (
              <div className="border-t border-tessera-border pt-2 mt-2">
                <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-1">Proposed catalogs so far</div>
                <div className="flex flex-wrap gap-1">
                  {(design.taxonomy || []).map((t: Taxo, i: number) => (
                    <span key={i} className="text-[11px] font-mono bg-gray-800 text-gray-300 rounded px-2 py-0.5" title={t.description}>{t.type}</span>
                  ))}
                </div>
              </div>
            )}
            <div ref={convoEndRef} />
          </div>
          {nextQ?.options && nextQ.options.length > 0 && (
            <div className="flex flex-wrap gap-2">
              {nextQ.options.map((o, i: number) => (
                <button key={i} onClick={() => sendDomainTurn(o)} disabled={busy} className="border border-tessera-border rounded-full px-3 py-1 text-xs text-gray-200 hover:border-tessera-accent disabled:opacity-50">{o}</button>
              ))}
            </div>
          )}
          <div className="flex gap-2">
            <input value={convoInput} onChange={e => setConvoInput(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendDomainTurn(convoInput); } }} placeholder={nextQ?.text || "Describe the domain, or answer the question above…"} className="flex-1 bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white" />
            <button onClick={() => sendDomainTurn(convoInput)} disabled={busy || !convoInput.trim()} className="bg-tessera-accent text-black font-medium rounded px-4 py-2 text-sm disabled:opacity-50">Send</button>
          </div>
          {convo.length > 0 && (design?.taxonomy || []).length > 0 && (
            <button onClick={() => { const dg = design || {}; setDom({ id: dg.domain?.suggested_id || "", name: dg.domain?.suggested_name || name, description: dg.domain?.description || description }); setTaxo((dg.taxonomy || []).map((t: Taxo) => ({ ...t, accepted: true }))); setConclusions(dg.conclusions_never_cataloged || []); setSeededFrom([]); setStep("review"); }} className="text-xs text-tessera-accent hover:underline">Skip ahead — review &amp; create now →</button>
          )}
          <button onClick={() => setStep("describe")} className="block text-xs text-gray-500 hover:text-white">Prefer a form? Use the classic wizard →</button>
        </div>
      )}

      {step === "describe" && (
        <div className="space-y-3">
          <div>
            <label className="block text-[11px] uppercase tracking-wider text-gray-500 mb-1">Name <span className="normal-case text-gray-600">(optional — the builder can refine it)</span></label>
            <input value={name} onChange={e => setName(e.target.value)} placeholder="e.g. AU Residential Property" className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white" />
          </div>
          <div>
            <label className="block text-[11px] uppercase tracking-wider text-gray-500 mb-1">Description</label>
            <textarea value={description} onChange={e => setDescription(e.target.value)} rows={3} placeholder="What does this domain cover? What kinds of durable facts should ecosystems in it reuse?" className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-gray-200" />
          </div>
          <button onClick={() => setStep("origin")} disabled={!description.trim()} className="bg-tessera-accent text-black font-medium rounded px-4 py-2 text-sm disabled:opacity-50">Next →</button>
        </div>
      )}

      {step === "origin" && (
        <div className="space-y-4">
          <p className="text-sm text-gray-400">How should we build the taxonomy?</p>
          <div className="grid grid-cols-2 gap-3">
            <button onClick={() => propose("describe")} disabled={busy} className="border border-tessera-border rounded-lg p-4 text-left hover:border-tessera-accent disabled:opacity-50">
              <div className="text-lg">🤖</div>
              <div className="text-sm font-medium text-white mt-1">Let the builder propose it</div>
              <div className="text-xs text-gray-500 mt-1">Top-down — infer the catalogs from your description.</div>
            </button>
            <div className="border border-tessera-border rounded-lg p-4">
              <div className="text-lg">🌱</div>
              <div className="text-sm font-medium text-white mt-1">Seed from an ecosystem</div>
              <div className="text-xs text-gray-500 mt-1 mb-2">Bottom-up — infer from an existing ecosystem&apos;s agents.</div>
              <select value={seedEco} onChange={e => setSeedEco(e.target.value)} className="w-full bg-gray-900 border border-tessera-border rounded px-2 py-1 text-xs text-white mb-2">{ecos.map(e => <option key={e.id} value={e.id}>{e.name || e.id}</option>)}</select>
              <button onClick={() => propose("seed")} disabled={busy || !seedEco} className="bg-gray-700 hover:bg-gray-600 text-white rounded px-3 py-1 text-xs disabled:opacity-50">Seed →</button>
            </div>
          </div>
          {busy && <div className="text-sm text-gray-500">Proposing taxonomy…</div>}
          <button onClick={() => setStep("describe")} className="text-xs text-gray-400 hover:text-white">← Back</button>
        </div>
      )}

      {step === "review" && dom && (
        <div className="space-y-4">
          <div className="bg-gray-900/60 border border-tessera-border rounded-lg p-4 space-y-2">
            <div className="flex gap-2">
              <input value={dom.id} onChange={e => setDom({ ...dom, id: e.target.value })} className="bg-gray-900 border border-tessera-border rounded px-2 py-1 text-sm text-white font-mono w-1/2" />
              <input value={dom.name} onChange={e => setDom({ ...dom, name: e.target.value })} className="bg-gray-900 border border-tessera-border rounded px-2 py-1 text-sm text-white w-1/2" />
            </div>
            <textarea value={dom.description} onChange={e => setDom({ ...dom, description: e.target.value })} rows={2} className="w-full bg-gray-900 border border-tessera-border rounded px-2 py-1 text-xs text-gray-300" />
          </div>
          <div className="text-[11px] uppercase tracking-wider text-gray-500">Proposed catalogs — tick to include</div>
          <div className="grid grid-cols-2 gap-2">
            {taxo.map((t, i: number) => (
              <label key={i} className={`flex gap-2 items-start border rounded-lg p-3 cursor-pointer ${t.accepted ? "border-tessera-border bg-gray-900/40" : "border-tessera-border bg-gray-900/20 opacity-50"}`}>
                <input type="checkbox" checked={!!t.accepted} onChange={e => setTaxo(taxo.map((x, j) => j === i ? { ...x, accepted: e.target.checked } : x))} className="accent-tessera-accent mt-0.5" />
                <div>
                  <div className="font-mono text-sm text-white">{t.type}</div>
                  <div className="font-mono text-[11px] text-gray-500">{t.key}</div>
                  <div className="text-xs text-gray-400 mt-1">{t.description}</div>
                </div>
              </label>
            ))}
          </div>
          {conclusions.length > 0 && (
            <div>
              <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-1">Never cataloged (conclusions)</div>
              <div className="flex flex-wrap gap-1">
                {conclusions.map((c, i: number) => (
                  <span key={i} className="text-[11px] bg-red-500/10 border border-red-500/30 text-red-300 rounded px-2 py-0.5">{c}</span>
                ))}
              </div>
            </div>
          )}
          <div className="flex gap-3 items-center">
            <button onClick={create} disabled={creating || !dom.id} className="bg-tessera-accent text-black font-medium rounded px-4 py-2 text-sm disabled:opacity-50">{creating ? "Creating…" : `Create domain (${taxo.filter(t => t.accepted).length} catalogs)`}</button>
            <button onClick={() => setStep(convo.length ? "converse" : "origin")} className="text-xs text-gray-400 hover:text-white">← {convo.length ? "Back to chat" : "Re-choose"}</button>
          </div>
        </div>
      )}

      {step === "ingest" && dom && (
        <div className="space-y-4">
          <div className="text-sm text-gray-300">✓ Domain <strong className="text-white">{dom.name}</strong> created. Now plumb its ingestion — where does each catalog&apos;s knowledge come from?</div>
          {ingestBusy && <div className="text-sm text-gray-500">🔎 Identifying authoritative sources…</div>}
          {ingest && (
            <>
              <div className="text-[11px] uppercase tracking-wider text-gray-500">Source of truth per catalog</div>
              <div className="space-y-1">
                {(ingest.classification || []).map((c, i: number) => (
                  <div key={i} className="flex gap-2 text-xs items-center">
                    <span className="font-mono text-white w-40 truncate">{c.type}</span>
                    <span className={c.disposition === "ingest-fed" ? "text-tessera-accent" : "text-gray-400"}>{c.disposition}</span>
                    <span className="text-gray-500">{c.why}</span>
                  </div>
                ))}
              </div>
              {(ingest.ingest?.sources || []).length > 0 ? (
                <>
                  <div className="text-[11px] uppercase tracking-wider text-gray-500">Proposed ingestion sources — review / edit URLs</div>
                  {(ingest.ingest?.sources || []).map((s, i: number) => (
                    <div key={i} className="border border-tessera-border rounded-lg p-3 space-y-1">
                      <div className="flex gap-2 items-center text-sm flex-wrap">
                        <span className="font-mono text-white">{s.name}</span>
                        <span className="text-[11px] text-gray-500">{(s.produces || []).join(", ")}</span>
                        <span className="text-[11px] px-2 py-0.5 rounded bg-gray-800 text-gray-300">{s.normalize?.mode}{s.normalize?.builder ? `:${s.normalize.builder}` : ""}</span>
                        <span className="text-[11px] text-gray-500">{s.fetch?.transport}</span>
                      </div>
                      <input value={s.fetch?.url || ""} onChange={e => { const src = [...(ingest.ingest?.sources || [])]; src[i] = { ...src[i], fetch: { ...src[i].fetch, url: e.target.value } }; setIngest({ ...ingest, ingest: { ...ingest.ingest, sources: src } }); }} className="w-full bg-gray-900 border border-tessera-border rounded px-2 py-1 text-[11px] font-mono text-gray-300" />
                      {String(s.fetch?.url || "").includes("#VERIFY") && <div className="text-[11px] text-yellow-500">⚠ unverified URL — confirm it&apos;s real before starting</div>}
                    </div>
                  ))}
                  <div className="flex gap-3 items-center">
                    <button onClick={() => saveAndStart(true)} disabled={starting} className="bg-tessera-accent text-black font-medium rounded px-4 py-2 text-sm disabled:opacity-50">{starting ? "Saving + starting…" : "Save + Start ingestion"}</button>
                    <button onClick={() => saveAndStart(false)} disabled={starting} className="text-xs text-gray-400 hover:text-white">Save without starting</button>
                  </div>
                </>
              ) : (
                <div className="text-sm text-gray-400">No external ingest needed — this domain&apos;s catalogs are <strong>run-fed</strong> (accreted from ecosystem runs).</div>
              )}
              <button onClick={() => onCreated(dom.id)} className="text-xs text-gray-400 hover:text-white block">{(ingest.ingest?.sources || []).length > 0 ? "Skip ingestion for now →" : "Finish →"}</button>
            </>
          )}
        </div>
      )}
    </div>
  );
}

function DomainDetail({ domainId, onBack }: { domainId: string; onBack: () => void }) {
  const [tab, setTab] = useState<"overview" | "governance" | "advanced">("overview");
  const [domain, setDomain] = useState<Record<string, unknown> | null>(null);
  const [loading, setLoading] = useState(true);
  const load = () => { setLoading(true); apiFetch(`${API_URL}/api/domains/${domainId}`).then(r => r.json()).then(setDomain).catch(() => {}).finally(() => setLoading(false)); };
  useEffect(() => { load(); /* eslint-disable-next-line react-hooks/exhaustive-deps */ }, [domainId]);

  const pendingCount = ((domain?.pending_changes as unknown[]) || []).length;
  const tabLabel = (t: string) => t === "overview" ? "Overview" : t === "governance" ? `Governance${pendingCount ? ` (${pendingCount})` : ""}` : "Ecosystem wiring";

  return (
    <div className="p-6 max-w-5xl mx-auto">
      <button onClick={onBack} className="text-xs text-gray-400 hover:text-white mb-3">← Domains</button>
      {loading || !domain ? <div className="text-sm text-gray-500">Loading…</div> : (
        <>
          <h1 className="text-xl font-semibold text-white">{(domain.name as string) || (domain.domain as string)} <span className="text-xs text-gray-500 font-normal">v{(domain.version as number) || 1} · owner {(domain.owner_email as string) || "—"}</span></h1>
          <p className="text-sm text-gray-400 mb-3">{domain.description as string}</p>
          <div className="flex gap-1 border-b border-tessera-border mb-4">
            {(["overview", "governance", "advanced"] as const).map(t => (
              <button key={t} onClick={() => setTab(t)} className={`px-3 py-2 text-sm ${tab === t ? "text-white border-b-2 border-tessera-accent" : "text-gray-400 hover:text-white"}`}>{tabLabel(t)}</button>
            ))}
          </div>
          {tab === "overview" ? <DomainOverview domain={domain} onSaved={load} />
            : tab === "governance" ? <DomainGovernance domainId={domainId} domain={domain} onChanged={load} />
            : <DomainWiring domainId={domainId} onChanged={load} />}
        </>
      )}
    </div>
  );
}

function DomainGovernance({ domainId, domain, onChanged }: { domainId: string; domain: Record<string, unknown>; onChanged: () => void }) {
  const pending = (domain.pending_changes as { id: string; kind: string; payload: { type?: string }; proposed_by: string }[]) || [];
  const log = (domain.change_log as { version: number; action: string; by: string }[]) || [];
  const ecos = (domain.ecosystems as string[]) || [];
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [nt, setNt] = useState({ type: "", key: "{id}", description: "" });

  const propose = async (kind: string, payload: Record<string, unknown>) => {
    setBusy(true); setErr(null);
    try {
      const res = await apiFetch(`${API_URL}/api/domains/${domainId}/propose`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ kind, payload }) });
      if (!res.ok) throw new Error((await res.json()).error || `HTTP ${res.status}`);
      setNt({ type: "", key: "{id}", description: "" }); onChanged();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };
  const ratify = async (change_id: string, decision: string) => {
    setBusy(true); setErr(null);
    try {
      const res = await apiFetch(`${API_URL}/api/domains/${domainId}/ratify`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ change_id, decision }) });
      if (!res.ok) throw new Error((await res.json()).error || `HTTP ${res.status}`);
      onChanged();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };

  return (
    <div className="space-y-5">
      <p className="text-sm text-gray-400">This domain is <strong>shared</strong> by {ecos.length} ecosystem{ecos.length === 1 ? "" : "s"}{ecos.length ? ` (${ecos.join(", ")})` : ""}. Changes are proposed by anyone and <strong>ratified by the owner</strong>, versioned + logged.</p>
      {err && <div className="bg-red-500/10 border border-red-500/40 rounded p-3 text-sm text-red-300">{err}</div>}

      <div>
        <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-2">Pending proposals ({pending.length})</div>
        {pending.length === 0 ? <div className="text-xs text-gray-500">none</div> : (
          <div className="space-y-2">
            {pending.map(c => (
              <div key={c.id} className="border border-tessera-accent/40 bg-tessera-accent/5 rounded-lg p-3 flex items-center justify-between">
                <div className="text-sm text-gray-200"><span className="font-mono text-tessera-accent">{c.kind}</span> <span className="font-mono">{c.payload?.type}</span> <span className="text-[11px] text-gray-500">— proposed by {c.proposed_by} · affects {ecos.length} eco{ecos.length === 1 ? "" : "s"}</span></div>
                <div className="flex gap-2">
                  <button onClick={() => ratify(c.id, "approve")} disabled={busy} className="bg-green-600 hover:bg-green-500 text-white rounded px-3 py-1 text-xs disabled:opacity-50">Approve</button>
                  <button onClick={() => ratify(c.id, "reject")} disabled={busy} className="bg-gray-700 hover:bg-gray-600 text-white rounded px-3 py-1 text-xs disabled:opacity-50">Reject</button>
                </div>
              </div>
            ))}
          </div>
        )}
      </div>

      <div className="border border-tessera-border rounded-lg p-3">
        <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-2">Propose a new catalog</div>
        <div className="flex gap-2">
          <input value={nt.type} onChange={e => setNt({ ...nt, type: e.target.value })} placeholder="entity-type" className="bg-gray-900 border border-tessera-border rounded px-2 py-1 text-sm font-mono text-white w-1/4" />
          <input value={nt.key} onChange={e => setNt({ ...nt, key: e.target.value })} placeholder="{key}" className="bg-gray-900 border border-tessera-border rounded px-2 py-1 text-xs font-mono text-gray-400 w-1/4" />
          <input value={nt.description} onChange={e => setNt({ ...nt, description: e.target.value })} placeholder="description" className="bg-gray-900 border border-tessera-border rounded px-2 py-1 text-xs text-gray-300 flex-1" />
          <button onClick={() => propose("add_catalog", nt)} disabled={busy || !nt.type} className="bg-tessera-accent text-black rounded px-3 py-1 text-xs font-medium disabled:opacity-50">Propose</button>
        </div>
      </div>

      <div>
        <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-2">Change log</div>
        {log.length === 0 ? <div className="text-xs text-gray-500">none</div> :
          <div className="space-y-1">{log.slice().reverse().map((l, i: number) => <div key={i} className="text-xs text-gray-400"><span className="font-mono text-gray-500">v{l.version}</span> {l.action} <span className="text-gray-600">— {l.by}</span></div>)}</div>}
      </div>
    </div>
  );
}

function DomainOverview({ domain, onSaved }: { domain: Record<string, unknown>; onSaved: () => void }) {
  const [taxo, setTaxo] = useState<Taxo[]>((domain.taxonomy as Taxo[]) || []);
  const [saving, setSaving] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);
  useEffect(() => { setTaxo((domain.taxonomy as Taxo[]) || []); }, [domain]);
  const ecosystems = (domain.ecosystems as string[]) || [];

  const save = async () => {
    setSaving(true); setMsg(null);
    try {
      const res = await apiFetch(`${API_URL}/api/domains`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ...domain, id: domain.domain, taxonomy: taxo }) });
      if (!res.ok) throw new Error((await res.json()).error || `HTTP ${res.status}`);
      setMsg("Saved"); onSaved();
    } catch (e) { setMsg(e instanceof Error ? e.message : String(e)); } finally { setSaving(false); }
  };

  return (
    <div className="space-y-4">
      <div>
        <div className="flex items-center justify-between mb-2">
          <div className="text-[11px] uppercase tracking-wider text-gray-500">Catalogs ({taxo.length})</div>
          <button onClick={() => setTaxo([...taxo, { type: "new-entity", key: "{id}", description: "", status: "proposed" }])} className="text-xs text-tessera-accent">＋ Add catalog</button>
        </div>
        <div className="space-y-2">
          {taxo.map((t, i: number) => (
            <div key={i} className="border border-tessera-border bg-gray-900/40 rounded-lg p-3 flex gap-2 items-start">
              <div className="flex-1 grid grid-cols-3 gap-2">
                <input value={t.type} onChange={e => setTaxo(taxo.map((x, j) => j === i ? { ...x, type: e.target.value } : x))} className="bg-gray-900 border border-tessera-border rounded px-2 py-1 text-sm font-mono text-white" />
                <input value={t.key} onChange={e => setTaxo(taxo.map((x, j) => j === i ? { ...x, key: e.target.value } : x))} className="bg-gray-900 border border-tessera-border rounded px-2 py-1 text-xs font-mono text-gray-400" />
                <input value={t.description} onChange={e => setTaxo(taxo.map((x, j) => j === i ? { ...x, description: e.target.value } : x))} placeholder="description" className="bg-gray-900 border border-tessera-border rounded px-2 py-1 text-xs text-gray-300" />
              </div>
              <button onClick={() => setTaxo(taxo.filter((_, j) => j !== i))} className="text-gray-500 hover:text-red-400 text-sm">✕</button>
            </div>
          ))}
        </div>
      </div>
      <div>
        <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-1">Mounted ecosystems</div>
        {ecosystems.length === 0 ? <div className="text-xs text-gray-500">none yet — wire one in the Ecosystem wiring tab</div> :
          <div className="flex flex-wrap gap-2">{ecosystems.map((e) => <span key={e} className="text-xs bg-gray-800 border border-tessera-border rounded-full px-2 py-0.5 text-gray-300">⬡ {e}</span>)}</div>}
      </div>
      <div className="flex items-center gap-3">
        <button onClick={save} disabled={saving} className="bg-tessera-accent text-black font-medium rounded px-4 py-2 text-sm disabled:opacity-50">{saving ? "Saving…" : "Save taxonomy"}</button>
        {msg && <span className="text-sm text-green-400">{msg}</span>}
      </div>
    </div>
  );
}

function DomainWiring({ domainId, onChanged }: { domainId: string; onChanged: () => void }) {
  const [ecos, setEcos] = useState<{ id: string; name?: string }[]>([]);
  const [ecoId, setEcoId] = useState("");
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [agents, setAgents] = useState<AgentCls[]>([]);
  const [gaps, setGaps] = useState<{ entity_type: string; agent: string }[]>([]);
  const [leaks, setLeaks] = useState<{ agent: string }[]>([]);
  const [seeded, setSeeded] = useState(false);
  const [assigning, setAssigning] = useState(false);
  const [plan, setPlan] = useState<{ catalogs_to_add?: string[]; agent_writers?: { agent: string; writes: string }[] } | null>(null);
  const [applying, setApplying] = useState(false);
  const [applied, setApplied] = useState<{ added_catalogs?: string[]; reused_catalogs?: string[]; leak_removed?: { namespace: string; removed: string[] }[]; recompiled?: boolean; backup_key?: string; warnings?: string[] } | null>(null);

  useEffect(() => { apiFetch(`${API_URL}/api/ecosystems`).then(r => r.json()).then((d) => { const l = Array.isArray(d) ? d : []; setEcos(l); setEcoId(p => p || (l[0]?.id ?? "")); }).catch(() => {}); }, []);

  const classify = async () => {
    if (!ecoId) return;
    setBusy(true); setErr(null); setSeeded(false); setPlan(null);
    try {
      const res = await apiFetch(`${API_URL}/api/domains/seed`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ecosystem_id: ecoId }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setAgents((data.agents || []).map((a: AgentCls) => ({ ...a })));
      setGaps(data.gaps || []); setLeaks(data.leaks || []); setSeeded(true);
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setBusy(false); }
  };

  const assign = async () => {
    setAssigning(true); setErr(null); setPlan(null);
    try {
      const res = await apiFetch(`${API_URL}/api/domains/${domainId}/assign`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ecosystem_id: ecoId, agents }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setPlan(data.apply_plan || null); onChanged();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setAssigning(false); }
  };

  const applyToEco = async () => {
    setApplying(true); setErr(null); setApplied(null);
    try {
      const res = await apiFetch(`${API_URL}/api/domains/${domainId}/apply`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ ecosystem_id: ecoId, agents }) });
      const data = await res.json();
      if (!res.ok) throw new Error(data.error || `HTTP ${res.status}`);
      setApplied(data); onChanged();
    } catch (e) { setErr(e instanceof Error ? e.message : String(e)); } finally { setApplying(false); }
  };

  const dispColor: Record<string, string> = { use: "text-green-400", promote: "text-tessera-accent", localize: "text-yellow-400", none: "text-gray-500" };

  return (
    <div className="space-y-4">
      <p className="text-sm text-gray-400">Wire an ecosystem to this domain: classify its agents (entity → catalog, or conclusion → run-only), then assign it to compute the apply-plan.</p>
      <div className="flex items-center gap-2">
        <select value={ecoId} onChange={e => setEcoId(e.target.value)} className="bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white min-w-[240px]">{ecos.map(e => <option key={e.id} value={e.id}>{e.name || e.id}</option>)}</select>
        <button onClick={classify} disabled={busy || !ecoId} className="bg-gray-700 hover:bg-gray-600 text-white rounded px-4 py-2 text-sm disabled:opacity-50">{busy ? "Classifying…" : "Classify agents"}</button>
      </div>
      {err && <div className="bg-red-500/10 border border-red-500/40 rounded p-3 text-sm text-red-300">{err}</div>}
      {seeded && (
        <>
          <div className="border border-tessera-border rounded-lg overflow-hidden">
            <table className="w-full text-sm"><tbody>
              {agents.map((a, i: number) => (
                <tr key={i} className="border-b border-tessera-border last:border-0">
                  <td className="px-3 py-2 text-gray-300">{a.id}</td>
                  <td className="px-3 py-2"><select value={a.disposition} onChange={e => setAgents(agents.map((x, j) => j === i ? { ...x, disposition: e.target.value } : x))} className={`bg-gray-900 border border-tessera-border rounded px-1.5 py-0.5 text-xs font-medium ${dispColor[a.disposition] || "text-gray-400"}`}>{["use", "promote", "localize", "none"].map(o => <option key={o} value={o}>{o}</option>)}</select></td>
                  <td className="px-3 py-2"><input value={a.entity_type || ""} onChange={e => setAgents(agents.map((x, j) => j === i ? { ...x, entity_type: e.target.value } : x))} placeholder="—" className="bg-gray-900 border border-tessera-border rounded px-1.5 py-0.5 text-xs font-mono text-gray-400 w-40" /></td>
                  <td className="px-3 py-2 text-[11px] text-gray-500">{a.reason}</td>
                </tr>
              ))}
            </tbody></table>
          </div>
          <div className="grid grid-cols-2 gap-4">
            <div className="bg-tessera-accent/5 border border-tessera-accent/30 rounded-lg p-3"><div className="text-[11px] uppercase tracking-wider text-tessera-accent mb-1">Missing catalogs</div>{gaps.length === 0 ? <div className="text-xs text-gray-500">none</div> : gaps.map((g, i: number) => <div key={i} className="text-xs text-gray-300">➕ <span className="font-mono">{g.entity_type}</span> <span className="text-gray-500">— {g.agent}</span></div>)}</div>
            <div className="bg-red-500/5 border border-red-500/30 rounded-lg p-3"><div className="text-[11px] uppercase tracking-wider text-red-300 mb-1">Conclusion leaks</div>{leaks.length === 0 ? <div className="text-xs text-gray-500">none</div> : leaks.map((l, i: number) => <div key={i} className="text-xs text-gray-300">! {l.agent}</div>)}</div>
          </div>
          <button onClick={assign} disabled={assigning} className="bg-tessera-accent text-black font-medium rounded px-4 py-2 text-sm disabled:opacity-50">{assigning ? "Assigning…" : `Assign ${ecoId} to domain`}</button>
          {plan && (
            <div className="bg-gray-900/60 border border-tessera-border rounded-lg p-4 space-y-1 text-xs text-gray-300">
              <div className="text-[11px] uppercase tracking-wider text-gray-500">Apply plan</div>
              <div>Catalogs to add: {plan.catalogs_to_add?.length ? plan.catalogs_to_add.map((c) => <span key={c} className="font-mono text-tessera-accent mr-2">➕{c}</span>) : <span className="text-gray-500">none</span>}</div>
              <div>Writers: {plan.agent_writers?.length ? plan.agent_writers.map((w, i: number) => <span key={i} className="mr-2">{w.agent}→<span className="font-mono text-green-400">{w.writes}</span></span>) : <span className="text-gray-500">none</span>}</div>
              <div className="text-[11px] text-gray-500">Preview — the YAML rewrite + ASL recompile (apply) is the next gated step.</div>
            </div>
          )}
          {plan && (
            <button onClick={applyToEco} disabled={applying} className="bg-red-600 hover:bg-red-500 text-white font-medium rounded px-4 py-2 text-sm disabled:opacity-50">
              {applying ? "Applying…" : `Apply to ${ecoId} (rewrite + recompile)`}
            </button>
          )}
          {applied && (
            <div className="bg-green-500/5 border border-green-500/30 rounded-lg p-4 space-y-1 text-xs text-gray-300">
              <div className="text-[11px] uppercase tracking-wider text-green-400">✓ Applied {applied.recompiled ? "+ recompiled" : "(recompile pending)"}</div>
              <div>Catalogs added: {applied.added_catalogs?.length ? applied.added_catalogs.map(c => <span key={c} className="font-mono text-green-400 mr-2">{c}</span>) : <span className="text-gray-500">none</span>}</div>
              <div>Reused (already existed): {applied.reused_catalogs?.length ? applied.reused_catalogs.join(", ") : <span className="text-gray-500">none</span>}</div>
              {applied.leak_removed && applied.leak_removed.length > 0 && <div>Leak writers removed: {applied.leak_removed.map((l, i: number) => <span key={i} className="mr-2 text-yellow-400">{l.namespace}</span>)}</div>}
              <div className="text-[11px] text-gray-500">Backup: <span className="font-mono">{applied.backup_key}</span></div>
            </div>
          )}
        </>
      )}
    </div>
  );
}

function RegistryView() {
  const [agents, setAgents] = useState<any[]>([]);
  const [tools, setTools] = useState<any[]>([]);

  useEffect(() => {
    apiFetch(`${API_URL}/api/agents`).then((r) => r.json()).then(setAgents).catch(() => {});
    apiFetch(`${API_URL}/api/tools`).then((r) => r.json()).then(setTools).catch(() => {});
  }, []);

  return (
    <div>
      <h2 className="text-xl font-bold mb-4">Agent Registry</h2>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
        <div>
          <h3 className="text-sm font-semibold text-gray-400 uppercase mb-3">Agents ({agents.length})</h3>
          <div className="space-y-2">
            {agents.map((agent: any, i: number) => (
              <div key={i} className="bg-battlefield-card border border-battlefield-border rounded p-3">
                <div className="font-medium text-sm">{agent.name || agent.agent_id}</div>
                <div className="text-xs text-gray-500">{agent.ecosystem} • {agent.status || "registered"}</div>
              </div>
            ))}
            {agents.length === 0 && <p className="text-gray-500 text-sm">No agents registered. Run an ecosystem to register agents.</p>}
          </div>
        </div>
        <div>
          <h3 className="text-sm font-semibold text-gray-400 uppercase mb-3">Tools ({tools.length})</h3>
          <div className="space-y-2">
            {tools.map((tool: any, i: number) => (
              <div key={i} className="bg-battlefield-card border border-battlefield-border rounded p-3">
                <div className="font-medium text-sm">{tool.name}</div>
                <div className="text-xs text-gray-500">{tool.description}</div>
              </div>
            ))}
            {tools.length === 0 && <p className="text-gray-500 text-sm">No tools registered.</p>}
          </div>
        </div>
      </div>
    </div>
  );
}

function BuilderView({ autoConverse, editEcoId, onEditHandled }: { autoConverse?: number; editEcoId?: string | null; onEditHandled?: () => void }) {
  const [ecoName, setEcoName] = useState("my-ecosystem");
  const [ecoDescription, setEcoDescription] = useState("");
  // Discovery metadata — author-owned config (builder-generated, wizard-editable).
  const [ecoDomain, setEcoDomain] = useState("");
  const [ecoTags, setEcoTags] = useState<string[]>([]);
  const [strategy, setStrategy] = useState<"orchestrated" | "choreography" | "quorum">("orchestrated");
  // Quorum archetype: the builder-emitted `quorum:` policy block (empanelment /
  // deliberation / verdict). Carried verbatim so it survives accept→serialize
  // (the wizard has no per-field editor for it — it's a generative archetype).
  const [quorumConfig, setQuorumConfig] = useState<any>(null);
  const [memoryStrategies, setMemoryStrategies] = useState<string[]>(["semantic", "episodic"]);
  const [qualityReport, setQualityReport] = useState(true);
  const [qualityChallenger, setQualityChallenger] = useState<"off" | "final" | "per_level">("per_level");
  const [qualityBlockBelow, setQualityBlockBelow] = useState<"STRONG" | "ADEQUATE" | "WEAK">("ADEQUATE");
  const [qualityGates, setQualityGates] = useState<{ citation: string; format: string; grounding: string; output_kind: string }>({ citation: "ask", format: "ask", grounding: "ask", output_kind: "ask" });
  const [qualityGrounding, setQualityGrounding] = useState(true);
  const [ecoInputs, setEcoInputs] = useState<{id: string; type: string; label: string; required: boolean; accept: string[]}[]>([]);
  const [agents, setAgents] = useState<BuilderAgent[]>([
    { id: "agent_1", name: "Agent 1", tools: [], prompt: "", edges: [], depends_on: [], triggers: [], emits: [], swarm: "" }
  ]);
  const [selectedAgent, setSelectedAgent] = useState<string>("agent_1");
  const [namespaces, setNamespaces] = useState<BuilderNamespace[]>([
    { name: "shared", write: ["agent_1"], read: ["all"] }
  ]);
  const [existingEcosystems, setExistingEcosystems] = useState<any[]>([]);
  const [ecoFilter, setEcoFilter] = useState("");
  const [previewTab, setPreviewTab] = useState<"yaml" | "graph">("graph");
  const [graphExpandPx, setGraphExpandPx] = useState(0);
  const [showDeployWizard, setShowDeployWizard] = useState(false);
  const [aiPrompt, setAiPrompt] = useState("");
  const [aiSuggesting, setAiSuggesting] = useState(false);
  const [aiSuggestion, setAiSuggestion] = useState<{ yaml: string; config: any; outputs?: { kind: string; artifacts: any[] }; quality?: any } | null>(null);
  const [aiSuggestError, setAiSuggestError] = useState<string | null>(null);
  const [showAiFlow, setShowAiFlow] = useState(false);
  const [pendingSuggestJobId, setPendingSuggestJobId] = useState<string | null>(null);
  // Deliverable outcome state (Outputs panel). kind: "report" (no download) or
  // "file_artifact" (produces downloadable file[s]).
  const [outcomeKind, setOutcomeKind] = useState<"report" | "file_artifact">("report");
  const [outcomeArtifacts, setOutcomeArtifacts] = useState<{ id: string; agent: string; format: string; save_as: string; download_label: string }[]>([]);
  const [audience, setAudience] = useState<string>("");

  // Persistent WebSocket for non-run server-push events (SUGGEST_RESULT etc.)
  const { clientId, connected: wsConnected, subscribe } = useClientWebSocket(WS_URL);

  // Conversational builder state (design-partner dialogue).
  const [convoMessages, setConvoMessages] = useState<{ role: "user" | "assistant"; content: string }[]>([]);
  const [designState, setDesignState] = useState<any>({});
  const [nextQuestion, setNextQuestion] = useState<{ id?: string; text: string; options?: string[] } | null>(null);
  const [streamingReply, setStreamingReply] = useState("");
  const [convoConverged, setConvoConverged] = useState(false);
  const [convoYaml, setConvoYaml] = useState<string | null>(null);
  const [convoInput, setConvoInput] = useState("");
  const [convoBusy, setConvoBusy] = useState(false);
  const [pendingConverseJobId, setPendingConverseJobId] = useState<string | null>(null);
  const [convoError, setConvoError] = useState<string | null>(null);
  // D.7: domain-aware builder — auto-match the description to an existing domain.
  const [selectedDomain, setSelectedDomain] = useState<{ domain: string; name: string; catalogs: { type: string; key: string }[] } | null>(null);
  const [domainSuggest, setDomainSuggest] = useState<{ id: string; name: string; reason: string } | null>(null);
  // D.7: no-match → offer to create a domain (bootstrapping) from the description.
  const [noDomainMatch, setNoDomainMatch] = useState(false);
  const [domainSeedText, setDomainSeedText] = useState("");
  const [domainDraft, setDomainDraft] = useState<{ id: string; name: string; description: string; catalogs: { type: string; key: string }[] } | null>(null);
  const [domainBusy, setDomainBusy] = useState(false);
  const acceptDomain = async (id: string) => {
    try {
      const r = await apiFetch(`${API_URL}/api/domains/${id}`);
      const d = await r.json();
      setSelectedDomain({ domain: d.domain || id, name: d.name || id, catalogs: (d.taxonomy || []).map((t: { type: string; key: string }) => ({ type: t.type, key: t.key })) });
      setDomainSuggest(null); setNoDomainMatch(false);
    } catch { /* ignore */ }
  };
  // Propose a brand-new domain (top-down) from the build description.
  const draftDomainFromBuild = async () => {
    setDomainBusy(true);
    try {
      const r = await apiFetch(`${API_URL}/api/domains/seed`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ description: domainSeedText }) });
      const d = await r.json();
      setDomainDraft({ id: d.domain?.suggested_id || "", name: d.domain?.suggested_name || "", description: d.domain?.description || "", catalogs: (d.taxonomy || []).map((t: { type: string; key: string }) => ({ type: t.type, key: t.key })) });
      setNoDomainMatch(false);
    } catch { /* ignore */ } finally { setDomainBusy(false); }
  };
  const createDraftDomain = async () => {
    if (!domainDraft) return;
    setDomainBusy(true);
    try {
      const r = await apiFetch(`${API_URL}/api/domains`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ id: domainDraft.id, name: domainDraft.name, description: domainDraft.description, taxonomy: domainDraft.catalogs.map(c => ({ ...c, status: "proposed" })) }) });
      const d = await r.json();
      setSelectedDomain({ domain: d.domain || domainDraft.id, name: domainDraft.name, catalogs: domainDraft.catalogs });
      setDomainDraft(null);
    } catch { /* ignore */ } finally { setDomainBusy(false); }
  };
  const convoEndRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    convoEndRef.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [convoMessages, streamingReply, nextQuestion, convoBusy]);

  useEffect(() => {
    return subscribe((event) => {
      if (event.type === "SUGGEST_RESULT") {
        if (event.jobId !== pendingSuggestJobId) return;
        setAiSuggesting(false);
        setPendingSuggestJobId(null);
        if (event.error) {
          setAiSuggestError(String(event.error));
          return;
        }
        if (event.yaml && event.config) {
          setAiSuggestion({ yaml: event.yaml as string, config: event.config, outputs: (event as any).outputs, quality: (event as any).quality });
        } else {
          setAiSuggestError("Suggest returned no yaml");
        }
      } else if (event.type === "CONVERSE_DELTA") {
        if ((event as any).jobId !== pendingConverseJobId) return;
        setStreamingReply((s) => s + ((event as any).text || ""));
      } else if (event.type === "CONVERSE_RESULT") {
        if ((event as any).jobId !== pendingConverseJobId) return;
        setConvoBusy(false);
        setPendingConverseJobId(null);
        const ev = event as any;
        if (ev.error) { setConvoError(String(ev.error)); setStreamingReply(""); return; }
        const replyText = ev.reply || streamingReply || "(no reply)";
        setConvoMessages((m) => [...m, { role: "assistant", content: replyText }]);
        setStreamingReply("");
        if (ev.design) setDesignState(ev.design);
        setNextQuestion(ev.next_question || null);
        setConvoConverged(!!ev.converged);
        setConvoYaml(ev.yaml || null);
        if (ev.converged && ev.yaml && ev.config) {
          setAiSuggestion({ yaml: ev.yaml, config: ev.config, outputs: ev.outputs, quality: ev.quality });
        }
      }
    });
  }, [subscribe, pendingSuggestJobId, pendingConverseJobId, streamingReply]);

  const sendTurn = async (text: string) => {
    const msg = text.trim();
    if (!msg || convoBusy) return;
    if (!wsConnected) { setConvoError("WebSocket not connected — refresh and try again."); return; }
    setConvoError(null);
    setConvoBusy(true);
    setStreamingReply("");
    setNextQuestion(null);
    const history = convoMessages.map((m) => ({ role: m.role, content: m.content }));
    setConvoMessages((m) => [...m, { role: "user", content: msg }]);
    setConvoInput("");
    // D.7: on the first turn, match the description to an existing domain (async, non-blocking).
    if (history.length === 0 && !selectedDomain) {
      setDomainSeedText(msg);
      apiFetch(`${API_URL}/api/domains/match`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ match: msg }) })
        .then(r => r.json()).then(d => {
          const b = d?.best;
          if (b?.id) {
            const cand = (d.candidates || []).find((c: { id: string; name: string }) => c.id === b.id);
            setDomainSuggest({ id: b.id, name: cand?.name || b.id, reason: b.reason || "" });
          } else {
            setNoDomainMatch(true);  // no relevant domain — offer to create one
          }
        }).catch(() => {});
    }
    try {
      const resp = await apiFetch(`${API_URL}/api/ecosystems/suggest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "converse", message: msg, history, design_state: designState, clientId, domain: selectedDomain || undefined }),
      });
      const data = await resp.json();
      if (data.error) throw new Error(data.error);
      if (data.status === "pending" && data.jobId) {
        setPendingConverseJobId(data.jobId);
      } else if (data.reply !== undefined) {
        // sync fallback (no clientId)
        setConvoBusy(false);
        setConvoMessages((m) => [...m, { role: "assistant", content: data.reply }]);
        if (data.design) setDesignState(data.design);
        setNextQuestion(data.next_question || null);
        setConvoConverged(!!data.converged);
        setConvoYaml(data.yaml || null);
        if (data.converged && data.yaml && data.config) setAiSuggestion({ yaml: data.yaml, config: data.config, outputs: data.outputs, quality: data.quality });
      }
    } catch (e: any) {
      setConvoBusy(false);
      setConvoError(e?.message || "Turn failed");
    }
  };

  const suggestEcosystem = async () => {
    if (!aiPrompt.trim()) return;
    if (!wsConnected) {
      setAiSuggestError("WebSocket not connected — cannot receive async result. Refresh and try again.");
      return;
    }
    setAiSuggesting(true);
    setAiSuggestion(null);
    setAiSuggestError(null);
    try {
      const resp = await apiFetch(`${API_URL}/api/ecosystems/suggest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description: aiPrompt, clientId }),
      });
      if (!resp.ok) {
        const text = await resp.text();
        throw new Error(`HTTP ${resp.status}: ${text.slice(0, 200)}`);
      }
      const data = await resp.json();
      if (data.error) throw new Error(data.error);
      if (data.status !== "pending" || !data.jobId) {
        throw new Error("Server did not accept async suggest job");
      }
      setPendingSuggestJobId(data.jobId);
      // Result will arrive via WebSocket SUGGEST_RESULT event (handled in subscribe above)
    } catch (e: any) {
      setAiSuggesting(false);
      setAiSuggestError(e?.message || "Suggest failed");
    }
  };

  const acceptSuggestion = () => {
    if (!aiSuggestion?.config) return;
    const config = aiSuggestion.config;
    const toArr = (v: any): any[] => Array.isArray(v) ? v : (v == null || v === "" ? [] : [v]);
    setEcoName(config.ecosystem?.name || "my-ecosystem");
    setEcoDescription(config.ecosystem?.description || "");
    setEcoDomain((config.domains && config.domains[0]) || "");
    setEcoTags(Array.isArray(config.tags) ? config.tags : (Array.isArray(config.ecosystem?.tags) ? config.ecosystem.tags : []));
    setStrategy(config.strategy || "orchestrated");
    // Quorum archetype: keep the emitted policy block (+ its verdict gate) so the
    // state→YAML serializer can re-emit it. No agent DAG for this shape.
    setQuorumConfig(config.strategy === "quorum" ? { ...(config.quorum || {}), _gate: config.quality?.gate } : null);
    setMemoryStrategies((config.memory?.strategies || ["semantic", "episodic"]).map((s: any) => typeof s === "string" ? s : s.type || s.name || "semantic"));
    setNamespaces((config.memory?.shared_namespaces || []).map((ns: any) => ({
      name: ns.name,
      write: toArr(ns.write),
      read: toArr(ns.read),
    })));
    setEcoInputs((config.ecosystem?.inputs || config.inputs || []).map((inp: any) => ({
      id: inp.id || "", type: inp.type || "text", label: inp.label || "", required: inp.required ?? true, accept: inp.accept || [],
    })));
    const loadedAgents = (config.agents || []).map((a: any) => ({
      id: a.id,
      name: a.name || a.id,
      tools: toArr(a.tools),
      prompt: a.prompt || "",
      edges: toArr(a.edges),
      depends_on: toArr(a.depends_on),
      triggers: toArr(a.triggers),
      emits: toArr(a.emits),
      swarm: a.swarm || "",
      tool_budget: (a.tool_budget && typeof a.tool_budget === "object") ? a.tool_budget : undefined,
      output_kind: a.output_kind || undefined,
      output_format: (a.output_format && typeof a.output_format === "object") ? (() => {
        const allowed = ["csv", "docx", "html", "json", "markdown", "pdf", "png", "yaml", "bundle"];
        let t = a.output_format.type || "html";
        let sa = a.output_format.save_as || "";
        if (!allowed.includes(t)) {
          const base = sa.includes(".") ? sa.slice(0, sa.lastIndexOf(".")) : (sa || "deliverable");
          t = "markdown"; sa = base + ".md";
        }
        return { type: t, save_as: sa, validate: a.output_format.validate ?? true };
      })() : undefined,
    }));
    setAgents(loadedAgents);
    // Capture the recommended quality posture (challenger mode + block_below +
    // gates) so the wizard's Quality step opens pre-filled with the builder's
    // recommendation. Falls back to config.quality, then sensible defaults.
    const q = aiSuggestion.quality || _parseQuality(config);
    setQualityReport(q.report ?? true);
    setQualityChallenger((q.challenger as any) || "per_level");
    setQualityBlockBelow((q.block_below as any) || "ADEQUATE");
    setQualityGrounding(q.grounding ?? true);
    setQualityGates({ citation: q.gates?.citation || "ask", format: q.gates?.format || "ask", grounding: q.gates?.grounding || "ask", output_kind: q.gates?.output_kind || "ask" });
    // Capture the declared outcome (deliverable type) so it survives the
    // accept -> generateYaml round-trip and shows in the Outputs panel.
    const oc = config.ecosystem?.outcome || config.outcome || aiSuggestion.outputs || {};
    const ocKind = oc.kind === "file_artifact" ? "file_artifact" : "report";
    setOutcomeKind(ocKind);
    setAudience(oc.audience || "");
    setOutcomeArtifacts((oc.artifacts || []).map((a: any) => {
      const allowed = ["csv", "docx", "html", "json", "markdown", "pdf", "png", "yaml", "bundle"];
      let fmt = a.format || "html";
      let sa = a.save_as || "";
      if (!allowed.includes(fmt)) {  // e.g. "zip"/bundle not supported yet -> single markdown
        const base = sa.includes(".") ? sa.slice(0, sa.lastIndexOf(".")) : (sa || "deliverable");
        fmt = "markdown"; sa = base + ".md";
      }
      return {
        id: a.id || "spec_bundle",
        agent: a.agent || "",
        format: fmt,
        save_as: sa,
        download_label: a.download_label || "Download",
      };
    }));
    if (loadedAgents.length > 0) setSelectedAgent(loadedAgents[0].id);
    setAiSuggestion(null);
    setAiPrompt("");
    setShowAiFlow(false);
    // Restore the old one-click flow: go straight into the deploy wizard
    // (Review & Save → Compile & Deploy). The wizard surfaces the Outputs
    // (deliverable) editor so the user confirms what the run produces.
    setShowDeployWizard(true);
  };

  // Load existing ecosystems
  useEffect(() => {
    apiFetch(`${API_URL}/api/ecosystems`)
      .then(r => r.json())
      .then(data => {
        if (!Array.isArray(data)) return;
        // Phase 5d: system-managed ecosystems are repo-managed and not editable
        // via the UI builder. Filter them out of the "Load existing" list
        // so they never appear as targets for AI-builder modification.
        // (The list-ecosystems Lambda still returns them for the main
        // ecosystem list, where they show with a 🔒 SYSTEM badge.)
        setExistingEcosystems(data.filter((eco: any) => !eco.platform));
      })
      .catch(() => {});
  }, []);

  const newEcosystem = () => {
    setShowAiFlow(true);
    setAiPrompt("");
    setAiSuggestion(null);
  };

  // Presenter open_builder — jump straight into the conversational build flow.
  useEffect(() => {
    if (autoConverse) { setShowAiFlow(true); setAiPrompt(""); setAiSuggestion(null); }
  }, [autoConverse]);

  const loadEcosystem = (id: string) => {
    apiFetch(`${API_URL}/api/ecosystems/${id}`).then(r => r.json()).then(async (config: any) => {
      const toArr = (v: any): any[] => Array.isArray(v) ? v : (v == null || v === "" ? [] : [v]);
      setEcoName(config.ecosystem?.name || id);
      setEcoDescription(config.ecosystem?.description || "");
      setEcoDomain((config.domains && config.domains[0]) || "");
      setEcoTags(Array.isArray(config.tags) ? config.tags : (Array.isArray(config.ecosystem?.tags) ? config.ecosystem.tags : []));
      setStrategy(config.strategy || "graph");
      setMemoryStrategies((config.memory?.strategies || ["semantic", "episodic"]).map((s: any) => typeof s === "string" ? s : s.type || s.name || "semantic"));
      setNamespaces((config.memory?.shared_namespaces || []).map((ns: any) => ({
        name: ns.name, write: toArr(ns.write), read: toArr(ns.read),
      })));
      setEcoInputs((config.ecosystem?.inputs || []).map((inp: any) => ({
        id: inp.id || "", type: inp.type || "text", label: inp.label || "", required: inp.required ?? true, accept: inp.accept || [],
      })));

      // Load agents with prompts from running agent services
      const agentPrompts: Record<string, string> = {
        recon: "You are a Red Team Recon agent. Probe ALL 3 production agents (customer_bot, data_pipeline, code_review) to discover their attack surface and trust boundaries.",
        injector: "You are a Red Team Injector agent. Craft and attempt prompt injection attacks against production agents. Try techniques ASI-03, ASI-07, ASI-05.",
        exfiltrator: "You are a Red Team Exfiltrator agent. After injection, attempt lateral movement from customer_bot to data_pipeline via shared_context, then extract data.",
        watcher: "You are a Blue Team Watcher agent. Monitor all production agents for behavioral anomalies. Alert if deviation > 0.5.",
        responder: "You are a Blue Team Responder agent. Contain compromised agents: revoke tokens, isolate agent, block memory access.",
        adapter: "You are a Blue Team Adapter agent. Analyze attacks, update guardrails, patch prompts, deploy countermeasures to drive Red success rate to zero.",
      };

      const agentTools: Record<string, string[]> = {
        recon: ["probe_api", "map_trust_boundaries"],
        injector: ["craft_injection", "attempt_exploit"],
        exfiltrator: ["lateral_move", "extract_data"],
        watcher: ["monitor_behavior", "score_deviation"],
        responder: ["revoke_tokens", "isolate_agent", "block_memory"],
        adapter: ["update_guardrails", "patch_prompt", "deploy_countermeasure"],
      };

      const loadedAgents = (config.agents || []).map((a: any) => ({
        id: a.id,
        name: a.name || a.id,
        tools: toArr(a.tools).length ? toArr(a.tools) : (agentTools[a.id] || []),
        prompt: a.prompt || agentPrompts[a.id] || "",
        edges: toArr(a.edges),
        depends_on: toArr(a.depends_on),
        triggers: toArr(a.triggers),
        emits: toArr(a.emits),
        swarm: a.swarm || "",
        tool_budget: (a.tool_budget && typeof a.tool_budget === "object") ? a.tool_budget : undefined,
        output_kind: a.output_kind || undefined,
        output_format: (a.output_format && typeof a.output_format === "object") ? {
          type: a.output_format.type || "html",
          save_as: a.output_format.save_as || "",
          validate: a.output_format.validate ?? true,
        } : undefined,
      }));
      setAgents(loadedAgents);
      // Preserve the recommended/saved quality posture when editing.
      const lq = _parseQuality(config);
      setQualityReport(lq.report);
      setQualityChallenger(lq.challenger as any);
      setQualityBlockBelow(lq.block_below as any);
      setQualityGrounding(lq.grounding);
      setQualityGates(lq.gates);
      // Preserve the deliverable outcome when editing an existing ecosystem.
      const oc = config.ecosystem?.outcome || config.outcome || {};
      setOutcomeKind(oc.kind === "file_artifact" ? "file_artifact" : "report");
      setAudience(oc.audience || "");
      setOutcomeArtifacts((oc.artifacts || []).map((a: any) => ({
        id: a.id || "spec_bundle", agent: a.agent || "", format: a.format || "html",
        save_as: a.save_as || "", download_label: a.download_label || "Download",
      })));
      if (loadedAgents.length > 0) setSelectedAgent(loadedAgents[0].id);
    }).catch(() => {});
  };

  // Deep-link edit: when the details page navigates here to edit an ecosystem,
  // load it into the builder exactly once.
  const editLoadedRef = useRef<string | null>(null);
  useEffect(() => {
    if (editEcoId && editLoadedRef.current !== editEcoId) {
      editLoadedRef.current = editEcoId;
      loadEcosystem(editEcoId);
      onEditHandled?.();
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [editEcoId]);

  const currentAgent = agents.find(a => a.id === selectedAgent);

  const [agentDescribePrompt, setAgentDescribePrompt] = useState("");
  const [agentDescribing, setAgentDescribing] = useState(false);
  const [showAgentAiFlow, setShowAgentAiFlow] = useState(false);
  const [agentSuggestion, setAgentSuggestion] = useState<BuilderAgent | null>(null);

  const addAgent = () => {
    setShowAgentAiFlow(true);
    setAgentDescribePrompt("");
    setAgentSuggestion(null);
  };

  const addBlankAgent = () => {
    const id = `agent_${agents.length + 1}`;
    setAgents([...agents, { id, name: `Agent ${agents.length + 1}`, tools: [], prompt: "", edges: [], depends_on: [], triggers: [], emits: [], swarm: "" }]);
    setSelectedAgent(id);
    setShowAgentAiFlow(false);
  };

  const describeAgent = async () => {
    if (!agentDescribePrompt.trim()) return;
    setAgentDescribing(true);
    setAgentSuggestion(null);
    try {
      const resp = await apiFetch(`${API_URL}/api/agents/suggest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ description: agentDescribePrompt, ecosystem_context: ecoName }),
      });
      const data = await resp.json();
      if (data.agent) {
        const a = data.agent;
        setAgentSuggestion({ id: a.id || `agent_${agents.length + 1}`, name: a.name || "New Agent", tools: a.tools || [], prompt: a.prompt || "", edges: a.edges || [], depends_on: a.depends_on || [], triggers: a.triggers || [], emits: a.emits || [], swarm: a.swarm || "" });
      }
    } catch { /* ignore */ }
    setAgentDescribing(false);
  };

  const acceptAgentSuggestion = () => {
    if (!agentSuggestion) return;
    setAgents([...agents, agentSuggestion]);
    setSelectedAgent(agentSuggestion.id);
    setAgentSuggestion(null);
    setAgentDescribePrompt("");
    setShowAgentAiFlow(false);
  };

  const cloneAgent = (sourceId: string) => {
    const source = agents.find(a => a.id === sourceId);
    if (!source) return;
    const id = `${source.id}_copy`;
    setAgents([...agents, { ...source, id, name: `${source.name} (copy)` }]);
    setSelectedAgent(id);
  };

  const removeAgent = (id: string) => {
    setAgents(agents.filter(a => a.id !== id));
    if (selectedAgent === id) setSelectedAgent(agents[0]?.id || "");
  };

  const updateAgent = (id: string, updates: Partial<BuilderAgent>) => {
    setAgents(agents.map(a => a.id === id ? { ...a, ...updates } : a));
  };

  const generatedYaml = generateYaml(ecoName, ecoDescription, strategy, memoryStrategies, agents, namespaces, {report: qualityReport, challenger: qualityChallenger, grounding: qualityGrounding, block_below: qualityBlockBelow, gates: qualityGates}, ecoInputs, { kind: outcomeKind, artifacts: outcomeArtifacts, audience }, ecoDomain, ecoTags, quorumConfig);

  return (
    <div className="h-[calc(100vh-100px)] flex flex-col">
      <div className="mb-4">
        <h2 className="text-2xl font-bold">Ecosystem Builder</h2>
        <p className="text-gray-500 text-sm">Configure agents, strategy, memory, and coordination</p>
      </div>

      {showAiFlow ? (
        <div className="flex-1 flex gap-4 min-h-0 overflow-hidden">
          {/* LEFT — conversation */}
          <div className="flex-1 flex flex-col min-w-0 max-w-3xl">
            <div className="mb-3">
              <h3 className="text-lg font-bold">Design your ecosystem</h3>
              <p className="text-xs text-gray-500">Describe what you want — I&apos;ll ask one thing at a time and keep the design &amp; assumptions on the right. Then we build it.</p>
            </div>

            {/* D.7: domain inheritance banner */}
            {selectedDomain ? (
              <div className="mb-2 px-3 py-2 rounded-lg bg-tessera-accent/10 border border-tessera-accent/30 text-xs text-gray-200 flex items-center justify-between">
                <span>🗂 Domain: <strong className="text-tessera-accent">{selectedDomain.name}</strong> — inherits {selectedDomain.catalogs.length} shared catalog{selectedDomain.catalogs.length === 1 ? "" : "s"}; agents will reuse them.</span>
                <button onClick={() => setSelectedDomain(null)} className="text-gray-400 hover:text-white">change</button>
              </div>
            ) : domainDraft ? (
              <div className="mb-2 px-3 py-2 rounded-lg bg-tessera-accent/10 border border-tessera-accent/30 text-xs text-gray-200 flex items-center justify-between">
                <span>🗂 New domain <strong className="text-tessera-accent">{domainDraft.name}</strong> ({domainDraft.catalogs.length} catalogs: {domainDraft.catalogs.map(c => c.type).slice(0, 5).join(", ")}{domainDraft.catalogs.length > 5 ? "…" : ""}). Create it + use it here?</span>
                <span className="flex gap-2">
                  <button onClick={createDraftDomain} disabled={domainBusy} className="bg-tessera-accent text-black rounded px-2 py-0.5 font-medium disabled:opacity-50">{domainBusy ? "…" : "Create"}</button>
                  <button onClick={() => setDomainDraft(null)} className="text-gray-400 hover:text-white">Cancel</button>
                </span>
              </div>
            ) : domainSuggest ? (
              <div className="mb-2 px-3 py-2 rounded-lg bg-gray-800 border border-tessera-border text-xs text-gray-300 flex items-center justify-between">
                <span>🗂 This looks like domain <strong className="text-white">{domainSuggest.name}</strong>{domainSuggest.reason ? ` — ${domainSuggest.reason}` : ""}. Wire it so new agents reuse its catalogs?</span>
                <span className="flex gap-2">
                  <button onClick={() => acceptDomain(domainSuggest.id)} className="bg-tessera-accent text-black rounded px-2 py-0.5 font-medium">Use domain</button>
                  <button onClick={() => setDomainSuggest(null)} className="text-gray-400 hover:text-white">Dismiss</button>
                </span>
              </div>
            ) : noDomainMatch ? (
              <div className="mb-2 px-3 py-2 rounded-lg bg-gray-800 border border-dashed border-tessera-border text-xs text-gray-300 flex items-center justify-between">
                <span>🗂 No existing domain fits this. Create one from this ecosystem so future related ecos reuse its catalogs?</span>
                <span className="flex gap-2">
                  <button onClick={draftDomainFromBuild} disabled={domainBusy} className="bg-gray-700 hover:bg-gray-600 text-white rounded px-2 py-0.5 disabled:opacity-50">{domainBusy ? "Proposing…" : "Create a domain"}</button>
                  <button onClick={() => setNoDomainMatch(false)} className="text-gray-400 hover:text-white">Skip</button>
                </span>
              </div>
            ) : null}

            {/* messages (scrollable, fills height) */}
            <div className="flex-1 bg-tessera-card glow-border rounded-xl p-4 overflow-y-auto space-y-3 min-h-0">
              {convoMessages.length === 0 && !streamingReply && !convoBusy && (
                <div className="text-sm text-gray-500 h-full flex items-center justify-center text-center px-6">Tell me what you want the ecosystem to do — e.g. &ldquo;Produce Kiro steering docs that enforce our AWS ASH scanner findings.&rdquo;</div>
              )}
              {convoMessages.map((m, i) => (
                <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                  <div className={`max-w-[85%] px-3 py-2 rounded-lg text-sm whitespace-pre-wrap ${m.role === "user" ? "bg-tessera-accent/20 text-tessera-accent" : "bg-gray-800 text-gray-200"}`}>{m.content}</div>
                </div>
              ))}
              {streamingReply && (
                <div className="flex justify-start">
                  <div className="max-w-[85%] px-3 py-2 rounded-lg text-sm whitespace-pre-wrap bg-gray-800/70 text-gray-300 italic">{streamingReply}<span className="not-italic animate-pulse">▍</span></div>
                </div>
              )}
              {convoBusy && !streamingReply && <div className="text-xs text-gray-500 italic animate-pulse">Builder is thinking…</div>}

              {/* The one current question — rendered via the unified DecisionSurface primitive */}
              {nextQuestion && !convoConverged && (
                <DecisionSurface
                  busy={convoBusy}
                  decision={{
                    id: nextQuestion.id || "q",
                    kind: "question",
                    title: "Builder",
                    prompt: nextQuestion.text,
                    options: (nextQuestion.options || []).map((o) => ({ id: o, label: o })),
                    allowText: false,
                    commit: "reversible",
                  } as DecisionPoint}
                  onResolve={(r) => sendTurn(r.optionId || r.text || "")}
                />
              )}

              {/* Convergence accept */}
              {convoConverged && aiSuggestion && (
                <div className="bg-tessera-card border border-tessera-accent/40 rounded-lg p-4">
                  <div className="flex items-center justify-between mb-2"><span className="text-sm font-bold text-tessera-accent">{aiSuggestion.config?.ecosystem?.name}</span><span className="text-xs text-gray-500">{aiSuggestion.config?.agents?.length || 0} agents</span></div>
                  <p className="text-xs text-gray-400 mb-3">{aiSuggestion.config?.ecosystem?.description}</p>
                  <div className="flex gap-2">
                    <button onClick={() => setConvoConverged(false)} className="text-xs text-gray-400 hover:text-white">Keep refining</button>
                    <button onClick={acceptSuggestion} className="ml-auto px-4 py-2 bg-tessera-gradient rounded-lg text-sm font-semibold">✓ Accept &amp; Configure</button>
                  </div>
                </div>
              )}
              <div ref={convoEndRef} />
            </div>

            {/* input pinned at the bottom of the left column */}
            {!convoConverged && (
              <div className="mt-3 bg-tessera-card glow-border rounded-xl p-3">
                <textarea
                  value={convoInput}
                  onChange={e => setConvoInput(e.target.value)}
                  onKeyDown={e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); sendTurn(convoInput); } }}
                  placeholder={convoMessages.length === 0 ? "Describe your ecosystem…" : "Reply, or type your own answer…"}
                  rows={2}
                  className="w-full bg-gray-900 border border-tessera-border rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 resize-none mb-2"
                />
                <div className="flex gap-2 items-center">
                  <button onClick={() => setShowAiFlow(false)} className="text-xs text-gray-400 hover:text-white transition">Skip — blank config</button>
                  <span className="text-[10px] text-gray-600">⌘/Ctrl+Enter</span>
                  <button onClick={() => sendTurn(convoInput)} disabled={convoBusy || !convoInput.trim()} className="ml-auto px-5 py-2 bg-tessera-gradient rounded-lg text-sm font-semibold disabled:opacity-40 transition">{convoBusy ? "…" : convoMessages.length === 0 ? "✨ Start" : "Send"}</button>
                </div>
                {convoError && <div className="mt-2 px-3 py-2 bg-red-500/10 border border-red-500/30 rounded-lg text-xs text-red-300">{convoError}</div>}
              </div>
            )}
          </div>

          {/* RIGHT — live design + assumptions */}
          <div className="w-80 shrink-0 overflow-y-auto space-y-3">
            <div className="bg-tessera-card glow-border rounded-xl p-4">
              <div className="text-xs font-bold text-tessera-accent uppercase tracking-wider mb-2">Design{designState?.intent?.name ? ` — ${designState.intent.name}` : ""}</div>
              {designState?.intent?.goal && <p className="text-[11px] text-gray-400 mb-2">{designState.intent.goal}</p>}
              {Array.isArray(designState?.agents) && designState.agents.length > 0 ? (
                <div className="space-y-1.5">
                  {designState.agents.map((a: any, i: number) => (
                    <div key={i} className="bg-gray-900 border border-gray-700 rounded px-2 py-1.5">
                      <div className="text-[12px] text-gray-200">{a.name || a.id}</div>
                      {a.role && <div className="text-[10px] text-gray-500">{a.role}</div>}
                    </div>
                  ))}
                </div>
              ) : (
                <p className="text-[11px] text-gray-600">The agents will appear here as we settle the design.</p>
              )}
            </div>

            <div className="bg-tessera-card glow-border rounded-xl p-4">
              <div className="text-xs font-bold text-tessera-accent uppercase tracking-wider mb-2">Assumptions</div>
              {Array.isArray(designState?.assumptions) && designState.assumptions.length > 0 ? (
                <div className="space-y-1.5">
                  {designState.assumptions.map((a: any, i: number) => {
                    const st = String(a.status || "open");
                    const color = st === "confirmed" || st === "grounded" ? "text-green-400" : st === "needs_verification" ? "text-amber-400" : "text-gray-400";
                    const icon = st === "confirmed" || st === "grounded" ? "✓" : st === "needs_verification" ? "⚠" : "?";
                    return (
                      <div key={i} className="flex items-start gap-2 text-[11px]">
                        <span className={`shrink-0 ${color}`}>{icon}</span>
                        <span className="text-gray-300">{a.statement}<span className="text-gray-600"> · {a.type}/{st}</span></span>
                      </div>
                    );
                  })}
                </div>
              ) : (
                <p className="text-[11px] text-gray-600">I&apos;ll list what I&apos;m assuming here — and flag anything I need you to confirm.</p>
              )}
            </div>
          </div>
        </div>
      ) : (
      <div className="grid grid-cols-12 gap-4 flex-1 min-h-0">
        {/* Left: Ecosystem Config */}
        <div className="col-span-3 bg-tessera-card glow-border rounded-xl p-4 overflow-y-auto">
          <h3 className="text-xs font-bold text-tessera-accent uppercase tracking-wider mb-3">Ecosystem</h3>

          <div className="mb-4 pb-3 border-b border-tessera-border">
            <button onClick={newEcosystem} className="w-full text-left px-2 py-1.5 text-xs bg-tessera-accent/20 text-tessera-accent hover:bg-tessera-accent/30 rounded font-semibold transition">＋ New ecosystem</button>
          </div>

          {existingEcosystems.length > 0 && (
            <div className="mb-4 pb-3 border-b border-tessera-border">
              <label className="text-xs text-gray-400 block mb-1">Load existing</label>
              <input value={ecoFilter} onChange={e => setEcoFilter(e.target.value)} placeholder="Filter..." className="w-full bg-gray-900 border border-tessera-border rounded px-2 py-1 text-xs text-white mb-1" />
              <div className="space-y-1 max-h-40 overflow-y-auto">
                {existingEcosystems.filter(eco => eco.name.toLowerCase().includes(ecoFilter.toLowerCase())).map(eco => (
                  <button key={eco.id} onClick={() => loadEcosystem(eco.id)} className="w-full text-left px-2 py-1.5 text-xs bg-gray-800 hover:bg-gray-700 rounded text-gray-300 transition">
                    {eco.name} <span className="text-gray-500">({eco.agents} agents)</span>
                  </button>
                ))}
              </div>
            </div>
          )}

          <label className="text-xs text-gray-400 block mb-1">Name</label>
          <input value={ecoName} onChange={e => setEcoName(e.target.value)} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white mb-3" />

          <label className="text-xs text-gray-400 block mb-1">Description</label>
          <input value={ecoDescription} onChange={e => setEcoDescription(e.target.value)} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white mb-3" />

          <label className="text-xs text-gray-400 block mb-1">Coordination Strategy</label>
          <select value={strategy} onChange={e => setStrategy(e.target.value as any)} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white mb-3">
            <option value="orchestrated">Orchestrated — DAG with depends_on</option>
            <option value="choreography">Choreography — Event-driven</option>
          </select>

          <label className="text-xs text-gray-400 block mb-1">Quality Features</label>
          <div className="space-y-1 mb-3">
            <label className="flex items-center gap-2 text-xs text-gray-300">
              <input type="checkbox" checked={qualityReport} onChange={e => setQualityReport(e.target.checked)} className="rounded" />
              Report agent (final summary)
            </label>
            <div className="flex items-center justify-between gap-2 text-xs text-gray-300">
              <span>Challenger (stress-test)</span>
              <select value={qualityChallenger} onChange={e => setQualityChallenger(e.target.value as any)} className="bg-gray-900 border border-tessera-border rounded px-1.5 py-0.5 text-[11px] text-white">
                <option value="off">Off</option>
                <option value="final">Final (end of run)</option>
                <option value="per_level">Per-level checkpoints</option>
              </select>
            </div>
            <label className="flex items-center gap-2 text-xs text-gray-300">
              <input type="checkbox" checked={qualityGrounding} onChange={e => setQualityGrounding(e.target.checked)} className="rounded" />
              Grounding checks (hallucination detection)
            </label>
          </div>

          <label className="text-xs text-gray-400 block mb-1">Memory Strategies</label>
          <div className="space-y-1 mb-3">
            {["semantic", "episodic", "summarization", "user_preference", "custom"].map(s => (
              <label key={s} className="flex items-center gap-2 text-xs text-gray-300">
                <input type="checkbox" checked={memoryStrategies.includes(s)} onChange={e => {
                  if (e.target.checked) setMemoryStrategies([...memoryStrategies, s]);
                  else setMemoryStrategies(memoryStrategies.filter(x => x !== s));
                }} className="rounded" />
                {s}
              </label>
            ))}
          </div>

          <label className="text-xs text-gray-400 block mb-1">Shared Namespaces</label>
          {namespaces.map((ns, i) => (
            <div key={i} className="bg-gray-900 border border-tessera-border rounded p-2 mb-2 text-xs">
              <input value={ns.name} onChange={e => { const n = [...namespaces]; n[i].name = e.target.value; setNamespaces(n); }} className="bg-transparent text-white w-full mb-1" placeholder="namespace name" />
              <div className="text-gray-500">write: {ns.write.join(", ")} | read: {ns.read.join(", ")}</div>
            </div>
          ))}
          <button onClick={() => setNamespaces([...namespaces, { name: "new_ns", write: [], read: ["all"] }])} className="text-xs text-tessera-accent hover:underline">+ Add namespace</button>

          <label className="text-xs text-gray-400 block mb-1 mt-3">Ecosystem Inputs</label>
          {ecoInputs.map((inp, i) => (
            <div key={i} className="bg-gray-900 border border-tessera-border rounded p-2 mb-2 text-xs">
              <div className="flex gap-1 mb-1">
                <input value={inp.label} onChange={e => { const n = [...ecoInputs]; n[i].label = e.target.value; n[i].id = e.target.value.toLowerCase().replace(/[^a-z0-9]+/g, "_"); setEcoInputs(n); }} className="bg-transparent text-white flex-1" placeholder="Label" />
                <select value={inp.type} onChange={e => { const n = [...ecoInputs]; n[i].type = e.target.value; setEcoInputs(n); }} className="bg-gray-800 text-white rounded px-1">
                  <option value="text">Text</option>
                  <option value="file">File</option>
                  <option value="select">Select</option>
                </select>
                <button onClick={() => setEcoInputs(ecoInputs.filter((_, j) => j !== i))} className="text-red-400 px-1">×</button>
              </div>
              {inp.type === "file" && <input value={(Array.isArray(inp.accept) ? inp.accept : []).join(", ")} onChange={e => { const n = [...ecoInputs]; n[i].accept = e.target.value.split(",").map(s => s.trim()); setEcoInputs(n); }} className="bg-transparent text-gray-400 w-full" placeholder=".pdf, .csv" />}
            </div>
          ))}
          <button onClick={() => setEcoInputs([...ecoInputs, { id: "", type: "text", label: "", required: true, accept: [] }])} className="text-xs text-tessera-accent hover:underline">+ Add input</button>
        </div>

        {/* Center: Agent List + Agent Editor */}
        <div className="col-span-6 flex flex-col gap-3 min-h-0">
          {showAgentAiFlow ? (
            <div className="flex-1 flex items-center justify-center">
              <div className="w-full max-w-lg">
                <div className="text-center mb-6">
                  <div className="text-4xl mb-3">🤖</div>
                  <h3 className="text-xl font-bold mb-1">Describe your agent</h3>
                  <p className="text-sm text-gray-500">Tell me what this agent should do and I'll configure it</p>
                </div>
                <div className="bg-tessera-card glow-border rounded-xl p-5">
                  <textarea
                    value={agentDescribePrompt}
                    onChange={e => setAgentDescribePrompt(e.target.value)}
                    placeholder="e.g. An agent that monitors CloudTrail logs for suspicious IAM changes and alerts the security team with remediation steps..."
                    rows={3}
                    className="w-full bg-gray-900 border border-tessera-border rounded-lg px-4 py-3 text-sm text-white placeholder-gray-500 resize-none mb-3"
                  />
                  <div className="flex gap-2">
                    <button onClick={addBlankAgent} className="px-4 py-2.5 text-sm text-gray-400 hover:text-white transition">Skip — blank agent</button>
                    <button onClick={() => setShowAgentAiFlow(false)} className="px-4 py-2.5 text-sm text-gray-400 hover:text-white transition">Cancel</button>
                    <button onClick={describeAgent} disabled={agentDescribing || !agentDescribePrompt.trim()} className="ml-auto px-5 py-2.5 bg-tessera-gradient rounded-lg text-sm font-semibold disabled:opacity-40 transition shadow-lg shadow-purple-500/20">
                      {agentDescribing ? "Thinking..." : "✨ Suggest Agent"}
                    </button>
                  </div>
                </div>
                {agentSuggestion && (
                  <div className="mt-4 bg-tessera-card glow-border rounded-xl p-5 border border-tessera-accent/30">
                    <div className="flex items-center justify-between mb-3">
                      <span className="text-sm font-bold text-tessera-accent">{agentSuggestion.name}</span>
                      <span className="text-xs text-gray-500">{agentSuggestion.id}</span>
                    </div>
                    {agentSuggestion.swarm && <p className="text-xs text-gray-500 mb-2">Swarm: {agentSuggestion.swarm}</p>}
                    <p className="text-xs text-gray-400 mb-3">{agentSuggestion.prompt}</p>
                    <div className="flex gap-2 flex-wrap mb-4">
                      {agentSuggestion.tools.map((t) => (
                        <span key={t} className="text-[10px] bg-gray-800 border border-gray-700 px-2 py-1 rounded-full text-gray-300">{t}</span>
                      ))}
                    </div>
                    <div className="flex gap-2">
                      <button onClick={() => setAgentSuggestion(null)} className="text-xs text-gray-400 hover:text-white">Try again</button>
                      <button onClick={acceptAgentSuggestion} className="ml-auto px-4 py-2 bg-tessera-gradient rounded-lg text-sm font-semibold shadow-lg shadow-purple-500/20">✓ Accept & Add</button>
                    </div>
                  </div>
                )}
              </div>
            </div>
          ) : (
          <>
          {/* Agent List */}
          <div className="bg-tessera-card glow-border rounded-xl p-4">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-xs font-bold text-tessera-accent uppercase tracking-wider">Agents ({agents.length})</h3>
              <button onClick={addAgent} className="text-xs bg-tessera-accent/20 text-tessera-accent px-2 py-1 rounded hover:bg-tessera-accent/30">+ Add Agent</button>
            </div>
            <div className="flex gap-2 flex-wrap">
              {agents.map(a => (
                <button key={a.id} onClick={() => setSelectedAgent(a.id)}
                  className={`px-3 py-1.5 rounded-lg text-xs transition ${selectedAgent === a.id ? "bg-tessera-accent/20 text-tessera-accent border border-tessera-accent/30" : "bg-gray-800 text-gray-400 hover:text-white"}`}>
                  {a.name || a.id}
                </button>
              ))}
            </div>
          </div>

          {/* Agent Editor */}
          {currentAgent && (
            <div className="flex-1 bg-tessera-card glow-border rounded-xl p-4 overflow-y-auto min-h-0">
              <div className="flex items-center justify-between mb-4">
                <h3 className="text-sm font-bold text-white">Configure: {currentAgent.name}</h3>
                <div className="flex gap-2">
                  <button onClick={() => cloneAgent(currentAgent.id)} className="text-xs text-tessera-accent hover:text-purple-300">Clone</button>
                  {agents.length > 1 && (
                    <button onClick={() => removeAgent(currentAgent.id)} className="text-xs text-red-400 hover:text-red-300">Remove</button>
                  )}
                </div>
              </div>

              <div className="grid grid-cols-2 gap-4">
                <div>
                  <label className="text-xs text-gray-400 block mb-1">Agent ID</label>
                  <div className="w-full bg-gray-900/50 border border-tessera-border rounded px-3 py-2 text-sm text-gray-500 font-mono">{currentAgent.id}</div>
                </div>
                <div>
                  <label className="text-xs text-gray-400 block mb-1">Display Name</label>
                  <input value={currentAgent.name} onChange={e => updateAgent(currentAgent.id, { name: e.target.value })} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white" />
                </div>
                <div>
                  <label className="text-xs text-gray-400 block mb-1">Swarm (optional)</label>
                  <input value={currentAgent.swarm} onChange={e => updateAgent(currentAgent.id, { swarm: e.target.value })} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white" placeholder="e.g. red, blue" />
                </div>
                <div>
                  <label className="text-xs text-gray-400 block mb-1">Depends on (upstream agents)</label>
                  <input value={currentAgent.depends_on.join(", ")} onChange={e => updateAgent(currentAgent.id, { depends_on: e.target.value.split(",").map(s => s.trim()).filter(Boolean) })} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white" placeholder="recon, watcher" />
                </div>
              </div>

              {strategy === "choreography" && (
              <div className="grid grid-cols-2 gap-4 mt-4">
                <div>
                  <label className="text-xs text-gray-400 block mb-1">Triggers on (events)</label>
                  <input value={currentAgent.triggers.join(", ")} onChange={e => updateAgent(currentAgent.id, { triggers: e.target.value.split(",").map(s => s.trim()).filter(Boolean) })} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white" placeholder="mission_start, anomaly_detected" />
                </div>
                <div>
                  <label className="text-xs text-gray-400 block mb-1">Emits (signals)</label>
                  <input value={currentAgent.emits.join(", ")} onChange={e => updateAgent(currentAgent.id, { emits: e.target.value.split(",").map(s => s.trim()).filter(Boolean) })} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white" placeholder="anomaly_detected, containment_complete" />
                </div>
              </div>
              )}

              <div className="mt-4">
                <label className="text-xs text-gray-400 block mb-1">Tools (comma-separated)</label>
                <input value={currentAgent.tools.join(", ")} onChange={e => updateAgent(currentAgent.id, { tools: e.target.value.split(",").map(s => s.trim()).filter(Boolean) })} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white" placeholder="probe_api, map_trust_boundaries" />
              </div>

              {/* Phase 5.1: per-tool budget overrides. Empty = use system default. */}
              {currentAgent.tools.length > 0 && (
                <div className="mt-4">
                  <label className="text-xs text-gray-400 block mb-1">
                    Tool budgets <span className="text-gray-500 font-normal">(override system defaults — leave blank to use defaults)</span>
                  </label>
                  <div className="space-y-1.5 bg-gray-900/40 border border-tessera-border rounded p-3">
                    {currentAgent.tools.map(toolName => {
                      const isSynth = currentAgent.depends_on.length > 0;
                      const role: "leaf" | "synthesizer" = isSynth ? "synthesizer" : "leaf";
                      const def = (DEFAULT_TOOL_BUDGETS[toolName]?.[role]) ?? "—";
                      const override = currentAgent.tool_budget?.[toolName];
                      const hasOverride = typeof override === "number";
                      return (
                        <div key={toolName} className="flex items-center gap-3 text-xs">
                          <code className="text-gray-300 min-w-[140px]">{toolName}</code>
                          <span className="text-gray-500 text-[10px]">default: <span className="text-gray-400">{String(def)}</span></span>
                          <input
                            type="number" min={0} max={999}
                            value={hasOverride ? String(override) : ""}
                            onChange={e => {
                              const val = e.target.value.trim();
                              const next = { ...(currentAgent.tool_budget || {}) };
                              if (val === "") {
                                delete next[toolName];
                              } else {
                                const n = parseInt(val, 10);
                                if (!Number.isNaN(n) && n >= 0) next[toolName] = n;
                              }
                              updateAgent(currentAgent.id, { tool_budget: Object.keys(next).length ? next : undefined });
                            }}
                            placeholder=""
                            className="w-16 bg-gray-900 border border-tessera-border rounded px-2 py-1 text-xs text-white font-mono"
                          />
                          {hasOverride && (
                            <span className="text-[10px] text-tessera-accent bg-purple-500/15 px-1.5 py-0.5 rounded">overridden</span>
                          )}
                        </div>
                      );
                    })}
                  </div>
                </div>
              )}

              <div className="mt-4">
                <label className="text-xs text-gray-400 block mb-1">System Prompt</label>
                <textarea value={currentAgent.prompt} onChange={e => updateAgent(currentAgent.id, { prompt: e.target.value })} rows={8} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white font-mono resize-none" placeholder="You are a security agent that..." />
              </div>
            </div>
          )}
          </>
          )}

          {/* Outputs / Deliverable panel — visible once agents exist. Lets the
              operator confirm and edit what the run produces before saving. */}
          {agents.length > 0 && (
            <div className="mt-4 bg-tessera-card border border-tessera-border rounded-xl p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold text-tessera-accent">Outputs</span>
                <span className="text-[10px] text-gray-500">what this ecosystem produces</span>
              </div>
              <div className="flex gap-2 mb-3">
                <button
                  onClick={() => setOutcomeKind("report")}
                  className={`flex-1 px-2 py-1.5 text-[11px] rounded border transition ${outcomeKind === "report" ? "bg-tessera-accent/20 text-tessera-accent border-tessera-accent/40" : "text-gray-400 border-tessera-border hover:border-gray-600"}`}
                >📊 Report only (no download)</button>
                <button
                  onClick={() => {
                    setOutcomeKind("file_artifact");
                    if (outcomeArtifacts.length === 0) {
                      const last = agents[agents.length - 1];
                      setOutcomeArtifacts([{ id: "deliverable", agent: last?.id || "", format: "html", save_as: `${(ecoName || "output").toLowerCase().replace(/[^a-z0-9]+/g, "-")}.html`, download_label: "Download" }]);
                    }
                  }}
                  className={`flex-1 px-2 py-1.5 text-[11px] rounded border transition ${outcomeKind === "file_artifact" ? "bg-tessera-accent/20 text-tessera-accent border-tessera-accent/40" : "text-gray-400 border-tessera-border hover:border-gray-600"}`}
                >📄 Downloadable file(s)</button>
              </div>
              {outcomeKind === "file_artifact" && (
                <div className="space-y-2">
                  {outcomeArtifacts.map((art, i) => {
                    const update = (patch: Partial<typeof art>) => {
                      const next = outcomeArtifacts.map((x, j) => j === i ? { ...x, ...patch } : x);
                      setOutcomeArtifacts(next);
                    };
                    return (
                      <div key={i} className="bg-gray-900 border border-tessera-border rounded p-2 space-y-1.5">
                        <div className="flex gap-1.5">
                          <select value={art.format} onChange={e => {
                            const fmt = e.target.value;
                            const base = (art.save_as || "deliverable").replace(/\.[a-z0-9]+$/i, "");
                            const ext = fmt === "markdown" ? "md" : fmt === "bundle" ? "zip" : fmt;
                            update({ format: fmt, save_as: `${base}.${ext}` });
                          }} className="bg-gray-800 border border-tessera-border rounded px-2 py-1 text-[11px] text-white">
                            {["html", "pdf", "csv", "yaml", "json", "markdown", "bundle"].map(f => <option key={f} value={f}>{f === "bundle" ? "BUNDLE (.zip)" : f.toUpperCase()}</option>)}
                          </select>
                          <select value={art.agent} onChange={e => update({ agent: e.target.value })} className="flex-1 bg-gray-800 border border-tessera-border rounded px-2 py-1 text-[11px] text-white" title="Producing agent">
                            <option value="">— producing agent —</option>
                            {agents.map(a => <option key={a.id} value={a.id}>{a.id}</option>)}
                          </select>
                          {outcomeArtifacts.length > 1 && (
                            <button onClick={() => setOutcomeArtifacts(outcomeArtifacts.filter((_, j) => j !== i))} className="text-gray-500 hover:text-red-400 text-xs px-1">✕</button>
                          )}
                        </div>
                        <input value={art.save_as} onChange={e => update({ save_as: e.target.value })} placeholder="filename.html" className="w-full bg-gray-800 border border-tessera-border rounded px-2 py-1 text-[11px] text-white font-mono" />
                        <input value={art.download_label} onChange={e => update({ download_label: e.target.value })} placeholder="Download label" className="w-full bg-gray-800 border border-tessera-border rounded px-2 py-1 text-[11px] text-white" />
                      </div>
                    );
                  })}
                  <button
                    onClick={() => setOutcomeArtifacts([...outcomeArtifacts, { id: `artifact_${outcomeArtifacts.length + 1}`, agent: agents[agents.length - 1]?.id || "", format: "html", save_as: "", download_label: "Download" }])}
                    className="text-[11px] text-tessera-accent hover:underline"
                  >+ add artifact</button>
                  {outcomeArtifacts.some(a => !a.agent || !a.save_as) && (
                    <p className="text-[10px] text-amber-400">⚠ Each artifact needs a producing agent and filename. The producing agent must have a matching output_format.</p>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* Right: YAML / Graph Preview */}
        <div className="col-span-3 bg-tessera-card glow-border rounded-xl overflow-hidden flex flex-col relative" style={{marginLeft: `-${graphExpandPx}px`, width: `calc(100% + ${graphExpandPx}px)`}}>
          {/* Drag handle */}
          <div
            className="absolute left-0 top-0 bottom-0 w-1.5 cursor-col-resize hover:bg-tessera-accent/30 z-10"
            onMouseDown={(e) => {
              const startX = e.clientX;
              const startW = graphExpandPx;
              const onMove = (ev: MouseEvent) => setGraphExpandPx(Math.max(0, startW + (startX - ev.clientX)));
              const onUp = () => { document.removeEventListener("mousemove", onMove); document.removeEventListener("mouseup", onUp); };
              document.addEventListener("mousemove", onMove);
              document.addEventListener("mouseup", onUp);
            }}
          />
          <div className="flex items-center justify-between p-3 pb-0">
            <div className="flex gap-2">
              <button onClick={() => setPreviewTab("graph")} className={`text-[10px] font-bold px-2 py-0.5 rounded ${previewTab === "graph" ? "bg-tessera-accent/20 text-tessera-accent" : "text-gray-500"}`}>Graph</button>
              <button onClick={() => setPreviewTab("yaml")} className={`text-[10px] font-bold px-2 py-0.5 rounded ${previewTab === "yaml" ? "bg-tessera-accent/20 text-tessera-accent" : "text-gray-500"}`}>YAML</button>
            </div>
            <button
              onClick={() => setShowDeployWizard(true)}
              className="text-xs bg-tessera-gradient px-3 py-1.5 rounded-lg font-semibold hover:opacity-90 transition shadow-lg shadow-purple-500/20"
            >
              💾 Save & Deploy
            </button>
          </div>
          <div className="flex-1 p-3 pt-2 overflow-hidden">
          {previewTab === "yaml" ? (
            <pre className="text-xs text-gray-300 font-mono whitespace-pre-wrap h-full overflow-y-auto">{generatedYaml}</pre>
          ) : (
            <div className="h-full rounded-lg overflow-hidden">
              <BuilderGraph key={agents.map(a => a.id + a.depends_on.join(",")).join("|")} agents={agents} onUpdateDeps={(agentId, deps) => { const updated = agents.map(a => a.id === agentId ? {...a, depends_on: deps} : a); setAgents(updated); }} />
            </div>
          )}
          </div>
        </div>
      </div>
      )}

      {showDeployWizard && (
        <DeployWizard
          yaml={generatedYaml}
          buildTranscript={convoMessages}
          agents={agents}
          ecoName={ecoName}
          ecoDescription={ecoDescription}
          ecoDomain={ecoDomain}
          ecoTags={ecoTags}
          setEcoName={setEcoName}
          setEcoDomain={setEcoDomain}
          setEcoTags={setEcoTags}
          outcomeKind={outcomeKind}
          outcomeArtifacts={outcomeArtifacts}
          setOutcomeKind={setOutcomeKind}
          setOutcomeArtifacts={setOutcomeArtifacts}
          audience={audience}
          setAudience={setAudience}
          quality={{ report: qualityReport, challenger: qualityChallenger, block_below: qualityBlockBelow, grounding: qualityGrounding, gates: qualityGates }}
          setChallenger={setQualityChallenger}
          setBlockBelow={setQualityBlockBelow}
          setGrounding={setQualityGrounding}
          setGates={setQualityGates}
          onClose={() => setShowDeployWizard(false)}
        />
      )}
    </div>
  );
}

interface BuilderAgent {
  id: string;
  name: string;
  tools: string[];
  prompt: string;
  edges: string[];
  depends_on: string[];
  triggers: string[];
  emits: string[];
  swarm: string;
  tool_budget?: Record<string, number>;
  output_kind?: string;
  output_format?: { type: string; save_as: string; validate?: boolean };
}

interface BuilderNamespace {
  name: string;
  write: string[];
  read: string[];
}

function _parseQuality(config: any): { report: boolean; challenger: "off" | "final" | "per_level"; block_below: "STRONG" | "ADEQUATE" | "WEAK"; grounding: boolean; gates: { citation: string; format: string; grounding: string; output_kind: string } } {
  const q = (config && config.quality) || {};
  const raw = q.challenger;
  let challenger: "off" | "final" | "per_level";
  if (raw === true) challenger = "final";
  else if (raw === false) challenger = "off";
  else { const s = String(raw ?? "per_level").toLowerCase(); challenger = (s === "off" || s === "final" || s === "per_level") ? (s as any) : "per_level"; }
  const bb = String((q.challenger_gate && q.challenger_gate.block_below) || "ADEQUATE").toUpperCase();
  const block_below = (["STRONG", "ADEQUATE", "WEAK"].includes(bb) ? bb : "ADEQUATE") as "STRONG" | "ADEQUATE" | "WEAK";
  const rg = q.gates || {};
  const pick = (v: any) => { const m = String(v ?? "ask").toLowerCase(); return ["ask", "warn", "halt"].includes(m) ? m : "ask"; };
  return {
    report: config?.report ?? q.report ?? true,
    challenger,
    block_below,
    grounding: q.grounding ?? true,
    gates: { citation: pick(rg.citation), format: pick(rg.format), grounding: pick(rg.grounding), output_kind: pick(rg.output_kind) },
  };
}

/**
 * Escape a value for embedding inside a YAML double-quoted scalar: `"${yamlDq(v)}"`.
 * The backslash MUST be escaped FIRST, then the double quote, then control chars —
 * escaping only `"` (the previous behaviour) left a literal `\` or `\"` in the value able
 * to break out of the quoted scalar. `\n`/`\t`/`\r` are turned into their YAML escapes so
 * a multi-line value stays a single valid scalar.
 */
function yamlDq(value: unknown): string {
  return String(value ?? "")
    .replace(/\\/g, "\\\\")
    .replace(/"/g, '\\"')
    .replace(/\n/g, "\\n")
    .replace(/\r/g, "\\r")
    .replace(/\t/g, "\\t");
}

function generateYaml(name: string, description: string, strategy: string, memoryStrategies: string[], agents: BuilderAgent[], namespaces: BuilderNamespace[], quality?: {report: boolean; challenger: "off" | "final" | "per_level" | boolean; grounding: boolean; block_below?: string; gates?: {citation: string; format: string; grounding: string; output_kind: string}}, inputs?: {id: string; type: string; label: string; required: boolean; accept: string[]}[], outcome?: { kind: string; audience?: string; artifacts: { id: string; agent: string; format: string; save_as: string; download_label: string }[] }, domain?: string, tags?: string[], quorum?: any): string {
  // Quorum archetype: a scrutiny/judgment PANEL. Emit the `quorum:` policy block
  // (empanelment / deliberation / verdict) instead of an agent DAG, and return
  // early — none of the DAG fields (entry_point, agents, shared_namespaces)
  // apply. Isolated: only reached when the builder converged to strategy: quorum.
  if (strategy === "quorum") {
    const q = quorum || {};
    const emp = q.empanelment || {};
    const sel = emp.select || {};
    const del = q.deliberation || {};
    const ver = q.verdict || {};
    const slug = (name || "defensibility-panel").toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-+|-+$/g, "").slice(0, 60) || "defensibility-panel";
    const list = (v: any, d: string[]) => (Array.isArray(v) && v.length ? v : d).map((x: any) => `"${yamlDq(x)}"`).join(", ");
    const ql: string[] = [`ecosystem:`, `  name: "${name}"`, `  description: "${description}"`, `  version: "1.0"`];
    if (inputs && inputs.length > 0) {
      ql.push(`  inputs:`);
      inputs.forEach(inp => {
        ql.push(`    - id: ${inp.id}`);
        ql.push(`      type: ${inp.type}`);
        ql.push(`      label: "${yamlDq(inp.label || "")}"`);
        ql.push(`      required: ${inp.required}`);
      });
    }
    ql.push(`  outcome:`);
    ql.push(`    kind: report`);
    ql.push(``);
    ql.push(`name: ${slug}`);
    ql.push(`strategy: quorum`);
    const tg = (tags || []).map(t => String(t).trim().toLowerCase()).filter(Boolean).slice(0, 6);
    if (tg.length) ql.push(`tags: [${tg.map(t => `"${yamlDq(t)}"`).join(", ")}]`);
    ql.push(``);
    ql.push(`quorum:`);
    ql.push(`  empanelment:`);
    ql.push(`    persona_source: "${emp.persona_source || "/catalog/scrutineer/"}"`);
    ql.push(`    select:`);
    ql.push(`      min: ${Number(sel.min ?? 3)}`);
    ql.push(`      max: ${Number(sel.max ?? 6)}`);
    ql.push(`      must_include: [${list(sel.must_include, [])}]`);
    ql.push(`      diversity: [${list(sel.diversity, [])}]`);
    ql.push(`    completeness_adversary: ${emp.completeness_adversary !== false}`);
    ql.push(`    justify_exclusions: ${emp.justify_exclusions !== false}`);
    ql.push(`  deliberation:`);
    ql.push(`    max_rounds: ${Number(del.max_rounds ?? 2)}`);
    ql.push(`    turn_policy: ${del.turn_policy || "moderated"}`);
    ql.push(`    escalation: ${del.escalation !== false}`);
    ql.push(`    preserve_dissent: ${del.preserve_dissent !== false}`);
    ql.push(`  verdict:`);
    ql.push(`    scale: [${list(ver.scale, ["defensible", "defensible_with_conditions", "indefensible"])}]`);
    ql.push(`    must_state: [${list(ver.must_state, ["against_whom_it_fails", "indefensible_elements", "remediation"])}]`);
    ql.push(``);
    ql.push(`quality:`);
    ql.push(`  gate:`);
    ql.push(`    block_below: ${(q._gate && q._gate.block_below) || "defensible_with_conditions"}`);
    ql.push(``);
    return ql.join("\n");
  }
  // Detect swarms from agent swarm fields
  const swarmMap: Record<string, string[]> = {};
  agents.forEach(a => {
    if (a.swarm) {
      if (!swarmMap[a.swarm]) swarmMap[a.swarm] = [];
      swarmMap[a.swarm].push(a.id);
    }
  });
  const swarmNames = Object.keys(swarmMap);

  const lines = [
    `ecosystem:`,
    `  name: "${name}"`,
    `  description: "${description}"`,
    `  version: "1.0"`,
  ];
  if (inputs && inputs.length > 0) {
    lines.push(`  inputs:`);
    inputs.forEach(inp => {
      lines.push(`    - id: ${inp.id}`);
      lines.push(`      type: ${inp.type}`);
      lines.push(`      label: "${inp.label}"`);
      lines.push(`      required: ${inp.required}`);
      if (inp.type === "file" && inp.accept && Array.isArray(inp.accept) && inp.accept.length) lines.push(`      accept: [${inp.accept.map(a => `"${a}"`).join(", ")}]`);
    });
  }
  lines.push(``);
  lines.push(`strategy: ${strategy}`);
  lines.push(`report: ${quality?.report ?? true}`);
  // Discovery metadata (top-level) — the ecosystems page groups by `domain`
  // and searches `tags`. Author-owned config; persisted as-is by save-ecosystem.
  const domClean = (domain || "").trim();
  if (domClean) { lines.push(`domains:`); lines.push(`  - ${domClean}`); }
  const tagsClean = (tags || []).map(t => String(t).trim().toLowerCase()).filter(Boolean).slice(0, 6);
  if (tagsClean.length) lines.push(`tags: [${tagsClean.map(t => `"${yamlDq(t)}"`).join(", ")}]`);
  lines.push(``);
  lines.push(`quality:`);
  // challenger: off|final|per_level (legacy boolean tolerated). Emit the YAML
  // the compiler reads: off -> false, final -> true, per_level -> per_level.
  const chRaw = quality?.challenger ?? "per_level";
  const chMode = chRaw === true ? "final" : chRaw === false ? "off" : String(chRaw);
  if (chMode === "off") {
    lines.push(`  challenger: false`);
  } else if (chMode === "per_level") {
    lines.push(`  challenger: per_level`);
    lines.push(`  challenger_gate:`);
    lines.push(`    block_below: ${quality?.block_below || "ADEQUATE"}`);
  } else {
    lines.push(`  challenger: true`);
  }
  lines.push(`  grounding: ${quality?.grounding ?? true}`);
  const g = quality?.gates;
  if (g) {
    lines.push(`  gates:`);
    lines.push(`    citation: ${g.citation || "ask"}`);
    lines.push(`    format: ${g.format || "ask"}`);
    lines.push(`    grounding: ${g.grounding || "ask"}`);
    lines.push(`    output_kind: ${g.output_kind || "ask"}`);
  }
  lines.push(``);
  // Outcome / deliverable declaration. file_artifact => downloadable file(s);
  // anything else => report-only (no download).
  if (outcome && outcome.kind === "file_artifact" && (outcome.artifacts || []).length) {
    lines.push(`outcome:`);
    lines.push(`  kind: file_artifact`);
    if (outcome.audience) lines.push(`  audience: "${yamlDq(outcome.audience)}"`);
    lines.push(`  artifacts:`);
    outcome.artifacts.forEach(art => {
      lines.push(`    - id: ${art.id}`);
      lines.push(`      agent: ${art.agent}`);
      lines.push(`      format: ${art.format}`);
      if (art.save_as) lines.push(`      save_as: ${art.save_as}`);
      lines.push(`      download_label: "${yamlDq(art.download_label || "Download")}"`);
    });
  } else {
    lines.push(`outcome:`);
    lines.push(`  kind: report`);
  }
  lines.push(``);
  lines.push(`entry_point: ${agents[0]?.id || "agent_1"}`);
  lines.push(``);
  lines.push(`memory:`);
  lines.push(`  strategies: [${memoryStrategies.join(", ")}]`);
  lines.push(`  shared_namespaces:`);
  namespaces.forEach(ns => {
    lines.push(`    - name: "${ns.name}"`);
    lines.push(`      write: [${ns.write.join(", ")}]`);
    lines.push(`      read: [${ns.read.join(", ")}]`);
  });
  lines.push(``);
  lines.push(`agents:`);
  // Map producing-agent -> artifact so the drafter's output_format always
  // matches the outcome artifact (compiler hard-fails on mismatch).
  const producerArtifact: Record<string, { format: string; save_as: string }> = {};
  if (outcome && outcome.kind === "file_artifact") {
    (outcome.artifacts || []).forEach(art => {
      if (art.agent) producerArtifact[art.agent] = { format: art.format, save_as: art.save_as };
    });
  }
  agents.forEach(a => {
    lines.push(`  - id: ${a.id}`);
    lines.push(`    name: "${a.name}"`);
    lines.push(`    registry_id: "tessera/${a.id}"`);
    if (a.swarm) lines.push(`    swarm: ${a.swarm}`);
    if (a.depends_on.length) lines.push(`    depends_on: [${a.depends_on.join(", ")}]`);
    if (a.triggers.length) lines.push(`    triggers: [${a.triggers.join(", ")}]`);
    if (a.emits.length) lines.push(`    emits: [${a.emits.join(", ")}]`);
    if (a.tools.length) lines.push(`    tools: [${a.tools.join(", ")}]`);
    if (a.tool_budget && Object.keys(a.tool_budget).length) {
      lines.push(`    tool_budget:`);
      Object.entries(a.tool_budget).forEach(([k, v]) => {
        lines.push(`      ${k}: ${v}`);
      });
    }
    // Output declaration: if this agent produces a declared file_artifact, the
    // artifact is authoritative (keeps producer.output_format == outcome.format,
    // which the compiler hard-checks). Otherwise emit the agent's own fields.
    const prod = producerArtifact[a.id];
    if (prod && prod.format) {
      lines.push(`    output_kind: ${a.output_kind || "drafter"}`);
      lines.push(`    output_format:`);
      lines.push(`      type: ${prod.format}`);
      if (prod.save_as) lines.push(`      save_as: ${prod.save_as}`);
      lines.push(`      validate: true`);
    } else {
      if (a.output_kind) lines.push(`    output_kind: ${a.output_kind}`);
      if (a.output_format && a.output_format.type) {
        lines.push(`    output_format:`);
        lines.push(`      type: ${a.output_format.type}`);
        if (a.output_format.save_as) lines.push(`      save_as: ${a.output_format.save_as}`);
        lines.push(`      validate: ${a.output_format.validate ?? true}`);
      }
    }
    if (a.prompt) lines.push(`    prompt: "${yamlDq(a.prompt)}"`);
  });

  // Generate swarms section if agents have swarm assignments
  if (swarmNames.length > 0) {
    lines.push(``);
    lines.push(`swarms:`);
    swarmNames.forEach(s => {
      lines.push(`  ${s}:`);
      lines.push(`    agents: [${swarmMap[s].join(", ")}]`);
      lines.push(`    strategy: graph`);
    });
    lines.push(``);
    lines.push(`parallel_swarms: [${swarmNames.join(", ")}]`);
  }

  lines.push(``);
  lines.push(`triggers:`);
  lines.push(`  - type: manual`);
  return lines.join("\n");
}

function ToolCatalogView() {
  const [tools, setTools] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [showModal, setShowModal] = useState(false);
  const [editing, setEditing] = useState<string | null>(null);
  const [testResult, setTestResult] = useState<{status: string; body: string} | null>(null);
  const [testing, setTesting] = useState(false);
  const [form, setForm] = useState({ name: "", description: "", type: "LAMBDA", lambdaArn: "", mcpServerUrl: "", openApiSpec: "", credType: "NONE", credName: "", credApiKey: "", credHeader: "X-API-Key" });
  const [saving, setSaving] = useState(false);

  const load = () => {
    setLoading(true);
    apiFetch(`${API_URL}/api/catalog`)
      .then(r => r.json())
      .then(data => { setTools(Array.isArray(data) ? data : []); setLoading(false); })
      .catch(() => { setTools([]); setLoading(false); });
  };
  useEffect(() => { load(); }, []);

  const openAdd = () => {
    setEditing(null);
    setTestResult(null);
    setForm({ name: "", description: "", type: "LAMBDA", lambdaArn: "", mcpServerUrl: "", openApiSpec: "", credType: "NONE", credName: "", credApiKey: "", credHeader: "X-API-Key" });
    setShowModal(true);
  };

  const openEdit = async (tool: any) => {
    setEditing(tool.name);
    setTestResult(null);
    setForm({ name: tool.name, description: tool.description || "", type: "LAMBDA", lambdaArn: "", mcpServerUrl: "", openApiSpec: "", credType: "NONE", credName: "", credApiKey: "", credHeader: "X-API-Key" });
    setShowModal(true);
    // Fetch full details
    try {
      const res = await apiFetch(`${API_URL}/api/catalog/${tool.name}`);
      const data = await res.json();
      const tc = data?.targetConfiguration?.mcp || {};
      let type = "LAMBDA";
      let lambdaArn = "";
      let openApiSpec = "";
      let mcpServerUrl = "";
      if (tc.openApiSchema) { type = "OPEN_API"; openApiSpec = tc.openApiSchema.inlinePayload || ""; }
      else if (tc.mcpServer) { type = "MCP_SERVER"; mcpServerUrl = tc.mcpServer.endpoint || ""; }
      else if (tc.lambda) { type = "LAMBDA"; lambdaArn = tc.lambda.lambdaArn || ""; }
      const creds = data?.credentialProviderConfigurations?.[0] || {};
      let credType = "NONE";
      let credName = "";
      let credHeader = "X-API-Key";
      if (creds.credentialProviderType === "API_KEY") {
        credType = "API_KEY";
        credName = (creds.credentialProvider?.apiKeyCredentialProvider?.providerArn || "").split("/").pop() || "";
        credHeader = creds.credentialProvider?.apiKeyCredentialProvider?.credentialParameterName || "X-API-Key";
      }
      setForm(f => ({ ...f, type, lambdaArn, openApiSpec, mcpServerUrl, credType, credName, credHeader }));
    } catch {}
  };

  const handleTest = async () => {
    setTesting(true);
    setTestResult(null);
    try {
      if (editing && form.credType === "API_KEY" && !form.credApiKey) {
        setTestResult({ status: "skip", body: "API key already configured — paste new key to re-test." });
        setTesting(false);
        return;
      }
      let testUrl = "";
      let testHeaders: Record<string, string> = {};
      if (form.type === "OPEN_API") {
        const spec = JSON.parse(form.openApiSpec);
        const baseUrl = spec.servers?.[0]?.url || "";
        const firstPath = Object.keys(spec.paths || {})[0] || "";
        testUrl = baseUrl + firstPath + "?symbol=AAPL";
        if (form.credType === "API_KEY" && form.credApiKey) {
          testHeaders[form.credHeader] = form.credApiKey;
        }
      } else if (form.type === "MCP_SERVER") {
        testUrl = form.mcpServerUrl;
      }
      if (testUrl) {
        const res = await apiFetch(`${API_URL}/api/catalog/test`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ url: testUrl, headers: testHeaders }),
        });
        const data = await res.json();
        setTestResult({ status: data.status || "unknown", body: data.body || JSON.stringify(data) });
      } else {
        setTestResult({ status: "skip", body: "No testable endpoint for this tool type." });
      }
    } catch (e: any) {
      setTestResult({ status: "error", body: e.message });
    }
    setTesting(false);
  };

  const [testModal, setTestModal] = useState<{name: string; status: "loading" | "done"; result?: any} | null>(null);

  const handleTestTool = async (tool: any) => {
    setTestModal({ name: tool.name, status: "loading" });
    try {
      const res = await apiFetch(`${API_URL}/api/catalog/${tool.name}`);
      const data = await res.json();
      const status = data.status || "UNKNOWN";
      const creds = data.credentialProviderConfigurations || [];
      const hasCred = creds.length > 0 && creds[0].credentialProviderType !== "GATEWAY_IAM_ROLE";
      setTestModal({ name: tool.name, status: "done", result: { toolStatus: status, hasCred, credType: creds[0]?.credentialProviderType || "NONE" } });
    } catch (e: any) {
      setTestModal({ name: tool.name, status: "done", result: { toolStatus: "ERROR", error: e.message } });
    }
  };

  const handleSave = async () => {
    setSaving(true);
    const body: any = { name: form.name, description: form.description, type: form.type, config: {} };
    if (form.type === "LAMBDA") body.config.lambdaArn = form.lambdaArn;
    else if (form.type === "MCP_SERVER") body.config.mcpServerUrl = form.mcpServerUrl;
    else if (form.type === "OPEN_API") body.config.openApiSpec = form.openApiSpec;
    if (form.credType === "API_KEY" && form.credApiKey) {
      body.credential = { type: "API_KEY", name: form.credName || form.name, apiKey: form.credApiKey, headerName: form.credHeader };
    }
    const method = editing ? "PUT" : "POST";
    const url = editing ? `${API_URL}/api/catalog/${editing}` : `${API_URL}/api/catalog`;
    const res = await fetch(url, { method, headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
    const data = await res.json();
    setSaving(false);
    if (data.error) {
      alert(`Error: ${data.error}`);
      return;
    }
    setShowModal(false);
    load();
  };

  const handleDelete = async (name: string) => {
    if (!confirm(`Delete tool "${name}"?`)) return;
    await apiFetch(`${API_URL}/api/catalog/${name}`, { method: "DELETE" });
    load();
  };

  return (
    <div className="p-6 max-w-4xl">
      <div className="flex items-center justify-between mb-4">
        <div>
          <h2 className="text-2xl font-bold">Tool Catalog</h2>
          <p className="text-sm text-gray-400">Register and manage custom tools in the Gateway</p>
        </div>
        <button onClick={openAdd} className="px-4 py-2 bg-tessera-accent/20 text-tessera-accent rounded-lg text-sm font-medium hover:bg-tessera-accent/30">+ Add Tool</button>
      </div>

      {/* System tools */}
      <div className="mb-6">
        <h3 className="text-xs uppercase tracking-wider text-gray-500 mb-2">System Tools (always available)</h3>
        <div className="flex flex-wrap gap-2">
          {["web_search", "generateUI", "memory_read", "memory_write", "calculator", "current_time", "http_request", "code_interpreter"].map(t => (
            <span key={t} className="px-2 py-1 bg-gray-700/50 text-gray-300 rounded text-xs">{t}</span>
          ))}
        </div>
      </div>

      {/* Registered custom tools */}
      <h3 className="text-xs uppercase tracking-wider text-gray-500 mb-2">Registered &amp; Available</h3>
      {loading ? (
        <p className="text-gray-500 text-sm">Loading...</p>
      ) : tools.filter(t => t.registered).length === 0 ? (
        <div className="border border-dashed border-gray-600 rounded-xl p-4 text-center text-gray-500 text-sm mb-6">No custom tools registered yet.</div>
      ) : (
        <div className="overflow-x-auto mb-6 rounded-lg border border-tessera-border">
          <table className="w-full text-sm">
            <thead><tr className="bg-gray-800/50 text-xs text-gray-400 uppercase">
              <th className="text-left px-3 py-2">Name</th>
              <th className="text-left px-3 py-2">Description</th>
              <th className="text-left px-3 py-2">Used by</th>
              <th className="text-left px-3 py-2">Status</th>
              <th className="text-right px-3 py-2">Actions</th>
            </tr></thead>
            <tbody>
              {tools.filter(t => t.registered).map(t => (
                <tr key={t.name} className="border-t border-gray-800 hover:bg-gray-800/30">
                  <td className="px-3 py-2 font-mono text-white whitespace-nowrap">{t.name}</td>
                  <td className="px-3 py-2 text-gray-400 text-xs">{t.description}</td>
                  <td className="px-3 py-2 text-gray-500 text-xs">{t.usedBy ? t.usedBy.join(", ") : "—"}</td>
                  <td className="px-3 py-2"><span className="text-xs px-2 py-0.5 rounded bg-green-900/30 text-green-400">{t.status}</span></td>
                  <td className="px-3 py-2 text-right whitespace-nowrap">
                    <button onClick={() => handleTestTool(t)} className="text-xs text-yellow-400 hover:text-yellow-300 mr-2">Test</button>
                    <button onClick={() => openEdit(t)} className="text-xs text-tessera-accent hover:text-tessera-accent/80 mr-2">Edit</button>
                    <button onClick={() => handleDelete(t.name)} className="text-xs text-red-400 hover:text-red-300">Delete</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      {/* Unregistered tools */}
      {tools.filter(t => !t.registered).length > 0 && (
        <>
          <h3 className="text-xs uppercase tracking-wider text-gray-500 mb-1">Requested but Unregistered</h3>
          <p className="text-xs text-gray-600 mb-2">Declared in ecosystem YAMLs — agents improvise with system tools until registered.</p>
          <div className="overflow-auto max-h-[400px] rounded-lg border border-dashed border-gray-700">
            <table className="w-full text-sm">
              <thead className="sticky top-0 bg-gray-900"><tr className="text-xs text-gray-500 uppercase">
                <th className="text-left px-3 py-2">Name</th>
                <th className="text-left px-3 py-2">Expected purpose</th>
                <th className="text-left px-3 py-2">Fallback</th>
                <th className="text-left px-3 py-2">Used by</th>
                <th className="text-right px-3 py-2"></th>
              </tr></thead>
              <tbody>
                {tools.filter(t => !t.registered).map(t => (
                  <tr key={t.name} className="border-t border-gray-800/50 hover:bg-gray-800/20">
                    <td className="px-3 py-1.5 font-mono text-gray-300 whitespace-nowrap text-xs">{t.name}</td>
                    <td className="px-3 py-1.5 text-gray-500 text-xs">{t.description || "—"}</td>
                    <td className="px-3 py-1.5 text-yellow-600 text-xs">{t.fallback || "Agent improvises"}</td>
                    <td className="px-3 py-1.5 text-gray-600 text-xs">{t.usedBy ? t.usedBy.join(", ") : "—"}</td>
                    <td className="px-3 py-1.5 text-right">
                      <button onClick={() => { setEditing(null); setTestResult(null); setForm({...form, name: t.name, description: t.description || ""}); setShowModal(true); }} className="text-xs px-2 py-0.5 bg-tessera-accent/10 text-tessera-accent rounded hover:bg-tessera-accent/20">Register</button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}

      {/* Add/Edit Modal */}
      {showModal && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setShowModal(false)}>
          <div className="bg-tessera-card border border-tessera-border rounded-xl p-6 w-[520px] max-h-[85vh] overflow-y-auto" onClick={e => e.stopPropagation()}>
            <h3 className="text-lg font-bold mb-4">{editing ? "Edit Tool" : "Register New Tool"}</h3>
            <div className="space-y-3">
              <div>
                <label className="text-xs text-gray-400">Name</label>
                <input value={form.name} onChange={e => setForm({...form, name: e.target.value})} disabled={!!editing} className="w-full mt-1 px-3 py-2 bg-gray-800 border border-gray-600 rounded text-sm disabled:opacity-50" placeholder="e.g. var_calculator" />
              </div>
              <div>
                <label className="text-xs text-gray-400">Description</label>
                <input value={form.description} onChange={e => setForm({...form, description: e.target.value})} className="w-full mt-1 px-3 py-2 bg-gray-800 border border-gray-600 rounded text-sm" placeholder="What this tool does" />
              </div>
              {!editing && (
                <>
                  <div>
                    <label className="text-xs text-gray-400">Target Type</label>
                    <select value={form.type} onChange={e => setForm({...form, type: e.target.value})} className="w-full mt-1 px-3 py-2 bg-gray-800 border border-gray-600 rounded text-sm">
                      <option value="LAMBDA">Lambda ARN</option>
                      <option value="OPEN_API">OpenAPI Spec</option>
                      <option value="MCP_SERVER">Remote MCP Server</option>
                    </select>
                  </div>
                  {form.type === "LAMBDA" && (
                    <div>
                      <label className="text-xs text-gray-400">Lambda ARN</label>
                      <input value={form.lambdaArn} onChange={e => setForm({...form, lambdaArn: e.target.value})} className="w-full mt-1 px-3 py-2 bg-gray-800 border border-gray-600 rounded text-sm font-mono" placeholder="arn:aws:lambda:us-east-1:..." />
                    </div>
                  )}
                  {form.type === "OPEN_API" && (
                    <div>
                      <label className="text-xs text-gray-400">OpenAPI Spec (JSON or YAML)</label>
                      <textarea value={form.openApiSpec} onChange={e => setForm({...form, openApiSpec: e.target.value})} rows={8} className="w-full mt-1 px-3 py-2 bg-gray-800 border border-gray-600 rounded text-sm font-mono" placeholder='{"openapi":"3.0.0","info":{"title":"..."},"servers":[{"url":"https://..."}],"paths":{...}}' />
                    </div>
                  )}
                  {form.type === "MCP_SERVER" && (
                    <div>
                      <label className="text-xs text-gray-400">MCP Server URL</label>
                      <input value={form.mcpServerUrl} onChange={e => setForm({...form, mcpServerUrl: e.target.value})} className="w-full mt-1 px-3 py-2 bg-gray-800 border border-gray-600 rounded text-sm" placeholder="https://..." />
                    </div>
                  )}
                </>
              )}

              {/* Credentials section */}
              <div className="border-t border-gray-700 pt-3 mt-3">
                <label className="text-xs text-gray-400">Authentication</label>
                <select value={form.credType} onChange={e => setForm({...form, credType: e.target.value})} className="w-full mt-1 px-3 py-2 bg-gray-800 border border-gray-600 rounded text-sm">
                  <option value="NONE">None (Gateway IAM Role)</option>
                  <option value="API_KEY">API Key</option>
                </select>
              </div>
              {form.credType === "API_KEY" && (
                <div className="space-y-2 pl-2 border-l-2 border-tessera-accent/30">
                  <div>
                    <label className="text-xs text-gray-400">Provider Name</label>
                    <input value={form.credName} onChange={e => setForm({...form, credName: e.target.value})} className="w-full mt-1 px-3 py-2 bg-gray-800 border border-gray-600 rounded text-sm" placeholder="e.g. finnhub-key" />
                  </div>
                  <div>
                    <label className="text-xs text-gray-400">API Key <span className="text-yellow-500">(stored securely, never shown again)</span></label>
                    <input type="password" value={form.credApiKey} onChange={e => setForm({...form, credApiKey: e.target.value})} className="w-full mt-1 px-3 py-2 bg-gray-800 border border-gray-600 rounded text-sm" placeholder="sk-..." />
                  </div>
                  <div>
                    <label className="text-xs text-gray-400">Header Name</label>
                    <input value={form.credHeader} onChange={e => setForm({...form, credHeader: e.target.value})} className="w-full mt-1 px-3 py-2 bg-gray-800 border border-gray-600 rounded text-sm" placeholder="X-API-Key" />
                  </div>
                </div>
              )}
            </div>
            <div className="flex justify-between mt-6">
              <button onClick={handleTest} disabled={testing} className="px-4 py-2 text-sm border border-yellow-600 text-yellow-400 rounded hover:bg-yellow-900/20 disabled:opacity-50">{testing ? "Testing..." : "🧪 Test Connection"}</button>
              <div className="flex gap-2">
                <button onClick={() => setShowModal(false)} className="px-4 py-2 text-sm text-gray-400 hover:text-white">Cancel</button>
                <button onClick={handleSave} disabled={saving || !form.name} className="px-4 py-2 bg-tessera-accent text-black rounded text-sm font-medium disabled:opacity-50">{saving ? "Saving..." : editing ? "Update" : "Save Tool"}</button>
              </div>
            </div>

            {/* Test result */}
            {testResult && (
              <div className="mt-3 p-3 bg-gray-900 rounded-lg border border-gray-700">
                <div className="flex items-center gap-2 mb-1">
                  <span className={`text-sm font-mono ${testResult.status === "200" || testResult.status === "skip" ? "text-green-400" : "text-red-400"}`}>
                    {testResult.status === "200" ? "✅ 200 OK" : testResult.status === "skip" ? "⏭ Skipped" : `❌ ${testResult.status}`}
                  </span>
                </div>
                <pre className="text-xs text-gray-400 overflow-auto max-h-32 whitespace-pre-wrap">{testResult.body.substring(0, 500)}</pre>
              </div>
            )}
          </div>
        </div>
      )}

      {/* Test Tool Dialog */}
      {testModal && (
        <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={() => setTestModal(null)}>
          <div className="bg-tessera-card border border-tessera-border rounded-xl p-6 w-[400px]" onClick={e => e.stopPropagation()}>
            <h3 className="text-lg font-bold mb-4">Test: {testModal.name}</h3>
            {testModal.status === "loading" ? (
              <div className="flex items-center gap-3 py-4">
                <div className="animate-spin h-5 w-5 border-2 border-tessera-accent border-t-transparent rounded-full"></div>
                <span className="text-sm text-gray-300">Checking tool status...</span>
              </div>
            ) : testModal.result ? (
              <div className="space-y-3">
                <div className="flex items-center gap-2">
                  <span className="text-2xl">{testModal.result.toolStatus === "READY" ? "✅" : "❌"}</span>
                  <span className="text-lg font-medium">{testModal.result.toolStatus === "READY" ? "Ready" : testModal.result.toolStatus}</span>
                </div>
                <div className="text-sm text-gray-400">
                  <p>Auth: {testModal.result.hasCred ? `✅ ${testModal.result.credType} configured` : "ℹ️ Gateway IAM Role"}</p>
                  <p className="mt-2 text-xs">The Gateway injects credentials automatically when agents call this tool.</p>
                </div>
                {testModal.result.error && <p className="text-sm text-red-400">{testModal.result.error}</p>}
              </div>
            ) : null}
            <div className="flex justify-end mt-4">
              <button onClick={() => setTestModal(null)} className="px-4 py-2 text-sm text-gray-400 hover:text-white">Close</button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
}

function AdminView() {
  const { authFetch, isAdmin } = useAuth();
  const [users, setUsers] = useState<any[]>([]);
  const [loading, setLoading] = useState(true);
  const [tab, setTab] = useState<"users" | "groups" | "sharing">("users");

  const loadUsers = useCallback(() => {
    setLoading(true);
    authFetch(`${API_URL}/api/admin/users`)
      .then(r => r.json())
      .then(data => { if (Array.isArray(data)) setUsers(data); })
      .catch(() => {})
      .finally(() => setLoading(false));
  }, [authFetch]);

  useEffect(() => { loadUsers(); }, [loadUsers]);

  const approve = async (sub: string) => {
    await authFetch(`${API_URL}/api/admin/users/${sub}/approve`, { method: "POST" });
    loadUsers();
  };

  const reject = async (sub: string) => {
    if (!confirm("Permanently delete this user?")) return;
    await authFetch(`${API_URL}/api/admin/users/${sub}/reject`, { method: "POST" });
    loadUsers();
  };

  if (!isAdmin) return <p className="text-gray-500">Admin access required.</p>;

  return (
    <div className="max-w-3xl">
      <h2 className="text-2xl font-bold mb-4">Administration</h2>
      <div className="flex gap-2 mb-4">
        <button onClick={() => setTab("users")} className={`px-4 py-2 text-sm rounded-lg ${tab === "users" ? "bg-tessera-accent/20 text-tessera-accent border border-tessera-accent/30" : "text-gray-400 hover:text-white"}`}>Users</button>
        <button onClick={() => setTab("groups")} className={`px-4 py-2 text-sm rounded-lg ${tab === "groups" ? "bg-tessera-accent/20 text-tessera-accent border border-tessera-accent/30" : "text-gray-400 hover:text-white"}`}>Groups</button>
        <button onClick={() => setTab("sharing")} className={`px-4 py-2 text-sm rounded-lg ${tab === "sharing" ? "bg-tessera-accent/20 text-tessera-accent border border-tessera-accent/30" : "text-gray-400 hover:text-white"}`}>Sharing</button>
      </div>

      {tab === "users" && (
        <>
          <p className="text-sm text-gray-400 mb-4">Users pending approval (disabled after sign-up)</p>
          {loading ? (
            <p className="text-gray-500 text-sm">Loading...</p>
          ) : users.length === 0 ? (
            <div className="border border-dashed border-gray-600 rounded-xl p-6 text-center text-gray-500 text-sm">
              No pending users.
            </div>
          ) : (
            <div className="space-y-2">
              {users.map(u => (
                <div key={u.sub} className="flex items-center justify-between bg-tessera-card border border-tessera-border rounded-lg p-3">
                  <div>
                    <div className="text-sm text-white">{u.email}</div>
                    <div className="text-xs text-gray-500">Signed up: {u.created}</div>
                  </div>
                  <div className="flex gap-2">
                    <button onClick={() => approve(u.sub)} className="px-3 py-1 text-xs bg-green-500/20 text-green-300 rounded hover:bg-green-500/30">Approve</button>
                    <button onClick={() => reject(u.sub)} className="px-3 py-1 text-xs bg-red-500/20 text-red-300 rounded hover:bg-red-500/30">Reject</button>
                  </div>
                </div>
              ))}
            </div>
          )}
        </>
      )}

      {tab === "groups" && <GroupsPanel />}
      {tab === "sharing" && <AdminSharingPanel />}
    </div>
  );
}

function AdminSharingPanel() {
  const { authFetch } = useAuth();
  const [principalType, setPrincipalType] = useState<"user" | "group">("user");
  const [principalId, setPrincipalId] = useState("");
  const [users, setUsers] = useState<{ sub: string; email: string }[]>([]);
  const [groups, setGroups] = useState<{ name: string }[]>([]);
  const [shares, setShares] = useState<{ ecosystemId: string; permission: string; policyId: string }[]>([]);
  const [loading, setLoading] = useState(false);
  const [ecosystems, setEcosystems] = useState<{ id: string; name: string }[]>([]);
  const [addEcoId, setAddEcoId] = useState("");
  const [addPerm, setAddPerm] = useState<"run" | "edit" | "full">("run");
  const [adding, setAdding] = useState(false);

  useEffect(() => {
    apiFetch(`${API_URL}/api/admin/users`).then(r => r.json()).then(d => { if (Array.isArray(d)) setUsers(d); }).catch(() => {});
    apiFetch(`${API_URL}/api/admin/groups`).then(r => r.json()).then(d => { if (Array.isArray(d)) setGroups(d); }).catch(() => {});
    apiFetch(`${API_URL}/api/ecosystems`).then(r => r.json()).then(d => { if (Array.isArray(d)) setEcosystems(d.map((e: any) => ({ id: e.id, name: e.name }))); }).catch(() => {});
  }, []);

  const loadShares = async (pType: string, pId: string) => {
    if (!pId) return;
    setLoading(true);
    try {
      const res = await apiFetch(`${API_URL}/api/admin/sharing?principal_type=${pType}&principal_id=${encodeURIComponent(pId)}`);
      const data = await res.json();
      if (Array.isArray(data)) setShares(data);
      else setShares([]);
    } catch { setShares([]); }
    setLoading(false);
  };

  const handleSelect = (id: string) => {
    setPrincipalId(id);
    if (id) loadShares(principalType, id);
    else setShares([]);
  };

  const handleRemove = async (policyId: string) => {
    // Find the ecosystemId for this policy to call the right endpoint
    await apiFetch(`${API_URL}/api/admin/sharing/${policyId}`, { method: "DELETE" });
    loadShares(principalType, principalId);
  };

  const handleAdd = async () => {
    if (!addEcoId || !principalId) return;
    setAdding(true);
    await apiFetch(`${API_URL}/api/ecosystems/${addEcoId}/sharing`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ principal_type: principalType, principal_id: principalId, permission: addPerm }),
    });
    setAdding(false);
    setAddEcoId("");
    loadShares(principalType, principalId);
  };

  const PERM_COLORS: Record<string, string> = { run: "bg-green-500/20 text-green-300", edit: "bg-blue-500/20 text-blue-300", full: "bg-purple-500/20 text-purple-300" };

  return (
    <div>
      <p className="text-sm text-gray-400 mb-4">View and manage sharing policies per user or group</p>
      <div className="flex gap-3 mb-3 items-center">
        <label className="flex items-center gap-1.5 text-xs text-gray-300">
          <input type="radio" checked={principalType === "user"} onChange={() => { setPrincipalType("user"); setPrincipalId(""); setShares([]); }} /> User
        </label>
        <label className="flex items-center gap-1.5 text-xs text-gray-300">
          <input type="radio" checked={principalType === "group"} onChange={() => { setPrincipalType("group"); setPrincipalId(""); setShares([]); }} /> Group
        </label>
      </div>
      {principalType === "user" ? (
        <select value={principalId} onChange={e => handleSelect(e.target.value)} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white mb-4">
          <option value="">Select a user…</option>
          {users.map(u => <option key={u.sub} value={u.sub}>{u.email}</option>)}
        </select>
      ) : (
        <select value={principalId} onChange={e => handleSelect(e.target.value)} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white mb-4">
          <option value="">Select a group…</option>
          {groups.map(g => <option key={g.name} value={g.name}>{g.name}</option>)}
        </select>
      )}

      {principalId && (
        <>
          {loading ? (
            <p className="text-sm text-gray-500">Loading shares...</p>
          ) : shares.length === 0 ? (
            <div className="border border-dashed border-gray-600 rounded-lg p-4 text-center text-gray-500 text-sm mb-4">No ecosystems shared with this {principalType}.</div>
          ) : (
            <div className="space-y-1.5 mb-4">
              {shares.map(s => (
                <div key={s.policyId} className="flex items-center justify-between bg-gray-800/50 rounded px-3 py-2">
                  <div className="flex items-center gap-2 min-w-0">
                    <span className="text-sm text-white truncate">{ecosystems.find(e => e.id === s.ecosystemId)?.name || s.ecosystemId}</span>
                    <span className={`text-[10px] px-1.5 py-0.5 rounded font-medium ${PERM_COLORS[s.permission] || "bg-gray-700 text-gray-300"}`}>{s.permission}</span>
                  </div>
                  <button onClick={() => handleRemove(s.policyId)} className="text-red-400 hover:text-red-300 text-xs shrink-0 ml-2">Remove</button>
                </div>
              ))}
            </div>
          )}

          {/* Add sharing */}
          <div className="border-t border-tessera-border pt-3">
            <h4 className="text-xs text-gray-400 uppercase tracking-wider mb-2">Share an ecosystem</h4>
            <div className="flex gap-2">
              <select value={addEcoId} onChange={e => setAddEcoId(e.target.value)} className="flex-1 bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white">
                <option value="">Select ecosystem…</option>
                {ecosystems.map(e => <option key={e.id} value={e.id}>{e.name}</option>)}
              </select>
              <select value={addPerm} onChange={e => setAddPerm(e.target.value as any)} className="bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white">
                <option value="run">run</option>
                <option value="edit">edit</option>
                <option value="full">full</option>
              </select>
              <button onClick={handleAdd} disabled={adding || !addEcoId} className="px-3 py-2 bg-tessera-accent/20 text-tessera-accent rounded text-sm hover:bg-tessera-accent/30 disabled:opacity-40">
                {adding ? "…" : "+ Add"}
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  );
}

function HelpView() {
  return (
    <div className="h-[calc(100vh-100px)]">
      <iframe src="/help/index.html" className="w-full h-full rounded-xl border border-tessera-border" />
    </div>
  );
}
