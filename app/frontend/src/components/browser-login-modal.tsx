"use client";
// Interactive sign-in modal for the authenticated-browser (browser_login) gate.
// It starts a live browser session (browser_login_open), lets the operator sign
// into an auth-gated site, then saves the session into a profile and RESUMES the
// paused run authenticated (browser_login_done) — or Skip to proceed without it.
//
// The in-app live view uses AgentCore's <BrowserLiveView>, which internally
// imports the Amazon DCV Web Client SDK ("dcv" + "dcv-ui"). "dcv-ui" is NOT on
// public npm (it's a license-gated AWS download), so we load the viewer at
// RUNTIME and degrade gracefully when the SDK hasn't been vendored — keeping the
// build green and the gate (Skip/Done) fully functional. See
// docs/features/browser-tool.md.
import { useEffect, useRef, useState } from "react";
import dynamic from "next/dynamic";

// The DCV-backed live viewer. Resolved via webpack aliases in next.config.js
// (bedrock-agentcore/browser/live-view + the vendored dcv/dcv-ui). Loaded
// client-side only (ssr:false) — DCV touches window/WebSocket/WASM.
const BrowserLiveView = dynamic(
  () => import("bedrock-agentcore/browser/live-view").then((m) => ({ default: m.BrowserLiveView })),
  { ssr: false, loading: () => <div className="text-sm text-gray-400 animate-pulse py-20">Loading live viewer…</div> },
);

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

type LiveViewProps = { signedUrl: string; remoteWidth?: number; remoteHeight?: number };

export function BrowserLoginModal({
  apiUrl, sessionId, agentId, domain, onClose,
}: {
  apiUrl: string; sessionId: string; agentId: string; domain?: string;
  onClose: (resolved: boolean) => void;
}) {
  const [phase, setPhase] = useState<"opening" | "ready" | "working" | "error">("opening");
  const [signedUrl, setSignedUrl] = useState("");
  const [dims, setDims] = useState({ w: 1440, h: 900 });
  const [err, setErr] = useState("");
  const openedRef = useRef(false);

  const call = async (action: "browser_login_open" | "browser_login_done" | "browser_login_skip") => {
    const r = await apiFetch(`${apiUrl}/api/runs/${encodeURIComponent(sessionId)}/gate-decision`, {
      method: "POST", headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ action, session_id: sessionId, agent_id: agentId }),
    });
    let d: any = {}; try { d = await r.json(); } catch { /* non-json */ }
    return { ok: r.ok, d };
  };

  // Start the live sign-in session once, on open.
  useEffect(() => {
    if (openedRef.current) return;
    openedRef.current = true;
    (async () => {
      const { ok, d } = await call("browser_login_open");
      if (ok && d.live_view_url) {
        setSignedUrl(d.live_view_url);
        setDims({ w: d.remote_width || 1440, h: d.remote_height || 900 });
        setPhase("ready");
      } else {
        setErr(d.error || "Could not start the sign-in session.");
        setPhase("error");
      }
    })();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  const finish = async (action: "browser_login_done" | "browser_login_skip") => {
    setPhase("working");
    const { ok, d } = await call(action);
    if (ok) onClose(true);
    else { setErr(d.error || "Action failed."); setPhase("ready"); }
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-[60] p-4" onClick={() => onClose(false)}>
      <div className="bg-tessera-card border border-tessera-border rounded-xl w-[92vw] max-w-[1200px] flex flex-col" style={{ maxHeight: "88vh" }} onClick={(e) => e.stopPropagation()}>
        <div className="flex items-center justify-between px-4 py-3 border-b border-tessera-border shrink-0">
          <div>
            <div className="text-sm font-bold">Sign in to {domain || "the web app"}</div>
            <div className="text-[11px] text-gray-500">The run is paused. Sign in (SSO/MFA fine), then Done to resume authenticated — or Skip to proceed without it.</div>
            {domain && (
              <div className="text-[10px] text-gray-500 mt-1">
                If the browser isn&apos;t on the sign-in page, go to{" "}
                <button
                  type="button"
                  onClick={() => { try { navigator.clipboard.writeText(`https://${domain}/`); } catch {} }}
                  className="font-mono text-tessera-accent hover:underline"
                  title="Copy URL"
                >https://{domain}/</button>{" "}in the browser above (Ctrl/⌘+L, paste, Enter).
              </div>
            )}
          </div>
          <button onClick={() => onClose(false)} className="text-gray-500 hover:text-white text-lg leading-none px-2">✕</button>
        </div>

        <div className="flex-1 min-h-0 bg-black/40 flex items-center justify-center overflow-hidden" style={{ minHeight: "45vh" }}>
          {phase === "opening" && <div className="text-sm text-gray-400 animate-pulse py-20">Starting a secure browser session…</div>}
          {phase === "error" && (
            <div className="text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded p-4 m-6 max-w-lg">
              {err}
              <div className="mt-3"><button onClick={() => onClose(false)} className="px-3 py-1.5 text-xs rounded bg-white/5 border border-tessera-border">Close</button></div>
            </div>
          )}
          {(phase === "ready" || phase === "working") && signedUrl && (
            <div className="w-full h-full" style={{ minHeight: "45vh" }}>
              <BrowserLiveView signedUrl={signedUrl} remoteWidth={dims.w} remoteHeight={dims.h} />
            </div>
          )}
        </div>

        {(phase === "ready" || phase === "working") && (
          <div className="flex items-center gap-2 px-4 py-3 border-t border-tessera-border shrink-0">
            <button onClick={() => finish("browser_login_done")} disabled={phase === "working"}
              className="px-4 py-2 text-sm rounded-md bg-tessera-gradient font-semibold disabled:opacity-50">
              {phase === "working" ? "Saving…" : "Done, I’m signed in"}
            </button>
            <button onClick={() => finish("browser_login_skip")} disabled={phase === "working"}
              className="px-3 py-2 text-xs rounded-md bg-white/5 text-gray-300 border border-tessera-border hover:bg-white/10">
              Skip (proceed without signing in)
            </button>
            <span className="ml-auto text-[10px] text-gray-600">The live session expires after ~5 min.</span>
          </div>
        )}
      </div>
    </div>
  );
}
