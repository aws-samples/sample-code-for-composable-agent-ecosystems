"use client";

import { useMemo, useState } from "react";
import { AGUIEvent } from "@/lib/ag-ui-types";
import { A2UISurface } from "@/lib/a2ui-protocol";
import { A2UISurfaceRenderer } from "./a2ui-surface";
import { A2UICard } from "./a2ui/card";
import { A2UITable } from "./a2ui/table";
import { A2UIChart } from "./a2ui/chart";
import { A2UIProgress } from "./a2ui/progress";
import ReactMarkdown from "react-markdown";
import { useAutoScroll } from "@/hooks/use-auto-scroll";

interface A2UIRendererProps {
  events: AGUIEvent[];
}

interface ToolCall {
  id: string;
  name: string;
  args: string;          // accumulated from TOOL_CALL_ARGS deltas
  result?: unknown;
  status: "running" | "complete";
}
export type { ToolCall };

interface TextItem {
  kind: "text";
  key: string;
  agent?: string;
  swarm?: string;
  content: string;
  toolCalls: ToolCall[];
}

interface SurfaceItem {
  kind: "surface";
  key: string;
  agent?: string;
  swarm?: string;
  surface: A2UISurface;
}

interface LegacyA2UIItem {
  kind: "legacy_a2ui";
  key: string;
  agent?: string;
  swarm?: string;
  component: any;
}

interface GroundingItem {
  kind: "grounding";
  key: string;
  agent?: string;
  swarm?: string;
  grounding: { grounded: boolean; action?: string };
}

interface OrphanToolItem {
  kind: "orphan_tool";
  key: string;
  agent?: string;
  swarm?: string;
  tool: ToolCall;
}

type RenderItem = TextItem | SurfaceItem | LegacyA2UIItem | GroundingItem | OrphanToolItem;

function isA2UISurface(value: unknown): value is A2UISurface {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return typeof v.surfaceId === "string" && Array.isArray(v.components);
}

function isLegacyComponent(value: unknown): boolean {
  if (!value || typeof value !== "object") return false;
  const v = value as Record<string, unknown>;
  return typeof v.type === "string" && ["card", "table", "chart", "progress"].includes(v.type as string);
}

/**
 * Reduce a stream of AG-UI events into renderable items.
 *
 * Tool calls are attached to their parent message via `parentMessageId` (AG-UI standard).
 * If a tool call has no parent (or parent isn't found yet), it's surfaced inline next to the
 * agent it belongs to so users can see it streaming. Text bubbles render their tool calls
 * as inline chips below the message content.
 */
