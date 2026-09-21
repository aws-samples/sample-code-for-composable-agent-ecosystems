"use client";

import { useState } from "react";
import { EventStream } from "@/components/event-stream";
import { AGUIEvent } from "@/lib/ag-ui-types";
import { AskPanel } from "./ask-panel";

/**
 * RunChat — the floating run-scoped widget (run window + history viewer).
 *
 * Its "Chat" tab is the shared {@link AskPanel} (agent-routed run Q&A — the same
 * engine used by the report Studio's Ask tab). "Events" shows the AG-UI stream.
 *
 * When `hideChat` is set (the run has a report Studio — i.e. a produced HTML
 * deliverable — which already hosts Ask), the widget drops its Chat tab and shows
 * Events only, so there's a single Ask surface. Report-only runs keep Chat here.
 */
export function RunChat({ sessionId, events = [], hideChat = false }:
  { sessionId: string; events?: AGUIEvent[]; hideChat?: boolean }) {
  const [open, setOpen] = useState(false);
  const [tab, setTab] = useState<"chat" | "events">(hideChat ? "events" : "chat");
  const [maximized, setMaximized] = useState(false);

  if (!sessionId) return null;

  if (!open) {
    return (
      <button onClick={() => setOpen(true)}
        className="fixed bottom-5 right-5 w-12 h-12 bg-tessera-gradient rounded-full shadow-lg shadow-purple-500/30 flex items-center justify-center text-lg hover:scale-105 transition z-50"
        title={hideChat ? "Run events" : "Chat with agents"}>{hideChat ? "📡" : "💬"}</button>
    );
  }

  const frame = maximized
    ? "fixed inset-4 md:inset-10 z-50"
    : "fixed bottom-5 right-5 w-96 h-[28rem] z-50";
  const showChat = !hideChat && tab === "chat";

  return (
    <div className={`${frame} bg-tessera-card border border-tessera-border rounded-xl shadow-2xl shadow-black/50 flex flex-col`}>
      <div className="flex items-center justify-between px-3 py-2 border-b border-tessera-border">
        <div className="flex gap-2 items-center">
          {!hideChat && (
            <button onClick={() => setTab("chat")} className={`text-[10px] font-bold px-2 py-0.5 rounded ${tab === "chat" ? "bg-tessera-accent/20 text-tessera-accent" : "text-gray-500"}`}>Chat</button>
          )}
          <button onClick={() => setTab("events")} className={`text-[10px] font-bold px-2 py-0.5 rounded ${(tab === "events" || hideChat) ? "bg-tessera-accent/20 text-tessera-accent" : "text-gray-500"}`}>Events</button>
        </div>
        <div className="flex items-center gap-2">
          <button onClick={() => setMaximized((m) => !m)} className="text-gray-500 hover:text-white text-xs" title={maximized ? "Restore" : "Maximize"}>{maximized ? "🗗" : "🗖"}</button>
          <button onClick={() => setOpen(false)} className="text-gray-500 hover:text-white text-sm" title="Minimize">—</button>
        </div>
      </div>

      {showChat ? (
        <AskPanel sessionId={sessionId} className="flex-1" />
      ) : (
        <div className="flex-1 overflow-y-auto p-2">
          {hideChat && (
            <div className="text-[10px] text-gray-500 mb-2 px-1">💬 To ask the agents, open the deliverable → <span className="text-tessera-accent">✨ Beautify</span> → <span className="text-tessera-accent">Ask</span>.</div>
          )}
          <EventStream events={events} />
        </div>
      )}
    </div>
  );
}
