"use client";

import { useState, useEffect } from "react";
import { BeautifyStudio } from "./beautify-studio";
import { getApiUrl } from "@/lib/runtime-config";

// Resolve the REST base at RUNTIME (window.__TESSERA_SOLO__) so the exported /solo build hits
// the eco's own API; falls back to build-time env for the platform build.
const API_URL_FALLBACK = process.env.NEXT_PUBLIC_API_URL || "";
const apiBase = () => getApiUrl() || API_URL_FALLBACK;

async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  // AWS Amplify authentication
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

/**
 * Phase 5f.4: Outcome panel.
 *
 * Renders the run's typed outcome — declared artifacts with Download
 * buttons, status indicator (complete / partial / empty / none), and
 * "Download bundle" CTA. Displayed in past-runs viewer + run-window
 * when the ecosystem declared `outcome.artifacts[]`.
 *
 * Generalises the optimizer-specific apply panel pattern to ANY
 * ecosystem. The optimizer keeps its own dedicated apply panel
 * (because it has the special apply-eco-patch flow); this panel
 * is for everything else.
 */

export interface DeclaredArtifact {
  id: string;
  agent: string;
  format: string;
  save_as: string;
  artifact_key: string;          // <agent>/<save_as> — for the artifact API
  download_label: string;
}

export interface Outcome {
  kind: string;
  declared_artifacts: DeclaredArtifact[];
  produced_artifacts: string[];
  missing_artifacts: string[];
  status: "complete" | "partial" | "empty" | "none";
}

const STATUS_STYLE: Record<string, string> = {
  complete: "bg-green-500/15 text-green-300 border-green-500/40",
  partial:  "bg-amber-500/15 text-amber-300 border-amber-500/40",
  empty:    "bg-red-500/15 text-red-300 border-red-500/40",
  none:     "bg-gray-700/30 text-gray-400 border-gray-600/40",
};

const STATUS_LABEL: Record<string, string> = {
  complete: "All artifacts produced",
  partial:  "Some artifacts missing",
  empty:    "No artifacts produced",
  none:     "No artifacts declared",
};