function reduceEvents(events: AGUIEvent[]): RenderItem[] {
  const items: RenderItem[] = [];
  const messageBuffers: Record<string, TextItem> = {};
  const toolBuffers: Record<string, ToolCall> = {};
  /** Map a toolCallId to the parent TextItem so we can attach result events later. */
  const toolToParent: Record<string, TextItem | null> = {};
  /** Map a toolCallId to an OrphanToolItem index so we can update it as events stream in. */
  const orphanToolByCallId: Record<string, OrphanToolItem> = {};

  for (let i = 0; i < events.length; i++) {
    const e = events[i] as any;
    const agent = e.agent as string | undefined;
    const swarm = e.swarm as string | undefined;

    if (e.type === "TEXT_MESSAGE_START") {
      const id = e.messageId || `msg-${i}`;
      const item: TextItem = { kind: "text", key: id, agent, swarm, content: "", toolCalls: [] };
      messageBuffers[id] = item;
      items.push(item);
      continue;
    }

    if (e.type === "TEXT_MESSAGE_CONTENT") {
      const id = e.messageId || `msg-${i}`;
      const delta = e.delta ?? e.content ?? "";
      let buf = messageBuffers[id];
      if (!buf) {
        buf = { kind: "text", key: id, agent, swarm, content: "", toolCalls: [] };
        messageBuffers[id] = buf;
        items.push(buf);
      }
      buf.content = (buf.content || "") + delta;
      continue;
    }

    if (e.type === "TEXT_MESSAGE_END") continue;

    if (e.type === "TOOL_CALL_START") {
      const callId = e.toolCallId || `tool-${i}`;
      const tool: ToolCall = {
        id: callId,
        name: e.toolCallName || e.tool || "tool",
        args: "",
        status: "running",
      };
      toolBuffers[callId] = tool;

      // Attach to parent message if we have one
      const parentId = e.parentMessageId || e.messageId;
      const parent = parentId ? messageBuffers[parentId] : undefined;
      if (parent) {
        parent.toolCalls.push(tool);
        toolToParent[callId] = parent;
      } else {
        // No parent yet — surface as orphan so the user sees it streaming
        const orphan: OrphanToolItem = {
          kind: "orphan_tool",
          key: `tool-orphan-${callId}`,
          agent,
          swarm,
          tool,
        };
        items.push(orphan);
        orphanToolByCallId[callId] = orphan;
        toolToParent[callId] = null;
      }
      continue;
    }

    if (e.type === "TOOL_CALL_ARGS") {
      const callId = e.toolCallId;
      const tool = callId ? toolBuffers[callId] : undefined;
      if (tool) tool.args = (tool.args || "") + (e.delta ?? "");
      continue;
    }

    if (e.type === "TOOL_CALL_END") {
      const callId = e.toolCallId;
      const tool = callId ? toolBuffers[callId] : undefined;
      if (tool) tool.status = "complete";
      continue;
    }

    if (e.type === "TOOL_CALL_RESULT") {
      const callId = e.toolCallId;
      const tool = callId ? toolBuffers[callId] : undefined;
      if (tool) {
        tool.result = e.result ?? e.content ?? null;
        tool.status = "complete";
      }
      continue;
    }

    if (e.type === "STATE_DELTA") {
      if (Array.isArray(e.delta)) {
        for (let opIdx = 0; opIdx < e.delta.length; opIdx++) {
          const op = e.delta[opIdx];
          const value = op?.value;
          if (isA2UISurface(value)) {
            items.push({ kind: "surface", key: `surface-${i}-${opIdx}`, agent, swarm, surface: value });
          } else if (isLegacyComponent(value)) {
            items.push({ kind: "legacy_a2ui", key: `a2ui-${i}-${opIdx}`, agent, swarm, component: value });
          }
        }
        continue;
      }
      if (isA2UISurface(e.a2ui)) {
        items.push({ kind: "surface", key: `surface-${i}`, agent, swarm, surface: e.a2ui });
        continue;
      }
      if (isLegacyComponent(e.a2ui)) {
        items.push({ kind: "legacy_a2ui", key: `a2ui-${i}`, agent, swarm, component: e.a2ui });
        continue;
      }
      if (e.grounding) {
        items.push({ kind: "grounding", key: `grounding-${i}`, agent, swarm, grounding: e.grounding });
        continue;
      }
    }
  }

  return items;
}

export function A2UIRenderer({ events }: A2UIRendererProps) {
  const scrollRef = useAutoScroll([events.length]);
  const items = useMemo(() => reduceEvents(events), [events]);

  if (items.length === 0) {
    return (
      <div className="flex items-center justify-center h-full text-gray-500">
        <p>Waiting for agent output...</p>
      </div>
    );
  }

  return (
    <div ref={scrollRef} className="space-y-3 text-sm h-full overflow-y-auto">
      {items.map((item) => (
        <div key={item.key}>{renderItem(item)}</div>
      ))}
    </div>
  );
}

