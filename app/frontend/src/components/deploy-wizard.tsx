"use client";

import { useState, useEffect, useRef } from "react";
import { BuilderGraph } from "./builder-graph";
import { useClientWebSocket } from "@/hooks/use-client-ws";
import { ScorecardPanel, type Scorecard, type GateOutcome } from "@/components/scorecard-panel";
import ReactMarkdown from "react-markdown";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";
const WS_URL = process.env.NEXT_PUBLIC_WS_URL || API_URL.replace(/^http/, "ws");
async function apiFetch(url: string, init?: RequestInit): Promise<Response> { const { fetchAuthSession } = await import("aws-amplify/auth"); let token = ""; try { const session = await fetchAuthSession({ forceRefresh: false }); token = session.tokens?.idToken?.toString() || ""; } catch {} const headers = new Headers(init?.headers); if (token) headers.set("Authorization", `Bearer ${token}`); return fetch(url, { ...init, headers }); }

interface OutcomeArtifact { id: string; agent: string; format: string; save_as: string; download_label: string }

interface QualityConfig {
  report: boolean;
  challenger: "off" | "final" | "per_level";
  block_below: "STRONG" | "ADEQUATE" | "WEAK";
  grounding: boolean;
  gates: { citation: string; format: string; grounding: string; output_kind: string };
}

interface DeployWizardProps {
  yaml: string;
  buildTranscript?: Array<{ role: string; content: string }>;
  agents: any[];
  ecoName: string;
  ecoDescription: string;
  ecoDomain?: string;
  ecoTags?: string[];
  setEcoName?: (v: string) => void;
  setEcoDomain?: (v: string) => void;
  setEcoTags?: (v: string[]) => void;
  outcomeKind?: "report" | "file_artifact";
  outcomeArtifacts?: OutcomeArtifact[];
  setOutcomeKind?: (k: "report" | "file_artifact") => void;
  setOutcomeArtifacts?: (a: OutcomeArtifact[]) => void;
  audience?: string;
  setAudience?: (a: string) => void;
  quality?: QualityConfig;
  setChallenger?: (v: "off" | "final" | "per_level") => void;
  setBlockBelow?: (v: "STRONG" | "ADEQUATE" | "WEAK") => void;
  setGrounding?: (v: boolean) => void;
  setGates?: (g: { citation: string; format: string; grounding: string; output_kind: string }) => void;
  onClose: () => void;
}

type Step = "summary" | "outputs" | "quality" | "saved" | "deploying" | "done" | "evaluation";

