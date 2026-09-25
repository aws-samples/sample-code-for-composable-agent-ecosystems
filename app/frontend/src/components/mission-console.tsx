"use client";

// MissionConsole — the conversational (file-aware) mission-capture surface,
// extracted VERBATIM (logic-wise) from app/page.tsx's run modal so it can be
// reused by the single-ecosystem /solo export. It converses to shape a mission
// (mode "chat", the default) with a one-shot "type it myself" escape hatch
// (mode "quick"), supports optional run-scoped file attachments, and calls
// `onConverged(mission, inputs, attachments, transcript)` when the operator
// launches.
//
// Backend contract (unchanged from the platform):
//   POST {apiUrl}/api/ecosystems/suggest  { mode:"mission", message, history,
//        mission_state, eco_context, attachments:[{name,ref}], clientId }
//        -> 202 { jobId, status:"pending" }  (streamed back over the client WS)
//   client WS: CONVERSE_DELTA { jobId, text } / CONVERSE_RESULT { jobId, reply,
//        mission_state, next_question, converged, mission, inputs }
//   POST {apiUrl}/api/uploads/presign { filename, sessionId:draftId, type:"run",
//        contentType } -> { uploadUrl, s3Uri }  (then PUT the file to uploadUrl)
//
// apiUrl / wsUrl are passed in so the platform build uses build-time env while the
// exported /solo build passes runtime-config (getApiUrl()/getWsUrl()) values.

import { useEffect, useRef, useState } from "react";
import { useClientWebSocket } from "@/hooks/use-client-ws";
import { DecisionSurface } from "@/components/decision-surface";
import type { DecisionPoint } from "@/lib/decision";
import { getIdToken } from "@/lib/auth";

export interface MissionEco {
  id: string;
  name: string;
  description?: string;
  inputs?: any[];
  outcome?: any;
}

async function authFetch(url: string, init?: RequestInit): Promise<Response> {
  const token = await getIdToken();
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(url, { ...init, headers });
}

