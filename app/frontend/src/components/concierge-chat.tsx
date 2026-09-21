"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import { TesseraSpinner } from "./tessera-spinner";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL || (process.env.NEXT_PUBLIC_API_URL || "").replace(/^http/, "ws");

async function idToken(): Promise<string> {
  try {
    const { fetchAuthSession } = await import("aws-amplify/auth");
    return (await fetchAuthSession({ forceRefresh: false })).tokens?.idToken?.toString() || "";
  } catch { return ""; }
}

interface Turn { role: "user" | "assistant"; content: string; tool?: string; }
export interface ConciergeAction { action: string; eco_id?: string; run_id?: string; view?: string; id?: string; domain?: string; query?: string; target?: string; note?: string; mission?: string; artifact_key?: string; sections?: { target: string; note?: string }[]; }

/**
 * Concierge — fast, streaming, tool-using front door over the AUTHENTICATED WebSocket.
 * Streams tokens; when it finds what you asked for it emits a navigation action
 * (e.g. open_run) that the app executes. Every message carries the Cognito id token
 * (per-message auth); the backend authorizes every run tool to the caller.
 */
export function ConciergeChat({ onAction, onPlan, title = "Concierge", context }:
  { onAction?: (a: ConciergeAction) => void; onPlan?: (plan: Record<string, unknown>) => void; title?: string; context?: Record<string, unknown> }) {
  const [open, setOpen] = useState(false);
  const [maximized, setMaximized] = useState(false);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const wsRef = useRef<WebSocket | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [turns, loading]);
  useEffect(() => () => { try { wsRef.current?.close(); } catch { /* noop */ } }, []);

  const appendToLastAssistant = (text: string) => setTurns(prev => {
    const next = [...prev];
    for (let i = next.length - 1; i >= 0; i--) if (next[i].role === "assistant") { next[i] = { ...next[i], content: next[i].content + text }; return next; }
    return [...next, { role: "assistant", content: text }];
  });
  const setLastAssistantTool = (tool: string) => setTurns(prev => {
    const next = [...prev];
    for (let i = next.length - 1; i >= 0; i--) if (next[i].role === "assistant") { next[i] = { ...next[i], tool }; return next; }
    return next;
  });

  const connect = useCallback((token: string): Promise<WebSocket> => new Promise((resolve, reject) => {
    if (wsRef.current && wsRef.current.readyState === WebSocket.OPEN) return resolve(wsRef.current);
    // The WS $connect authorizer reads the Cognito ID token from the `token` query param
    // (browsers can't set WS handshake headers). The per-message frame also carries it.
    const wsUrl = token ? `${WS_URL}${WS_URL.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}` : WS_URL;
    const ws = new WebSocket(wsUrl);
    wsRef.current = ws;
    ws.onopen = () => resolve(ws);
    ws.onerror = (e) => reject(e);
    ws.onmessage = (ev) => {
      let m: Record<string, unknown>; try { m = JSON.parse(ev.data); } catch { return; }
      switch (m.type) {
        case "CONCIERGE_DELTA": appendToLastAssistant(String(m.text || "")); break;
        case "CONCIERGE_TOOL": setLastAssistantTool(String(m.name || "")); break;
        case "CONCIERGE_ACTION": {
          const act = m as unknown as ConciergeAction;
          onAction?.(act);
          // Immersive takeovers get the stage — minimize the chat so it doesn't overlap.
          if (act.action === "replay_run" || act.action === "spotlight_outcome") setOpen(false);
          break;
        }
        case "CONCIERGE_PLAN": onPlan?.(m); setLoading(false); setOpen(false); break;
        case "CONCIERGE_DONE": setLoading(false); break;
        case "CONCIERGE_ERROR": appendToLastAssistant(`\n\n_(error: ${m.error})_`); setLoading(false); break;
      }
    };
    ws.onclose = () => { if (wsRef.current === ws) wsRef.current = null; };
  }), [onAction, onPlan]);

  const send = useCallback(async () => {
    if (!input.trim() || loading) return;
    const message = input.trim();
    setInput("");
    const history = turns.map(t => ({ role: t.role, content: t.content }));
    setTurns(prev => [...prev, { role: "user", content: message }, { role: "assistant", content: "" }]);
    setLoading(true);
    try {
      const token = await idToken();
      if (!token) { appendToLastAssistant("(please sign in)"); setLoading(false); return; }
      const ws = await connect(token);
      ws.send(JSON.stringify({ action: "concierge", token, message, history, context: context || {} }));
    } catch (e) {
      appendToLastAssistant(`(connection failed: ${e})`); setLoading(false);
    }
  }, [input, loading, turns, connect, context]);

  if (!open) {
    return (
      <button onClick={() => setOpen(true)}
        className="fixed bottom-5 left-5 h-12 px-4 bg-tessera-gradient rounded-full shadow-lg shadow-purple-500/30 flex items-center gap-2 text-sm font-semibold hover:scale-105 transition z-50"
        title={`Ask the ${title}`}>✨ {title}</button>
    );
  }
  const frame = maximized ? "fixed inset-4 md:inset-10 z-50" : "fixed bottom-5 left-5 w-96 h-[30rem] z-50";
  return (
    <div data-presenter-chrome className={`${frame} bg-tessera-card border border-tessera-border rounded-xl shadow-2xl shadow-black/50 flex flex-col`}>
      <div className="flex items-center justify-between px-3 py-2 border-b border-tessera-border">
        <span className="text-xs font-bold text-tessera-accent">✨ {title}</span>
        <div className="flex items-center gap-2">
          {turns.length > 0 && <button onClick={() => setTurns([])} className="text-[10px] text-gray-500 hover:text-red-300" title="Clear">Clear</button>}
          <button onClick={() => setMaximized(m => !m)} className="text-gray-500 hover:text-white text-xs" title={maximized ? "Restore" : "Maximize"}>{maximized ? "🗗" : "🗖"}</button>
          <button onClick={() => setOpen(false)} className="text-gray-500 hover:text-white text-sm" title="Minimize">—</button>
        </div>
      </div>
      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-3">
        {turns.length === 0 && (
          <p className="text-[10px] text-gray-500 text-center mt-8">
            Ask what Tessera can do — or say &quot;bring up the result of X for Y&quot; and I&apos;ll find it and open it.
            I only see your own work.
          </p>
        )}
        {turns.map((t, i) => (
          t.role === "user" ? (
            <div key={i} className="text-[10px] text-gray-400 text-right">You: {t.content}</div>
          ) : (
            <div key={i} className="border-l-2 border-tessera-accent/30 pl-2">
              {t.tool && <div className="text-[9px] text-gray-600 mb-0.5">⚙ {t.tool}</div>}
              <div className="text-[11px] text-gray-300 prose prose-invert prose-xs max-w-none prose-p:my-0.5 prose-headings:my-1 prose-headings:text-tessera-accent">
                <ReactMarkdown>{t.content || (loading && i === turns.length - 1 ? "…" : "")}</ReactMarkdown>
              </div>
            </div>
          )
        ))}
        {loading && <div className="flex items-center gap-1.5 text-[10px] text-gray-500"><TesseraSpinner size={12} /> …</div>}
      </div>
      <div className="p-2 border-t border-tessera-border">
        <div className="flex gap-1.5">
          <input value={input} onChange={e => setInput(e.target.value)}
            onKeyDown={e => e.key === "Enter" && send()}
            placeholder="Ask, or 'bring up …'"
            className="flex-1 bg-gray-900 border border-tessera-border rounded px-2.5 py-1.5 text-[11px] text-white placeholder-gray-500" />
          <button onClick={send} disabled={loading || !input.trim()}
            className="px-2.5 py-1.5 bg-tessera-gradient rounded text-[10px] font-semibold disabled:opacity-40">↑</button>
        </div>
      </div>
    </div>
  );
}
