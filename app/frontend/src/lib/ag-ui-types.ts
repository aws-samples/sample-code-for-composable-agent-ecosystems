/**
 * AG-UI Event Types — typed events streamed from agents to frontend.
 * Aligned with the native AG-UI protocol (https://docs.ag-ui.com).
 */

export type AGUIEventType =
  | "RUN_STARTED"
  | "RUN_FINISHED"
  | "RUN_ERROR"
  | "TEXT_MESSAGE_START"
  | "TEXT_MESSAGE_CONTENT"
  | "TEXT_MESSAGE_END"
  | "TOOL_CALL_START"
  | "TOOL_CALL_ARGS"
  | "TOOL_CALL_END"
  | "TOOL_CALL_RESULT"
  | "STATE_SNAPSHOT"
  | "STATE_DELTA"
  | "USER_INPUT_REQUIRED";

export interface AGUIEvent {
  type: AGUIEventType;
  agent?: string;
  swarm?: string;
  timestamp?: number;
  [key: string]: unknown;
}

export interface RunStartedEvent extends AGUIEvent {
  type: "RUN_STARTED";
  ecosystem?: string;
  threadId?: string;
  runId?: string;
}

export interface TextMessageStartEvent extends AGUIEvent {
  type: "TEXT_MESSAGE_START";
  messageId: string;
  role?: "assistant" | "user";
}

export interface TextMessageContentEvent extends AGUIEvent {
  type: "TEXT_MESSAGE_CONTENT";
  messageId?: string;
  delta?: string;
  /** Legacy field for backwards compatibility. */
  content?: string;
}

export interface TextMessageEndEvent extends AGUIEvent {
  type: "TEXT_MESSAGE_END";
  messageId: string;
}

export interface ToolCallStartEvent extends AGUIEvent {
  type: "TOOL_CALL_START";
  toolCallId: string;
  toolCallName: string;
  parentMessageId?: string;
}

export interface ToolCallArgsEvent extends AGUIEvent {
  type: "TOOL_CALL_ARGS";
  toolCallId: string;
  delta: string;
}

export interface ToolCallEndEvent extends AGUIEvent {
  type: "TOOL_CALL_END";
  toolCallId: string;
}

export interface ToolCallResultEvent extends AGUIEvent {
  type: "TOOL_CALL_RESULT";
  toolCallId: string;
  messageId?: string;
  content: unknown;
}

/** JSON Patch operation as defined by RFC 6902. */
export interface StateDeltaOperation {
  op: "add" | "remove" | "replace" | "move" | "copy" | "test";
  path: string;
  value?: unknown;
  from?: string;
}

export interface StateDeltaEvent extends AGUIEvent {
  type: "STATE_DELTA";
  delta?: StateDeltaOperation[];
  /** Legacy fields kept for backwards compatibility. */
  a2ui?: unknown;
  grounding?: unknown;
  status?: string;
}

export interface StateSnapshotEvent extends AGUIEvent {
  type: "STATE_SNAPSHOT";
  snapshot: Record<string, unknown>;
}

export interface UserInputRequiredEvent extends AGUIEvent {
  type: "USER_INPUT_REQUIRED";
  decision_id: string;
  question: string;
  options?: string[];
  input_type?: "choice" | "text";
}
