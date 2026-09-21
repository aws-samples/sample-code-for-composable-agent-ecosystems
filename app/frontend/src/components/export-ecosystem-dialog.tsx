"use client";

// ExportEcosystemDialog — productized ecosystem export (Model 1, self-serve).
//
// POSTs /api/ecosystems/{id}/export, which runs the export toolchain server-side
// (BoM analyze + Terraform generate + zip) and returns a presigned download URL for
// a self-contained Terraform package the customer deploys themselves. The generated
// package is stored in the export bucket with a 7-day lifecycle expiry, so the UI
// tells the user the download is available for 7 days, then auto-deleted (re-exportable
// any time).

import { useState } from "react";

const API_URL = process.env.NEXT_PUBLIC_API_URL || "http://localhost:8080";

async function apiFetch(url: string, init?: RequestInit): Promise<Response> {
  const { fetchAuthSession } = await import("aws-amplify/auth");
  let token = "";
  try {
    const session = await fetchAuthSession({ forceRefresh: false });
    token = session.tokens?.idToken?.toString() || "";
  } catch {
    /* proceed without token */
  }
  const headers = new Headers(init?.headers);
  if (token) headers.set("Authorization", `Bearer ${token}`);
  return fetch(url, { ...init, headers });
}

interface BomLambda {
  name: string;
}

interface ExportResult {
  ecosystem_id: string;
  version?: string;
  region?: string;
  download_url: string;
  bytes?: number;
  complete?: boolean;
  note?: string;
  expires_days?: number;
  bom?: {
    strategy?: string;
    version?: string;
    lambdas?: Array<string | BomLambda>;
    tools?: string[];
    needs_memory?: boolean;
    needs_gateway?: boolean;
    [k: string]: unknown;
  };
}

function humanBytes(n?: number): string {
  if (!n || n <= 0) return "";
  const units = ["B", "KB", "MB", "GB"];
  let v = n;
  let i = 0;
  while (v >= 1024 && i < units.length - 1) {
    v /= 1024;
    i += 1;
  }
  return `${v.toFixed(v >= 10 || i === 0 ? 0 : 1)} ${units[i]}`;
}

export function ExportEcosystemDialog({
  ecosystemId,
  ecosystemName,
  onClose,
}: {
  ecosystemId: string;
  ecosystemName: string;
  onClose: () => void;
}) {
  const [status, setStatus] = useState<"idle" | "running" | "done" | "error">("idle");
  const [error, setError] = useState<string>("");
  const [result, setResult] = useState<ExportResult | null>(null);

  const runExport = async () => {
    setStatus("running");
    setError("");
    setResult(null);
    try {
      const res = await apiFetch(`${API_URL}/api/ecosystems/${encodeURIComponent(ecosystemId)}/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: "{}",
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok || data.error) {
        setError(data.error || `Export failed (HTTP ${res.status})`);
        setStatus("error");
        return;
      }
      setResult(data as ExportResult);
      setStatus("done");
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
      setStatus("error");
    }
  };

  const expiresDays = result?.expires_days ?? 7;
  const lambdaCount = result?.bom?.lambdas?.length ?? 0;
  const toolCount = result?.bom?.tools?.length ?? 0;

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-tessera-card border border-tessera-border rounded-xl p-6 w-[560px] max-h-[85vh] overflow-y-auto"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="flex items-center justify-between mb-4">
          <h3 className="text-lg font-bold">Export — {ecosystemName}</h3>
          <button onClick={onClose} className="text-gray-400 hover:text-white text-lg" aria-label="Close">
            ✕
          </button>
        </div>

        {/* Intro / idle */}
        {status === "idle" && (
          <div className="space-y-4">
            <p className="text-sm text-gray-300 leading-relaxed">
              Generate a self-contained Terraform package for this ecosystem. You deploy it into your own
              AWS account with <code className="text-tessera-accent">terraform apply</code> — no connection back
              to this platform. The package includes the ecosystem definition, its single-eco module, the
              agent runtime source, and a deploy runbook.
            </p>
            <div className="text-xs text-gray-400 bg-gray-800/50 border border-tessera-border rounded-lg p-3 leading-relaxed">
              This produces a downloadable <span className="text-gray-200">.zip</span>. The package is available to
              download for <span className="text-gray-200">{expiresDays} days</span>, then it&apos;s automatically
              deleted. You can re-export any time.
            </div>
            <button
              onClick={runExport}
              className="w-full py-2.5 bg-tessera-accent/20 text-tessera-accent rounded-lg text-sm font-medium hover:bg-tessera-accent/30 transition"
            >
              ⬇ Generate export package
            </button>
          </div>
        )}

        {/* Running */}
        {status === "running" && (
          <div className="py-8 text-center space-y-3">
            <div className="inline-block h-8 w-8 animate-spin rounded-full border-2 border-tessera-accent border-t-transparent" />
            <p className="text-sm text-gray-300">Generating the Terraform package…</p>
            <p className="text-xs text-gray-500">
              Analyzing the ecosystem, rendering Terraform, and packaging the module + runtime. This can take up
              to a minute.
            </p>
          </div>
        )}

        {/* Error */}
        {status === "error" && (
          <div className="space-y-4">
            <div className="text-sm text-red-300 bg-red-500/10 border border-red-500/30 rounded-lg p-3">
              {error || "Export failed."}
            </div>
            <button
              onClick={runExport}
              className="w-full py-2 bg-gray-700/50 text-gray-200 rounded-lg text-sm hover:bg-gray-700 transition"
            >
              Try again
            </button>
          </div>
        )}

        {/* Done */}
        {status === "done" && result && (
          <div className="space-y-4">
            {result.complete === false && (
              <div className="text-xs text-amber-200 bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 leading-relaxed">
                {result.note ||
                  "Partial package — some export inputs were unavailable, so parts of the package are placeholders."}
              </div>
            )}

            <a
              href={result.download_url}
              className="block w-full text-center py-2.5 bg-tessera-accent/20 text-tessera-accent rounded-lg text-sm font-medium hover:bg-tessera-accent/30 transition"
              download
            >
              ⬇ Download package{result.bytes ? ` (${humanBytes(result.bytes)})` : ""}
            </a>

            <div className="text-xs text-amber-200 bg-amber-500/10 border border-amber-500/30 rounded-lg p-3 leading-relaxed">
              This package is available to download for <span className="font-semibold">{expiresDays} days</span>,
              then it&apos;s automatically deleted. You can re-export any time.
            </div>

            {/* BoM summary */}
            <div className="text-xs text-gray-400 border-t border-tessera-border pt-3 space-y-1">
              <div className="uppercase tracking-wider text-[10px] text-gray-500 mb-1">Package contents</div>
              {result.bom?.strategy && (
                <div>
                  Strategy: <span className="text-gray-200">{result.bom.strategy}</span>
                </div>
              )}
              {result.version && (
                <div>
                  Version: <span className="text-gray-200">{result.version}</span>
                </div>
              )}
              {result.region && (
                <div>
                  Region: <span className="text-gray-200">{result.region}</span>
                </div>
              )}
              {lambdaCount > 0 && (
                <div>
                  Lambdas: <span className="text-gray-200">{lambdaCount}</span>
                </div>
              )}
              {toolCount > 0 && (
                <div>
                  Tools: <span className="text-gray-200">{toolCount}</span>
                </div>
              )}
            </div>

            <p className="text-[11px] text-gray-500 leading-relaxed">
              Unzip the package and follow <span className="text-gray-300">README.md</span> to deploy it into your
              own AWS account.
            </p>
          </div>
        )}
      </div>
    </div>
  );
}
