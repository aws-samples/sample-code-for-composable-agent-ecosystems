"use client";
/**
 * Quorum ADVANCED settings, chosen before the room convenes (quorum/index.html §12).
 *
 * Why this exists: every knob that governs the room used to be a literal in Python, so we could not
 * experiment, could not compare two runs, and could not attribute an outcome to a setting. The values
 * chosen here are validated server-side (fail closed) and recorded with the run as
 * `_run_config.json`, next to `_run_metrics.json` — so a comparison is a diff rather than an argument
 * from memory.
 *
 * Defaults here MUST match tessera_platform/quorum/settings.py DEFAULTS: an untouched panel sends {}.
 */
import { useMemo, useState } from "react";

export type QuorumSettings = Record<string, any>;


/** Verified invokable in this account/region. Family diversity is the point: two sizes of the same
 *  family share the same agreement bias, so a "balanced" room draws across VENDORS. */
const MODELS: { id: string; label: string }[] = [
  { id: "", label: "Platform default (Sonnet 5)" },
  // Sonnet 5 / Opus 5 reject the `temperature` field; the runtime discovers that while probing at
  // launch and omits it for those models. Measured: Sonnet 5 in every seat reached coverage 1.0 on the
  // review rubric and was the only configuration to raise the panel's standing blind spot.
  { id: "us.anthropic.claude-sonnet-5", label: "Claude Sonnet 5 (Anthropic) — best measured" },
  { id: "us.anthropic.claude-opus-5", label: "Claude Opus 5 (Anthropic)" },
  { id: "us.anthropic.claude-sonnet-4-5-20250929-v1:0", label: "Claude Sonnet 4.5 (Anthropic)" },
  { id: "us.anthropic.claude-opus-4-5-20251101-v1:0", label: "Claude Opus 4.5 (Anthropic)" },
  { id: "us.anthropic.claude-haiku-4-5-20251001-v1:0", label: "Claude Haiku 4.5 (Anthropic, fast)" },
  { id: "us.amazon.nova-pro-v1:0", label: "Nova Pro (Amazon)" },
  { id: "us.meta.llama3-1-70b-instruct-v1:0", label: "Llama 3.1 70B (Meta)" },
  { id: "us.deepseek.r1-v1:0", label: "DeepSeek R1 (reasoning — needs a 1200+ token turn budget)" },
];
const MEMBER_MODES: { id: string; label: string; hint: string }[] = [
  { id: "", label: "Same as chair/default", hint: "one model in every seat — the room can agree because it is one mind wearing six name badges" },
  { id: "balance", label: "Balance across vendors", hint: "each seat is assigned a model from the pool at convene, so disagreement has somewhere to come from" },
  { id: "pick", label: "Pick one for all members", hint: "every member on a specific model, with the chair pinned separately" },
];

const PRESETS: Record<string, { label: string; hint: string; settings: QuorumSettings }> = {
  default: {
    label: "Default",
    hint: "The measured-best configuration: Sonnet 5 in every seat, a contrarian panel, a deterministic turn allocator and no board self-review. Reached coverage 1.0 at lower cost than the previous baseline.",
    settings: {},
  },
  fast: {
    label: "Fast",
    hint: "Fewest model calls per turn. Gates prevent rather than regenerate; the board never self-reviews.",
    settings: {
      gates: { mode: "prevent", max_retries_per_turn: 0 },
      whiteboard: { critique: { when: "off" }, sketch_calls_per_turn: 1 },
      models: { latency_budget_per_turn_s: 10 },
    },
  },
  rigorous: {
    label: "Rigorous",
    hint: "Quality over latency: regenerate faulty turns, review drawings, and invite the human every 4 turns.",
    settings: {
      gates: { mode: "reject_and_regenerate", max_retries_per_turn: 1 },
      whiteboard: { critique: { when: "on_problems" } },
      floor: { chair_offers_floor_every: 4 },
    },
  },
  prev: {
    label: "Previous baseline",
    hint: "The pre-experiment configuration — Sonnet 4.5, a model moderator, a balanced panel and board self-review on. Kept for comparison against the current default.",
    settings: {
      models: { default: "us.anthropic.claude-sonnet-4-5-20250929-v1:0", max_tokens: { turn: 520 } },
      personas: { agreeableness: "balanced" },
      whiteboard: { critique: { when: "on_problems" } },
      room: { turn_allocation: "moderated" },
    },
  },
  diverse: {
    label: "Model diversity",
    hint: "Chair and verdict pinned; member seats spread across vendors. Isolates the effect of one model in every seat.",
    settings: { models: { members: "balance" } },
  },
  chaired: {
    label: "Chaired agenda",
    hint: "A moderator owns the agenda — opens, parks and closes items — instead of it emerging from self-labelled topics.",
    settings: {
      room: { chair_mode: "moderator", agenda_ownership: "chaired" },
    },
  },
};

