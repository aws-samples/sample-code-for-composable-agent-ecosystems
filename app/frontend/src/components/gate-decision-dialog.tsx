"use client";

import { useState, useRef, useEffect } from "react";
import { getApiUrl } from "@/lib/runtime-config";

// Resolve the REST base at RUNTIME (window.__TESSERA_SOLO__) so the exported /solo build hits
// the eco's own API; falls back to build-time env for the platform build.
const API_URL_FALLBACK = process.env.NEXT_PUBLIC_API_URL || "";
const apiBase = () => getApiUrl() || API_URL_FALLBACK;

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

interface GateDecision {
  agent_id: string;
  gate_type: string;
  error_summary: string;
  task_token: string;
  session_id: string;
  payload_key?: string;
  verdict?: string;
  weaknesses?: string;
  blocker?: string;
  checkpoint_layer?: number;
}

/**
 * A quality gate as a CONVERSATION: discuss the verdict / give guidance, then
 * decide (Accept / Loop / Abort). Guidance is collected from BOTH the sent chat
 * messages AND whatever is currently typed in the input box (so a decision never
 * silently drops un-sent text). Loop requires guidance (else it just repeats);
 * Loop and Abort confirm first; Accept injects an authoritative "accepted —
 * don't re-raise" note so later checkpoints don't re-fire for the same reason.
 */
