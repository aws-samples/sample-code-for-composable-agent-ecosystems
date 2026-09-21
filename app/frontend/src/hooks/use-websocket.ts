"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Single shared WebSocket connection for all run tabs.
 * Routes incoming AG-UI events to registered listeners by session_id.
 * Handles reconnect with exponential backoff and session re-registration.
 */

type EventListener = (event: any) => void;

interface UseWebSocketOptions {
  url: string;
}

export function useWebSocket({ url }: UseWebSocketOptions) {
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const listenersRef = useRef<Map<string, EventListener>>(new Map());
  const globalListenersRef = useRef<Set<EventListener>>(new Set());
  const reconnectRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const reconnectAttempts = useRef(0);
  const pingRef = useRef<ReturnType<typeof setInterval> | null>(null);
  const activeSessionsRef = useRef<Set<string>>(new Set());

  const connect = useCallback(() => {
    if (!url) return;
    // Idempotent: don't open a second socket if one is already live or in progress.
    // Without this guard, every re-render / StrictMode double-invoke that re-ran connect
    // spawned a new socket (and the mount cleanup closed the previous one), producing the
    // connect→disconnect churn that left no OPEN socket when the user clicked Launch.
    const existing = wsRef.current?.readyState;
    if (existing === WebSocket.OPEN || existing === WebSocket.CONNECTING) return;

    // The WS API authorizes the $connect handshake via a Cognito ID token passed as the
    // `token` query-string param (browsers can't set WS handshake headers). Fetch it first,
    // then open the socket. Per-message frames still carry the token (defense in depth).
    void (async () => {
      let token = "";
      try {
        const { fetchAuthSession } = await import("aws-amplify/auth");
        token = (await fetchAuthSession({ forceRefresh: false })).tokens?.idToken?.toString() || "";
      } catch { /* session not hydrated yet — handled below */ }

      // The $connect authorizer requires the token on the handshake query string. Opening a
      // tokenless socket is guaranteed to be rejected (401) and just churns, so instead we
      // wait and retry until the Amplify session is available (e.g. right after page load).
      if (!token) {
        const delay = Math.min(10000, 500 * Math.pow(2, reconnectAttempts.current));
        reconnectAttempts.current += 1;
        if (reconnectRef.current) clearTimeout(reconnectRef.current);
        reconnectRef.current = setTimeout(connect, delay);
        return;
      }

      // Guard again after the await (a socket may have opened while we fetched the token).
      const nowState = wsRef.current?.readyState;
      if (nowState === WebSocket.OPEN || nowState === WebSocket.CONNECTING) return;

      const base = url.replace(/^http/, "ws");
      const wsUrl = `${base}${base.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
      const ws = new WebSocket(wsUrl);
      wsRef.current = ws;

      ws.onopen = () => {
      setConnected(true);
      reconnectAttempts.current = 0;
      // Re-register all active sessions on reconnect
      for (const sid of activeSessionsRef.current) {
        ws.send(JSON.stringify({ action: "register", clientId: crypto.randomUUID(), sessionId: sid }));
      }
      // Also re-register sessions from persisted run tabs (survives page refresh)
      try {
        const stored = localStorage.getItem("tessera.runTabs");
        if (stored) {
          const tabs = JSON.parse(stored) as { id: string }[];
          for (const tab of tabs) {
            if (tab.id && !activeSessionsRef.current.has(tab.id)) {
              activeSessionsRef.current.add(tab.id);
              ws.send(JSON.stringify({ action: "register", clientId: crypto.randomUUID(), sessionId: tab.id }));
            }
          }
        }
      } catch {}
      // Keepalive ping every 5 min
      if (pingRef.current) clearInterval(pingRef.current);
      pingRef.current = setInterval(() => {
        if (ws.readyState === WebSocket.OPEN) {
          try { ws.send(JSON.stringify({ action: "ping" })); } catch {}
        }
      }, 5 * 60 * 1000);
    };

    ws.onmessage = (msg) => {
      try {
        const event = JSON.parse(msg.data);
        const sid = event.session_id || "";
        // Route to the specific session listener if matched
        if (sid && listenersRef.current.has(sid)) {
          listenersRef.current.get(sid)!(event);
        } else {
          // No session_id or no matching listener — send to all listeners
          listenersRef.current.forEach((listener) => listener(event));
        }
        // Always notify global listeners
        globalListenersRef.current.forEach((l) => l(event));
      } catch {}
    };

    ws.onclose = () => {
      setConnected(false);
      if (pingRef.current) { clearInterval(pingRef.current); pingRef.current = null; }
      // Reconnect with exponential backoff if there are active sessions
      if (activeSessionsRef.current.size > 0) {
        const delay = Math.min(30000, 1000 * Math.pow(2, reconnectAttempts.current));
        reconnectAttempts.current += 1;
        reconnectRef.current = setTimeout(connect, delay);
      }
    };

      ws.onerror = () => { ws.close(); };
    })();
  }, [url]);

  // Connect once on mount. Depend on [url], NOT [connect]: connect's identity can change
  // across renders, and tearing the socket down + reopening on every such change caused
  // the connect→disconnect thrash (so the socket was often CLOSED when Launch fired). We
  // only close the socket on real unmount / url change. connect() is idempotent (guarded),
  // so calling it again is a no-op when a socket is already live.
  useEffect(() => {
    connect();
    return () => {
      if (reconnectRef.current) clearTimeout(reconnectRef.current);
      if (pingRef.current) clearInterval(pingRef.current);
      wsRef.current?.close();
      wsRef.current = null;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [url]);

  /** Register a listener for events with a specific session_id */
  const subscribe = useCallback((sessionId: string, listener: EventListener) => {
    listenersRef.current.set(sessionId, listener);
    activeSessionsRef.current.add(sessionId);
    // Register session on the WebSocket so backend routes events to us
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify({ action: "register", clientId: crypto.randomUUID(), sessionId }));
    }
    return () => {
      listenersRef.current.delete(sessionId);
      activeSessionsRef.current.delete(sessionId);
      // Unregister from backend
      if (wsRef.current?.readyState === WebSocket.OPEN) {
        wsRef.current.send(JSON.stringify({ action: "unregister", sessionId }));
      }
    };
  }, []);

  /** Register a global listener (receives all events regardless of session) */
  const subscribeGlobal = useCallback((listener: EventListener) => {
    globalListenersRef.current.add(listener);
    return () => { globalListenersRef.current.delete(listener); };
  }, []);

  /** Send a message (launch, ping, etc.) */
  const send = useCallback((data: Record<string, unknown>) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) {
      wsRef.current.send(JSON.stringify(data));
    }
  }, []);

  /** Wait (up to ~10s) until the shared socket is OPEN, reconnecting if it's closed. */
  const ensureOpen = useCallback(async (): Promise<boolean> => {
    for (let i = 0; i < 40; i++) {
      const s = wsRef.current?.readyState;
      if (s === WebSocket.OPEN) return true;
      if (s === undefined || s === WebSocket.CLOSED || s === WebSocket.CLOSING) {
        try { connect(); } catch { /* retry below */ }
      }
      await new Promise((r) => setTimeout(r, 250));
    }
    return wsRef.current?.readyState === WebSocket.OPEN;
  }, [connect]);

  /** Launch an ecosystem run — sends launch message and returns the session_id from RUN_STARTED */
  const launch = useCallback((ecosystemId: string, mission: string, inputs?: Record<string, string>, userId?: string, dryRun?: boolean, runConfig?: Record<string, unknown>, attachments?: Array<{ name: string; ref: string }>, missionTranscript?: Array<{ role: string; content: string }>): Promise<string> => {
    return new Promise((resolve, reject) => {
      doLaunch(resolve, reject, ecosystemId, mission, inputs, userId, dryRun, runConfig, attachments, missionTranscript);
    });
  }, []);

  const doLaunch = async (resolve: (sid: string) => void, reject: (e: Error) => void, ecosystemId: string, mission: string, inputs?: Record<string, string>, userId?: string, dryRun?: boolean, runConfig?: Record<string, unknown>, attachments?: Array<{ name: string; ref: string }>, missionTranscript?: Array<{ role: string; content: string }>) => {
    let settled = false;
    const handler = (event: any) => {
      if (event.type === "RUN_STARTED" && event.ecosystem === ecosystemId) {
        globalListenersRef.current.delete(handler);
        const sid = event.session_id || "";
        if (sid) {
          activeSessionsRef.current.add(sid);
          if (!settled) { settled = true; resolve(sid); }
        } else if (!settled) {
          settled = true; reject(new Error("RUN_STARTED without session_id"));
        }
      }
    };
    globalListenersRef.current.add(handler);
    setTimeout(() => {
      globalListenersRef.current.delete(handler);
      if (!settled) { settled = true; reject(new Error("Launch timeout")); }
    }, 30000);

    // Fetch the token FIRST (async, can take a moment), THEN ensure the socket is OPEN
    // immediately before sending. The previous order (check-open → await token → send) had
    // a race: the socket could idle-close during the token await, so the send landed on a
    // CLOSED socket and the launch frame never reached the backend (the run appeared to
    // "start" in the UI but no SFN execution began). Re-verifying + waiting for OPEN right
    // before the send closes that gap.
    let token = "";
    try {
      const { fetchAuthSession } = await import("aws-amplify/auth");
      const session = await fetchAuthSession({ forceRefresh: false });
      token = session.tokens?.idToken?.toString() || "";
    } catch { /* proceed without token — backend will reject and we surface it */ }

    const ok = await ensureOpen();
    if (!ok || !wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
      globalListenersRef.current.delete(handler);
      if (!settled) { settled = true; reject(new Error("WebSocket not connected")); }
      return;
    }

    try {
      wsRef.current.send(JSON.stringify({
        action: "launch",
        ecosystem_id: ecosystemId,
        mission,
        inputs: inputs || {},
        user_id: userId || "",
        dry_run: !!dryRun,
        run_config: runConfig || {},
        attachments: attachments || [],
        mission_transcript: missionTranscript || [],
        token,
      }));
    } catch (e) {
      globalListenersRef.current.delete(handler);
      if (!settled) { settled = true; reject(new Error("launch send failed: " + e)); }
    }
  };

  return { connected, subscribe, subscribeGlobal, send, launch };
}
