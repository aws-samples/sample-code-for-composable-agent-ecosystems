"use client";

import { useState, useRef, useEffect, useCallback } from "react";
import ReactMarkdown from "react-markdown";
import { TesseraSpinner } from "./tessera-spinner";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";
const WS_URL = process.env.NEXT_PUBLIC_WS_URL || API_URL.replace(/^http/, "ws");

async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const { fetchAuthSession } = await import("aws-amplify/auth");
  let token = "";
  try {
    const session = await fetchAuthSession({ forceRefresh: false });
    token = session.tokens?.idToken?.toString() || "";
  } catch {}
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(url, { ...init, headers });
}

interface AskResponse { agent_id?: string; agent_name?: string; response: string; handoff_from?: string }

/**
 * Ask a run question over the WebSocket (async). The REST /chat route is capped by API
 * Gateway's 29s integration timeout, which report-generation questions exceed; the WS path
 * has no such cap — the chat Lambda pushes ASK_RESPONSE back to this same connection when
 * done. Opens a dedicated short-lived socket for the one question, resolves on ASK_RESPONSE,
 * rejects on ASK_ERROR / timeout. The caller falls back to REST if this rejects fast.
 */
function askOverWebSocket(sessionId: string, question: string, targetAgent: string, token: string): Promise<AskResponse> {
  return new Promise((resolve, reject) => {
    let settled = false;
    let ws: WebSocket;
    // Token goes on the handshake URL — the $connect authorizer checks it there, before any
    // frame. (It's also sent in the chat frame below for per-message verification.)
    if (!token) { reject(new Error("not signed in")); return; }
    const wsUrl = `${WS_URL}${WS_URL.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
    try { ws = new WebSocket(wsUrl); } catch (e) { reject(e); return; }
    // Generous ceiling — well beyond the 29s REST cap, under the report-gen worst case.
    const timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      try { ws.close(); } catch { /* ignore */ }
      reject(new Error("timed out waiting for the answer"));
    }, 180_000);
    const finish = (fn: () => void) => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      try { ws.close(); } catch { /* ignore */ }
      fn();
    };
    ws.onopen = () => {
      ws.send(JSON.stringify({ action: "chat", session_id: sessionId, question, target_agent: targetAgent || undefined, token }));
    };
    ws.onmessage = (msg) => {
      let ev: any;
      try { ev = JSON.parse(msg.data); } catch { return; }
      if (ev.type === "ASK_RESPONSE" && ev.session_id === sessionId) {
        const r = (ev.responses || [])[0];
        finish(() => (r ? resolve(r) : reject(new Error("empty response"))));
      } else if (ev.type === "ASK_ERROR" && ev.session_id === sessionId) {
        finish(() => reject(new Error(ev.error || "ask failed")));
      }
      // ASK_STARTED is an ack — ignored (the UI already shows a thinking state).
    };
    ws.onerror = () => finish(() => reject(new Error("websocket error")));
  });
}

interface Turn {
  role: "user" | "assistant";
  content: string;
  agent_id?: string;
  agent_name?: string;
  handoff_from?: string;
}

/**
 * AskPanel — the single agent-routed run-Q&A surface.
 *
 * Posts to `/api/runs/{sid}/chat`, which routes the question to the ecosystem's
 * own agents (auto-route or pinned) and answers grounded in the run. This is the
 * one implementation shared by the run-window/history widget (RunChat) and the
 * report Studio's "Ask" tab — no duplicate chat brains.
 */
export function AskPanel({ sessionId, className = "", onSpotlight }:
  { sessionId: string; className?: string; onSpotlight?: (text: string) => void }) {
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const [turns, setTurns] = useState<Turn[]>([]);
  const [agents, setAgents] = useState<{ id: string; name: string }[]>([]);
  const [target, setTarget] = useState("");
  const [loadedFor, setLoadedFor] = useState<string | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);

  const agentName = useCallback((id?: string) => agents.find((a) => a.id === id)?.name || id || "", [agents]);

  const chatPost = useCallback(
    (extra: Record<string, unknown>) =>
      apiFetch(`${API_URL}/api/runs/${encodeURIComponent(sessionId)}/chat`, {
        method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(extra),
      }),
    [sessionId],
  );

  useEffect(() => {
    if (!sessionId || loadedFor === sessionId) return;
    setLoadedFor(sessionId);
    (async () => {
      try {
        const r = await chatPost({ action: "load" });
        const d = await r.json().catch(() => ({}));
        if (Array.isArray(d.agents)) setAgents(d.agents);
        if (Array.isArray(d.history)) {
          setTurns(d.history.map((t: any) => ({
            role: t.role === "user" ? "user" : "assistant", content: t.content, agent_id: t.agent_id, agent_name: t.agent_id,
          })));
        }
      } catch { /* ignore */ }
    })();
  }, [sessionId, loadedFor, chatPost]);

  useEffect(() => {
    if (scrollRef.current) scrollRef.current.scrollTop = scrollRef.current.scrollHeight;
  }, [turns, loading]);

  const send = async () => {
    if (!input.trim() || !sessionId || loading) return;
    const question = input;
    setInput("");
    setTurns((prev) => [...prev, { role: "user", content: question }]);
    setLoading(true);
    // Prefer the async WebSocket path (no 29s cap — report-generation answers take a
    // minute+). Fall back to the REST endpoint only if the WS attempt fails to connect
    // (e.g. token unavailable), accepting the 29s cap for that degraded case.
    const applyResponse = (r: AskResponse) => {
      setTurns((prev) => [...prev, { role: "assistant", content: r.response, agent_id: r.agent_id, agent_name: r.agent_name, handoff_from: r.handoff_from || "" }]);
      if (r.agent_id) setTarget((t) => (t ? r.agent_id! : t));
    };
    try {
      const { fetchAuthSession } = await import("aws-amplify/auth");
      let token = "";
      try { token = (await fetchAuthSession({ forceRefresh: false })).tokens?.idToken?.toString() || ""; } catch { /* fall back */ }

      if (token) {
        try {
          const r = await askOverWebSocket(sessionId, question, target, token);
          applyResponse(r);
          return;
        } catch (wsErr) {
          // WS attempt failed — fall through to REST (bounded by 29s, but better than nothing).
          console.warn("Ask over WebSocket failed, falling back to REST:", wsErr);
        }
      }

      const resp = await chatPost({ question, target_agent: target || undefined });
      const data = await resp.json().catch(() => ({}));
      const r = (data.responses || [])[0];
      if (r) applyResponse(r);
      else setTurns((prev) => [...prev, { role: "assistant", content: `(no response${data.error ? ": " + data.error : ""})` }]);
    } catch (e) {
      setTurns((prev) => [...prev, { role: "assistant", content: `(request failed: ${e})` }]);
    } finally {
      setLoading(false);
    }
  };

  const clearHistory = async () => {
    try { await chatPost({ action: "clear" }); } catch { /* ignore */ }
    setTurns([]);
  };

  return (
    <div className={`flex flex-col min-h-0 ${className}`}>
      <div className="flex items-center gap-2 px-3 py-1.5 border-b border-tessera-border/60">
        <span className="text-[10px] text-gray-500">Ask:</span>
        <select
          value={target}
          onChange={(e) => setTarget(e.target.value)}
          className="bg-gray-900 border border-tessera-border rounded px-1.5 py-1 text-[10px] text-gray-200 max-w-[55%]"
        >
          <option value="">Auto-route (recommended)</option>
          {agents.map((a) => <option key={a.id} value={a.id}>{a.name}</option>)}
        </select>
        {target && <span className="text-[9px] text-tessera-accent">pinned</span>}
        {turns.length > 0 && (
          <button onClick={clearHistory} className="ml-auto text-[10px] text-gray-500 hover:text-red-300" title="Clear conversation history">Clear</button>
        )}
      </div>

      <div ref={scrollRef} className="flex-1 overflow-y-auto p-3 space-y-3">
        {turns.length === 0 && (
          <p className="text-[11px] text-gray-500 leading-relaxed">
            Ask the agents that produced this run — Auto-route picks the right agent and keeps the
            thread with them; pick a specific agent to pin. Answers are grounded in the run; history is saved.
          </p>
        )}
        {turns.map((t, i) => (
          t.role === "user" ? (
            <div key={i} className="text-[10px] text-gray-400 text-right">You: {t.content}</div>
          ) : (
            <div key={i} className="border-l-2 border-tessera-accent/30 pl-2">
              {t.handoff_from && (
                <div className="text-[9px] text-amber-300/90 mb-0.5">↪ {agentName(t.handoff_from)} handed off to {t.agent_name || agentName(t.agent_id)}</div>
              )}
              <span className="text-[9px] text-tessera-accent font-semibold">{t.agent_name || agentName(t.agent_id)}</span>
              <div className="text-[11px] text-gray-300 prose prose-invert prose-xs max-w-none prose-p:my-0.5">
                <ReactMarkdown>{t.content}</ReactMarkdown>
              </div>
              {onSpotlight && (
                <button
                  onClick={() => onSpotlight(t.content)}
                  title="Highlight the related passage in the report"
                  className="mt-0.5 text-[9px] text-purple-300 hover:text-purple-200"
                >
                  🔦 Spotlight in report
                </button>
              )}
            </div>
          )
        ))}
        {loading && <div className="flex items-center gap-1.5 text-[10px] text-gray-500"><TesseraSpinner size={12} /> Thinking…</div>}
      </div>

      <div className="p-2 border-t border-tessera-border">
        <div className="flex gap-1.5">
          <input
            value={input}
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && send()}
            placeholder={target ? `Ask ${agentName(target)}…` : "Ask about this run…"}
            className="flex-1 bg-gray-900 border border-tessera-border rounded px-2.5 py-1.5 text-[11px] text-white placeholder-gray-500"
          />
          <button onClick={send} disabled={loading || !input.trim()}
            className="px-2.5 py-1.5 bg-tessera-gradient rounded text-[10px] font-semibold disabled:opacity-40">↑</button>
        </div>
      </div>
    </div>
  );
}
