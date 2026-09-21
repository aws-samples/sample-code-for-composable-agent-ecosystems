"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Persistent WebSocket connection used for server-push events that aren't tied
 * to a specific run (e.g. SUGGEST_RESULT). Generates a stable clientId per tab,
 * registers it with the backend, and exposes an event subscription mechanism.
 */

type ServerEvent = { type: string; [key: string]: unknown };
type Listener = (event: ServerEvent) => void;

function getOrCreateClientId(): string {
  if (typeof window === "undefined") return "";
  const key = "tessera.clientId";
  let id = window.sessionStorage.getItem(key);
  if (!id) {
    id = crypto.randomUUID();
    window.sessionStorage.setItem(key, id);
  }
  return id;
}

export function useClientWebSocket(wsUrl: string) {
  const [clientId] = useState<string>(() => getOrCreateClientId());
  const [connected, setConnected] = useState(false);
  const wsRef = useRef<WebSocket | null>(null);
  const listenersRef = useRef<Set<Listener>>(new Set());
  const reconnectAttemptsRef = useRef(0);

  const subscribe = useCallback((listener: Listener) => {
    listenersRef.current.add(listener);
    return () => {
      listenersRef.current.delete(listener);
    };
  }, []);

  useEffect(() => {
    if (!wsUrl || !clientId) return;
    let cancelled = false;
    let reconnectTimer: ReturnType<typeof setTimeout> | null = null;

    const open = async () => {
      // The $connect authorizer requires the Cognito ID token on the handshake query string.
      // Fetch it first; if the session isn't ready yet, back off and retry rather than open a
      // tokenless socket that is guaranteed to be 401'd at $connect.
      let token = "";
      try {
        const { fetchAuthSession } = await import("aws-amplify/auth");
        token = (await fetchAuthSession({ forceRefresh: false })).tokens?.idToken?.toString() || "";
      } catch { /* session not hydrated yet */ }
      if (cancelled) return;
      if (!token) {
        const delay = Math.min(10_000, 500 * Math.pow(2, reconnectAttemptsRef.current));
        reconnectAttemptsRef.current += 1;
        reconnectTimer = setTimeout(open, delay);
        return;
      }

      const ws = new WebSocket(`${wsUrl}${wsUrl.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`);
      wsRef.current = ws;

      ws.onopen = () => {
        reconnectAttemptsRef.current = 0;
        setConnected(true);
        ws.send(JSON.stringify({ action: "register", clientId }));
      };

      ws.onmessage = (msg) => {
        try {
          const event: ServerEvent = JSON.parse(msg.data);
          listenersRef.current.forEach((l) => l(event));
        } catch {
          // ignore malformed events
        }
      };

      ws.onclose = () => {
        setConnected(false);
        if (cancelled) return;
        // Exponential backoff reconnect, capped at 30s
        const delay = Math.min(30_000, 1000 * Math.pow(2, reconnectAttemptsRef.current));
        reconnectAttemptsRef.current += 1;
        reconnectTimer = setTimeout(open, delay);
      };

      ws.onerror = () => {
        ws.close();
      };
    };

    open();

    return () => {
      cancelled = true;
      if (reconnectTimer) clearTimeout(reconnectTimer);
      wsRef.current?.close();
    };
  }, [wsUrl, clientId]);

  return { clientId, connected, subscribe };
}