export function GateDecisionDialog({ decision, onResolved }: { decision: GateDecision; onResolved: () => void }) {
  const isChallenger = decision.gate_type === "challenger";
  // A challenger critique can also surface via the output_kind gate (e.g. a
  // verdict-consistency violation: a BLOCKER finding but a non-WEAK verdict). The
  // verdict + weaknesses are enriched server-side, so lead with the reviewer's
  // ACTUAL assessment instead of the raw rule-violation string.
  const hasVerdict = !!decision.verdict;
  const verdict = (decision.verdict || "").toUpperCase();
  const firstWeak = (decision.weaknesses || "").split("\n").map(s => s.trim()).find(Boolean) || decision.error_summary || "";
  const blocker = (decision.blocker || "").trim();
  const blockerInconsistent = !!blocker && !!verdict && verdict !== "WEAK";

  let opener: string;
  if (isChallenger) {
    opener = `Checkpoint at layer ${decision.checkpoint_layer ?? "?"} came back ${verdict || "WEAK"}. Top concern: ${firstWeak.slice(0, 200)}. Ask me about it or tell me how to fix it — then Accept, Loop, or Abort.`;
  } else if (blockerInconsistent) {
    opener = `${decision.agent_id} reviewed the pipeline and rated it ${verdict} overall — but it also flagged a BLOCKER: ${blocker.slice(0, 220)}. A blocker normally means the verdict should be WEAK, which is why this paused. You can send it back to fix the blocker (Loop), Accept ${verdict} as-is, or ask me about it first.`;
  } else if (hasVerdict) {
    opener = `${decision.agent_id} rated this ${verdict}. Top concern: ${firstWeak.slice(0, 200)}. Discuss or give guidance, then decide.`;
  } else {
    opener = `The ${decision.gate_type} gate flagged ${decision.agent_id}: ${(decision.error_summary || "").slice(0, 200)}. Discuss or give guidance, then decide.`;
  }

  const [messages, setMessages] = useState<{ role: "user" | "assistant"; content: string }[]>([{ role: "assistant", content: opener }]);
  const [input, setInput] = useState("");
  const [chatting, setChatting] = useState(false);
  const [submitting, setSubmitting] = useState(false);
  const [result, setResult] = useState<string | null>(null);
  const [dismissed, setDismissed] = useState(false);
  const [showDetails, setShowDetails] = useState(false);
  // Confirm/guard state: which decision is pending confirmation, the guidance it
  // will carry, and (for loop) a "needs guidance" block.
  const [pending, setPending] = useState<{ kind: "loop" | "abort"; guidance: string } | null>(null);
  const [needGuidance, setNeedGuidance] = useState(false);
  const endRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages, chatting, pending, needGuidance]);

  // Guidance = everything the operator has said: sent chat messages PLUS whatever
  // is currently typed but not yet sent (so clicking a button never loses it).
  const collectGuidance = () => {
    const sent = messages.filter(m => m.role === "user").map(m => m.content);
    const draft = input.trim();
    return [...sent, ...(draft ? [draft] : [])].join("\n").trim();
  };

  // On Accept, record a TERSE authoritative "accepted — resolved" acknowledgment
  // so downstream agents and LATER checkpoints don't re-raise the same issue and
  // re-fire the gate. We deliberately DO NOT echo the challenger's concern text
  // here — doing so floods run-global operator guidance with "the request is
  // unactionable / misspelled" noise that drowns the operator's real directives.
  // The operator's own typed guidance (if any) is prepended separately by onAccept.
  const acceptanceNote = () => {
    const where = isChallenger
      ? `the checkpoint at layer ${decision.checkpoint_layer ?? "?"}`
      : `the ${decision.gate_type} gate on ${decision.agent_id}`;
    return (
      `[OPERATOR DECISION — ACCEPTED] The operator reviewed ${where} (verdict: ${decision.verdict || "n/a"}) ` +
      `and ACCEPTED it as-is. Its concern(s) are accepted and resolved — do NOT re-raise or block on them ` +
      `again. This line is an acknowledgment only, not a new instruction.`
    );
  };

  const gatePost = (extra: Record<string, unknown>) => apiFetch(
    `${apiBase()}/api/runs/${encodeURIComponent(decision.session_id)}/gate-decision`,
    { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(extra) },
  );

  const send = async () => {
    const msg = input.trim();
    if (!msg || chatting) return;
    const history = messages.map(m => ({ role: m.role, content: m.content }));
    setMessages(m => [...m, { role: "user", content: msg }]);
    setInput("");
    setChatting(true);
    try {
      const res = await gatePost({
        action: "chat", message: msg, history,
        verdict: decision.verdict, weaknesses: decision.weaknesses,
        gate_type: decision.gate_type, agent_id: decision.agent_id,
        checkpoint_layer: decision.checkpoint_layer,
      });
      const data = await res.json().catch(() => ({}));
      setMessages(m => [...m, { role: "assistant", content: res.ok ? (data.reply || "(no reply)") : `Error: ${data.error || res.statusText}` }]);
    } catch (e) {
      setMessages(m => [...m, { role: "assistant", content: `Request failed: ${e}` }]);
    } finally {
      setChatting(false);
    }
  };

  const submit = async (choice: "continue" | "retry" | "stop", operatorInput: string) => {
    setPending(null);
    setNeedGuidance(false);
    setSubmitting(true);
    try {
      const res = await gatePost({
        task_token: decision.task_token, decision: choice,
        agent_id: decision.agent_id, gate_type: decision.gate_type,
        payload_key: decision.payload_key || "", session_id: decision.session_id || "",
        operator_input: operatorInput,
      });
      if (res.ok) {
        setInput("");
        setResult(choice === "continue" ? "Accepted — proceeding (guidance recorded so later checkpoints won't re-fire)…" : choice === "retry" ? "Looping — re-running with your guidance…" : "Aborted.");
        setTimeout(() => { setDismissed(true); onResolved(); }, 900);
      } else {
        const data = await res.json().catch(() => ({}));
        setResult(`Failed: ${data.error || res.statusText}`);
        setSubmitting(false);
      }
    } catch (e) {
      setResult(`Request failed: ${e}`);
      setSubmitting(false);
    }
  };

  // Button handlers — apply the guards before submitting.
  const onAccept = () => {
    const g = collectGuidance();
    submit("continue", (g ? g + "\n\n" : "") + acceptanceNote());
  };
  const onLoop = () => {
    const g = collectGuidance();
    if (!g) { setNeedGuidance(true); inputRef.current?.focus(); return; }  // block guidance-less loop
    setPending({ kind: "loop", guidance: g });                             // confirm with summary
  };
  const onAbort = () => setPending({ kind: "abort", guidance: collectGuidance() });  // always confirm

  if (dismissed) return null;
  const done = !!result;
  const busy = submitting || done;

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
      <div className="bg-tessera-card border border-purple-500/40 rounded-xl w-[620px] max-w-[92vw] max-h-[85vh] flex flex-col">
        <div className="flex items-center gap-2 px-5 py-3 border-b border-tessera-border">
          <span className="text-purple-400">◆</span>
          <h3 className="text-sm font-bold text-purple-200">
            {isChallenger ? `Checkpoint L${decision.checkpoint_layer ?? "?"}` : `Gate · ${decision.gate_type}`}
          </h3>
          <span className={`ml-2 text-[11px] font-mono ${decision.verdict === "WEAK" ? "text-red-300" : "text-purple-300"}`}>{decision.verdict || ""}</span>
          {(decision.weaknesses || decision.error_summary) && (
            <button onClick={() => setShowDetails(s => !s)} className="ml-auto text-[11px] text-gray-400 hover:text-white">{showDetails ? "Hide details" : "View details"}</button>
          )}
        </div>

        {showDetails && (
          <div className="px-5 py-2 border-b border-tessera-border bg-gray-900/60 text-[11px] text-gray-300 font-mono whitespace-pre-wrap max-h-[180px] overflow-y-auto">
            {decision.weaknesses || decision.error_summary}
          </div>
        )}

        {/* conversation */}
        <div className="flex-1 overflow-y-auto px-5 py-3 space-y-3 min-h-[160px]">
          {messages.map((m, i) => (
            <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
              <div className={`max-w-[85%] px-3 py-2 rounded-lg text-sm whitespace-pre-wrap ${m.role === "user" ? "bg-tessera-accent/20 text-tessera-accent" : "bg-gray-800 text-gray-200"}`}>{m.content}</div>
            </div>
          ))}
          {chatting && <div className="text-xs text-gray-500 italic animate-pulse">Thinking…</div>}
          <div ref={endRef} />
        </div>

        {/* input */}
        {!done && !pending && (
          <div className="px-5 py-2 border-t border-tessera-border">
            <div className="flex gap-2">
              <input ref={inputRef} value={input}
                onChange={e => { setInput(e.target.value); if (needGuidance) setNeedGuidance(false); }}
                onKeyDown={e => { if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); send(); } }}
                disabled={chatting || submitting}
                placeholder="Ask about the verdict, or give corrective guidance…"
                className={`flex-1 bg-gray-900 border rounded px-3 py-2 text-sm text-white placeholder-gray-600 disabled:opacity-40 ${needGuidance ? "border-amber-500" : "border-tessera-border"}`} />
              <button onClick={send} disabled={chatting || submitting || !input.trim()}
                className="px-3 py-2 text-xs rounded border border-tessera-border text-gray-300 hover:bg-white/5 disabled:opacity-30">Send</button>
            </div>
            <p className="text-[10px] text-gray-500 mt-1">Your typed text counts as guidance even if you don't press Send — Accept/Loop will include it.</p>
          </div>
        )}

        {needGuidance && (
          <div className="mx-5 mb-2 text-xs text-amber-200 bg-amber-500/10 border border-amber-500/40 rounded p-3">
            <strong>Loop needs guidance.</strong> Re-running this layer with no new direction will almost certainly
            produce the same result and re-trigger this exact gate. Add corrective guidance above, then Loop.
            If you want to proceed without changes, use <span className="text-green-300">Accept</span> instead.
          </div>
        )}

        {result && <div className="mx-5 mb-2 text-xs text-center text-purple-200 bg-purple-500/10 border border-purple-500/30 rounded p-2">{result}</div>}

        {/* confirm panels for Loop / Abort */}
        {pending?.kind === "loop" && (
          <div className="mx-5 mb-3 text-xs bg-amber-500/10 border border-amber-500/40 rounded p-3 space-y-2">
            <div className="text-amber-200 font-semibold">Loop {isChallenger ? `layer ${decision.checkpoint_layer ?? "?"}` : decision.agent_id} with this guidance?</div>
            <div className="text-gray-300 bg-gray-900/70 border border-tessera-border rounded p-2 max-h-[140px] overflow-y-auto whitespace-pre-wrap">{pending.guidance}</div>
            <div className="flex gap-2 justify-end">
              <button onClick={() => setPending(null)} disabled={busy} className="px-3 py-1.5 rounded border border-tessera-border text-gray-300 hover:bg-white/5 disabled:opacity-30">Cancel</button>
              <button onClick={() => submit("retry", pending.guidance)} disabled={busy} className="px-3 py-1.5 rounded bg-amber-500/30 text-amber-100 border border-amber-500/50 hover:bg-amber-500/40 disabled:opacity-30 font-semibold">↻ Confirm Loop</button>
            </div>
          </div>
        )}
        {pending?.kind === "abort" && (
          <div className="mx-5 mb-3 text-xs bg-red-500/10 border border-red-500/40 rounded p-3 space-y-2">
            <div className="text-red-200 font-semibold">Abort the entire run?</div>
            <div className="text-gray-300">This stops the run for good — it cannot be resumed. You&apos;ll need to start a new run.</div>
            <div className="flex gap-2 justify-end">
              <button onClick={() => setPending(null)} disabled={busy} className="px-3 py-1.5 rounded border border-tessera-border text-gray-300 hover:bg-white/5 disabled:opacity-30">Cancel</button>
              <button onClick={() => submit("stop", pending.guidance)} disabled={busy} className="px-3 py-1.5 rounded bg-red-500/30 text-red-100 border border-red-500/50 hover:bg-red-500/40 disabled:opacity-30 font-semibold">✕ Confirm Abort</button>
            </div>
          </div>
        )}

        {/* decision buttons (hidden while a confirm panel is up) */}
        {!pending && (
          <div className="flex gap-2 px-5 py-3 border-t border-tessera-border">
            <button onClick={onAbort} disabled={busy}
              className="px-3 py-2 text-xs font-semibold rounded bg-red-500/20 text-red-200 border border-red-500/40 hover:bg-red-500/30 disabled:opacity-30">✕ Abort</button>
            <button onClick={onLoop} disabled={busy}
              className="ml-auto px-3 py-2 text-xs font-semibold rounded bg-amber-500/20 text-amber-200 border border-amber-500/40 hover:bg-amber-500/30 disabled:opacity-30">↻ Loop with guidance</button>
            <button onClick={onAccept} disabled={busy}
              className="px-3 py-2 text-xs font-semibold rounded bg-green-500/20 text-green-200 border border-green-500/40 hover:bg-green-500/30 disabled:opacity-30">✓ Accept</button>
          </div>
        )}
      </div>
    </div>
  );
}