/** Only the paths that differ from the panel's own defaults, for the pre-launch summary. */
function flatten(obj: any, prefix = ""): Record<string, any> {
  const out: Record<string, any> = {};
  for (const [k, v] of Object.entries(obj || {})) {
    const path = prefix ? `${prefix}.${k}` : k;
    if (v && typeof v === "object" && !Array.isArray(v)) Object.assign(out, flatten(v, path));
    else out[path] = v;
  }
  return out;
}

function setPath(obj: QuorumSettings, path: string, value: any): QuorumSettings {
  const next = JSON.parse(JSON.stringify(obj || {}));
  const parts = path.split(".");
  let cur: any = next;
  parts.slice(0, -1).forEach((p) => { cur[p] = cur[p] || {}; cur = cur[p]; });
  cur[parts[parts.length - 1]] = value;
  return next;
}
function getPath(obj: QuorumSettings, path: string, dflt: any) {
  let cur: any = obj;
  for (const p of path.split(".")) { if (cur == null || typeof cur !== "object" || !(p in cur)) return dflt; cur = cur[p]; }
  return cur;
}

type Field =
  | { path: string; label: string; hint: string; kind: "enum"; options: string[]; dflt: string }
  | { path: string; label: string; hint: string; kind: "num"; min: number; max: number; step?: number; dflt: number }
  | { path: string; label: string; hint: string; kind: "bool"; dflt: boolean };

