"use client";

import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { AGUIEvent, UserInputRequiredEvent } from "@/lib/ag-ui-types";

interface UseAGUIOptions {
  url: string;
  onEvent?: (event: AGUIEvent) => void;
}

/**
 * Run lifecycle state — distinct from `isRunning` (which tracks WS activity).
 *
 * For multi-agent ecosystems the WS receives a RUN_FINISHED per agent, so
 * `isRunning` toggles off when the FIRST agent finishes, not when the whole
 * SFN execution ends. To get the truth we track SFN execution status via
 * polling runs-api.
 */
export type RunStatus = "idle" | "running" | "succeeded" | "failed";

/**
 * Connection state for the live event channel.
 *  - "live"     → WebSocket is open and we recently received an event
 *  - "polling"  → WebSocket is closed/idle but SFN status polling is active
 *                 (typical after API Gateway 10-min idle timeout drops the WS,
 *                 OR after a page reload that resumed via localStorage)
 *  - "offline"  → No WS and no active polling — UI shows last-known state
 *
 * Surfaced in the RunView header so users can tell whether they're getting
 * real-time tokens or 5s-tick polled snapshots.
 */
export type ConnectionState = "live" | "polling" | "offline";

export function useAGUI({ url, onEvent }: UseAGUIOptions) {
  const [events, setEvents] = useState<AGUIEvent[]>([]);
  const [isRunning, setIsRunning] = useState(false);
  const [pendingDecision, setPendingDecision] = useState<UserInputRequiredEvent | null>(null);
  const [sessionId, setSessionId] = useState<string | null>(null);
  // Tracked execution — set by startRun (after RUN_STARTED arrives) or by
  // trackExecution (called when user redrives a past run, or when we restore
  // an in-flight run from localStorage after a page reload). Drives runStatus
  // and SFN polling.
  const [trackedExec, setTrackedExec] = useState<{ ecosystemId: string; execName: string } | null>(() => {
    // Restore from localStorage on hook init — if there's an in-flight
    // execution recorded, we'll let the polling effect verify it's still
    // RUNNING in SFN and otherwise clear it.
    if (typeof window === "undefined") return null;
    try {
      const raw = window.localStorage.getItem("tessera.activeRun");
      if (!raw) return null;
      const parsed = JSON.parse(raw);
      if (parsed?.ecosystemId && parsed?.execName) {
        return { ecosystemId: parsed.ecosystemId, execName: parsed.execName };
      }
    } catch { /* ignore parse errors */ }
    return null;
  });
  const [sfnStatus, setSfnStatus] = useState<string | null>(null);
  // Phase descriptors of non-agent work currently in flight (grounding,
  // external-grounding, etc.). Surfaced so the live RunView can show
  // "▶ verifying findings" between layers instead of looking idle.
  const [inFlightPhase, setInFlightPhase] = useState<{ grounding: string[]; external: string[] }>({
    grounding: [],
    external: [],
  });
  // Phase 5f: typed outcome record polled from the runs-api detail response.
  // Includes declared artifacts, produced/missing IDs, status. RunView
  // passes this through to <OutcomePanel /> for download buttons.
  const [outcome, setOutcome] = useState<any | null>(null);
  // WS health — drives the connectionState derived value below. wsOpen is
  // toggled in onopen/onclose/onerror; lastEventAt records the last meaningful
  // event so we can show "live (events flowing)" vs "live (idle)" if needed.
  const [wsOpen, setWsOpen] = useState(false);
  const [lastEventAt, setLastEventAt] = useState<number | null>(null);
  const wsRef = useRef<WebSocket | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Keepalive: Amazon API Gateway WebSocket APIs idle out at 10 min unless the client
  // sends a message. Server-pushed events do NOT reset the idle timer. We send
  // a no-op `ping` every 5 min while the WS is open so long-running ecosystems
  // (BuyerSide, Stage at ~25-30 min) don't lose the live event stream.
  const pingRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const apiBase = process.env.NEXT_PUBLIC_API_URL || url.replace(/\/?$/, "");

  // Derived: connection state for the live channel.
  const connectionState: ConnectionState = useMemo(() => {
    if (wsOpen) return "live";
    if (pollRef.current) return "polling";
    return "offline";
  }, [wsOpen, sfnStatus]);  // sfnStatus dep so polling-active changes re-derive

  // Derived status — combines WS activity, terminal events, and SFN polling.
  // Order of precedence: SFN status (most authoritative) > terminal WS events > isRunning.
  const runStatus: RunStatus = useMemo(() => {
    if (sfnStatus === "RUNNING") return "running";
    if (sfnStatus === "SUCCEEDED") return "succeeded";
    if (sfnStatus === "FAILED" || sfnStatus === "TIMED_OUT" || sfnStatus === "ABORTED") return "failed"; // AWS Step Functions API status values
    // Fallback to event-based heuristic if we have no SFN poll yet
    if (isRunning) return "running";
    if (events.some((e) => e.type === "RUN_ERROR")) return "failed";
    if (events.some((e) => e.type === "RUN_FINISHED")) return "succeeded";
    return "idle";
  }, [sfnStatus, isRunning, events]);

  // Poll SFN execution status while we have a tracked execution. Polls
  // every 5s. Stops when status becomes terminal. ALSO synthesizes
  // STATE_DELTA events for completed/failed/skipped agents so the DAG
  // shows correct state even when the WS is dead (idle timeout, page
  // reload, browser sleep) — this is the resilience layer that keeps
  // the live RunView usable across all WS-disconnect scenarios.
  useEffect(() => {
    // Persist tracked exec across page reloads. Cleared on terminal status
    // by the polling tick below.
    if (typeof window !== "undefined") {
      if (trackedExec) {
        window.localStorage.setItem("tessera.activeRun", JSON.stringify(trackedExec));
      } else {
        window.localStorage.removeItem("tessera.activeRun");
      }
    }
    if (!trackedExec) return;
    // On a page reload we restore trackedExec from localStorage but sessionId starts null.
    // sessionId drives the RunView (AskPanel/RunChat), so seed it from the tracked exec name
    // (session_id == exec name) if it isn't set yet — otherwise a rehydrated finished run
    // renders without its chat/report surfaces wired up.
    setSessionId((sid) => sid || trackedExec.execName);
    let cancelled = false;
    const tick = async () => {
      try {
        const { fetchAuthSession } = await import("aws-amplify/auth");
        let _tok = "";
        try { _tok = (await fetchAuthSession({ forceRefresh: false })).tokens?.idToken?.toString() || ""; } catch {}
        const r = await fetch(`${apiBase}/api/ecosystems/${encodeURIComponent(trackedExec.ecosystemId)}/runs/${encodeURIComponent(trackedExec.execName)}`,
          _tok ? { headers: { Authorization: `Bearer ${_tok}` } } : undefined);
        if (!r.ok) return;
        const data = await r.json();
        if (cancelled) return;
        const s = data?.execution?.status as string | null;
        setSfnStatus(s || null);
        setInFlightPhase({
          grounding: data?.grounding_in_flight || [],
          external: data?.external_in_flight || [],
        });
        // Phase 5f.4: capture typed outcome for OutcomePanel rendering.
        if (data?.outcome) {
          setOutcome(data.outcome);
        }

        // Synthesize STATE_DELTA events for completed/failed/skipped agents
        // we haven't already seen events for. Dedupe by (agent, status).
        // Without this, after a page reload the DAG would render every agent
        // as "pending" since the events array starts empty.
        const polledAgents: any[] = data?.agents || [];
        const failedAgents: any[] = data?.failed_agents || [];
        const skippedAgents: string[] = data?.skipped_agents || [];
        const runningAgents: string[] = data?.running_agents || [];
        setEvents((prev) => {
          const knownKey = new Set(
            prev
              .filter((e) => e.type === "STATE_DELTA" && (e as any).status && (e as any).agent)
              .map((e) => `${(e as any).agent}|${(e as any).status}`)
          );
          // Track which agents already have any TEXT_MESSAGE_CONTENT — used to
          // avoid synthesizing a second copy from polled output for agents
          // that already streamed content via WS.
          const haveText = new Set(
            prev
              .filter((e) => e.type === "TEXT_MESSAGE_CONTENT" && (e as any).agent)
              .map((e) => (e as any).agent)
          );
          const additions: AGUIEvent[] = [];
          for (const a of polledAgents) {
            const key = `${a.agent_id}|complete`;
            if (a.agent_id && !knownKey.has(key)) {
              additions.push({ type: "STATE_DELTA", agent: a.agent_id, status: "complete" } as any);
            }
            // Synthesize a single TEXT_MESSAGE_CONTENT event with the full
            // agent output for any completed agent that hasn't streamed text
            // via the WS yet. Without this, the bottom ribbon and focused-
            // agent panel show "Waiting..." for agents that completed before
            // the WS opened (page reload, post-idle-timeout, etc.). Polling
            // already has the full output via runs-api detail.
            if (a.agent_id && a.output && !haveText.has(a.agent_id)) {
              additions.push({
                type: "TEXT_MESSAGE_CONTENT",
                agent: a.agent_id,
                delta: a.output,
              } as any);
              haveText.add(a.agent_id);
            }
          }
          for (const fa of failedAgents) {
            const key = `${fa.agent_id}|failed`;
            if (fa.agent_id && !knownKey.has(key)) {
              additions.push({ type: "STATE_DELTA", agent: fa.agent_id, status: "failed" } as any);
            }
          }
          for (const aid of skippedAgents) {
            const key = `${aid}|skipped`;
            if (aid && !knownKey.has(key)) {
              additions.push({ type: "STATE_DELTA", agent: aid, status: "skipped" } as any);
            }
          }
          // Running synthesis — synthesize STATE_DELTA running events for
          // agents currently in flight in SFN. Without this, after a page
          // refresh or 10-min WS idle drop, the DAG and "currently running"
          // line stay empty for in-flight agents — they only update when
          // the agent reaches a terminal state. With this, polling fills
          // the gap so users see the live "running" pulse from polled state.
          for (const aid of runningAgents) {
            const key = `${aid}|running`;
            if (aid && !knownKey.has(key)) {
              additions.push({ type: "STATE_DELTA", agent: aid, status: "running" } as any);
            }
          }
          return additions.length > 0 ? [...prev, ...additions] : prev;
        });

        if (s && s !== "RUNNING") {
          // Terminal — stop POLLING, but KEEP the tracked-run pointer in localStorage.
          // A long run can outlive the WS idle timeout and finish while the tab is away;
          // if we deleted the pointer here, a page reload would show the empty/static DAG
          // instead of the finished run. Keeping it lets the mount-time poll rehydrate the
          // completed DAG + outcome. A NEW launch resets trackedExec (startRun sets it
          // null), so this never resurrects a stale run over a fresh one.
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
        }
      } catch { /* ignore transient poll errors */ }
    };
    // Immediate fetch + poll every 5s.
    tick();
    pollRef.current = setInterval(tick, 5000);
    return () => {
      cancelled = true;
      if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
    };
  }, [trackedExec, apiBase]);

  /** Track an existing/redriven execution without opening a fresh WS.
   *  Used by the redrive flow — SFN is already running, we just need the badge
   *  + DAG + content to reflect live progress. */
  const trackExecution = useCallback((ecosystemId: string, execName: string) => {
    setSfnStatus("RUNNING");
    setTrackedExec({ ecosystemId, execName });
    setSessionId(execName);
  }, []);

  const startRun = useCallback(
    async (input: Record<string, unknown> = {}) => {
      const ecosystemId = input.ecosystem_id as string;
      if (!ecosystemId) return;

      setEvents([]);
      setSfnStatus(null);
      setTrackedExec(null);
      setOutcome(null);
      setIsRunning(true);

      // The launch handler REQUIRES a signature-verified Cognito ID token (it derives the
      // run's user identity from the verified `sub`, never a client-supplied id). Fetch it
      // before opening the socket and include it in the launch payload — without it the
      // backend returns 401 and the run never starts (the Runs page just shows a static DAG).
      let _token = "";
      try {
        const { fetchAuthSession } = await import("aws-amplify/auth");
        _token = (await fetchAuthSession({ forceRefresh: false })).tokens?.idToken?.toString() || "";
      } catch { /* no session — backend will reject, surfaced below */ }

      const wsBase = process.env.NEXT_PUBLIC_WS_URL || url.replace("http", "ws");
      // The $connect authorizer verifies the token from the handshake query string (browsers
      // can't set WS headers), so it must be on the URL — not only in the launch frame, which
      // arrives after the handshake. Without it the connection is 401'd at $connect.
      if (!_token) { setIsRunning(false); throw new Error("Not signed in — please sign in and try again."); }
      const wsUrl = `${wsBase}${wsBase.includes("?") ? "&" : "?"}token=${encodeURIComponent(_token)}`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
        setWsOpen(true);
        setLastEventAt(Date.now());
        ws.send(JSON.stringify({ action: "launch", ecosystem_id: ecosystemId, mission: input.mission || "", inputs: input.inputs || {}, token: _token }));
        // Start keepalive — see comment on pingRef above. 5-min cadence
        // comfortably under API Gateway's 10-min idle timeout.
        if (pingRef.current) clearInterval(pingRef.current);
        pingRef.current = setInterval(() => {
          if (wsRef.current?.readyState === WebSocket.OPEN) {
            try { wsRef.current.send(JSON.stringify({ action: "ping" })); }
            catch { /* swallow — onclose will handle */ }
          }
        }, 5 * 60 * 1000);
      };

      ws.onmessage = (msg) => {
        try {
          const event: AGUIEvent = JSON.parse(msg.data);
          setEvents((prev) => [...prev, event]);
          setLastEventAt(Date.now());
          onEvent?.(event);

          if (event.type === "RUN_STARTED" && (event as any).session_id) {
            const sid = (event as any).session_id as string;
            setSessionId(sid);
            // Begin tracking the SFN execution (session_id IS the exec name).
            setTrackedExec({ ecosystemId, execName: sid });
            setSfnStatus("RUNNING");
          }
          if (event.type === "USER_INPUT_REQUIRED") {
            setPendingDecision(event as UserInputRequiredEvent);
          }
          // NB: we no longer flip isRunning false on per-agent RUN_FINISHED.
          // For multi-agent ecosystems each agent emits its own RUN_FINISHED;
          // the workflow as a whole is still going. SFN polling drives the
          // authoritative runStatus. We only flip isRunning on RUN_ERROR
          // (terminal at agent level) so the WS state is still meaningful.
          if (event.type === "RUN_ERROR") {
            setIsRunning(false);
          }
        } catch {}
      };

      ws.onclose = () => {
        setWsOpen(false);
        if (pingRef.current) { clearInterval(pingRef.current); pingRef.current = null; }
        // Reconnect if a run is still active (SFN polling will confirm)
        if (trackedExec) {
          const wsBase = process.env.NEXT_PUBLIC_WS_URL || url.replace("http", "ws");
          setTimeout(async () => {
            // Token must be on the handshake URL (the $connect authorizer checks it there).
            let rtoken = "";
            try {
              const { fetchAuthSession } = await import("aws-amplify/auth");
              rtoken = (await fetchAuthSession({ forceRefresh: false })).tokens?.idToken?.toString() || "";
            } catch { /* no session — reconnect will 401, run polling still drives status */ }
            if (!rtoken) return;
            const rurl = `${wsBase}${wsBase.includes("?") ? "&" : "?"}token=${encodeURIComponent(rtoken)}`;
            const rws = new WebSocket(rurl);
            wsRef.current = rws;
            rws.onopen = () => {
              setWsOpen(true);
              // Re-register session so agent-invoker can find this connection
              const sid = trackedExec.execName || sessionId;
              if (sid) {
                rws.send(JSON.stringify({ action: "register", clientId: crypto.randomUUID(), sessionId: sid }));
              }
              // Restart keepalive
              if (pingRef.current) clearInterval(pingRef.current);
              pingRef.current = setInterval(() => {
                if (rws.readyState === WebSocket.OPEN) {
                  try { rws.send(JSON.stringify({ action: "ping" })); } catch {}
                }
              }, 5 * 60 * 1000);
            };
            rws.onmessage = ws.onmessage;
            rws.onclose = ws.onclose;
            rws.onerror = () => { rws.close(); };
          }, 2000);
        } else {
          setIsRunning(false);
        }
      };

      ws.onerror = () => {
        setIsRunning(false);
        setWsOpen(false);
        if (pingRef.current) { clearInterval(pingRef.current); pingRef.current = null; }
      };
    },
    [url, onEvent]
  );

  const submitDecision = useCallback((decisionId: string, response: string) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ type: "decision", decision_id: decisionId, response }));
    }
    setPendingDecision(null);
  }, []);

  useEffect(() => {
    return () => {
      wsRef.current?.close();
      if (pollRef.current) clearInterval(pollRef.current);
      if (pingRef.current) clearInterval(pingRef.current);
    };
  }, []);

  return {
    events,
    isRunning,
    runStatus,
    connectionState,
    sessionId,
    sfnStatus,
    inFlightPhase,
    outcome,
    lastEventAt,
    pendingDecision,
    startRun,
    submitDecision,
    trackExecution,
  };
}