export function OutcomePanel({
  sessionId,
  outcome,
  running,
  compact,
}: {
  sessionId: string | null;
  outcome: Outcome | null | undefined;
  running?: boolean;
  /** When true, render only a header-style button (to sit in an existing button row). */
  compact?: boolean;
}) {
  const [downloadingId, setDownloadingId] = useState<string | null>(null);
  const [downloadingBundle, setDownloadingBundle] = useState(false);
  const [errorMsg, setErrorMsg] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const [beautifyArt, setBeautifyArt] = useState<DeclaredArtifact | null>(null);
  // Beautify versions: per-artifact saved-version manifest + the selected version.
  const [manifests, setManifests] = useState<Record<string, any>>({});
  const [selVer, setSelVer] = useState<Record<string, string>>({});

  // When the panel opens, load each HTML artifact's beautified manifest (if any)
  // and default the selection to the latest saved (published) version.
  useEffect(() => {
    if (!open || !sessionId || !outcome) return;
    outcome.declared_artifacts.forEach(async (art) => {
      const isHtml = (art.format || "").toLowerCase() === "html" || art.save_as.toLowerCase().endsWith(".html");
      if (!isHtml || !outcome.produced_artifacts.includes(art.id)) return;
      const parts = art.artifact_key.split("/");
      const basename = parts.pop();
      const dir = parts.join("/");
      const mkey = `${dir ? dir + "/" : ""}beautified/manifest.json`;
      try {
        const res = await apiFetch(`${apiBase()}/api/runs/${encodeURIComponent(sessionId)}/artifacts/${mkey}?inline=true`);
        const data = await res.json();
        if (typeof data?.content === "string") {
          const m = JSON.parse(data.content);
          setManifests((v) => ({ ...v, [art.id]: m }));
          setSelVer((s) => (s[art.id] ? s : { ...s, [art.id]: m.published || "original" }));
        }
      } catch { /* no saved versions — leave as original */ }
    });
  }, [open, sessionId, outcome]);

  if (!outcome || !sessionId) return null;
  // The outcome is an END-OF-RUN concept: while the run is still in flight the
  // declared artifacts legitimately don't exist yet, so a "deliverable missing /
  // degraded" banner would be a false alarm. Only render once the run is terminal
  // (the parent passes running=false when the run has finished).
  if (running) return null;
  // Don't render the panel for ecosystems that didn't declare artifacts —
  // shows nothing rather than a redundant "no artifacts declared" card.
  if (outcome.status === "none" || outcome.declared_artifacts.length === 0) return null;

  const downloadArtifact = async (artifactKey: string, artifactId: string) => {
    setDownloadingId(artifactId);
    setErrorMsg(null);
    try {
      const res = await apiFetch(
        `${apiBase()}/api/runs/${encodeURIComponent(sessionId)}/artifacts/${artifactKey}`,
      );
      const data = await res.json();
      if (data?.download_url) {
        window.open(data.download_url, "_blank");
      } else {
        setErrorMsg(`Download failed: ${data?.error || "unknown"}`);
      }
    } catch (e) {
      setErrorMsg(`Download request failed: ${e}`);
    } finally {
      setDownloadingId(null);
    }
  };

  const downloadBundle = async () => {
    setDownloadingBundle(true);
    setErrorMsg(null);
    try {
      const res = await apiFetch(
        `${apiBase()}/api/runs/${encodeURIComponent(sessionId)}/bundle`,
      );
      const data = await res.json();
      if (data?.download_url) {
        window.open(data.download_url, "_blank");
      } else {
        setErrorMsg(`Bundle download failed: ${data?.error || "unknown"}`);
      }
    } catch (e) {
      setErrorMsg(`Bundle request failed: ${e}`);
    } finally {
      setDownloadingBundle(false);
    }
  };
  // The artifact key to download/view for this artifact: the selected saved
  // version if one is chosen, else the original machine output.
  const effectiveKey = (art: DeclaredArtifact): string => {
    const sel = selVer[art.id];
    return sel && sel !== "original" ? sel : art.artifact_key;
  };
  const isBeautified = (art: DeclaredArtifact): boolean => {
    const sel = selVer[art.id];
    return !!sel && sel !== "original";
  };

  // Open a specific artifact version (rendered) in a new tab.
  const viewKey = async (key: string) => {
    setErrorMsg(null);
    try {
      const res = await apiFetch(
        `${apiBase()}/api/runs/${encodeURIComponent(sessionId)}/artifacts/${key}?inline=true`,
      );
      const data = await res.json();
      if (typeof data?.content === "string") {
        const url = URL.createObjectURL(new Blob([data.content], { type: "text/html" }));
        window.open(url, "_blank");
        setTimeout(() => URL.revokeObjectURL(url), 60000);
      } else if (data?.download_url) {
        window.open(data.download_url, "_blank");
      } else {
        setErrorMsg("Could not open this version.");
      }
    } catch (e) {
      setErrorMsg(`Could not open: ${e}`);
    }
  };

  const statusStyle = STATUS_STYLE[outcome.status] || STATUS_STYLE.none;
  const statusLabel = STATUS_LABEL[outcome.status] || outcome.status;
  const degraded = outcome.status === "empty" || outcome.status === "partial";

  return (
    <>
      {(() => {
        const trigger = (
          <button
            onClick={() => setOpen(true)}
            title="View the run's deliverables"
            className={compact
              ? "px-3 py-2 text-xs font-semibold rounded border border-tessera-accent/30 bg-tessera-accent/15 text-tessera-accent hover:bg-tessera-accent/25 hover:border-tessera-accent/50 transition whitespace-nowrap inline-flex items-center gap-1.5"
              : "inline-flex items-center gap-2 px-3 py-1.5 rounded-lg border border-tessera-border bg-tessera-card hover:border-tessera-accent/50 transition"}
          >
            <span className={compact ? "" : "text-sm"}>📦</span>
            <span className={compact ? "" : "text-sm font-semibold text-white"}>Outcome</span>
            <span className={`inline-block px-1.5 py-0.5 rounded border text-[9px] font-bold tracking-wider uppercase ${statusStyle}`}>{outcome.status}</span>
            {!compact && <span className="text-[11px] text-gray-400">{outcome.produced_artifacts.length}/{outcome.declared_artifacts.length}</span>}
            {degraded && <span className="text-[10px] text-amber-300">⚠</span>}
          </button>
        );
        return compact ? trigger : <div className="mb-2 shrink-0">{trigger}</div>;
      })()}

      {open && (
        <div className="fixed inset-0 z-[70] flex items-center justify-center bg-black/60 backdrop-blur-sm p-4" onClick={() => setOpen(false)}>
          <div className="bg-tessera-card border border-tessera-border rounded-xl w-full max-w-2xl max-h-[80vh] overflow-y-auto p-5 shadow-2xl" onClick={(e) => e.stopPropagation()}>
            <div className="flex items-center justify-between gap-3 mb-3">
              <div className="flex items-center gap-2 min-w-0">
                <span className="text-lg">📦</span>
                <span className="text-base font-bold text-white">Outcome</span>
                <span className={`inline-block px-1.5 py-0.5 rounded border text-[9px] font-bold tracking-wider uppercase ${statusStyle}`}>{outcome.status}</span>
                <span className="text-[11px] text-gray-400 truncate">{statusLabel} · kind: {outcome.kind} · {outcome.produced_artifacts.length}/{outcome.declared_artifacts.length} produced</span>
              </div>
              <button onClick={() => setOpen(false)} className="text-gray-400 hover:text-white text-sm shrink-0" title="Close">✕</button>
            </div>

            {degraded && (
              <div className={`mb-3 text-[11px] rounded p-2 border ${
                outcome.status === "empty"
                  ? "text-red-100 bg-red-500/10 border-red-500/40"
                  : "text-amber-100 bg-amber-500/10 border-amber-500/40"
              }`}>
                <strong>⚠ Run finished, but the deliverable is {outcome.status === "empty" ? "missing" : "incomplete"}.</strong>{" "}
                The pipeline completed, yet {outcome.missing_artifacts.length || "some"} declared artifact
                {(outcome.missing_artifacts.length === 1) ? "" : "s"} {(outcome.missing_artifacts.length === 1) ? "was" : "were"} not produced
                {outcome.missing_artifacts.length ? ` (${outcome.missing_artifacts.join(", ")})` : ""}. Treat this as a <strong>degraded</strong> run, not a clean success.
              </div>
            )}

            <div className="flex items-center justify-end mb-2">
              <button
                onClick={downloadBundle}
                disabled={downloadingBundle}
                title="Download a .zip containing every output, tool result, and artifact for this run"
                className="px-3 py-1.5 text-xs font-semibold rounded bg-gray-700/50 text-gray-200 border border-gray-600/50 hover:bg-gray-700 disabled:opacity-30 disabled:cursor-not-allowed transition whitespace-nowrap"
              >
                {downloadingBundle ? "⬇ Bundling…" : "⬇ Download bundle (.zip)"}
              </button>
            </div>

            <div className="space-y-1">
              {outcome.declared_artifacts.map((art) => {
                const produced = outcome.produced_artifacts.includes(art.id);
                return (
                  <div
                    key={art.id}
                    className={`flex items-center justify-between gap-3 px-2.5 py-1.5 rounded border text-xs ${
                      produced ? "border-green-500/30 bg-green-500/5" : "border-red-500/30 bg-red-500/5"
                    }`}
                  >
                    <div className="flex items-center gap-2 min-w-0">
                      <span className={produced ? "text-green-400" : "text-red-400"}>{produced ? "✓" : "⚠"}</span>
                      <div className="min-w-0">
                        <div className={`font-semibold truncate ${produced ? "text-gray-200" : "text-gray-400"}`}>{art.download_label}</div>
                        <div className="text-[10px] text-gray-500 truncate font-mono">{art.format} · {art.agent}/{art.save_as}</div>
                      </div>
                    </div>
                    <div className="flex items-center gap-1.5 shrink-0">
                      {produced && ((art.format || "").toLowerCase() === "html" || art.save_as.toLowerCase().endsWith(".html")) && (
                        <>
                          {manifests[art.id]?.versions?.length > 0 && (
                            <select
                              value={selVer[art.id] || "original"}
                              onChange={(e) => setSelVer((s) => ({ ...s, [art.id]: e.target.value }))}
                              title="Choose which version to view / download"
                              className="px-1.5 py-1 text-[10px] rounded border border-tessera-border bg-tessera-bg text-gray-200 max-w-[160px]"
                            >
                              <option value="original">Original (machine)</option>
                              {[...manifests[art.id].versions].reverse().map((v: any) => (
                                <option key={v.key} value={v.key}>
                                  {v.provenance} · {new Date(v.saved_at).toLocaleString()}
                                </option>
                              ))}
                            </select>
                          )}
                          <button
                            onClick={() => setBeautifyArt(art)}
                            title="Open the Beautify & Standardize studio for this deliverable"
                            className="px-2.5 py-1 text-[11px] font-semibold rounded border border-purple-400/40 bg-purple-500/15 text-purple-200 hover:bg-purple-500/25 whitespace-nowrap transition"
                          >
                            ✨ Beautify
                          </button>
                          <button
                            onClick={() => viewKey(effectiveKey(art))}
                            title="Open the selected version in a new tab"
                            className="px-2.5 py-1 text-[11px] font-semibold rounded border border-sky-400/40 bg-sky-500/15 text-sky-200 hover:bg-sky-500/25 whitespace-nowrap transition"
                          >
                            👁 View
                          </button>
                        </>
                      )}
                      <button
                        onClick={() => produced && downloadArtifact(effectiveKey(art), art.id)}
                        disabled={!produced || downloadingId === art.id}
                        title={produced ? `Download ${isBeautified(art) ? "the selected beautified version" : art.save_as}` : `Not produced — run did not emit ${art.save_as}`}
                        className={`px-2.5 py-1 text-[11px] font-semibold rounded border whitespace-nowrap transition ${
                          produced
                            ? "bg-tessera-accent/10 text-tessera-accent border-tessera-accent/30 hover:bg-tessera-accent/20 disabled:opacity-50"
                            : "bg-gray-700/30 text-gray-500 border-gray-600/40 cursor-not-allowed"
                        }`}
                      >
                        {downloadingId === art.id ? "…" : produced ? (isBeautified(art) ? "⬇ Download ✨" : "⬇ Download") : "missing"}
                      </button>
                    </div>
                  </div>
                );
              })}
            </div>

            {errorMsg && (
              <div className="mt-2 text-[11px] text-red-300 bg-red-500/10 border border-red-500/30 rounded p-2">{errorMsg}</div>
            )}
          </div>
        </div>
      )}

      {beautifyArt && sessionId && (
        <BeautifyStudio
          sessionId={sessionId}
          artifactKey={beautifyArt.artifact_key}
          artifactLabel={beautifyArt.download_label || beautifyArt.save_as}
          onClose={() => { setBeautifyArt(null); setOpen(false); }}
        />
      )}
    </>
  );
}
