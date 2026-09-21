"use client";

import { useRef, useEffect } from "react";
import { useRunInstance } from "@/hooks/use-run-instance";
import { RunTab } from "@/hooks/use-run-tabs";

/**
 * Wrapper that instantiates useRunInstance for a single tab and passes
 * the run state down via render props. Each active tab gets one of these.
 */
export function RunTabContent({
  tab,
  subscribe,
  onStatusChange,
  children,
}: {
  tab: RunTab;
  subscribe: (sessionId: string, listener: (event: any) => void) => () => void;
  onStatusChange: (status: "running" | "succeeded" | "failed" | "idle") => void;
  children: (props: {
    events: any[];
    runStatus: string;
    inFlightPhase: { grounding: string[]; external: string[] };
    sessionId: string;
    outcome: any;
    pendingGates: any[];
    isRunning: boolean;
    connectionState: string;
  }) => React.ReactNode;
}) {
  const { events, runStatus, inFlightPhase, sessionId, outcome, pendingGates } = useRunInstance({
    ecosystemId: tab.ecosystemId,
    sessionId: tab.sessionId,
    subscribe,
  });

  // Notify parent of status changes for tab dot color
  const prevStatus = useRef(runStatus);
  useEffect(() => {
    if (prevStatus.current !== runStatus) {
      prevStatus.current = runStatus;
      onStatusChange(runStatus);
    }
  }, [runStatus, onStatusChange]);

  return (
    <>
      {children({
        events,
        runStatus,
        inFlightPhase,
        sessionId,
        outcome,
        pendingGates,
        isRunning: runStatus === "running",
        connectionState: "live",
      })}
    </>
  );
}
