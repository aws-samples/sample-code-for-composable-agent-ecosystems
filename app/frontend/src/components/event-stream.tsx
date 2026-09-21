"use client";

import { AGUIEvent } from "@/lib/ag-ui-types";
import { useAutoScroll } from "@/hooks/use-auto-scroll";

interface EventStreamProps {
  events: AGUIEvent[];
}

const EVENT_COLORS: Record<string, string> = {
  RUN_STARTED: "text-green-400",
  RUN_FINISHED: "text-green-400",
  TEXT_MESSAGE_CONTENT: "text-gray-300",
  TOOL_CALL_START: "text-yellow-400",
  TOOL_CALL_END: "text-yellow-300",
  STATE_DELTA: "text-blue-400",
  USER_INPUT_REQUIRED: "text-purple-400",
  RUN_ERROR: "text-red-400",
};

const EVENT_ICONS: Record<string, string> = {
  RUN_STARTED: "▶",
  RUN_FINISHED: "✓",
  TOOL_CALL_START: "🔧",
  TOOL_CALL_END: "🔧",
  STATE_DELTA: "Δ",
  USER_INPUT_REQUIRED: "⏸",
  RUN_ERROR: "✗",
};

export function EventStream({ events }: EventStreamProps) {
  const scrollRef = useAutoScroll([events.length]);

  return (
    <div ref={scrollRef} className="h-full overflow-y-auto">
      <h3 className="text-xs font-semibold text-gray-500 uppercase mb-2 sticky top-0 bg-tessera-card">Event Stream</h3>
      <div className="space-y-1 font-mono text-xs">
        {events.map((event, i) => {
          const result = (event as any).result;
          const isCrossEcoRead = event.type === "TOOL_CALL_END" && result && typeof result === "object" && result.namespace && result.namespace.startsWith("/catalog/");
          return (
            <div key={i} className={isCrossEcoRead ? "text-yellow-300 bg-yellow-500/5 px-1 rounded" : (EVENT_COLORS[event.type] || "text-gray-400")}>
              <span className="text-gray-600">[{event.agent || "system"}]</span>{" "}
              {isCrossEcoRead && <span className="text-yellow-400">⛓ cross-eco read </span>}
              <span aria-label={`Event type: ${event.type}`}>{EVENT_ICONS[event.type] ? `${EVENT_ICONS[event.type]} ` : ""}{event.type}</span>
              {event.type === "TEXT_MESSAGE_CONTENT" && `: ${(event as any).content}`}
              {event.type === "TOOL_CALL_START" && `: ${(event as any).tool}`}
              {isCrossEcoRead && <span className="text-yellow-500"> → {result.namespace} ({result.matched_count} risks)</span>}
            </div>
          );
        })}
      </div>
    </div>
  );
}
