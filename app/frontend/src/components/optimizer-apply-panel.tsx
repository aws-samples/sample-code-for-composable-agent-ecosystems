"use client";

import { useEffect, useState } from "react";
import { getApiUrl } from "@/lib/runtime-config";

const API_URL_FALLBACK = process.env.NEXT_PUBLIC_API_URL || "";
const apiBase = () => getApiUrl() || API_URL_FALLBACK;
async function apiFetch(url: string, init?: RequestInit): Promise<Response> { const { fetchAuthSession } = await import("aws-amplify/auth"); let token = ""; try { const session = await fetchAuthSession({ forceRefresh: false }); token = session.tokens?.idToken?.toString() || ""; } catch {} const headers = new Headers(init?.headers); if (token) headers.set("Authorization", `Bearer ${token}`); return fetch(url, { ...init, headers }); }

/**
 * Phase 5d: post-optimizer apply panel.
 *
 * Renders a banner + modal in the platform-optimizer run-window so the
 * operator can apply the patch the optimizer produced. The current MVP
 * is intentionally low-tech — the user copies the proposed YAML from
 * the patch_synthesizer's output (visible in the run window already)
 * and pastes it into the modal. Future iterations can:
 *   - Auto-extract the YAML from the run output
 *   - Render a side-by-side diff
 *   - Show per-change toggles
 *
 * For now: target_eco_id + new_yaml + (optional) approved_change_ids.
 */
