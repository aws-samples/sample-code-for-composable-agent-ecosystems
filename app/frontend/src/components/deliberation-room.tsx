"use client";

/**
 * Deliberation Room — the interactive run surface for the Quorum archetype.
 *
 * A live meeting the human sits in: panelists speak one after another (streamed word-by-word),
 * the meeting FLOWS on its own, and the human grabs the floor by raising their hand. The panel
 * only stops for the human when a member puts a direct question to them. State is reduced from
 * streamed WS events; commands go back over the run socket via the `quorum` action. Rendered only
 * for quorum archetypes — every other ecosystem keeps RunView untouched.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import dynamic from "next/dynamic";

const QuorumWhiteboard = dynamic(() => import("./quorum-whiteboard"), {
  ssr: false,
  loading: () => <div className="grid place-items-center h-full text-xs text-gray-400">Loading whiteboard…</div>,
});

type Ev = Record<string, any>;
interface Member { persona_id: string; name?: string; stance?: string; summoned?: boolean }
interface Entry { entry_id: string; member: string; type: string; text: string; detail?: string; target?: string | null; severity?: string; round?: number }
interface Excl { persona_id: string; reason?: string; name?: string; stance?: string }

interface RoomState {
  phase: string; members: Member[]; excluded: Excl[]; justification: string;
  entries: Entry[]; verdict: Ev | null; error: string | null;
  streamMember: string | null; streamText: string; pauseQuestion: string; pauseAskedBy: string;
  wbOpen: boolean; wbOps: Ev[]; wbErased: string[]; wbPending: Ev[]; wbRequest: Ev | null;
  floorHolder: string;   // server-authoritative: "panel" | "human_requested" | "human"
}

const HUMAN = "human";
const MOD = "moderator";
const HUES = ["#0ea5e9", "#8b5cf6", "#ec4899", "#22c55e", "#f59e0b", "#14b8a6", "#f43f5e", "#a3e635", "#eab308"];
const hueFor = (id: string) => HUES[[...id].reduce((a, c) => a + c.charCodeAt(0), 0) % HUES.length];
const initials = (n: string) => n.split(/\s+/).slice(0, 2).map((w) => w[0]?.toUpperCase() || "").join("") || "?";

function reduce(events: Ev[]): RoomState {
  const s: RoomState = { phase: "convening", members: [], excluded: [], justification: "", entries: [], verdict: null, error: null, streamMember: null, streamText: "", pauseQuestion: "", pauseAskedBy: "", wbOpen: false, wbOps: [], wbErased: [], wbPending: [], wbRequest: null, floorHolder: "panel" };
  const idx = (id: string) => s.members.findIndex((m) => m.persona_id === id);
  // Persist whatever a member streamed, even if the finalizing entry never arrives, so a turn
  // can NEVER silently disappear when the next speaker starts.
  const commitStream = () => {
    if (s.streamMember && s.streamText.trim()) {
      const last = s.entries[s.entries.length - 1];
      if (!(last && last.member === s.streamMember && last.text.trim() === s.streamText.trim())) {
        s.entries.push({ entry_id: `stream-${s.entries.length}`, member: s.streamMember, type: "claim", text: s.streamText.trim(), target: null });
      }
    }
    s.streamMember = null; s.streamText = "";
  };
  for (const e of events) {
    switch (e.type) {
      case "RUN_STARTED": case "QUORUM_CONVENING":
        if (s.phase === "convening" || s.phase === "done") s.phase = "convening"; break;
      case "QUORUM_PLAN": { s.error = null;
        const cards: Member[] = (e.members || (e.seated || []).map((id: string) => ({ persona_id: id }))) as Member[];
        s.members = cards.map((c) => ({ ...c, summoned: (e.summoned || []).includes(c.persona_id) }));
        s.excluded = (e.excluded_cards || e.excluded || []) as Excl[];
        s.justification = e.justification || ""; break;
      }
      case "QUORUM_MEMBER_SEATED": {
        const id = e.member || e.persona_id;
        if (id && idx(id) < 0) s.members.push({ persona_id: id, name: e.name, stance: e.stance, summoned: true });
        s.excluded = s.excluded.filter((x) => x.persona_id !== id); break;
      }
      case "MEMBER_SUMMONED": {
        const id = e.member;
        if (id && idx(id) < 0) s.members.push({ persona_id: id, name: e.name, stance: e.stance, summoned: true });
        s.excluded = s.excluded.filter((x) => x.persona_id !== id); break;
      }
      case "QUORUM_MEMBER_EXCLUDED": {
        const id = e.persona_id || e.member;
        s.members = s.members.filter((m) => m.persona_id !== id);
        if (id && !s.excluded.find((x) => x.persona_id === id)) s.excluded.push({ persona_id: id, reason: e.reason }); break;
      }
      case "MEMBER_TURN": commitStream(); break;   // a new speaker starts — persist the previous one
      case "MEMBER_PASSED": s.streamMember = null; s.streamText = ""; break;   // yielded, nothing to show
      case "QUORUM_SYNC": {   // authoritative resync: fill in anything we missed while disconnected
        commitStream();
        if (Array.isArray(e.members) && e.members.length) s.members = e.members as Member[];
        const pl = e.plan || {};
        if (Array.isArray(pl.excluded)) s.excluded = pl.excluded as Excl[];
        if (pl.justification) s.justification = pl.justification;
        // Dedupe by member+text, NOT entry_id: locally-committed stream text has a synthetic id, so
        // id-only matching double-rendered every message the authoritative sync brought back.
        const norm = (x: any) => `${x.member}|${(x.text || "").trim().slice(0, 120).toLowerCase()}`;
        const have = new Map(s.entries.map((x) => [norm(x), x]));
        for (const en of (e.entries || [])) {
          const k = norm(en);
          const existing = have.get(k);
          if (!existing) { s.entries.push(en as Entry); have.set(k, en as Entry); }
          else if (String(existing.entry_id).startsWith("stream-")) {
            // replace the synthetic copy with the authoritative one (keeps type/target badges)
            Object.assign(existing, en);
          }
        }
        if (e.verdict) s.verdict = e.verdict;
        if (e.phase) s.phase = e.phase;
        break;
      }
      case "QUORUM_DELTA":
        if (s.streamMember !== e.member) { commitStream(); s.streamMember = e.member; s.streamText = ""; }
        s.streamText += (e.text || ""); break;
      case "DELIBERATION_STARTED": case "QUORUM_RESUMED": s.phase = "deliberating"; s.error = null; break;
      case "QUORUM_ENTRY": {
        s.error = null;
        const dupe = s.entries.find((x) => x.member === e.member &&
          (x.text || "").trim().slice(0, 120).toLowerCase() === (e.text || "").trim().slice(0, 120).toLowerCase());
        if (dupe) { Object.assign(dupe, { entry_id: e.entry_id, type: e.type, target: e.target, severity: e.severity, seq: e.seq, detail: e.detail });
                    if (s.streamMember === e.member) { s.streamMember = null; s.streamText = ""; } break; }
        if (s.streamMember === e.member) { s.streamMember = null; s.streamText = ""; }  // real entry supersedes the stream
        else commitStream();
        s.entries.push({ entry_id: e.entry_id, member: e.member, type: e.type, text: e.text, detail: e.detail, target: e.target, severity: e.severity, round: e.round }); break;
      }
      case "QUORUM_PAUSE": commitStream(); if (e.reason) s.phase = e.reason; if (e.reason === "await_human") { s.pauseQuestion = e.question || ""; s.pauseAskedBy = e.asked_by || ""; } break;
      case "FLOOR": s.floorHolder = e.holder || "panel"; break;
      case "WHITEBOARD_OPENED": s.wbOpen = true; break;
      case "WHITEBOARD_OP": s.wbOpen = true; s.wbOps = [...s.wbOps, e]; break;
      case "WHITEBOARD_STATE": s.wbOps = (e.ops || []) as Ev[]; s.wbErased = (e.erased || []) as string[]; s.wbPending = (e.pending_erase || []) as Ev[]; if (s.wbOps.length) s.wbOpen = true; break;
      case "WHITEBOARD_REQUEST": s.wbRequest = { by: e.by, text: e.text }; break;
      case "WHITEBOARD_ERASE_REQUEST": s.wbPending = [...s.wbPending, e]; break;
      case "WHITEBOARD_ERASE": s.wbErased = [...s.wbErased, e.op_id]; s.wbPending = s.wbPending.filter((r) => r.op_id !== e.op_id); break;
      case "WHITEBOARD_ERASE_DENIED": s.wbPending = s.wbPending.filter((r) => r.op_id !== e.op_id); break;
      case "VERDICT_RENDERED": commitStream(); s.verdict = e; s.phase = "verdict"; break;      case "RUN_FINISHED": if (s.phase !== "verdict") s.phase = "done"; break;
      case "RUN_ERROR": s.error = e.error || "error"; s.phase = "error"; break;
    }
  }
  // Fix 6 — final safety net. A turn could render TWICE: the locally committed stream copy (no
  // type/target, so no badges) plus the authoritative QUORUM_ENTRY, whenever their text differed
  // even slightly (word-cap, re-delivered delta, reconnect). The per-case guards compare exact
  // text and so miss that. Collapse on heavily normalised text, keeping the AUTHORITATIVE copy.
  const dkey = (x: Entry) => `${x.member}|${(x.text || "").toLowerCase().replace(/[^a-z0-9]+/g, "").slice(0, 48)}`;
  const isStream = (x: Entry) => String(x.entry_id).startsWith("stream-");
  const kept: Entry[] = [];
  const byKey = new Map<string, Entry>();
  for (const e of s.entries) {
    const k = dkey(e);
    // exact-normalised match, or one text is a prefix of the other (a doubled/truncated stream)
    let prev = byKey.get(k);
    if (!prev) {
      for (const [k2, cand] of byKey) {
        if (cand.member !== e.member || k2.length < 34) continue;
        const a = k.split("|")[1], b = k2.split("|")[1];
        if (a.startsWith(b) || b.startsWith(a)) { prev = cand; break; }
      }
    }
    if (!prev) { byKey.set(k, e); kept.push(e); continue; }
    if (isStream(prev) && !isStream(e)) Object.assign(prev, e);   // upgrade to the real entry
  }
  s.entries = kept;
  return s;
}

const TYPE_STYLE: Record<string, string> = {
  dissent: "bg-red-500/15 text-red-300", objection: "bg-amber-500/15 text-amber-300",
  question: "bg-violet-500/15 text-violet-300", rebuttal: "bg-sky-500/15 text-sky-300",
  concession: "bg-emerald-500/15 text-emerald-300", claim: "bg-gray-700/40 text-gray-400",
};
const GRADE: Record<string, { c: string; ring: string; label: string }> = {
  defensible: { c: "text-emerald-400", ring: "border-emerald-500/50", label: "Defensible" },
  defensible_with_conditions: { c: "text-amber-400", ring: "border-amber-500/50", label: "Defensible with conditions" },
  indefensible: { c: "text-red-400", ring: "border-red-500/50", label: "Indefensible" },
};

function Avatar({ id, name, sm }: { id: string; name: string; sm?: boolean }) {
  const cls = `${sm ? "w-7 h-7 text-[10px]" : "w-9 h-9 text-xs"} rounded-full grid place-items-center font-bold shrink-0`;
  if (id === HUMAN) return <div className={`${cls} bg-white text-gray-900`}>You</div>;
  if (id === MOD) return <div className={`${cls} bg-gray-200 text-gray-900`}>MOD</div>;
  return <div className={`${cls} text-gray-900`} style={{ background: hueFor(id) }}>{initials(name)}</div>;
}

function WaitDots({ label }: { label?: string }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-gray-400">
      {label && <span className="text-[13px]">{label}</span>}
      <span className="inline-flex gap-1">
        <span className="w-1.5 h-1.5 rounded-full bg-sky-400/80 animate-bounce" style={{ animationDelay: "0ms" }} />
        <span className="w-1.5 h-1.5 rounded-full bg-violet-400/80 animate-bounce" style={{ animationDelay: "150ms" }} />
        <span className="w-1.5 h-1.5 rounded-full bg-pink-400/80 animate-bounce" style={{ animationDelay: "300ms" }} />
      </span>
    </span>
  );
}

export function DeliberationRoom({ sessionId, events, send }: { sessionId: string; events: Ev[]; send: (d: Record<string, unknown>) => void }) {
  const st = useMemo(() => reduce(events), [events]);
  const nameOf = (id: string) => id === HUMAN ? "You" : id === MOD ? "Moderator" : (st.members.find((m) => m.persona_id === id)?.name || st.excluded.find((x) => x.persona_id === id)?.name || id);

  const [draft, setDraft] = useState("");
  const [handRaised, setHandRaised] = useState(false);       // local echo of request_floor
  const [isDrawing, setIsDrawing] = useState(false);         // whiteboard is animating — hold the floor
  const drawingRef = useRef(false);   // live copy of isDrawing for the advance timer (avoids stale closure)
  const setDrawing = (v: boolean) => { drawingRef.current = v; setIsDrawing(v); };
  const holdRef = useRef(false);      // TRUE whenever the panel must not start a new turn
  const [optimistic, setOptimistic] = useState<Entry[]>([]);
  const [expanded, setExpanded] = useState<Record<string, boolean>>({});
  const endRef = useRef<HTMLDivElement>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const inflightRef = useRef(false);
  const queuedRef = useRef<{ command: string; extra: Record<string, unknown> } | null>(null);
  const lastEventAtRef = useRef(Date.now());
  const phaseRef = useRef<string>("convening");
  const [tick, setTick] = useState(0);
  const [ending, setEnding] = useState(false);
  const [revealed, setRevealed] = useState(0);
  const [awaitEcho, setAwaitEcho] = useState<string | null>(null);

  const settleN = useMemo(() => events.filter((e: any) => ["QUORUM_ENTRY", "QUORUM_PAUSE", "VERDICT_RENDERED", "QUORUM_RESUMED", "RUN_ERROR", "RUN_FINISHED", "QUORUM_STATE", "MEMBER_PASSED", "QUORUM_SYNC"].includes(e.type)).length, [events]);
  const awaiting = st.phase === "await_human";
  // Floor control is SERVER-authoritative (Fix 1): we reflect it, we do not infer it. The only
  // client-owned hold is the drawing animation, which the server can't know about.
  // A requested floor counts as HELD for the UI. Treating it as a separate waiting state deadlocked
  // the room: the client stopped advancing (right) but the server only promoted requested -> human
  // inside a turn (wrong), so nothing could move and the input box never appeared.
  const iHoldFloor = st.floorHolder === "human" || st.floorHolder === "human_requested" || awaiting;
  holdRef.current = iHoldFloor || isDrawing;

  const dispatch = (command: string, extra: Record<string, unknown> = {}, human = true) => {
    if (command === "advance" && holdRef.current) return;   // never talk over the human
    if (inflightRef.current) { if (human) queuedRef.current = { command, extra }; return; }
    inflightRef.current = true;
    send({ action: "quorum", command, session_id: sessionId, ...extra });
  };
  useEffect(() => {  // a response settled: release the lock, flush any queued human action
    inflightRef.current = false;
    if (queuedRef.current) { const q = queuedRef.current; queuedRef.current = null; dispatch(q.command, q.extra, true); }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [settleN]);
  useEffect(() => { lastEventAtRef.current = Date.now(); }, [events.length]);
  useEffect(() => { phaseRef.current = st.phase; }, [st.phase]);
  useEffect(() => {  // watchdog: if a command is in flight but NOTHING has streamed for 4s, assume the
    const iv = setInterval(() => {  // "turn finished" signal was lost — release so the room never freezes
      const quiet = Date.now() - lastEventAtRef.current;
      if (inflightRef.current && quiet > 30000) { inflightRef.current = false; setTick((t) => t + 1); }
      // Heard nothing for a while on a live session? Re-sync from authoritative server state — the
      // meeting may have progressed while our WebSocket was stale (lost events used to strand us).
      if (quiet > 20000 && !["verdict", "done", "error", "convening"].includes(phaseRef.current)) {
        lastEventAtRef.current = Date.now();
        send({ action: "quorum", command: "sync", session_id: sessionId });
      }
      // never let a queued human command sit unsent
      if (!inflightRef.current && queuedRef.current) { const q = queuedRef.current; queuedRef.current = null; dispatch(q.command, q.extra, true); }
    }, 1000);
    return () => clearInterval(iv);
  }, []);
  // Continuous flow: keep the meeting moving. Single-flight (inflightRef) prevents overlap while a
  // turn streams; we do NOT gate on the streaming bubble, so a stale bubble can't stall the room.
  useEffect(() => {
    if (st.phase !== "deliberating" || inflightRef.current || iHoldFloor || isDrawing || ending || awaitEcho || queuedRef.current) return;
    const t = setTimeout(() => {
      if (!inflightRef.current && !holdRef.current && !ending && !awaitEcho && st.phase === "deliberating")
        dispatch("advance", {}, false);
    }, 700);
    return () => clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [st.phase, settleN, iHoldFloor, isDrawing, ending, awaitEcho, sessionId, tick]);
  // Clear the "waiting for my message to land" gate once the server echoes it (or after a safety timeout).
  useEffect(() => {
    if (awaitEcho && st.entries.some((e) => e.member === HUMAN && e.text.trim() === awaitEcho.trim())) setAwaitEcho(null);
  }, [st.entries.length, awaitEcho]);
  useEffect(() => {
    const c = scrollRef.current; if (!c) return;
    // Only stick to the bottom if the user is ALREADY near it — never yank the view while they're
    // reading higher up (e.g. their own message) during streaming.
    if (c.scrollHeight - c.scrollTop - c.clientHeight < 140) endRef.current?.scrollIntoView({ behavior: "auto", block: "end" });
  }, [st.entries.length, optimistic.length, revealed, st.phase]);
  useEffect(() => { orderRef.current = []; }, [sessionId]);
  // On mount, ask the server for the authoritative session state (reload / reconnect safe).
  useEffect(() => {
    if (!sessionId) return;
    // Delay slightly: on a fresh launch the session is created a moment later, and an
    // immediate sync would race it. Harmless server-side either way (returns "pending").
    const t = setTimeout(() => send({ action: "quorum", command: "sync", session_id: sessionId }), 4000);
    return () => clearTimeout(t);
    /* eslint-disable-next-line react-hooks/exhaustive-deps */
  }, [sessionId]);
  useEffect(() => { if (iHoldFloor) taRef.current?.focus(); }, [iHoldFloor]);
  // reading-paced reveal of the streaming text (~40 chars/sec) so you can actually read along
  useEffect(() => { setRevealed(0); }, [st.streamMember]);
  useEffect(() => {
    if (!st.streamMember || revealed >= st.streamText.length) return;
    const iv = setInterval(() => setRevealed((r) => Math.min(st.streamText.length, r + 2)), 50);
    return () => clearInterval(iv);
  }, [st.streamMember, st.streamText, revealed]);
  useEffect(() => { if (st.phase === "error") setEnding(false); }, [st.phase]);

  const submitHuman = () => {
    const text = draft.trim(); if (!text) return;
    setOptimistic((o) => (o.some((x) => x.text.trim() === text) ? o : [...o, { entry_id: `opt-${Date.now()}`, member: HUMAN, type: "say", text }]));
    setAwaitEcho(text);
    setTimeout(() => setAwaitEcho((cur) => (cur === text ? null : cur)), 8000);  // safety valve
    dispatch("human_say", { text, entry_type: "claim" }, true);
    setDraft(""); setHandRaised(false);
  };
  const ruleNow = () => { if (typeof window !== "undefined" && window.confirm("End the debate now and have the panel deliver its verdict?")) { setEnding(true); dispatch("rule_now", {}, true); } };
  const stopSession = () => { if (typeof window !== "undefined" && window.confirm("End this session now, without a verdict?")) { setEnding(true); dispatch("stop", {}, true); } };
  const cmd = (command: string, extra: Record<string, unknown> = {}) => dispatch(command, extra, true);

  const dissents = st.entries.filter((e) => e.type === "dissent");
  const pendingQ = awaiting ? [...st.entries].reverse().find((e) => e.target === HUMAN && e.member !== HUMAN) : null;
  const askText = pendingQ?.text || st.pauseQuestion || "They'd like your take before continuing.";
  const askBy = pendingQ ? nameOf(pendingQ.member) : (st.pauseAskedBy ? nameOf(st.pauseAskedBy) : "A panelist");
  // Stable, APPEND-ONLY render order: every message keeps the position where it FIRST appeared and
  // is never re-sorted. Your optimistic message is appended the instant you send, and the server
  // echo (same key) is de-duped — so your message never jumps, even mid-deliberation.
  const orderRef = useRef<{ key: string; e: Entry }[]>([]);
  const keyOf = (e: Entry) => e.member === HUMAN ? `human-${(e.text || "").trim().slice(0, 60).toLowerCase()}` : e.entry_id;
  {
    const seen = new Set(orderRef.current.map((x) => x.key));
    for (const e of st.entries) { const k = keyOf(e); if (!seen.has(k)) { orderRef.current.push({ key: k, e }); seen.add(k); } }
    for (const o of optimistic) { const k = keyOf(o); if (!seen.has(k)) { orderRef.current.push({ key: k, e: o }); seen.add(k); } }
  }
  const rows = orderRef.current;

  const cue: Record<string, { t: string; c: string }> = {
    convening: { t: "Assembling the panel for your decision…", c: "text-gray-400" },
    empanel_gate: { t: "Review the panel — drop, seat, or accept to begin.", c: "text-sky-400" },
    deliberating: { t: "The panel is in session — raise your hand to speak.", c: "text-gray-400" },
    await_human: { t: "A panelist has put a question to you.", c: "text-violet-300" },
    convergence_offer: { t: "The discussion has run its course.", c: "text-sky-400" },
    verdict: { t: "The panel has ruled.", c: "text-emerald-400" },
    done: { t: "Session complete.", c: "text-gray-400" }, error: { t: "The session hit an error.", c: "text-red-400" },
  };
  const c = cue[st.phase] || cue.deliberating;
  const flowing = st.phase === "deliberating" && !handRaised && !awaiting;

  return (
    <div className={`grid ${st.wbOpen ? "grid-cols-[220px_minmax(0,1fr)_minmax(0,1.05fr)]" : "grid-cols-[240px_1fr]"} gap-4 text-gray-100 h-[calc(100vh-170px)] min-h-0`}>
      {/* Left rail: the table */}
      <div className="flex flex-col gap-3 overflow-y-auto pr-1 min-h-0">
        <div className="bg-gray-900 border border-gray-700 rounded-xl p-3">
          <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-2">At the table · {st.members.length}</div>
          {st.members.length === 0 && <WaitDots label="Assembling" />}
          <div className="flex flex-col gap-2.5">
            {st.members.map((m) => (
              <div key={m.persona_id} className="flex gap-2.5 items-start">
                <Avatar id={m.persona_id} name={m.name || m.persona_id} sm />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-1.5">
                    <span className="text-[13px] font-semibold truncate">{m.name || m.persona_id}</span>
                    {st.streamMember === m.persona_id && <span className="text-[10px] text-emerald-400 animate-pulse">●</span>}
                  </div>
                  {m.stance && <div className="text-[11px] text-gray-500 leading-snug line-clamp-2">{m.stance}</div>}
                  {st.phase === "empanel_gate" && <button onClick={() => cmd("empanel", { empanel: { drop: [m.persona_id] } })} className="mt-0.5 text-[11px] text-red-400 hover:text-red-300">drop</button>}
                </div>
              </div>
            ))}
          </div>
        </div>
        {st.excluded.length > 0 && (
          <div className="bg-gray-900 border border-gray-700 rounded-xl p-3">
            <div className="text-[11px] uppercase tracking-wider text-gray-500 mb-2">Left out (on purpose)</div>
            {st.excluded.slice(0, 6).map((x) => (
              <div key={x.persona_id} className="text-xs mb-2">
                <div className="font-medium text-gray-300">{x.name || x.persona_id}</div>
                {x.reason && <div className="text-[11px] text-gray-500 leading-snug">{x.reason}</div>}
                <button onClick={() => cmd("summon", { ids: [x.persona_id] })} className="mt-0.5 text-[11px] text-sky-400 hover:text-sky-300">+ bring them in</button>
              </div>
            ))}
          </div>
        )}
        {dissents.length > 0 && (
          <div className="bg-gray-900 border border-red-500/30 rounded-xl p-3">
            <div className="text-[11px] uppercase tracking-wider text-red-300 mb-2">On the record: dissent · {dissents.length}</div>
            {dissents.map((d) => <div key={d.entry_id} className="text-xs mb-1.5"><span className="font-semibold">{nameOf(d.member)}:</span> {d.text}</div>)}
          </div>
        )}
      </div>

      {/* Right: the meeting */}
      <div className="flex flex-col min-h-0">
        <div className="flex items-center justify-between mb-2 shrink-0">
          <div>
            <div className="text-lg font-bold bg-gradient-to-r from-sky-400 via-violet-400 to-pink-400 bg-clip-text text-transparent">Deliberation Room</div>
            <div className={`text-[13px] ${c.c} flex items-center gap-2`}>{c.t}{flowing && <span className="text-emerald-400/70 text-[11px]">● in session</span>}</div>
          </div>
          {st.phase !== "verdict" && st.phase !== "done" && !ending && (
            <div className="flex gap-2">
              {(st.phase === "deliberating" || awaiting || st.phase === "convergence_offer") && <button onClick={ruleNow} title="End the debate and have the panel deliver its verdict now" className="text-xs px-3 py-1.5 rounded-lg border border-gray-600 text-gray-300 hover:bg-gray-800">End &amp; rule</button>}
              <button onClick={stopSession} title="End the session now without a verdict" className="text-xs px-3 py-1.5 rounded-lg border border-red-600/60 text-red-300 hover:bg-red-900/30">End session</button>
            </div>
          )}
        </div>

        {/* Everything scrolls together here; the composer below stays pinned and always visible. */}
        <div ref={scrollRef} className="flex-1 min-h-0 overflow-y-auto bg-gray-900/40 border border-gray-800 rounded-xl px-3">
          {st.phase === "convening" && <div className="h-full grid place-items-center py-10"><WaitDots label="Convening the panel" /></div>}

          {st.phase === "empanel_gate" && (
            <div className="py-3">
              <div className="text-[13px] text-gray-300 mb-2">{st.justification || "Panel convened. Review the table, then begin."}</div>
              <button onClick={() => cmd("empanel", { empanel: { accept: true } })} className="bg-emerald-500 hover:bg-emerald-400 text-emerald-950 font-bold rounded-lg px-4 py-2 text-sm">Bring the meeting to order →</button>
            </div>
          )}

          {rows.map(({ key, e }) => {
            const human = e.member === HUMAN;
            return (
              <div key={key} className={`flex gap-3 py-2.5 border-b border-gray-800/40 ${human ? "bg-sky-500/[0.06]" : ""}`}>
                <Avatar id={e.member} name={nameOf(e.member)} sm />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-[13px] font-semibold">{nameOf(e.member)}</span>
                    {!human && e.member !== MOD && e.type !== "claim" && <span className={`text-[10px] px-1.5 py-0.5 rounded ${TYPE_STYLE[e.type] || ""}`}>{e.type}</span>}
                    {e.target && e.target !== HUMAN && <span className="text-[10px] text-gray-500">↳ to {nameOf(e.target)}</span>}
                    {e.target === HUMAN && <span className="text-[10px] px-1.5 py-0.5 rounded bg-violet-500/20 text-violet-300">to you</span>}
                  </div>
                  <div className="text-[14px] leading-relaxed whitespace-pre-wrap text-gray-100 mt-0.5">{e.text}</div>
                  {e.detail && (
                    <div className="mt-1">
                      <button onClick={() => setExpanded((x) => ({ ...x, [key]: !x[key] }))}
                        className="text-[11px] text-sky-400/80 hover:text-sky-300">
                        {expanded[key] ? "▾ hide the detail" : "▸ detail"}
                      </button>
                      {expanded[key] && (
                        <div className="mt-1 text-[13px] leading-relaxed whitespace-pre-wrap text-gray-400 border-l-2 border-gray-700 pl-2.5">
                          {e.detail}
                        </div>
                      )}
                    </div>
                  )}
                </div>
              </div>
            );
          })}

          {st.streamMember && (
            <div className="flex gap-3 py-2.5">
              <Avatar id={st.streamMember} name={nameOf(st.streamMember)} sm />
              <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2"><span className="text-[13px] font-semibold">{nameOf(st.streamMember)}</span><span className="text-[10px] text-emerald-400 animate-pulse">speaking…</span></div>
                <div className="text-[14px] leading-relaxed whitespace-pre-wrap text-gray-100 mt-0.5">{st.streamText.slice(0, revealed)}<span className="animate-pulse">▍</span></div>
              </div>
            </div>
          )}

          {st.phase === "deliberating" && !st.streamMember && !ending && <div className="py-3"><WaitDots label="The panel is thinking" /></div>}

          {st.phase === "convergence_offer" && !ending && (
            <div className="bg-gray-900 border border-sky-500/40 rounded-xl p-3 my-2.5 flex items-center gap-3">
              <span className="text-[13px] text-gray-300 flex-1">The panel has talked itself out for now — raise your hand to add something, or call for the ruling.</span>
              <button onClick={ruleNow} className="bg-sky-500 hover:bg-sky-400 text-sky-950 font-bold rounded-lg px-4 py-2 text-sm">Deliver the verdict</button>
            </div>
          )}

          {ending && !st.verdict && st.phase !== "done" && <div className="py-4"><WaitDots label="Wrapping up" /></div>}
          {st.phase === "done" && !st.verdict && <div className="py-4 text-gray-400 text-sm">Session ended.</div>}
          {st.verdict && <VerdictCard v={st.verdict} nameOf={nameOf} />}
          {st.error && <div className="bg-gray-900 border border-red-500/40 rounded-xl p-3 my-2.5 text-red-300 text-sm">Error: {st.error}</div>}
          <div ref={endRef} />
        </div>

        {/* Floor control — pinned, always visible */}
        {st.phase !== "verdict" && st.phase !== "done" && st.phase !== "convening" && !ending && (
          <div className="mt-2.5 shrink-0">
            {!handRaised && !awaiting ? (
              <div className="flex items-center justify-between bg-gray-900 border border-gray-800 rounded-xl px-3 py-2">
                <span className="text-[13px] text-gray-400">{flowing ? "The panel is talking — raise your hand when you want to jump in." : "Your move."}</span>
                <button onClick={() => { setHandRaised(true); cmd("request_floor"); }} className="text-sm font-semibold px-3 py-1.5 rounded-lg bg-violet-500/20 text-violet-200 border border-violet-500/40 hover:bg-violet-500/30">✋ Raise your hand</button>
              </div>
            ) : (!iHoldFloor) ? (
              <div className="flex items-center justify-between bg-gray-900 border border-amber-500/40 rounded-xl px-3 py-2">
                <span className="text-[13px] text-amber-300 flex items-center gap-2">✋ Hand raised — {isDrawing ? "waiting for the board" : "handing you the floor"}<WaitDots /></span>
                <button onClick={() => { setHandRaised(false); cmd("release_floor"); }} className="text-xs text-gray-500 hover:text-gray-300 px-2 py-1">lower hand</button>
              </div>
            ) : (
              <div className={`rounded-xl p-2 bg-gray-900 ring-2 ${awaiting ? "ring-violet-500/60" : "ring-sky-500/50"}`}>
                <div className="text-[12px] px-1 pb-1.5 text-gray-300">{awaiting ? (<span><span className="text-violet-300 font-semibold">{askBy} asked you:</span> {askText}</span>) : "You have the floor — the panel is waiting for you."}</div>
                <div className="flex gap-2 items-end">
                  <textarea ref={taRef} value={draft} onChange={(e) => setDraft(e.target.value)} rows={2}
                    onKeyDown={(e) => { if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) submitHuman(); }}
                    placeholder="Speak to the panel…  (⌘/Ctrl+Enter to send)"
                    className="flex-1 bg-transparent text-gray-100 border-none outline-none resize-none text-sm py-1.5 px-1" />
                  {!awaiting && <button onClick={() => { setHandRaised(false); cmd("release_floor"); }} className="text-xs text-gray-500 hover:text-gray-300 px-2 py-2">lower hand</button>}
                  <button onClick={submitHuman} className="bg-white text-gray-900 font-bold rounded-lg px-4 py-2 text-sm shrink-0">Speak</button>
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {/* Whiteboard column — appears when a member (or the submitter) opens the board */}
      {st.wbOpen && (
        <div className="min-h-0 h-full">
          <QuorumWhiteboard
            ops={st.wbOps as any}
            erased={st.wbErased}
            pending={st.wbPending as any}
            request={st.wbRequest as any}
            nameFor={nameOf}
            colorFor={hueFor}
            onDrawingChange={setDrawing}
            onDraw={(ops) => send({ action: "quorum", command: "wb_draw", session_id: sessionId, ops })}
            onEraseConsent={(opId, approve) => send({ action: "quorum", command: "wb_erase_consent", session_id: sessionId, op_id: opId, approve })}
          />
        </div>
      )}
    </div>
  );
}

function VerdictCard({ v, nameOf }: { v: Ev; nameOf: (id: string) => string }) {
  const g = GRADE[v.grade] || { c: "text-gray-200", ring: "border-gray-600", label: String(v.grade || "") };
  return (
    <div className={`bg-gray-900 border-2 ${g.ring} rounded-xl p-4 mt-3`}>
      <div className="flex items-center gap-3"><span className="text-[11px] uppercase tracking-wider text-gray-500">The ruling</span><span className={`text-xl font-extrabold ${g.c}`}>{g.label}</span></div>
      {v.summary && <div className="text-[14px] mt-2 leading-relaxed whitespace-pre-wrap text-gray-200">{v.summary}</div>}
      {Array.isArray(v.against_whom) && v.against_whom.length > 0 && <div className="text-[13px] mt-2"><span className="font-semibold">Fails against:</span> {v.against_whom.join(", ")}</div>}
      {Array.isArray(v.indefensible_elements) && v.indefensible_elements.length > 0 && (
        <div className="mt-3"><div className="text-[13px] font-semibold mb-1">Indefensible elements</div>
          {v.indefensible_elements.map((el: Ev, i: number) => <div key={i} className="text-[13px] mt-1">• {el.element || el.argument} {el.by && <span className="text-gray-500">— {nameOf(el.by)}</span>}</div>)}
        </div>
      )}
      {Array.isArray(v.remediation) && v.remediation.length > 0 && (
        <div className="mt-3"><div className="text-[13px] font-semibold mb-1">What would make it defensible</div>
          {v.remediation.map((r: string, i: number) => <div key={i} className="text-[13px] mt-1">• {r}</div>)}
        </div>
      )}
      {v.composition_summary && <div className="text-[11px] text-gray-500 mt-3">{v.composition_summary}</div>}
    </div>
  );
}