export function MissionConsole({
  eco,
  apiUrl,
  wsUrl,
  onConverged,
}: {
  eco: MissionEco;
  apiUrl: string;
  wsUrl: string;
  onConverged: (
    mission: string,
    inputs: Record<string, string>,
    attachments: Array<{ name: string; ref: string }>,
    transcript: Array<{ role: string; content: string }>,
  ) => void;
}) {
  const [missionMode, setMissionMode] = useState<"chat" | "quick">("chat");
  const [mission, setMission] = useState("");
  const [inputValues, setInputValues] = useState<Record<string, string>>({});
  const [attachments, setAttachments] = useState<Array<{ name: string; content: string }>>([]);
  const [attachError, setAttachError] = useState<string | null>(null);
  const [mConvo, setMConvo] = useState<{ role: "user" | "assistant"; content: string }[]>([]);
  const [mState, setMState] = useState<any>({});
  const [mQuestion, setMQuestion] = useState<{ id?: string; text: string; options?: string[] } | null>(null);
  const [mStream, setMStream] = useState("");
  const [mBusy, setMBusy] = useState(false);
  const [mConverged, setMConverged] = useState(false);
  const [mFinalMission, setMFinalMission] = useState("");
  const [mFinalInputs, setMFinalInputs] = useState<Record<string, string>>({});
  const [mJobId, setMJobId] = useState<string | null>(null);
  const [mError, setMError] = useState<string | null>(null);
  const [mInput, setMInput] = useState("");
  const mEndRef = useRef<HTMLDivElement>(null);
  const mScrollRef = useRef<HTMLDivElement>(null);
  const { clientId: mClientId, connected: mWsConnected, subscribe: mSubscribe } = useClientWebSocket(wsUrl);
  // Per-mount draft id so optional run uploads land in a run-scoped S3 prefix.
  // Use a crypto RNG (not Math.random) so the id is unpredictable.
  const [draftId] = useState(() => `draft-${crypto.randomUUID().replace(/-/g, "").slice(0, 8)}`);

  useEffect(() => {
    return mSubscribe((event: any) => {
      if (event.type === "CONVERSE_DELTA") {
        if (event.jobId !== mJobId) return;
        setMStream((s) => s + (event.text || ""));
      } else if (event.type === "CONVERSE_RESULT") {
        if (event.jobId !== mJobId) return;
        setMBusy(false); setMJobId(null);
        if (event.error) { setMError(String(event.error)); setMStream(""); return; }
        setMConvo((m) => [...m, { role: "assistant", content: event.reply || mStream || "(no reply)" }]);
        setMStream("");
        if (event.mission_state) setMState(event.mission_state);
        setMQuestion(event.next_question || null);
        setMConverged(!!event.converged);
        if (event.converged) { setMFinalMission(event.mission || ""); setMFinalInputs(event.inputs || {}); }
      }
    });
  }, [mSubscribe, mJobId, mStream]);

  useEffect(() => {
    // Scroll ONLY the messages container to its bottom — NOT scrollIntoView,
    // which bubbles up and scrolls the whole modal down.
    const el = mScrollRef.current;
    if (el) el.scrollTop = el.scrollHeight;
  }, [mConvo, mStream, mQuestion, mBusy]);

  const sendMissionTurn = async (text: string) => {
    const msg = text.trim();
    if (!msg || mBusy) return;
    if (!mWsConnected) { setMError("Live connection not ready — try again in a moment."); return; }
    setMError(null); setMBusy(true); setMStream(""); setMQuestion(null);
    const history = mConvo.map((m) => ({ role: m.role, content: m.content }));
    setMConvo((m) => [...m, { role: "user", content: msg }]);
    setMInput("");
    const eco_context = { name: eco.name, description: eco.description || "", inputs: eco.inputs || [], outcome: eco.outcome || null };
    const att = attachments.map((a) => ({ name: a.name, ref: a.content }));
    try {
      const resp = await authFetch(`${apiUrl}/api/ecosystems/suggest`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "mission", message: msg, history, mission_state: mState, eco_context, attachments: att, clientId: mClientId }),
      });
      const data = await resp.json();
      if (data.error) throw new Error(data.error);
      if (data.status === "pending" && data.jobId) { setMJobId(data.jobId); }
      else if (data.reply !== undefined) {
        setMBusy(false);
        setMConvo((m) => [...m, { role: "assistant", content: data.reply }]);
        if (data.mission_state) setMState(data.mission_state);
        setMQuestion(data.next_question || null);
        setMConverged(!!data.converged);
        if (data.converged) { setMFinalMission(data.mission || ""); setMFinalInputs(data.inputs || {}); }
      }
    } catch (e: any) { setMBusy(false); setMError(e?.message || "Turn failed"); }
  };

  // Upload one optional run-scoped file, classified server-side.
  const uploadRunAttachment = async (f: File) => {
    setAttachError(null);
    try {
      const res = await authFetch(`${apiUrl}/api/uploads/presign`, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ filename: f.name, sessionId: draftId, type: "run", contentType: f.type || "application/octet-stream" }) });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.error || !data.uploadUrl) {
        setAttachError(`Couldn't attach "${f.name}": ${data.error || `server error (HTTP ${res.status})`}`);
        return;
      }
      const put = await fetch(data.uploadUrl, { method: "PUT", body: f, headers: { "Content-Type": f.type || "application/octet-stream" } });
      if (!put.ok) {
        setAttachError(`Couldn't upload "${f.name}": storage rejected it (HTTP ${put.status}).`);
        return;
      }
      setAttachments((prev) => [...prev, { name: f.name, content: data.s3Uri }]);
    } catch (err: any) {
      setAttachError(`Couldn't upload "${f.name}": ${err?.message || "network/upload error"}. Check the file type and size (max 4.5MB).`);
      console.error("Run attachment upload failed:", err);
    }
  };

  const attRefs = () => attachments.map((a) => ({ name: a.name, ref: a.content }));
  const transcript = () => mConvo.map((m) => ({ role: m.role, content: m.content }));

  // Launch is enabled when the chat has converged OR (quick mode) a mission is typed.
  const quickReady = missionMode === "quick" && mission.trim().length > 0;
  const canLaunch = mConverged || quickReady;

  const handleLaunch = () => {
    if (missionMode === "quick") {
      onConverged(mission.trim(), inputValues, attRefs(), transcript());
    } else {
      onConverged(mFinalMission, mFinalInputs, attRefs(), transcript());
    }
  };

  return (
    <div className="space-y-4">
      {/* Universal optional file upload — available regardless of declared inputs. */}
      <div>
        <label className="block text-xs text-gray-400 mb-1">Attach files <span className="text-gray-600">(optional)</span></label>
        <label className="cursor-pointer inline-block px-3 py-1.5 rounded text-xs bg-gray-700/50 text-gray-200 hover:bg-gray-700 border border-tessera-border">
          + Add files
          <input type="file" multiple onChange={async (e) => { const fs = e.target.files; if (!fs) return; for (let i = 0; i < fs.length; i++) await uploadRunAttachment(fs[i]); e.target.value = ""; }} className="hidden" />
        </label>
        {attachments.length > 0 && <div className="mt-1.5 space-y-0.5">{attachments.map((a, i) => <div key={i} className="flex items-center gap-1 text-[10px] text-gray-400"><span>📎</span><span className="truncate max-w-[360px]">{a.name}</span><button type="button" onClick={() => setAttachments(p => p.filter((_, j) => j !== i))} className="text-red-400 ml-1">✕</button></div>)}</div>}
        {attachError && <div className="mt-1.5 text-[11px] text-red-300 bg-red-500/10 border border-red-500/30 rounded px-2 py-1 flex items-start gap-1.5"><span>⚠</span><span className="flex-1">{attachError}</span><button type="button" onClick={() => setAttachError(null)} className="text-red-400">✕</button></div>}
        <p className="text-[10px] text-gray-600 mt-1">Any file type — they&apos;ll be considered in the mission chat and given to the run.</p>
      </div>

      {missionMode === "quick" && Array.isArray(eco.inputs) && eco.inputs.length > 0 && (
        <div className="space-y-3">
          {eco.inputs.map((input: any) => (
            <div key={input.id}>
              <label className="block text-xs text-gray-400 mb-1">{input.label} {input.required && <span className="text-red-400">*</span>}</label>
              {input.type === "select" ? (
                <select value={inputValues[input.id] || ""} onChange={(e) => setInputValues(v => ({ ...v, [input.id]: e.target.value }))} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white"><option value="">Select...</option>{(input.options || []).map((o: string) => <option key={o} value={o}>{o}</option>)}</select>
              ) : (
                <input type="text" value={inputValues[input.id] || ""} onChange={(e) => setInputValues(v => ({ ...v, [input.id]: e.target.value }))} placeholder={input.label} className="w-full bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white" />
              )}
            </div>
          ))}
        </div>
      )}

      {/* Mission capture — conversational by default, one-shot escape hatch. */}
      <div>
        <div className="flex items-center justify-between mb-1">
          <label className="text-xs text-gray-400">Mission</label>
          <button type="button" onClick={() => setMissionMode(m => m === "chat" ? "quick" : "chat")} className="text-[10px] text-gray-500 hover:text-tessera-accent transition">
            {missionMode === "chat" ? "Type it myself →" : "← Plan it conversationally"}
          </button>
        </div>

        {missionMode === "quick" ? (
          <textarea value={mission} onChange={e => setMission(e.target.value)} placeholder="Describe the mission for this run..." rows={3} className="w-full bg-gray-900 border border-tessera-border rounded-lg px-4 py-3 text-sm text-white placeholder-gray-500 resize-none" />
        ) : (
          <div className="flex gap-3 items-start">
            <div className="flex-1 min-w-0 bg-tessera-card glow-border rounded-lg p-3">
              <div ref={mScrollRef} className="max-h-72 overflow-y-auto space-y-2 mb-2">
                {mConvo.length === 0 && !mStream && !mBusy && (
                  <div className="text-xs text-gray-500 text-center py-4 px-2">Tell me what you want this run to achieve{attachments.length > 0 ? ` — I can see your ${attachments.length} attached file${attachments.length > 1 ? "s" : ""}` : ""}. I&apos;ll ask a couple of quick questions, then we launch.</div>
                )}
                {mConvo.map((m, i) => (
                  <div key={i} className={`flex ${m.role === "user" ? "justify-end" : "justify-start"}`}>
                    <div className={`max-w-[85%] px-3 py-2 rounded-lg text-sm whitespace-pre-wrap ${m.role === "user" ? "bg-tessera-accent/20 text-tessera-accent" : "bg-gray-800 text-gray-200"}`}>{m.content}</div>
                  </div>
                ))}
                {mStream && <div className="flex justify-start"><div className="max-w-[85%] px-3 py-2 rounded-lg text-sm whitespace-pre-wrap bg-gray-800/70 text-gray-300 italic">{mStream}<span className="not-italic animate-pulse">▍</span></div></div>}
                {mBusy && !mStream && <div className="text-xs text-gray-500 italic animate-pulse">Planning…</div>}
                {mQuestion && !mConverged && (
                  <DecisionSurface
                    busy={mBusy}
                    decision={{ id: mQuestion.id || "q", kind: "question", title: "Run planner", prompt: mQuestion.text, options: (mQuestion.options || []).map(o => ({ id: o, label: o })), allowText: false, commit: "reversible" } as DecisionPoint}
                    onResolve={(r) => sendMissionTurn(r.optionId || r.text || "")}
                  />
                )}
                {mConverged && (
                  <div className="bg-tessera-accent/5 border border-tessera-accent/40 rounded-lg p-3">
                    <div className="text-[11px] font-bold text-tessera-accent uppercase tracking-wider mb-1">Mission ready</div>
                    <p className="text-xs text-gray-200 whitespace-pre-wrap">{mFinalMission}</p>
                    {Object.keys(mFinalInputs).length > 0 && <div className="mt-2 text-[10px] text-gray-400 space-y-0.5">{Object.entries(mFinalInputs).map(([k, v]) => <div key={k}><span className="text-gray-500">{k}:</span> {String(v)}</div>)}</div>}
                    <button type="button" onClick={() => setMConverged(false)} className="mt-2 text-[10px] text-gray-500 hover:text-white">Keep refining</button>
                  </div>
                )}
                <div ref={mEndRef} />
              </div>
              {!mConverged && (
                <div className="flex gap-2 items-end">
                  <textarea value={mInput} onChange={e => setMInput(e.target.value)} onKeyDown={e => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) { e.preventDefault(); sendMissionTurn(mInput); } }} placeholder={mConvo.length === 0 ? "What should this run do?" : "Reply, or type your own answer…"} rows={2} className="flex-1 bg-gray-900 border border-tessera-border rounded-lg px-3 py-2 text-sm text-white placeholder-gray-500 resize-none" />
                  <button type="button" onClick={() => sendMissionTurn(mInput)} disabled={mBusy || !mInput.trim()} className="px-4 py-2 bg-tessera-gradient rounded-lg text-sm font-semibold disabled:opacity-40 transition">{mBusy ? "…" : mConvo.length === 0 ? "✨ Start" : "Send"}</button>
                </div>
              )}
              {mError && <div className="mt-2 px-3 py-2 bg-red-500/10 border border-red-500/30 rounded-lg text-[11px] text-red-300">{mError}</div>}
              {!mConverged
                ? <p className="text-[10px] text-gray-600 mt-1.5">⌘/Ctrl+Enter to send. Launch unlocks once the mission is ready.</p>
                : <p className="text-[10px] text-green-400/80 mt-1.5">✓ Mission ready — hit Launch below. (Use “Keep refining” to adjust.)</p>}
            </div>
            {/* RIGHT — live run plan (mirrors the builder's intent/assumptions panel) */}
            <div className="w-60 shrink-0 bg-tessera-card glow-border rounded-lg p-3 max-h-[420px] overflow-y-auto">
              <div className="text-[11px] font-bold text-tessera-accent uppercase tracking-wider mb-2">Run plan</div>
              {mState?.goal ? <p className="text-[11px] text-gray-300 mb-2 whitespace-pre-wrap">{mState.goal}</p> : <p className="text-[11px] text-gray-600 italic mb-2">Your goal will take shape as we talk.</p>}
              {Array.isArray(mState?.checklist) && mState.checklist.length > 0 && (
                <div className="mb-2">
                  <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">Briefing ({mState.checklist.filter((c: any) => c?.resolved).length}/{mState.checklist.length})</div>
                  {mState.checklist.map((c: any, i: number) => (
                    <div key={c.id || i} className="text-[10px] flex items-start gap-1 mb-0.5">
                      <span className={c.resolved ? "text-green-400" : "text-gray-600"}>{c.resolved ? "✓" : "○"}</span>
                      <span className="text-gray-400"><span className={c.resolved ? "text-gray-300" : ""}>{c.label}</span>{c.resolved && c.value ? <span className="text-gray-500"> — {String(c.value)}</span> : ""}</span>
                    </div>
                  ))}
                </div>
              )}
              {(() => {
                const inps: Record<string, any> = { ...(mState?.inputs || {}), ...mFinalInputs };
                return Object.keys(inps).length > 0 ? (
                  <div className="mb-2">
                    <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">Captured</div>
                    {Object.entries(inps).map(([k, v]) => <div key={k} className="text-[11px] text-gray-300 truncate"><span className="text-gray-500">{k}:</span> {String(v)}</div>)}
                  </div>
                ) : null;
              })()}
              {Array.isArray(eco.inputs) && eco.inputs.length > 0 && (
                <div className="mb-2">
                  <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">Required inputs</div>
                  {eco.inputs.map((inp: any) => {
                    const have = (mState?.inputs || {})[inp.id] || mFinalInputs[inp.id];
                    return <div key={inp.id} className="text-[10px] flex items-center gap-1"><span className={have ? "text-green-400" : "text-gray-600"}>{have ? "✓" : "○"}</span><span className="text-gray-400 truncate">{inp.label}{inp.required && !have ? " *" : ""}</span></div>;
                  })}
                </div>
              )}
              {attachments.length > 0 && (
                <div className="mb-2">
                  <div className="text-[10px] text-gray-500 uppercase tracking-wider mb-1">Files</div>
                  {attachments.map((a, i) => <div key={i} className="text-[10px] text-gray-400 truncate">📎 {a.name}</div>)}
                </div>
              )}
              {mState?.notes && <p className="text-[10px] text-gray-500 mt-1 whitespace-pre-wrap">{mState.notes}</p>}
              <div className="mt-2 text-[10px]">{mConverged ? <span className="text-green-400">✓ Ready to launch</span> : <span className="text-gray-600">Gathering details…</span>}</div>
            </div>
          </div>
        )}
      </div>

      <div className="flex justify-end pt-1">
        <button
          type="button"
          onClick={handleLaunch}
          disabled={!canLaunch}
          className="px-5 py-2.5 text-sm font-bold text-white rounded-lg bg-tessera-gradient shadow-[0_4px_14px_rgba(139,92,246,0.3)] hover:opacity-95 transition disabled:opacity-40"
        >
          Launch run →
        </button>
      </div>
    </div>
  );
}