const GROUPS: { title: string; note: string; fields: Field[] }[] = [
  {
    title: "The room",
    note: "Who runs the meeting, and who owns the agenda. Today the agenda is a by-product of topics members label themselves — nobody owns it.",
    fields: [
      { path: "room.chair_mode", label: "Chair", hint: "facilitator synthesises and draws but has no authority; moderator owns the agenda", kind: "enum", options: ["facilitator", "moderator", "none"], dflt: "facilitator" },
      { path: "room.agenda_ownership", label: "Agenda", hint: "emergent = members self-label topics; chaired = the chair opens/parks/closes items", kind: "enum", options: ["emergent", "chaired", "preset"], dflt: "emergent" },
      { path: "room.turn_allocation", label: "Turn allocation", hint: "who speaks next", kind: "enum", options: ["fairness_first", "moderated", "round_robin"], dflt: "fairness_first" },
      { path: "room.human_role", label: "Your role", hint: "design_owner answers for the proposal; observer is not addressed; chair runs the room", kind: "enum", options: ["design_owner", "observer", "chair"], dflt: "design_owner" },
      { path: "room.seat_proponent", label: "Seat a proponent", hint: "off in an interactive review, because you are the proponent", kind: "bool", dflt: false },
    ],
  },
  {
    title: "The members",
    note: "How hard they push. Agreeableness decides whether a member concedes or presses when challenged.",
    fields: [
      { path: "personas.agreeableness", label: "Agreeableness", hint: "contrarian presses; accommodating concedes early (and risks consensus collapse)", kind: "enum", options: ["contrarian", "balanced", "accommodating"], dflt: "contrarian" },
    ],
  },
  {
    title: "Quality gates",
    note: "This is the latency dial. A rejected turn is REGENERATED, and every regeneration is another model call — measured at a third of turns.",
    fields: [
      { path: "gates.mode", label: "Mode", hint: "prevent = tell the model what is allowed up front; reject_and_regenerate = catch it after; warn_only = log and allow", kind: "enum", options: ["prevent", "single_retry", "reject_and_regenerate", "warn_only", "off"], dflt: "reject_and_regenerate" },
      { path: "gates.max_retries_per_turn", label: "Max retries / turn", hint: "0 means a faulty turn yields instead of costing another call", kind: "num", min: 0, max: 3, dflt: 1 },
      { path: "gates.novelty.threshold", label: "Novelty threshold", hint: "similarity above this counts as repeating the room", kind: "num", min: 0, max: 1, step: 0.05, dflt: 0.55 },
      { path: "gates.saturation.remarks", label: "Remarks before a topic saturates", hint: "how long the room may work one topic", kind: "num", min: 1, max: 20, dflt: 3 },
      { path: "gates.saturation.member_cap", label: "Remarks per member per topic", hint: "stops one member owning a topic", kind: "num", min: 1, max: 20, dflt: 2 },
      { path: "gates.saturation.count_concessions", label: "Concessions count toward saturation", hint: "off lets members keep agreeing with each other", kind: "bool", dflt: true },
    ],
  },
  {
    title: "The floor",
    note: "How the room reaches you. Measured failure: a session of nine turns asked you nothing at all.",
    fields: [
      { path: "floor.pause_on", label: "Pause for you on", hint: "question_only is strictest; any_directed stops for every mention and risks ping-pong", kind: "enum", options: ["question_only", "explicit_address", "any_directed", "never"], dflt: "explicit_address" },
      { path: "floor.pause_rate_limit_turns", label: "Min turns between pauses", hint: "a real question is never rate-limited", kind: "num", min: 0, max: 20, dflt: 2 },
      { path: "floor.chair_offers_floor_every", label: "Chair invites you every N turns", hint: "0 = never; you can still raise your hand", kind: "num", min: 0, max: 50, dflt: 0 },
    ],
  },
  {
    title: "The whiteboard",
    note: "Drawing costs a model call, and self-review costs another — the slowest call in the system.",
    fields: [
      { path: "whiteboard.enabled", label: "Enabled", hint: "per-ecosystem default applies when untouched", kind: "bool", dflt: false },
      { path: "whiteboard.sketch_calls_per_turn", label: "Board calls per turn", hint: "0 stops drawing entirely; this was 3 when turns took 39s", kind: "num", min: 0, max: 5, dflt: 1 },
      { path: "whiteboard.critique.when", label: "Self-review", hint: "the member looks at its own drawing before the room does", kind: "enum", options: ["off", "on_problems", "always"], dflt: "off" },
      { path: "whiteboard.critique.attach_image", label: "Show it the rendered image", hint: "visual_only pays for the image when the fault is visual", kind: "enum", options: ["never", "visual_only", "always"], dflt: "visual_only" },
      { path: "whiteboard.node_cap", label: "Max nodes per sketch", hint: "keeps a diagram legible", kind: "num", min: 2, max: 12, dflt: 7 },
    ],
  },
  {
    title: "Budgets and limits",
    note: "",
    fields: [
      { path: "models.max_tokens.turn", label: "Tokens per member turn", hint: "reasoning models spend this thinking — DeepSeek R1 produced no usable turn at 520 and needs 1200+", kind: "num", min: 200, max: 4000, step: 100, dflt: 1200 },
      { path: "models.latency_budget_per_turn_s", label: "Latency budget per turn (s)", hint: "over budget, optional calls (self-review, then drawing) are dropped", kind: "num", min: 1, max: 600, dflt: 20 },
      { path: "limits.max_rounds", label: "Max rounds", hint: "", kind: "num", min: 1, max: 20, dflt: 4 },
      { path: "limits.converge_after_passes", label: "Converge after N passes", hint: "consecutive members with nothing to add", kind: "num", min: 1, max: 20, dflt: 3 },
    ],
  },
];

/** Mirrors settings.py DEFAULTS.models.balance_pool. */
const DEFAULT_POOL = [
  "us.anthropic.claude-sonnet-4-5-20250929-v1:0",
  "us.amazon.nova-pro-v1:0",
  "us.anthropic.claude-haiku-4-5-20251001-v1:0",
];

