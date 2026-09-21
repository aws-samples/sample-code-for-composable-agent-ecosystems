"use client";

import { useState } from "react";
import { DecisionPoint, DecisionResolution, DecisionOption } from "@/lib/decision";

/**
 * One renderer for every human decision point — eco inputs, builder questions,
 * quality gates. Implements the platform's universal protocol:
 *   surface (prompt + assumptions) -> ground (options/fields/verified) -> confirm.
 * Talk-or-click: options are tappable; free text allowed when allowText.
 * Irreversible options are visually marked and always require an explicit click
 * (the button-committed boundary).
 */
export function DecisionSurface({
  decision,
  busy,
  onResolve,
}: {
  decision: DecisionPoint;
  busy?: boolean;
  onResolve: (r: DecisionResolution) => void;
}) {
  const [text, setText] = useState("");
  const [fields, setFields] = useState<Record<string, string>>(
    Object.fromEntries((decision.fields || []).map((f) => [f.id, f.value || ""])),
  );

  const resolveOption = (opt: DecisionOption) =>
    onResolve({ decisionId: decision.id, optionId: opt.id, text: text.trim() || undefined, fields });
  const resolveText = () =>
    onResolve({ decisionId: decision.id, text: text.trim() || undefined, fields });

  const accentByKind: Record<string, string> = {
    gate: "border-amber-500/40",
    question: "border-tessera-accent/40",
    input: "border-tessera-accent/40",
    design_review: "border-tessera-accent/40",
    approval: "border-amber-500/40",
  };
  const label = decision.kind === "gate" ? "Decision required"
    : decision.kind === "question" ? "Your answer needed"
    : decision.kind === "input" ? "Input needed"
    : decision.kind === "approval" ? "Approval needed" : "Review";

  return (
    <div className={`bg-tessera-accent/10 border rounded-lg p-3 ${accentByKind[decision.kind] || "border-tessera-accent/40"}`}>
      <div className="flex items-center gap-1.5 mb-1.5">
        <span className="text-tessera-accent">◆</span>
        <span className="text-[10px] uppercase tracking-wide text-tessera-accent font-bold">{label}</span>
        <span className="text-[10px] text-gray-500 ml-auto">{decision.title}</span>
      </div>
      <div className="text-sm text-white mb-2 font-medium whitespace-pre-wrap">{decision.prompt}</div>

      {/* ground: surfaced assumptions */}
      {decision.assumptions && decision.assumptions.length > 0 && (
        <div className="mb-2 space-y-0.5">
          {decision.assumptions.map((a, i) => {
            const st = a.status || "open";
            const color = st === "confirmed" || st === "grounded" ? "text-green-400" : st === "needs_verification" ? "text-amber-400" : "text-gray-400";
            const icon = st === "confirmed" || st === "grounded" ? "✓" : st === "needs_verification" ? "⚠" : "?";
            return <div key={i} className="text-[11px] text-gray-300"><span className={color}>{icon}</span> {a.statement}</div>;
          })}
        </div>
      )}

      {/* structured fields */}
      {(decision.fields || []).map((f) => (
        <div key={f.id} className="mb-2">
          <label className="text-[11px] text-gray-400 block mb-1">{f.label}</label>
          {f.type === "textarea" ? (
            <textarea value={fields[f.id] || ""} onChange={(e) => setFields({ ...fields, [f.id]: e.target.value })}
              placeholder={f.placeholder} rows={2}
              className="w-full bg-gray-900 border border-tessera-border rounded px-2 py-1.5 text-[12px] text-white" />
          ) : (
            <input value={fields[f.id] || ""} onChange={(e) => setFields({ ...fields, [f.id]: e.target.value })}
              placeholder={f.placeholder}
              className="w-full bg-gray-900 border border-tessera-border rounded px-2 py-1.5 text-[12px] text-white" />
          )}
        </div>
      ))}

      {/* talk: optional free-text reply */}
      {decision.allowText && (
        <textarea value={text} onChange={(e) => setText(e.target.value)}
          placeholder="Reply, or add corrective guidance…" rows={2}
          className="w-full bg-gray-900 border border-tessera-border rounded px-2 py-1.5 text-[12px] text-white mb-2" />
      )}

      {/* click: options (irreversible ones marked danger and require this explicit click) */}
      {decision.options && decision.options.length > 0 && (
        <div className="flex flex-wrap gap-1.5">
          {decision.options.map((opt) => (
            <button key={opt.id} onClick={() => resolveOption(opt)} disabled={busy}
              className={`text-[12px] rounded-full px-3 py-1 border disabled:opacity-40 ${
                opt.intent === "danger" ? "bg-red-500/15 text-red-300 border-red-500/40 hover:bg-red-500/25"
                : opt.intent === "primary" ? "bg-tessera-accent/25 text-tessera-accent border-tessera-accent/50 hover:bg-tessera-accent/35"
                : "bg-tessera-accent/15 text-tessera-accent border-tessera-accent/40 hover:bg-tessera-accent/25"}`}>
              {opt.label}
            </button>
          ))}
          {decision.allowText && (
            <button onClick={resolveText} disabled={busy || !text.trim()}
              className="text-[12px] rounded-full px-3 py-1 border border-gray-600 text-gray-300 hover:bg-white/5 disabled:opacity-40">Send reply</button>
          )}
        </div>
      )}
      {!decision.options?.length && decision.allowText && (
        <button onClick={resolveText} disabled={busy || !text.trim()}
          className="text-[12px] rounded-full px-3 py-1 border border-tessera-accent/40 text-tessera-accent disabled:opacity-40">Send</button>
      )}
    </div>
  );
}