function renderItem(item: RenderItem) {
  if (item.kind === "grounding") {
    const isGrounded = item.grounding.grounded;
    return (
      <div className={`flex items-center gap-2 px-3 py-1.5 rounded-lg text-xs ${isGrounded ? "bg-green-500/10 border border-green-500/20" : "bg-amber-500/10 border border-amber-500/20"}`}>
        <span>{isGrounded ? "✓" : "⚠"}</span>
        <span className={isGrounded ? "text-green-400" : "text-amber-400"}>{item.agent || "unknown"}</span>
        <span className="text-gray-500">{isGrounded ? "grounded" : "ungrounded claims detected"}</span>
      </div>
    );
  }

  if (item.kind === "surface") {
    return <A2UISurfaceRenderer surface={item.surface} />;
  }

  if (item.kind === "legacy_a2ui") {
    const c = item.component;
    switch (c.type) {
      case "card": return <A2UICard {...c} />;
      case "table": return <A2UITable {...c} />;
      case "chart": return <A2UIChart {...c} />;
      case "progress": return <A2UIProgress {...c} />;
      default: return null;
    }
  }

  if (item.kind === "orphan_tool") {
    return (
      <div className="flex items-start gap-2">
        <span className="w-2 h-2 rounded-full mt-1.5 bg-gray-500" />
        <div className="flex-1 min-w-0">
          <span className="text-xs text-gray-500">{item.agent || "system"}</span>
          <div className="mt-1">
            <ToolChip tool={item.tool} />
          </div>
        </div>
      </div>
    );
  }

  if (item.kind === "text") {
    const swarm = item.swarm;
    const dotColor = swarm === "red" ? "bg-red-500" : swarm === "blue" ? "bg-blue-500" : "bg-gray-500";
    const hasContent = item.content && item.content.trim().length > 0;
    const hasTools = item.toolCalls.length > 0;
    if (!hasContent && !hasTools) return null;

    return (
      <div className="flex items-start gap-2">
        <span className={`w-2 h-2 rounded-full mt-1.5 ${dotColor}`} aria-label={`${swarm || "neutral"} swarm`} role="img" />
        <span className="sr-only">{swarm || "neutral"}</span>
        <div className="flex-1 min-w-0">
          <span className="text-xs text-gray-500">{item.agent || "system"}</span>
          {hasContent && (
            <div className="text-gray-300 prose prose-invert prose-sm max-w-none prose-p:my-1 prose-ul:my-1 prose-li:my-0 prose-headings:my-2 prose-code:text-blue-300 prose-pre:bg-gray-900 prose-pre:border prose-pre:border-battlefield-border">
              <ReactMarkdown>{item.content}</ReactMarkdown>
            </div>
          )}
          {hasTools && (
            <div className="mt-1.5 flex flex-col gap-1">
              {item.toolCalls.map((t) => (
                <ToolChip key={t.id} tool={t} />
              ))}
            </div>
          )}
        </div>
      </div>
    );
  }

  return null;
}

/**
 * Compact tool-call chip rendered inline within a message bubble.
 * Collapsed by default — `🔧 toolName(args) → ✓` style. Click expands result.
 */
export function ToolChip({ tool }: { tool: ToolCall }) {
  const [expanded, setExpanded] = useState(false);
  const args = formatArgs(tool.args);
  const isRunning = tool.status === "running";
  const statusIcon = isRunning ? "⋯" : tool.result === undefined ? "✓" : isError(tool.result) ? "✗" : "✓";
  const statusColor = isRunning ? "text-gray-500" : statusIcon === "✗" ? "text-red-400" : "text-green-400";
  const expandable = tool.result !== undefined && tool.result !== null && tool.result !== "";

  // Memory tool indicators — different emoji + cross-ecosystem badge for clarity.
  const isMemoryWrite = tool.name === "memory_write";
  const isMemoryRead = tool.name === "memory_read";
  const memoryEmoji = isMemoryWrite ? "📝" : isMemoryRead ? "🔎" : "🔧";
  const namespace = (isMemoryRead || isMemoryWrite) ? extractNamespace(tool.args) : "";
  // A namespace like /tessera/catalog/risks is "shared catalog" territory — flag any
  // /catalog/* or /org/* reads as cross-ecosystem (they're cross-tenant by design).
  const isCrossEcosystem = isMemoryRead && /^\/?tessera\/(catalog|org)\//.test(namespace);

  // Phase 5.1: tool budget badge — runtime appends `_budget: {used, cap, remaining}`
  // to JSON-object results. Render N/M with amber/red threshold colouring so the
  // operator can see consumption per tool call.
  const budget = extractBudget(tool.result);
  const budgetExhausted = isBudgetExhausted(tool.result);

  return (
    <div className="text-[11px] font-mono">
      <button
        type="button"
        onClick={() => expandable && setExpanded(!expanded)}
        className={`inline-flex items-center gap-1.5 px-2 py-0.5 rounded border border-gray-700 bg-gray-900/40 hover:border-gray-500 transition ${expandable ? "cursor-pointer" : "cursor-default"}`}
      >
        <span className="text-gray-500">{memoryEmoji}</span>
        <span className="text-gray-300">{tool.name}</span>
        {args && <span className="text-gray-500 truncate max-w-xs">({args})</span>}
        {isCrossEcosystem && (
          <span className="text-purple-300 bg-purple-500/15 border border-purple-500/30 rounded px-1 text-[9px] uppercase tracking-wide" title="Reading from a shared cross-ecosystem namespace">
            cross-eco
          </span>
        )}
        {budget && (
          <span
            className={
              "rounded px-1 text-[9px] tabular-nums " +
              (budget.used >= budget.cap
                ? "bg-red-500/15 border border-red-500/30 text-red-300"
                : budget.used / Math.max(budget.cap, 1) >= 0.6
                  ? "bg-amber-500/15 border border-amber-500/30 text-amber-300"
                  : "bg-gray-700/30 border border-gray-600/30 text-gray-400")
            }
            title={`Tool budget: ${budget.used} of ${budget.cap} used (remaining: ${budget.remaining})`}
          >
            {budget.used}/{budget.cap}
          </span>
        )}
        {budgetExhausted && (
          <span className="text-red-300 bg-red-500/15 border border-red-500/30 rounded px-1 text-[9px] uppercase tracking-wide" title="Tool budget exhausted — agent must synthesize from existing evidence">
            cap hit
          </span>
        )}
        <span className={statusColor}>→ {statusIcon}</span>
      </button>
      {expanded && tool.result !== undefined && (
        <pre className="mt-1 ml-2 p-2 text-[10px] text-gray-400 bg-gray-900/60 border border-gray-800 rounded overflow-x-auto whitespace-pre-wrap break-all">
          {formatResult(tool.result)}
        </pre>
      )}
    </div>
  );
}

