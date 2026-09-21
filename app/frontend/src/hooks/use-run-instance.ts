"use client";

async function apiFetch(url: string, init?: RequestInit): Promise<Response> { const { fetchAuthSession } = await import("aws-amplify/auth"); let token = ""; try { const session = await fetchAuthSession({ forceRefresh: false }); token = session.tokens?.idToken?.toString() || ""; } catch {} const headers = new Headers(init?.headers); if (token) headers.set("Authorization", `Bearer ${token}`); return fetch(url, { ...init, headers }); }

import { useCallback, useEffect, useRef, useState, useMemo } from "react";
import { AGUIEvent } from "@/lib/ag-ui-types";
import { getApiUrl } from "@/lib/runtime-config";

export type RunStatus = "idle" | "running" | "succeeded" | "failed";

// Resolve the REST base at RUNTIME (getApiUrl reads window.__TESSERA_SOLO__) rather than a
// build-time NEXT_PUBLIC_API_URL const — the exported /solo build has no build-time URL, so a
// stale const would make run-status polls hit a relative/empty URL (→ CloudFront 403) instead of
// the eco's API. Read per-render so it's correct in both the platform and exported builds.
const POLL_INTERVAL = 5000;

interface UseRunInstanceOptions {
  ecosystemId: string;
  sessionId: string;
  /** Called by the shared WebSocket with events for this session */
  subscribe: (sessionId: string, listener: (event: any) => void) => () => void;
}

export function useRunInstance({ ecosystemId, sessionId, subscribe }: UseRunInstanceOptions) {
  const [events, setEvents] = useState<AGUIEvent[]>([]);
  const [sfnStatus, setSfnStatus] = useState<string | null>("RUNNING");
  const [outcome, setOutcome] = useState<any | null>(null);
  const [pendingGates, setPendingGates] = useState<any[]>([]);
  const [inFlightPhase, setInFlightPhase] = useState<{ grounding: string[]; external: string[] }>({ grounding: [], external: [] });
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  // Derived status
  const runStatus: RunStatus = useMemo(() => {
    if (sfnStatus === "RUNNING") return "running";
    if (sfnStatus === "SUCCEEDED") return "succeeded";
    if (sfnStatus === "FAILED" || sfnStatus === "TIMED_OUT" || sfnStatus === "ABORTED") return "failed"; // AWS Step Functions API status values
    if (events.some((e) => e.type === "RUN_ERROR")) return "failed";
    if (events.some((e) => e.type === "RUN_FINISHED")) return "succeeded";
    return "idle";
  }, [sfnStatus, events]);

  // Subscribe to WebSocket events for this session
  useEffect(() => {
    if (!sessionId) return;
    const unsub = subscribe(sessionId, (event: any) => {
      setEvents((prev) => [...prev, event]);
      // Track in-flight grounding phases
      if (event.type === "STATE_DELTA") {
        if (event.grounding_started) {
          setInFlightPhase((p) => ({ ...p, grounding: [...p.grounding, event.agent] }));
        } else if (event.grounding_complete) {
          setInFlightPhase((p) => ({ ...p, grounding: p.grounding.filter((a) => a !== event.agent) }));
        }
        if (event.external_started) {
          setInFlightPhase((p) => ({ ...p, external: [...p.external, event.agent] }));
        } else if (event.external_complete) {
          setInFlightPhase((p) => ({ ...p, external: p.external.filter((a) => a !== event.agent) }));
        }
      }
    });
    return unsub;
  }, [sessionId, subscribe]);

  // Poll SFN status
  useEffect(() => {
    if (!ecosystemId || !sessionId) return;
    const poll = async () => {
      try {
        const res = await apiFetch(`${getApiUrl()}/api/ecosystems/${ecosystemId}/runs/${sessionId}`);
        if (!res.ok) return;
        const data = await res.json();
        const status = data.execution?.status || data.status || data.sfn_status;
        if (status) setSfnStatus(status);
        if (data.outcome) setOutcome(data.outcome);

        // Synthesize STATE_DELTA (status) AND TEXT_MESSAGE_CONTENT (output) events from
        // polled agent snapshots. Without the text synthesis, a tab whose live WS text
        // already streamed past (or whose WS dropped — e.g. an older run among several
        // concurrent ones) shows the DAG but an empty detail panel ("Waiting for agent
        // output..."). The runs-api detail carries each completed agent's full output, so
        // we emit the same TEXT_MESSAGE_CONTENT the live stream would have. Deduped so
        // repeated polls don't duplicate the text.
        const agentList = data.agents || [];
        if (Array.isArray(agentList)) {
          for (const agent of agentList) {
            const agentStatus = agent.status || (agent.output_size > 0 ? "complete" : undefined);
            if (agentStatus === "complete" || agentStatus === "failed") {
              setEvents((prev) => {
                const additions: any[] = [];
                if (!prev.some((e) => e.type === "STATE_DELTA" && e.agent === agent.agent_id && (e as any).status === agentStatus)) {
                  additions.push({ type: "STATE_DELTA", agent: agent.agent_id, status: agentStatus });
                }
                if (agent.output && !prev.some((e) => e.type === "TEXT_MESSAGE_CONTENT" && e.agent === agent.agent_id)) {
                  additions.push({ type: "TEXT_MESSAGE_CONTENT", agent: agent.agent_id, messageId: `${agent.agent_id}-poll`, delta: agent.output });
                }
                return additions.length ? [...prev, ...additions] : prev;
              });
            }
          }
        }
        // Also check running_agents from the API
        if (data.running_agents && Array.isArray(data.running_agents)) {
          for (const agentId of data.running_agents) {
            setEvents((prev) => {
              if (prev.some((e) => e.type === "STATE_DELTA" && e.agent === agentId && (e as any).status === "running")) return prev;
              return [...prev, { type: "STATE_DELTA", agent: agentId, status: "running" } as any];
            });
          }
        }

        // Pending gate decisions (ask mode — SFN paused waiting for operator)
        if (data.pending_gates && Array.isArray(data.pending_gates)) {
          setPendingGates(data.pending_gates);
        } else {
          setPendingGates([]);
        }

        // Stop polling on terminal status
        if (status === "SUCCEEDED" || status === "FAILED" || status === "TIMED_OUT" || status === "ABORTED") { // AWS Step Functions API status values
          if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; }
        }
      } catch {}
    };

    poll(); // immediate first poll
    pollRef.current = setInterval(poll, POLL_INTERVAL);
    return () => { if (pollRef.current) { clearInterval(pollRef.current); pollRef.current = null; } };
  }, [ecosystemId, sessionId]);

  return {
    events,
    runStatus,
    sfnStatus,
    outcome,
    inFlightPhase,
    sessionId,
    pendingGates,
  };
}
