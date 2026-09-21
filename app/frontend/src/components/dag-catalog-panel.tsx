"use client";
// DagCatalogPanel — the tabbed LEFT panel of the run + history windows.
//
//   ⚡ Flow  (default)  → the run/topology DAG (DagMap)
//   🗂 <Domain> tabs    → the authoritative INPUT CATALOGS the ecosystem's agents
//                         ground in, one tab per mounted domain (CatalogBrowser).
//
// The catalog tabs are LAZY: a domain's CatalogBrowser only mounts (and fetches)
// when its tab is first opened. The Flow tab may carry an optional footer (e.g. the
// history view's "Resume from <agent>" button), shown only while Flow is active.
import { useState } from "react";
import { DagMap } from "./dag-map";
import { CatalogBrowser } from "./catalog-browser";

type DagMapProps = React.ComponentProps<typeof DagMap>;

const prettyDomain = (d: string) =>
  d.replace(/[-_]/g, " ").replace(/\b\w/g, (c) => c.toUpperCase());

export function DagCatalogPanel({
  dagProps,
  domains,
  flowFooter,
  className = "",
}: {
  dagProps: DagMapProps;
  domains?: string[];
  flowFooter?: React.ReactNode;
  className?: string;
}) {
  const [tab, setTab] = useState<string>("__flow__");
  const doms = (domains || []).filter(Boolean);
  // Guard: if the active catalog tab's domain disappears (config change), fall back.
  const activeIsCatalog = tab !== "__flow__";
  const effectiveTab = activeIsCatalog && !doms.includes(tab) ? "__flow__" : tab;

  const Tab = ({ id, label, title }: { id: string; label: string; title?: string }) => (
    <button
      onClick={() => setTab(id)}
      title={title}
      className={`whitespace-nowrap px-2.5 py-1.5 text-[11px] font-medium border-b-2 transition ${
        effectiveTab === id
          ? "border-tessera-accent text-tessera-accent"
          : "border-transparent text-gray-400 hover:text-gray-200"
      }`}
    >
      {label}
    </button>
  );

  return (
    <div className={`h-full flex flex-col bg-tessera-card border border-tessera-border rounded-xl overflow-hidden ${className}`}>
      {/* Tab bar */}
      <div className="flex items-center gap-0.5 border-b border-tessera-border overflow-x-auto shrink-0 px-1" style={{ scrollbarWidth: "thin" }}>
        <Tab id="__flow__" label="⚡ Flow" title="Run topology (agent DAG)" />
        {doms.map((d) => (
          <Tab key={d} id={d} label={`🗂 ${prettyDomain(d)}`} title={`Input catalog: ${d}`} />
        ))}
      </div>

      {/* Body */}
      <div className="flex-1 min-h-0 overflow-auto">
        {effectiveTab === "__flow__" ? (
          <DagMap {...dagProps} />
        ) : (
          // key forces a clean remount + fetch per domain
          <CatalogBrowser key={effectiveTab} domainId={effectiveTab} pipelineOnly />
        )}
      </div>

      {/* Flow-only footer (e.g. Resume-from-agent in the history view) */}
      {effectiveTab === "__flow__" && flowFooter}
    </div>
  );
}