/** Extract `namespace` arg from a memory tool call's args JSON for badging. */
function extractNamespace(rawArgs: string): string {
  if (!rawArgs) return "";
  try {
    const obj = JSON.parse(rawArgs);
    return typeof obj?.namespace === "string" ? obj.namespace : "";
  } catch {
    return "";
  }
}

/** Phase 5.1: pull the runtime-injected `_budget` field from a tool result. */
function extractBudget(rawResult: unknown): { used: number; cap: number; remaining: number } | null {
  if (!rawResult) return null;
  let obj: any = rawResult;
  if (typeof rawResult === "string") {
    try { obj = JSON.parse(rawResult); } catch { return null; }
  }
  if (!obj || typeof obj !== "object") return null;
  const b = obj._budget;
  if (!b || typeof b !== "object") return null;
  if (typeof b.used !== "number" || typeof b.cap !== "number") return null;
  return { used: b.used, cap: b.cap, remaining: typeof b.remaining === "number" ? b.remaining : Math.max(0, b.cap - b.used) };
}

/** Phase 5.1: detect the structured tool_budget_exhausted error returned by runtime. */
function isBudgetExhausted(rawResult: unknown): boolean {
  if (!rawResult) return false;
  let obj: any = rawResult;
  if (typeof rawResult === "string") {
    try { obj = JSON.parse(rawResult); } catch { return false; }
  }
  return !!(obj && typeof obj === "object" && obj.error === "tool_budget_exhausted");
}

function formatArgs(raw: string): string {
  if (!raw) return "";
  const trimmed = raw.trim();
  if (!trimmed) return "";
  // Try to parse JSON for prettier display
  try {
    const obj = JSON.parse(trimmed);
    if (typeof obj === "object" && obj !== null) {
      const entries = Object.entries(obj).slice(0, 3);
      const parts = entries.map(([k, v]) => `${k}: ${typeof v === "string" ? `"${truncate(v, 20)}"` : truncate(String(v), 20)}`);
      return parts.join(", ");
    }
  } catch {
    // Not JSON — return truncated raw
  }
  return truncate(trimmed, 50);
}

function truncate(s: string, n: number): string {
  return s.length > n ? s.slice(0, n - 1) + "…" : s;
}

function isError(result: unknown): boolean {
  if (typeof result === "object" && result !== null) {
    return "error" in result || ("status" in result && (result as any).status === "error");
  }
  return false;
}

function formatResult(result: unknown): string {
  if (result === null || result === undefined) return "—";
  if (typeof result === "string") return result;
  return JSON.stringify(result, null, 2);
}