export function DeployWizard({ yaml, buildTranscript, agents, ecoName, ecoDescription, ecoDomain, ecoTags, setEcoName, setEcoDomain, setEcoTags, outcomeKind, outcomeArtifacts, setOutcomeKind, setOutcomeArtifacts, audience, setAudience, quality, setChallenger, setBlockBelow, setGrounding, setGates, onClose }: DeployWizardProps) {
  const [step, setStep] = useState<Step>("summary");
  const [error, setError] = useState("");
  const [asl, setAsl] = useState<any>(null);
  // Domains replace categories: load the governed domain list so the operator
  // assigns this ecosystem to a domain (which drives knowledge inheritance + the
  // ecosystems page grouping). id is stored; name is shown.
  const [domainOptions, setDomainOptions] = useState<{ id: string; name: string }[]>([]);
  useEffect(() => {
    apiFetch(`${API_URL}/api/domains`).then(r => r.json())
      .then(d => setDomainOptions(Array.isArray(d) ? d.map((x: any) => ({ id: x.id, name: x.name || x.id })) : []))
      .catch(() => {});
  }, []);

  const ecoId = ecoName.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");

  const handleSave = async () => {
    try {
      const resp = await apiFetch(`${API_URL}/api/ecosystems/${ecoId}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ yaml, build_transcript: buildTranscript || [] }),
      });
      const data = await resp.json();
      if (data.saved) {
        setStep("saved");
      } else {
        setError("Save failed");
      }
    } catch (e) {
      setError("Save failed — is the API running?");
    }
  };

  const handleDeploy = async () => {
    setStep("deploying");
    try {
      const resp = await apiFetch(`${API_URL}/api/ecosystems/${ecoId}/deploy`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
      });
      const data = await resp.json();
      if (data.deployed) {
        setAsl(data.asl);
        setStep("done");
      } else {
        setError(data.error || "Deploy failed");
        setStep("saved");
      }
    } catch (e) {
      setError("Deploy failed");
      setStep("saved");
    }
  };

  return (
    <div className="fixed inset-0 bg-black/70 flex items-center justify-center z-50">
      <div className="bg-tessera-card border border-tessera-border rounded-xl w-[700px] max-h-[80vh] overflow-y-auto p-6">
        {/* Header */}
        <div className="flex items-center justify-between mb-4">
          <h2 className="text-lg font-bold">
            {step === "summary" && "Review"}
            {step === "outputs" && "Outputs"}
            {step === "quality" && "Quality"}
            {step === "saved" && "Deploy Step Function"}
            {step === "deploying" && "Deploying..."}
            {step === "done" && "Deployed ✓"}
            {step === "evaluation" && "Evaluation"}
          </h2>
          <button onClick={onClose} className="text-gray-500 hover:text-white">✕</button>
        </div>

        {/* Step indicator */}
        <div className="flex gap-2 mb-6">
          {["summary", "outputs", "quality", "saved", "done", "evaluation"].map((s, i) => (
            <div key={s} className={`flex-1 h-1 rounded ${["summary", "outputs", "quality", "saved", "deploying", "done", "evaluation"].indexOf(step) >= i ? "bg-tessera-accent" : "bg-gray-700"}`} />
          ))}
        </div>

        {error && <div className="text-red-400 text-xs mb-3 bg-red-500/10 border border-red-500/20 rounded p-2">{error}</div>}

        {/* Step: Summary */}
        {step === "summary" && (
          <div>
            <div className="mb-4">
              {/* Name + domain are builder-generated but overridable here. */}
              {setEcoName ? (
                <>
                  <label className="text-[10px] uppercase tracking-wide text-gray-500">Name</label>
                  <input
                    value={ecoName}
                    onChange={e => setEcoName(e.target.value)}
                    className="w-full bg-gray-900 border border-tessera-border rounded px-2.5 py-1.5 text-sm text-white mt-0.5 mb-2"
                  />
                </>
              ) : (
                <h3 className="text-sm font-semibold text-white mb-1">{ecoName}</h3>
              )}
              <p className="text-xs text-gray-400">{ecoDescription}</p>

              <div className="mt-3">
                <label className="text-[10px] uppercase tracking-wide text-gray-500">Domain</label>
                {setEcoDomain ? (
                  <>
                    <select
                      value={ecoDomain || ""}
                      onChange={e => setEcoDomain(e.target.value)}
                      className="w-full bg-gray-900 border border-tessera-border rounded px-2.5 py-1.5 text-sm text-white mt-0.5"
                    >
                      <option value="">— Unassigned —</option>
                      {domainOptions.map(d => <option key={d.id} value={d.id}>{d.name}</option>)}
                    </select>
                    <p className="text-[10px] text-gray-500 mt-1">The domain owns this ecosystem's shared knowledge taxonomy; assigning it lets agents reuse the domain's catalogs.</p>
                  </>
                ) : (
                  <p className="text-xs text-gray-300 mt-0.5">{(domainOptions.find(d => d.id === ecoDomain)?.name) || ecoDomain || "Unassigned"}</p>
                )}
              </div>

              {setEcoTags && (
                <div className="mt-3">
                  <label className="text-[10px] uppercase tracking-wide text-gray-500">Tags <span className="text-gray-600 normal-case">(comma-separated)</span></label>
                  <input
                    value={(ecoTags || []).join(", ")}
                    onChange={e => setEcoTags(e.target.value.split(",").map(t => t.trim().toLowerCase()).filter(Boolean).slice(0, 6))}
                    placeholder="e.g. trading, equities, risk"
                    className="w-full bg-gray-900 border border-tessera-border rounded px-2.5 py-1.5 text-sm text-white mt-0.5"
                  />
                </div>
              )}

              <p className="text-xs text-gray-500 mt-3">{agents.length} agents • ID: {ecoId}</p>
            </div>

            <div className="mb-4 h-48 bg-gray-900 rounded-lg overflow-hidden">
              <BuilderGraph key={agents.map(a => a.id).join(",")} agents={agents} onUpdateDeps={() => {}} />
            </div>

            <details className="mb-4">
              <summary className="text-xs text-gray-500 cursor-pointer">View YAML</summary>
              <pre className="text-[10px] text-gray-400 font-mono mt-2 max-h-40 overflow-y-auto bg-gray-900 p-2 rounded">{yaml}</pre>
            </details>

            <div className="flex gap-2 justify-end">
              <button onClick={onClose} className="px-4 py-2 text-sm text-gray-400 hover:text-white">Cancel</button>
              <button onClick={() => setStep("outputs")} className="px-4 py-2 bg-tessera-gradient rounded-lg text-sm font-semibold">Next: Outputs →</button>
            </div>
          </div>
        )}

        {/* Step: Outputs — confirm what the run produces */}
        {step === "outputs" && (
          <div>
            <p className="text-xs text-gray-400 mb-3">Confirm what this ecosystem produces. A downloadable deliverable needs a producing agent and a filename.</p>
            {setOutcomeKind && setOutcomeArtifacts && (
              <div className="mb-4 bg-gray-900 border border-tessera-border rounded-lg p-3">
                <div className="flex gap-2 mb-3">
                  <button
                    onClick={() => setOutcomeKind("report")}
                    className={`flex-1 px-2 py-2 text-[11px] rounded border transition ${outcomeKind !== "file_artifact" ? "bg-tessera-accent/20 text-tessera-accent border-tessera-accent/40" : "text-gray-400 border-tessera-border hover:border-gray-600"}`}
                  >📊 Report only (no download)</button>
                  <button
                    onClick={() => {
                      setOutcomeKind("file_artifact");
                      if (!outcomeArtifacts || outcomeArtifacts.length === 0) {
                        const last = agents[agents.length - 1];
                        setOutcomeArtifacts([{ id: "deliverable", agent: last?.id || "", format: "html", save_as: `${(ecoName || "output").toLowerCase().replace(/[^a-z0-9]+/g, "-")}.html`, download_label: "Download" }]);
                      }
                    }}
                    className={`flex-1 px-2 py-2 text-[11px] rounded border transition ${outcomeKind === "file_artifact" ? "bg-tessera-accent/20 text-tessera-accent border-tessera-accent/40" : "text-gray-400 border-tessera-border hover:border-gray-600"}`}
                  >📄 Downloadable file(s)</button>
                </div>
                {outcomeKind === "file_artifact" && (
                  <div className="space-y-2">
                    {(outcomeArtifacts || []).map((art, i) => {
                      const arr = outcomeArtifacts || [];
                      const update = (patch: Partial<OutcomeArtifact>) => setOutcomeArtifacts(arr.map((x, j) => j === i ? { ...x, ...patch } : x));
                      return (
                        <div key={i} className="bg-gray-800 border border-tessera-border rounded p-2 space-y-1.5">
                          <div className="flex gap-1.5">
                            <select value={art.format} onChange={e => {
                              const fmt = e.target.value;
                              const base = (art.save_as || "deliverable").replace(/\.[a-z0-9]+$/i, "");
                              const ext = fmt === "markdown" ? "md" : fmt === "bundle" ? "zip" : fmt;
                              update({ format: fmt, save_as: `${base}.${ext}` });
                            }} className="bg-gray-900 border border-tessera-border rounded px-2 py-1 text-[11px] text-white">
                              {["html", "pdf", "csv", "yaml", "json", "markdown", "bundle"].map(f => <option key={f} value={f}>{f === "bundle" ? "BUNDLE (.zip)" : f.toUpperCase()}</option>)}
                            </select>
                            <select value={art.agent} onChange={e => update({ agent: e.target.value })} className="flex-1 bg-gray-900 border border-tessera-border rounded px-2 py-1 text-[11px] text-white" title="Producing agent">
                              <option value="">— producing agent —</option>
                              {agents.map(a => <option key={a.id} value={a.id}>{a.id}</option>)}
                            </select>
                            {arr.length > 1 && (
                              <button onClick={() => setOutcomeArtifacts(arr.filter((_, j) => j !== i))} className="text-gray-500 hover:text-red-400 text-xs px-1">✕</button>
                            )}
                          </div>
                          <input value={art.save_as} onChange={e => update({ save_as: e.target.value })} placeholder="filename.html" className="w-full bg-gray-900 border border-tessera-border rounded px-2 py-1 text-[11px] text-white font-mono" />
                          <input value={art.download_label} onChange={e => update({ download_label: e.target.value })} placeholder="Download label" className="w-full bg-gray-900 border border-tessera-border rounded px-2 py-1 text-[11px] text-white" />
                        </div>
                      );
                    })}
                    <button
                      onClick={() => setOutcomeArtifacts([...(outcomeArtifacts || []), { id: `artifact_${(outcomeArtifacts || []).length + 1}`, agent: agents[agents.length - 1]?.id || "", format: "html", save_as: "", download_label: "Download" }])}
                      className="text-[11px] text-tessera-accent hover:underline"
                    >+ add artifact</button>
                    {(outcomeArtifacts || []).some(a => !a.agent || !a.save_as) && (
                      <p className="text-[10px] text-amber-400">⚠ Each artifact needs a producing agent and filename.</p>
                    )}
                    {setAudience && (
                      <div className="pt-2 mt-1 border-t border-tessera-border">
                        <label className="block text-[11px] text-gray-200 font-medium mb-1">
                          Audience <span className="text-gray-500 font-normal">— who reads &amp; acts on this deliverable?</span>
                        </label>
                        <input
                          value={audience || ""}
                          onChange={e => setAudience(e.target.value)}
                          placeholder='e.g. "board of directors", "the customer", "a regulator", "a clinician"'
                          className="w-full bg-gray-900 border border-tessera-border rounded px-2 py-1 text-[11px] text-white"
                        />
                        <p className="text-[10px] text-gray-500 mt-1">
                          Set this for decision / assessment / report deliverables: the platform then renders
                          audience-appropriate provenance (Authority · Evidence · Confidence) and a verifiable
                          Assurance &amp; Provenance block, keeping internal machine detail out of the document.
                          Leave blank for creative or pure-data artifacts.
                        </p>
                      </div>
                    )}
                  </div>
                )}
              </div>
            )}
            <div className="flex gap-2 justify-end">
              <button onClick={() => setStep("summary")} className="px-4 py-2 text-sm text-gray-400 hover:text-white">← Back</button>
              <button onClick={() => setStep("quality")} className="px-4 py-2 bg-tessera-gradient rounded-lg text-sm font-semibold">Next: Quality →</button>
            </div>
          </div>
        )}

        {/* Step: Quality — recommended posture, editable */}
        {step === "quality" && quality && (
          <div>
            <p className="text-xs text-gray-400 mb-3">The builder recommended this quality posture. Review and adjust, then confirm.</p>

            {/* Challenger mode */}
            <div className="mb-4 bg-gray-900 border border-tessera-border rounded-lg p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold text-tessera-accent">Challenger</span>
                <span className="text-[10px] text-gray-500">adversarial review</span>
              </div>
              <div className="space-y-1.5">
                {([
                  { v: "per_level", label: "Per-level checkpoints", desc: "Review each layer; pause for Accept/Loop/Abort when a layer is weak. Catches bad premises early." },
                  { v: "final", label: "Final only", desc: "One end-of-run red-team (advisory)." },
                  { v: "off", label: "Off", desc: "No adversarial review." },
                ] as const).map(opt => (
                  <label key={opt.v} className={`flex items-start gap-2 p-2 rounded border cursor-pointer transition ${quality.challenger === opt.v ? "bg-tessera-accent/10 border-tessera-accent/40" : "border-tessera-border hover:border-gray-600"}`}>
                    <input type="radio" name="challenger" checked={quality.challenger === opt.v} onChange={() => setChallenger && setChallenger(opt.v)} className="mt-0.5" />
                    <span>
                      <span className="text-[12px] text-white block">{opt.label}</span>
                      <span className="text-[10px] text-gray-500">{opt.desc}</span>
                    </span>
                  </label>
                ))}
              </div>
              {quality.challenger === "per_level" && (
                <div className="flex items-center gap-2 mt-3">
                  <span className="text-[11px] text-gray-400">Pause when a layer is below</span>
                  <select value={quality.block_below} onChange={e => setBlockBelow && setBlockBelow(e.target.value as any)} className="bg-gray-800 border border-tessera-border rounded px-2 py-1 text-[11px] text-white">
                    <option value="ADEQUATE">ADEQUATE (pause on WEAK)</option>
                    <option value="STRONG">STRONG (pause unless STRONG)</option>
                    <option value="WEAK">WEAK (advisory; rarely pauses)</option>
                  </select>
                </div>
              )}
            </div>

            {/* Grounding */}
            <label className="flex items-center gap-2 mb-4 text-xs text-gray-300">
              <input type="checkbox" checked={quality.grounding} onChange={e => setGrounding && setGrounding(e.target.checked)} className="rounded" />
              Grounding checks (hallucination detection against upstream + tool results)
            </label>

            {/* Gates */}
            <div className="mb-4 bg-gray-900 border border-tessera-border rounded-lg p-3">
              <div className="flex items-center justify-between mb-2">
                <span className="text-xs font-bold text-tessera-accent">Validator gates</span>
                <span className="text-[10px] text-gray-500">on failure: ask / warn / halt</span>
              </div>
              <div className="space-y-1.5">
                {(["citation", "format", "grounding", "output_kind"] as const).map(name => (
                  <div key={name} className="flex items-center justify-between gap-2">
                    <span className="text-[11px] text-gray-300">{name}</span>
                    <select
                      value={(quality.gates as any)[name] || "ask"}
                      onChange={e => setGates && setGates({ ...quality.gates, [name]: e.target.value })}
                      className="bg-gray-800 border border-tessera-border rounded px-2 py-1 text-[11px] text-white"
                    >
                      <option value="ask">ask (pause)</option>
                      <option value="warn">warn (continue)</option>
                      <option value="halt">halt (fail)</option>
                    </select>
                  </div>
                ))}
              </div>
            </div>

            <div className="flex gap-2 justify-end">
              <button onClick={() => setStep("outputs")} className="px-4 py-2 text-sm text-gray-400 hover:text-white">← Back</button>
              <button onClick={handleSave} className="px-4 py-2 bg-tessera-gradient rounded-lg text-sm font-semibold">Save to S3 →</button>
            </div>
          </div>
        )}

        {/* Step: Saved — show Step Function graph */}
        {step === "saved" && (
          <div>
            <div className="mb-3 text-xs text-green-400">✓ YAML saved to S3 as {ecoId}.yaml</div>

            <div className="mb-4">
              <h3 className="text-sm font-semibold text-white mb-2">Step Functions State Machine</h3>
              <p className="text-xs text-gray-400 mb-3">This will be compiled from your DAG and deployed as an AWS Step Functions state machine.</p>

              <div className="h-48 bg-gray-900 rounded-lg overflow-hidden">
                <BuilderGraph key={agents.map(a => a.id).join(",") + "-sfn"} agents={agents} onUpdateDeps={() => {}} />
              </div>

              <div className="mt-3 text-[10px] text-gray-500">
                <p>• Parallel agents run concurrently in Parallel states</p>
                <p>• Sequential agents run in order based on depends_on</p>
                <p>• Each agent invokes AgentCore Runtime via Lambda</p>
              </div>
            </div>

            <div className="flex gap-2 justify-end">
              <button onClick={onClose} className="px-4 py-2 text-sm text-gray-400 hover:text-white">Close (save only)</button>
              <button onClick={handleDeploy} className="px-4 py-2 bg-tessera-gradient rounded-lg text-sm font-semibold">Compile & Deploy →</button>
            </div>
          </div>
        )}

        {/* Step: Deploying */}
        {step === "deploying" && (
          <div className="text-center py-8">
            <div className="text-2xl mb-3 animate-pulse">⚙️</div>
            <p className="text-sm text-gray-400">Compiling DAG → Step Functions ASL...</p>
            <p className="text-xs text-gray-500 mt-1">Creating state machine in AWS...</p>
          </div>
        )}

        {/* Step: Done */}
        {step === "done" && (
          <div>
            <div className="mb-3 text-xs text-green-400">✓ State machine deployed successfully</div>

            <div className="mb-4">
              <h3 className="text-sm font-semibold text-white mb-2">Deployed State Machine</h3>
              <div className="h-48 bg-gray-900 rounded-lg overflow-hidden">
                <BuilderGraph key={agents.map(a => a.id).join(",") + "-done"} agents={agents} onUpdateDeps={() => {}} />
              </div>
            </div>

            <div className="bg-gray-900 rounded p-3 mb-4">
              <p className="text-[10px] text-gray-500 mb-1">State Machine ARN:</p>
              <p className="text-xs text-gray-300 font-mono break-all">{asl?.state_machine_arn || "arn:aws:states:..."}</p>
            </div>

            <div className="mt-4 mb-3 bg-tessera-accent/5 border border-tessera-accent/20 rounded-lg p-3">
              <p className="text-xs text-gray-300">
                <span className="font-semibold text-tessera-accent">Next: Evaluation.</span> Set up an offline evaluation
                suite — decision accuracy, temporal-policy correctness, evidence faithfulness and abstention —
                that scores this ecosystem against golden cases and can gate deploy on regression. This step is
                optional; you can skip it and set it up later.
              </p>
            </div>
            <div className="flex gap-2 justify-end">
              <button onClick={onClose} className="px-4 py-2 text-sm text-gray-400 hover:text-white">Skip &amp; finish</button>
              <button onClick={() => setStep("evaluation")} className="px-4 py-2 bg-tessera-gradient rounded-lg text-sm font-semibold">Next: Set up evaluation →</button>
            </div>
          </div>
        )}

        {/* Step: Evaluation — propose → curate → approve → run → scorecard (spec §8/§9) */}
        {step === "evaluation" && (
          <EvaluationStep ecoId={ecoId} yaml={yaml} onBack={() => setStep("done")} onClose={onClose} />
        )}
      </div>
    </div>
  );
}

interface EvalProposal { evaluation?: any; golden_cases?: any[]; llm_enriched?: boolean; reply?: string; error?: string }

// EvaluationStep — drives the full Phase-5 eval flow from the Builder wizard:
//   propose (suggest mode=eval_proposal, WS async EVAL_PROPOSAL_RESULT)
//     → curate/edit → approve (suggest mode=eval_approve writes golden set +
//     returns the evaluation: block) → merge + re-save (PUT builds the Eval SM)
//     → launch (POST /eval starts the Eval State Machine) → poll → scorecard.
// Eco-AGNOSTIC — renders whatever generic metrics/cases the proposal carries.
function EvaluationStep({ ecoId, yaml, onBack, onClose }: { ecoId: string; yaml: string; onBack: () => void; onClose: () => void }) {
  const { clientId, connected, subscribe } = useClientWebSocket(WS_URL);
  const [phase, setPhase] = useState<"propose" | "review" | "approving" | "saved" | "launching" | "running" | "scored">("propose");
  const [config, setConfig] = useState<any>(null);
  const [proposal, setProposal] = useState<EvalProposal | null>(null);
  const [evalJson, setEvalJson] = useState("");
  const [casesJson, setCasesJson] = useState("");
  const [err, setErr] = useState<string | null>(null);
  const [jobId, setJobId] = useState<string | null>(null);
  const [showJson, setShowJson] = useState(false);
  const [scorecard, setScorecard] = useState<Scorecard | null>(null);
  const [scoreGate, setScoreGate] = useState<GateOutcome | null>(null);
  const [evalStatus, setEvalStatus] = useState<any>(null);
  const startedRef = useRef(false);
  const jobIdRef = useRef<string | null>(null);
  const pollRef = useRef<ReturnType<typeof setInterval> | null>(null);

  const applyProposal = (p: EvalProposal) => {
    if (p?.error) { setErr(String(p.error)); return; }
    setProposal(p);
    setEvalJson(JSON.stringify(p.evaluation ?? {}, null, 2));
    setCasesJson(JSON.stringify(p.golden_cases ?? [], null, 2));
    setPhase("review");
  };

  useEffect(() => {
    return subscribe((event: any) => {
      if (event.type !== "EVAL_PROPOSAL_RESULT") return;
      if (jobIdRef.current && event.jobId !== jobIdRef.current) return;
      jobIdRef.current = null; setJobId(null);
      applyProposal(event);
    });
  }, [subscribe]);

  const startProposal = async () => {
    setErr(null); setPhase("propose"); setProposal(null);
    try {
      const cfgRes = await apiFetch(`${API_URL}/api/ecosystems/${ecoId}`);
      const cfg = await cfgRes.json();
      setConfig(cfg);
      const resp = await apiFetch(`${API_URL}/api/ecosystems/suggest`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "eval_proposal", config: cfg, clientId }),
      });
      const data = await resp.json();
      if (data.error) throw new Error(data.error);
      if (data.status === "pending" && data.jobId) { jobIdRef.current = data.jobId; setJobId(data.jobId); }
      else applyProposal(data);   // sync fallback (no clientId)
    } catch (e: any) { setErr(e?.message || "proposal failed"); }
  };

  // Kick off once the WS is connected (so the async EVAL_PROPOSAL_RESULT reaches us).
  useEffect(() => {
    if (startedRef.current) return;
    if (!connected) return;
    startedRef.current = true;
    void startProposal();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [connected]);

  const approve = async () => {
    setErr(null);
    let evaluation: any, golden_cases: any;
    try { evaluation = JSON.parse(evalJson); golden_cases = JSON.parse(casesJson); }
    catch (e: any) { setErr("Invalid JSON in the edited suite: " + (e?.message || "")); return; }
    setPhase("approving");
    try {
      const resp = await apiFetch(`${API_URL}/api/ecosystems/suggest`, {
        method: "POST", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "eval_approve", eco_id: ecoId, approved: { evaluation, golden_cases }, config }),
      });
      const data = await resp.json();
      if (data.error) throw new Error(data.error);
      // Merge the returned evaluation: block into the eco + re-save so the
      // compiler builds the per-eco Eval State Machine.
      const put = await apiFetch(`${API_URL}/api/ecosystems/${ecoId}`, {
        method: "PUT", headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ yaml, evaluation: data.evaluation }),
      });
      const pd = await put.json();
      if (!pd.saved) throw new Error(pd.error || "re-save failed");
      setPhase("saved");
    } catch (e: any) { setErr(e?.message || "approve failed"); setPhase("review"); }
  };

  const launch = async () => {
    setErr(null); setPhase("launching");
    try {
      const resp = await apiFetch(`${API_URL}/api/ecosystems/${ecoId}/runs/_eval/launch`, { method: "POST" });
      const data = await resp.json();
      if (!resp.ok || !data.started) throw new Error(data.error || "launch failed");
      setPhase("running");
      startPolling();
    } catch (e: any) { setErr(e?.message || "launch failed"); setPhase("saved"); }
  };

  const startPolling = () => {
    if (pollRef.current) clearInterval(pollRef.current);
    const tick = async () => {
      try {
        const r = await apiFetch(`${API_URL}/api/ecosystems/${ecoId}/runs/_eval/status`);
        const d = await r.json();
        setEvalStatus(d);
        const latest = d.latest;
        if (d.scorecard_present) {
          const sr = await apiFetch(`${API_URL}/api/ecosystems/${ecoId}?artifact=scorecard`);
          if (sr.ok) { const sd = await sr.json(); setScorecard(sd.scorecard || sd); setScoreGate(sd.gate || null); }
          setPhase("scored");
          if (pollRef.current) clearInterval(pollRef.current);
        } else if (latest && latest.status && latest.status !== "RUNNING" && latest.status !== "SUCCEEDED") {
          // terminal failure with no scorecard — stop and surface status
          setPhase("scored");
          if (pollRef.current) clearInterval(pollRef.current);
        }
      } catch { /* transient poll error */ }
    };
    tick();
    pollRef.current = setInterval(tick, 5000);
  };
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current); }, []);

  // Live-parsed metric summary for the review table (from the editable JSON).
  let parsedMetrics: any[] = [];
  let parsedCaseCount = 0;
  let parsedThresholds: Record<string, number> = {};
  try { parsedMetrics = (JSON.parse(evalJson || "{}").metrics) || []; } catch { /* editing */ }
  try { parsedThresholds = (JSON.parse(evalJson || "{}").thresholds) || {}; } catch { /* editing */ }
  try { parsedCaseCount = (JSON.parse(casesJson || "[]") || []).length; } catch { /* editing */ }

  // Generic scorer descriptors — domain-blind; drives the badge + legend so the
  // proposed suite reads clearly instead of as a raw JSON dump.
  const SCORER_INFO: Record<string, { label: string; color: string; desc: string }> = {
    categorical:       { label: "accuracy",     color: "bg-sky-500/15 text-sky-300 border-sky-500/30",         desc: "exact match on a labelled output field" },
    numeric_tolerance: { label: "numeric",      color: "bg-teal-500/15 text-teal-300 border-teal-500/30",      desc: "value within a tolerance" },
    set_recall:        { label: "coverage",     color: "bg-indigo-500/15 text-indigo-300 border-indigo-500/30", desc: "required points are present" },
    citation_resolves: { label: "faithfulness", color: "bg-emerald-500/15 text-emerald-300 border-emerald-500/30", desc: "every claim traces to real evidence" },
    abstention:        { label: "abstention",   color: "bg-amber-500/15 text-amber-300 border-amber-500/30",   desc: "correctly holds/refers when evidence is thin" },
    bedrock_judge:     { label: "LLM judge",    color: "bg-fuchsia-500/15 text-fuchsia-300 border-fuchsia-500/30", desc: "independent model scores narrative quality" },
  };
  const scorerBadge = (s: string) => SCORER_INFO[s] || { label: s, color: "bg-gray-500/15 text-gray-300 border-gray-500/30", desc: "" };
  const usedScorers = Array.from(new Set(parsedMetrics.map((m: any) => m.scorer))).filter(Boolean);

  return (
    <div>
      {err && <div className="text-red-400 text-xs mb-3 bg-red-500/10 border border-red-500/20 rounded p-2">{err}</div>}

      {phase === "propose" && (
        <div className="py-6">
          <div className="bg-tessera-accent/5 border border-tessera-accent/20 rounded-lg p-3 mb-6">
            <p className="text-xs text-gray-300 leading-relaxed">
              <span className="font-semibold text-tessera-accent">What is evaluation?</span> It scores this ecosystem
              against a curated set of <em>golden cases</em> with known-correct answers — measuring decision accuracy,
              temporal-policy correctness, evidence faithfulness, and whether it correctly abstains — and can gate
              deploy on regression. The scorers are generic; the suite proposed below is grounded in{" "}
              <span className="font-mono text-gray-200">{ecoId}</span>'s own outputs. You review and curate it, then approve.
            </p>
          </div>
          <div className="text-center py-6">
            <div className="animate-spin rounded-full h-9 w-9 border-2 border-gray-700 border-t-tessera-accent mx-auto mb-4" />
            <p className="text-sm text-gray-300">{connected ? "Proposing an evaluation suite…" : "Connecting…"}</p>
            <p className="text-xs text-gray-500 mt-1">Reading the agents &amp; output contracts to draft metrics, golden cases, and judge rubrics.</p>
          </div>
        </div>
      )}

      {(phase === "review" || phase === "approving") && proposal && (
        <div>
          {/* Enrichment status */}
          <div className="flex items-center gap-2 mb-2">
            <span className="text-[11px] font-bold text-tessera-accent uppercase tracking-wide">Proposed evaluation suite</span>
            {proposal.llm_enriched === true && <span className="text-[10px] px-1.5 py-0.5 rounded bg-green-500/15 text-green-300 border border-green-500/30">✓ enriched</span>}
            {proposal.llm_enriched === false && <span className="text-[10px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-300 border border-amber-500/30">heuristic seed</span>}
          </div>

          {/* Rationale — rendered as markdown so it reads as prose, not a wall of text */}
          {proposal.reply && (
            <div className="bg-gray-900 border border-tessera-border rounded-lg p-3 mb-3 text-xs text-gray-300 leading-relaxed [&_p]:mb-2 [&_p:last-child]:mb-0 [&_strong]:text-white [&_em]:text-gray-200 [&_code]:font-mono [&_code]:text-tessera-accent">
              <ReactMarkdown>{proposal.reply}</ReactMarkdown>
            </div>
          )}

          <p className="text-[11px] text-gray-500 mb-2">
            Each metric is scored against your golden cases. Deterministic scorers run in-platform; the LLM-judge
            scorers use an independent model. Review below, edit if needed, then approve — approval writes the golden
            set and compiles this ecosystem's Evaluation State Machine.
          </p>

          {/* Metrics — badged rows with plain-English purpose */}
          <div className="bg-gray-900 border border-tessera-border rounded-lg p-3 mb-3">
            <div className="flex items-center justify-between mb-2.5">
              <span className="text-[11px] font-bold text-gray-300 uppercase tracking-wide">Metrics</span>
              <span className="text-[10px] text-gray-500">{parsedMetrics.length} metrics · {parsedCaseCount} golden cases</span>
            </div>
            <div className="space-y-1.5">
              {parsedMetrics.length === 0 && <p className="text-[11px] text-amber-400">No metrics parsed — check the suite JSON below.</p>}
              {parsedMetrics.map((m: any, i: number) => {
                const b = scorerBadge(m.scorer);
                const target = m.expected_value != null ? `= ${m.expected_value}`
                  : (parsedThresholds[m.id] != null ? `≥ ${parsedThresholds[m.id]}` : "");
                const fieldLabel = m.field || (m.builtin ? (m.builtin || []).join(", ") : m.custom_rubric ? "narrative rubric" : "—");
                return (
                  <div key={i} className="flex items-start gap-2.5 p-2 rounded bg-gray-800/60 border border-gray-800">
                    <span className={`shrink-0 text-[9px] px-1.5 py-0.5 rounded border font-medium ${b.color}`}>{b.label}</span>
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="text-[12px] font-mono text-gray-200 truncate">{m.id}</span>
                        {target && <span className="text-[10px] text-gray-400 shrink-0">{target}</span>}
                      </div>
                      <div className="text-[10px] text-gray-500 truncate">
                        <span className="font-mono">{fieldLabel}</span>{b.desc ? ` — ${b.desc}` : ""}
                      </div>
                    </div>
                  </div>
                );
              })}
            </div>
            {/* Legend of scorer types in play */}
            {usedScorers.length > 0 && (
              <div className="flex flex-wrap gap-1.5 mt-3 pt-2.5 border-t border-gray-800">
                {usedScorers.map((s: string) => {
                  const b = scorerBadge(s);
                  return <span key={s} className={`text-[9px] px-1.5 py-0.5 rounded border ${b.color}`} title={b.desc}>{b.label}</span>;
                })}
              </div>
            )}
          </div>

          <button onClick={() => setShowJson(s => !s)} className="text-[11px] text-tessera-accent hover:underline mb-2">{showJson ? "▾ Hide" : "▸ Edit"} suite JSON (curate metrics & golden cases)</button>
          {showJson && (
            <div className="space-y-2 mb-3">
              <div>
                <label className="text-[10px] uppercase tracking-wide text-gray-500">evaluation block</label>
                <textarea value={evalJson} onChange={e => setEvalJson(e.target.value)} rows={10} className="w-full bg-gray-900 border border-tessera-border rounded px-2 py-1.5 text-[11px] text-gray-200 font-mono" />
              </div>
              <div>
                <label className="text-[10px] uppercase tracking-wide text-gray-500">golden_cases (SME fills inputs/expected + labels.reviewed)</label>
                <textarea value={casesJson} onChange={e => setCasesJson(e.target.value)} rows={10} className="w-full bg-gray-900 border border-tessera-border rounded px-2 py-1.5 text-[11px] text-gray-200 font-mono" />
              </div>
            </div>
          )}

          <div className="flex gap-2 justify-end">
            <button onClick={onBack} className="px-4 py-2 text-sm text-gray-400 hover:text-white">← Back</button>
            <button onClick={startProposal} disabled={phase === "approving"} className="px-4 py-2 text-sm text-gray-400 hover:text-white">↻ Re-propose</button>
            <button onClick={approve} disabled={phase === "approving"} className="px-4 py-2 bg-tessera-gradient rounded-lg text-sm font-semibold disabled:opacity-40">{phase === "approving" ? "Approving…" : "✓ Approve & build Eval SM"}</button>
          </div>
        </div>
      )}

      {phase === "saved" && (
        <div>
          <div className="mb-3 text-xs text-green-400">✓ Golden set written to S3 and the Evaluation State Machine was compiled.</div>
          <p className="text-xs text-gray-400 mb-4">Launch an evaluation run now — it drives the ecosystem headlessly over your golden cases and produces a scorecard.</p>
          <div className="flex gap-2 justify-end">
            <button onClick={onClose} className="px-4 py-2 text-sm text-gray-400 hover:text-white">Close</button>
            <button onClick={launch} className="px-4 py-2 bg-tessera-gradient rounded-lg text-sm font-semibold">▶ Launch evaluation run</button>
          </div>
        </div>
      )}

      {(phase === "launching" || phase === "running") && (
        <div className="text-center py-8">
          <div className="text-2xl mb-3 animate-pulse">⚙️</div>
          <p className="text-sm text-gray-400">{phase === "launching" ? "Starting the Evaluation State Machine…" : "Evaluation running — scoring golden cases…"}</p>
          {evalStatus?.latest && <p className="text-xs text-gray-500 mt-1 font-mono">{evalStatus.latest.name} · {evalStatus.latest.status}</p>}
          <p className="text-[10px] text-gray-600 mt-2">This can take a few minutes. The scorecard also appears in the run view once ready.</p>
        </div>
      )}

      {phase === "scored" && (
        <div>
          {scorecard ? (
            <ScorecardPanel scorecard={scorecard} gate={scoreGate} />
          ) : (
            <div className="text-xs text-amber-400 bg-amber-500/10 border border-amber-500/20 rounded p-3">
              The evaluation run finished ({evalStatus?.latest?.status || "unknown"}) but no scorecard was produced yet.
              {evalStatus?.latest?.status !== "SUCCEEDED" && " Check the eval run in Step Functions."}
            </div>
          )}
          <div className="flex gap-2 justify-end mt-3">
            <button onClick={launch} className="px-4 py-2 text-sm text-gray-400 hover:text-white">↻ Re-run</button>
            <button onClick={onClose} className="px-4 py-2 bg-tessera-gradient rounded-lg text-sm font-semibold">Done ✓</button>
          </div>
        </div>
      )}
    </div>
  );
}
