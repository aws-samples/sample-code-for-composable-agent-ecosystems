// The unifying primitive for every human decision point in Tessera.
//
// Eco inputs, builder questions, and quality gates are the SAME thing — a point
// where the platform surfaces a need/assumption and asks the human to resolve it
// under one protocol: surface -> ground -> confirm. They differ only by `kind`,
// the surface rendered, and timing. This module is the single contract.
//
// See docs/spec-conversational-platform.md ("Decision points as one primitive").

export type DecisionKind = "input" | "question" | "gate" | "design_review" | "approval";

// Reversible decisions can be made by free text or a click. Irreversible ones
// (deploy / abort / delete / spend-money) MUST be committed by an explicit
// affordance — never inferred from free text. This is the button-committed boundary.
export type CommitKind = "reversible" | "irreversible";

export interface DecisionAssumption {
  statement: string;
  type?: "intent" | "fact" | "consistency";
  status?: "open" | "grounded" | "confirmed" | "needs_verification";
}

export interface DecisionOption {
  /** Stable id sent back on resolve. */
  id: string;
  /** Human label shown on the button/chip. */
  label: string;
  /** Visual weight — danger marks irreversible/destructive options. */
  intent?: "default" | "primary" | "danger";
}

export interface DecisionField {
  id: string;
  label: string;
  type: "text" | "textarea";
  placeholder?: string;
  required?: boolean;
  value?: string;
}

export interface DecisionPoint {
  id: string;
  kind: DecisionKind;
  /** Short label, e.g. "Checkpoint L0", "Builder question". */
  title: string;
  /** The prompt/question/verdict shown to the human. */
  prompt: string;
  /** Tappable options (e.g. Accept/Loop/Abort, Single/Bundle). */
  options?: DecisionOption[];
  /** Structured inputs to collect (e.g. eco inputs, corrective guidance). */
  fields?: DecisionField[];
  /** Whether a free-text reply is allowed in addition to options. */
  allowText?: boolean;
  /** Assumptions this decision rests on (the surfaced "ground" view). */
  assumptions?: DecisionAssumption[];
  /** Worst-case reversibility of resolving this point. Drives the boundary. */
  commit?: CommitKind;
}

export interface DecisionResolution {
  decisionId: string;
  /** The chosen option id, if any. */
  optionId?: string;
  /** Free-text reply / corrective guidance, if any. */
  text?: string;
  /** Collected field values, if any. */
  fields?: Record<string, string>;
}

/** Convenience: is this a destructive/irreversible option that needs an explicit click? */
export function isIrreversible(dp: DecisionPoint, opt?: DecisionOption): boolean {
  return dp.commit === "irreversible" || opt?.intent === "danger";
}
