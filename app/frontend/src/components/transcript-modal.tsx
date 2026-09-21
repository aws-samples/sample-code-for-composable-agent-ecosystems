"use client";

// Reusable conversation-transcript viewer. Used for both the mission transcript
// (run history) and the builder transcript (ecosystem details page). Shows a
// "Reconstructed" badge when the transcript was synthesized rather than captured live.
import ReactMarkdown from "react-markdown";

export interface TranscriptMessage {
  role: string;
  content: string;
}

export function TranscriptModal({
  title,
  subtitle,
  messages,
  reconstructed,
  onClose,
}: {
  title: string;
  subtitle?: string;
  messages: TranscriptMessage[];
  reconstructed?: boolean;
  onClose: () => void;
}) {
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-[60]" onClick={onClose}>
      <div className="bg-tessera-card border border-tessera-border rounded-xl w-[680px] max-w-[92vw] max-h-[85vh] flex flex-col" onClick={(e) => e.stopPropagation()}>
        <div className="flex items-start justify-between p-4 border-b border-tessera-border">
          <div>
            <div className="flex items-center gap-2">
              <h3 className="text-sm font-semibold text-white">{title}</h3>
              {reconstructed && (
                <span
                  title="This conversation was not captured live — it was realistically reconstructed from the saved artifacts."
                  className="text-[9px] uppercase tracking-wide px-1.5 py-0.5 rounded-full bg-amber-500/15 text-amber-300 border border-amber-500/30"
                >
                  ✨ Reconstructed
                </span>
              )}
            </div>
            {subtitle && <p className="text-[11px] text-gray-500 mt-0.5">{subtitle}</p>}
          </div>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-lg leading-none">✕</button>
        </div>

        <div className="flex-1 overflow-y-auto p-4 space-y-3">
          {messages.length === 0 ? (
            <p className="text-xs text-gray-500">No conversation recorded.</p>
          ) : (
            messages.map((m, i) => {
              const isUser = m.role === "user";
              return (
                <div key={i} className={`flex ${isUser ? "justify-end" : "justify-start"}`}>
                  <div className={`max-w-[85%] rounded-lg px-3 py-2 text-xs leading-relaxed ${isUser ? "bg-tessera-accent/15 border border-tessera-accent/25 text-gray-100" : "bg-gray-800/60 border border-tessera-border text-gray-300"}`}>
                    <div className="text-[9px] uppercase tracking-wide text-gray-500 mb-1">{isUser ? "You" : "Assistant"}</div>
                    <div className="prose prose-invert prose-xs max-w-none [&_p]:my-1 [&_ul]:my-1 [&_li]:my-0">
                      <ReactMarkdown>{m.content || ""}</ReactMarkdown>
                    </div>
                  </div>
                </div>
              );
            })
          )}
        </div>
      </div>
    </div>
  );
}