export function OptimizerApplyPanel({
  optimizerSessionId,
  defaultTargetEcoId,
  isComplete,
  onApplied,
}: {
  /** session_id of the platform-optimizer run that produced this patch. */
  optimizerSessionId: string | null;
  /** Pre-filled target_eco_id if we know it from the optimize trigger. */
  defaultTargetEcoId?: string;
  /** Only show the panel after the optimizer run finishes. */
  isComplete: boolean;
  /** Called after a successful apply with the snapshot version + new SM ARN. */
  onApplied?: (result: { target_eco_id: string; snapshot_version: number; state_machine_arn: string }) => void;
}) {
  const [open, setOpen] = useState(false);
  const [targetEcoId, setTargetEcoId] = useState(defaultTargetEcoId || "");
  const [newYaml, setNewYaml] = useState("");
  const [approvedIds, setApprovedIds] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [resultMsg, setResultMsg] = useState<string | null>(null);
  const [resultKind, setResultKind] = useState<"success" | "error" | null>(null);
  // Phase 5f: auto-load the typed YAML produced by patch_synthesizer.
  // patch_synthesizer's output_format declares save_as: proposed_patch.yaml,
  // so the artifact lives at runs/<session>/patch_synthesizer/proposed_patch.yaml.
  // We fetch it inline (small text) and pre-fill the textarea — no more
  // copy-paste from the markdown output.
  const [autoLoading, setAutoLoading] = useState(false);
  const [autoLoadMsg, setAutoLoadMsg] = useState<string | null>(null);

  useEffect(() => {
    if (defaultTargetEcoId && !targetEcoId) setTargetEcoId(defaultTargetEcoId);
  }, [defaultTargetEcoId, targetEcoId]);

  // Auto-load the typed YAML when the modal opens, IF the optimizer's
  // run completed AND we don't already have YAML pasted. Best-effort —
  // if the artifact isn't found (e.g. older optimizer run before Phase 5e
  // shipped), the operator can still paste manually.
  useEffect(() => {
    if (!open) return;
    if (!optimizerSessionId) return;
    if (newYaml.trim()) return;     // user has already started editing
    let cancelled = false;
    setAutoLoading(true);
    setAutoLoadMsg(null);
    const url = `${apiBase()}/api/runs/${encodeURIComponent(optimizerSessionId)}/artifacts/patch_synthesizer/proposed_patch.yaml?inline=true`;
    apiFetch(url)
      .then(async (r) => {
        if (!r.ok) {
          throw new Error(`HTTP ${r.status}`);
        }
        return r.json();
      })
      .then((data) => {
        if (cancelled) return;
        if (data?.content) {
          setNewYaml(String(data.content));
          setAutoLoadMsg(`✓ Auto-loaded ${data.size_bytes ?? "?"}B from ${data.filename || "proposed_patch.yaml"}`);
        }
      })
      .catch((e) => {
        if (cancelled) return;
        setAutoLoadMsg(
          `Couldn't auto-load typed YAML (${e}). Paste manually from the patch_synthesizer output above.`
        );
      })
      .finally(() => { if (!cancelled) setAutoLoading(false); });
    return () => { cancelled = true; };
  }, [open, optimizerSessionId, newYaml]);

  if (!isComplete) return null;

  const submit = async () => {
    if (!targetEcoId.trim() || !newYaml.trim()) {
      setResultMsg("Target ecosystem ID and new YAML are required.");
      setResultKind("error");
      return;
    }
    setSubmitting(true);
    setResultMsg(null);
    setResultKind(null);
    try {
      const res = await apiFetch(`${apiBase()}/api/apply-eco-patch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          target_ecosystem_id: targetEcoId.trim(),
          new_yaml: newYaml,
          approved_change_ids: approvedIds
            .split(",")
            .map((s) => s.trim())
            .filter(Boolean),
          optimizer_session_id: optimizerSessionId || "",
          applied_by: "operator",
        }),
      });
      const data = await res.json();
      if (res.ok && data.applied) {
        setResultMsg(
          `✓ Applied to ${data.target_ecosystem_id}. Snapshot saved as v${data.snapshot_version}. ${
            data.deployed ? "ASL recompiled." : "Recompile pending — check logs."
          }`,
        );
        setResultKind("success");
        if (onApplied) onApplied(data);
        // Don't auto-close so the user can read the success message.
      } else {
        setResultMsg(`Apply rejected: ${data.error || "unknown reason"}${data.detail ? " — " + data.detail : ""}`);
        setResultKind("error");
      }
    } catch (e) {
      setResultMsg(`Apply request failed: ${e}`);
      setResultKind("error");
    } finally {
      setSubmitting(false);
    }
  };

  return (
    <>
      {/* Banner — sits below the run-view header */}
      <div className="mb-3 bg-amber-500/10 border border-amber-500/30 rounded-xl px-4 py-2.5 flex items-center justify-between gap-4 shrink-0">
        <div className="flex items-center gap-2 min-w-0">
          <span className="text-amber-300 text-base">⚡</span>
          <div className="min-w-0">
            <div className="text-sm font-semibold text-amber-200">Optimizer complete</div>
            <div className="text-[11px] text-amber-300/70 truncate">
              Review the patch_synthesizer's findings above, then apply the proposed YAML.
            </div>
          </div>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <button
            onClick={async () => {
              if (!optimizerSessionId) return;
              try {
                // Phase 5f: fetch a presigned URL from the artifact API and
                // open it — browser downloads the typed file directly.
                const res = await apiFetch(
                  `${apiBase()}/api/runs/${encodeURIComponent(optimizerSessionId)}/artifacts/patch_synthesizer/proposed_patch.yaml`,
                );
                const data = await res.json();
                if (data?.download_url) {
                  window.open(data.download_url, "_blank");
                } else if (data?.error) {
                  alert(`Download failed: ${data.error}`);
                }
              } catch (e) {
                alert(`Download request failed: ${e}`);
              }
            }}
            disabled={!optimizerSessionId}
            title="Download proposed_patch.yaml"
            className="px-3 py-1.5 text-xs font-semibold rounded bg-gray-700/50 text-gray-200 border border-gray-600/50 hover:bg-gray-700 disabled:opacity-30 disabled:cursor-not-allowed transition whitespace-nowrap"
          >
            ⬇ Download YAML
          </button>
          <button
            onClick={() => setOpen(true)}
            className="px-3 py-1.5 text-xs font-semibold rounded bg-amber-500/20 text-amber-200 border border-amber-500/40 hover:bg-amber-500/30 hover:border-amber-500/60 transition whitespace-nowrap"
          >
            Apply patch →
          </button>
        </div>
      </div>

      {/* Modal */}
      {open && (
        <div
          className="fixed inset-0 bg-black/70 flex items-center justify-center z-50"
          onClick={() => !submitting && setOpen(false)}
        >
          <div
            className="bg-tessera-card border border-amber-500/30 rounded-xl p-6 w-[640px] max-w-[90vw] max-h-[85vh] overflow-y-auto"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-base font-bold text-amber-200">Apply Optimizer Patch</h3>
              <button
                onClick={() => setOpen(false)}
                disabled={submitting}
                className="text-xs text-gray-400 hover:text-white disabled:opacity-30"
              >
                ✕
              </button>
            </div>

            <p className="text-xs text-gray-400 mb-4 leading-relaxed">
              The optimizer's <code className="text-amber-300">patch_synthesizer</code> proposed a new YAML
              for your target ecosystem. The typed YAML below is auto-loaded from the run's artifact
              (<code className="text-amber-300">patch_synthesizer/proposed_patch.yaml</code>); review and edit
              if needed, then apply. The current YAML will be archived as a versioned snapshot.
            </p>

            {autoLoadMsg && (
              <div
                className={`mb-3 text-[11px] rounded p-2 ${
                  autoLoadMsg.startsWith("✓")
                    ? "text-amber-200 bg-amber-500/10 border border-amber-500/30"
                    : "text-gray-300 bg-gray-700/30 border border-gray-600/40"
                }`}
              >
                {autoLoadMsg}
              </div>
            )}

            <label className="block mb-3">
              <span className="text-[11px] text-gray-400 uppercase tracking-wide">Target ecosystem ID</span>
              <input
                type="text"
                value={targetEcoId}
                onChange={(e) => setTargetEcoId(e.target.value)}
                placeholder="e.g. fsi-anz-personas-painpoints-tessera"
                disabled={submitting}
                className="w-full mt-1 bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white font-mono"
              />
            </label>

            <label className="block mb-3">
              <span className="text-[11px] text-gray-400 uppercase tracking-wide">New YAML (full file)</span>
              <textarea
                value={newYaml}
                onChange={(e) => setNewYaml(e.target.value)}
                placeholder={
                  autoLoading
                    ? "Loading proposed_patch.yaml from optimizer run…"
                    : "ecosystem:\n  name: ...\n  ..."
                }
                rows={12}
                disabled={submitting || autoLoading}
                className="w-full mt-1 bg-gray-900 border border-tessera-border rounded px-3 py-2 text-xs text-white font-mono resize-y"
              />
            </label>

            <label className="block mb-4">
              <span className="text-[11px] text-gray-400 uppercase tracking-wide">
                Approved change IDs (optional, comma-separated)
              </span>
              <input
                type="text"
                value={approvedIds}
                onChange={(e) => setApprovedIds(e.target.value)}
                placeholder="C001, C003, C007"
                disabled={submitting}
                className="w-full mt-1 bg-gray-900 border border-tessera-border rounded px-3 py-2 text-sm text-white font-mono"
              />
              <span className="text-[10px] text-gray-500">For audit trail — recorded alongside the snapshot.</span>
            </label>

            {resultMsg && (
              <div
                className={`mb-3 text-xs rounded p-2 ${
                  resultKind === "success"
                    ? "text-green-200 bg-green-500/10 border border-green-500/30"
                    : "text-red-300 bg-red-500/10 border border-red-500/30"
                }`}
              >
                {resultMsg}
              </div>
            )}

            <div className="flex gap-2 justify-end">
              <button
                onClick={() => setOpen(false)}
                disabled={submitting}
                className="px-3 py-2 text-xs text-gray-300 hover:text-white disabled:opacity-30"
              >
                Cancel
              </button>
              <button
                onClick={submit}
                disabled={submitting || !targetEcoId.trim() || !newYaml.trim()}
                className="px-4 py-2 text-xs font-semibold rounded bg-amber-500/20 text-amber-200 border border-amber-500/40 hover:bg-amber-500/30 hover:border-amber-500/60 disabled:opacity-30 disabled:cursor-not-allowed transition"
              >
                {submitting ? "Applying…" : "Apply patch"}
              </button>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