export function QuorumAdvancedPanel({ value, onChange }: {
  value: QuorumSettings;
  onChange: (s: QuorumSettings) => void;
}) {
  const [open, setOpen] = useState<Record<string, boolean>>({ "Quality gates": true });
  const [preset, setPreset] = useState("default");
  const initialMembers = String(getPath(value, "models.members", "") || "");
  const [memberMode, setMemberMode] = useState(
    initialMembers === "" || initialMembers === "balance" ? initialMembers : "pick");
  const [pickedMember, setPickedMember] = useState(
    initialMembers && initialMembers !== "balance" ? initialMembers : "");
  const changed = useMemo(() => flatten(value), [value]);
  const changedCount = Object.keys(changed).length;

  return (
    <div className="rounded-lg border border-tessera-border bg-tessera-card p-3 space-y-3">
      <div className="flex items-center justify-between gap-2">
        <div>
          <div className="text-sm font-medium">Advanced — how the room runs</div>
          <div className="text-xs text-tessera-muted">
            Recorded with the run, so this one can be compared with the next.
          </div>
        </div>
        {changedCount > 0 && (
          <button type="button" onClick={() => { onChange({}); setPreset("default"); }}
                  className="text-xs px-2 py-1 rounded border border-tessera-border hover:bg-tessera-bg">
            Reset ({changedCount})
          </button>
        )}
      </div>

      <div className="flex flex-wrap gap-1.5">
        {Object.entries(PRESETS).map(([k, p]) => (
          <button key={k} type="button" title={p.hint}
                  onClick={() => { setPreset(k); onChange(JSON.parse(JSON.stringify(p.settings))); }}
                  className={`text-xs px-2 py-1 rounded border ${preset === k
                    ? "border-tessera-accent text-tessera-accent" : "border-tessera-border hover:bg-tessera-bg"}`}>
            {p.label}
          </button>
        ))}
      </div>
      <div className="text-xs text-tessera-muted">{PRESETS[preset]?.hint}</div>

      {/* Models — rendered by hand because "who sits where" is not a simple enum. The DAG's model
          profile has no meaning for a room, so it is hidden for quorum and this replaces it. */}
      <div className="border-t border-tessera-border pt-2">
        <button type="button" onClick={() => setOpen((o) => ({ ...o, models: !o.models }))}
                className="w-full flex items-center justify-between text-left">
          <span className="text-xs font-medium">Models — who sits where</span>
          <span className="text-xs text-tessera-muted">{open.models ? "−" : "+"}</span>
        </button>
        {open.models && (
          <div className="mt-2 space-y-2">
            <div className="text-[11px] leading-snug text-tessera-muted">
              The chair is pinned separately, and the verdict is rendered on the chair&apos;s model: if the
              judge changes between two runs, the comparison measures the judge instead of the room.
            </div>
            <label className="block">
              <div className="flex items-center justify-between gap-2">
                <span className={`text-xs ${getPath(value, "models.default", "") ? "text-tessera-accent" : ""}`}>
                  Every seat
                </span>
                <select value={String(getPath(value, "models.default", ""))}
                        onChange={(e) => onChange(setPath(value, "models.default", e.target.value))}
                        className="text-xs bg-tessera-bg border border-tessera-border rounded px-1.5 py-0.5 max-w-[60%]">
                  {MODELS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                </select>
              </div>
              <div className="text-[11px] text-tessera-muted">
                the baseline for the whole room — the chair and members below override it
              </div>
            </label>

            <label className="block">
              <div className="flex items-center justify-between gap-2">
                <span className={`text-xs ${getPath(value, "models.chair", "") ? "text-tessera-accent" : ""}`}>
                  Chair / facilitator
                </span>
                <select value={String(getPath(value, "models.chair", ""))}
                        onChange={(e) => onChange(setPath(value, "models.chair", e.target.value))}
                        className="text-xs bg-tessera-bg border border-tessera-border rounded px-1.5 py-0.5 max-w-[60%]">
                  {MODELS.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                </select>
              </div>
              <div className="text-[11px] text-tessera-muted">also runs turn allocation and the verdict</div>
            </label>

            <label className="block">
              <div className="flex items-center justify-between gap-2">
                <span className={`text-xs ${memberMode ? "text-tessera-accent" : ""}`}>Members</span>
                <select value={memberMode}
                        onChange={(e) => {
                          const mode = e.target.value;
                          setMemberMode(mode);
                          onChange(setPath(value, "models.members", mode === "pick" ? (pickedMember || MODELS[1].id) : mode));
                        }}
                        className="text-xs bg-tessera-bg border border-tessera-border rounded px-1.5 py-0.5 max-w-[60%]">
                  {MEMBER_MODES.map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                </select>
              </div>
              <div className="text-[11px] text-tessera-muted">
                {MEMBER_MODES.find((m) => m.id === memberMode)?.hint}
              </div>
            </label>

            {memberMode === "pick" && (
              <label className="flex items-center justify-between gap-2">
                <span className="text-xs">All members on</span>
                <select value={pickedMember}
                        onChange={(e) => { setPickedMember(e.target.value); onChange(setPath(value, "models.members", e.target.value)); }}
                        className="text-xs bg-tessera-bg border border-tessera-border rounded px-1.5 py-0.5 max-w-[60%]">
                  {MODELS.filter((m) => m.id).map((m) => <option key={m.id} value={m.id}>{m.label}</option>)}
                </select>
              </label>
            )}

            {memberMode === "balance" && (
              <div>
                <div className="text-xs mb-1">Pool — seats are dealt round-robin at convene</div>
                <div className="space-y-1">
                  {MODELS.filter((m) => m.id).map((m) => {
                    const pool: string[] = getPath(value, "models.balance_pool", DEFAULT_POOL) as string[];
                    const on = pool.includes(m.id);
                    return (
                      <label key={m.id} className="flex items-center gap-2 text-[11px]">
                        <input type="checkbox" checked={on}
                               onChange={() => onChange(setPath(value, "models.balance_pool",
                                 on ? pool.filter((x) => x !== m.id) : [...pool, m.id]))} />
                        <span className={on ? "" : "text-tessera-muted"}>{m.label}</span>
                      </label>
                    );
                  })}
                </div>
                <div className="text-[11px] text-tessera-muted mt-1">
                  A room needs at least one model in the pool, and the resolved seat→model map is
                  recorded with the run so a balanced room can be reproduced.
                </div>
              </div>
            )}
          </div>
        )}
      </div>

      {GROUPS.map((g) => (
        <div key={g.title} className="border-t border-tessera-border pt-2">
          <button type="button" onClick={() => setOpen((o) => ({ ...o, [g.title]: !o[g.title] }))}
                  className="w-full flex items-center justify-between text-left">
            <span className="text-xs font-medium">{g.title}</span>
            <span className="text-xs text-tessera-muted">{open[g.title] ? "−" : "+"}</span>
          </button>
          {open[g.title] && (
            <div className="mt-2 space-y-2">
              {g.note && <div className="text-[11px] leading-snug text-tessera-muted">{g.note}</div>}
              {g.fields.map((f) => {
                const cur = getPath(value, f.path, f.dflt);
                const isChanged = cur !== f.dflt;
                return (
                  <label key={f.path} className="block">
                    <div className="flex items-center justify-between gap-2">
                      <span className={`text-xs ${isChanged ? "text-tessera-accent" : ""}`}>{f.label}</span>
                      {f.kind === "bool" ? (
                        <input type="checkbox" checked={!!cur}
                               onChange={(e) => onChange(setPath(value, f.path, e.target.checked))} />
                      ) : f.kind === "enum" ? (
                        <select value={String(cur)} onChange={(e) => onChange(setPath(value, f.path, e.target.value))}
                                className="text-xs bg-tessera-bg border border-tessera-border rounded px-1.5 py-0.5">
                          {f.options.map((o) => <option key={o} value={o}>{o}</option>)}
                        </select>
                      ) : (
                        <input type="number" value={Number(cur)} min={f.min} max={f.max} step={f.step || 1}
                               onChange={(e) => onChange(setPath(value, f.path, Number(e.target.value)))}
                               className="w-20 text-xs bg-tessera-bg border border-tessera-border rounded px-1.5 py-0.5" />
                      )}
                    </div>
                    {f.hint && <div className="text-[11px] text-tessera-muted pr-16">{f.hint}</div>}
                  </label>
                );
              })}
            </div>
          )}
        </div>
      ))}

      {changedCount > 0 && (
        <div className="border-t border-tessera-border pt-2">
          <div className="text-xs font-medium mb-1">Changed from default</div>
          <ul className="text-[11px] font-mono text-tessera-muted space-y-0.5">
            {Object.entries(changed).map(([k, v]) => <li key={k}>{k} = {String(v)}</li>)}
          </ul>
        </div>
      )}
    </div>
  );
}
