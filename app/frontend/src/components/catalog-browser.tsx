"use client";
// Generic Catalog Browser component — renders ONE domain's catalog (domainId prop) from
// its `catalog` descriptor (served by the domains API). Per-type view config drives the
// cards + detail tabs via a generic renderer library. Reused by the /catalog route and
// the in-app "Catalog Browser" view. No per-domain code.
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useAuth } from "@/components/auth-provider";
import { useBackStack } from "@/hooks/use-back-stack";
import { PipelineGraph, PNode, PEdge } from "./pipeline-graph";
import { RiskBanner, categoryColor } from "./risk-banner";
import { TesseraSpinner } from "./tessera-spinner";

// CATALOG_API must end in exactly one `/` — callers append `catalog/...` with no leading
// slash. The API endpoint output has no trailing slash; normalize so a slash-less build
// can't produce `...amazonaws.comcatalog/...` (malformed host → "Failed to fetch").
const CATALOG_API = (() => { const u = process.env.NEXT_PUBLIC_CATALOG_API_URL || ""; return u ? u.replace(/\/?$/, "/") : ""; })();
const API_URL = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");

const C = { bg: "#0f172a", card: "#1e293b", border: "#334155", text: "#e2e8f0", muted: "#94a3b8", accent: "#3b82f6", red: "#ef4444", amber: "#f59e0b", green: "#22c55e" };
const COLOR: Record<string, string> = { red: C.red, amber: C.amber, green: C.green, accent: C.accent, muted: C.muted };
// Canonical URLs for recognized report sources — the synthesizer emits {stat, source, year}
// with NO url; we link the source here so there are never fabricated links (WS-4.4 / B1).
const SOURCE_URL: [RegExp, string][] = [
  [/verizon|dbir/i, "https://www.verizon.com/business/resources/reports/dbir/"],
  [/ibm.*(cost|breach)|cost of a data breach/i, "https://www.ibm.com/reports/data-breach"],
  [/ic3|fbi/i, "https://www.ic3.gov/AnnualReport/Reports"],
  [/enisa/i, "https://www.enisa.europa.eu/topics/cyber-threats/threats-and-trends"],
  [/cisa|kev/i, "https://www.cisa.gov/known-exploited-vulnerabilities-catalog"],
  [/ponemon/i, "https://www.ibm.com/reports/data-breach"],
  [/mandiant|m-trends/i, "https://www.mandiant.com/m-trends"],
  [/crowdstrike/i, "https://www.crowdstrike.com/global-threat-report/"],
  [/microsoft.*digital defense|mddr/i, "https://www.microsoft.com/en-us/security/business/microsoft-digital-defense-report"],
];
const sourceUrl = (s: string): string => { for (const [re, u] of SOURCE_URL) if (re.test(s || "")) return u; return ""; };
type Rec = Record<string, any>;
type TypeCount = { type: string; count: number };
type View = Record<string, any>;

function getPath(obj: any, path: string): any { return String(path || "").split(".").reduce((o, k) => (o == null ? o : o[k]), obj); }
function resolve(r: Rec, spec: any): any {
  if (Array.isArray(spec)) { for (const k of spec) { const v = getPath(r, k); if (v != null && v !== "") return v; } return undefined; }
  return spec ? getPath(r, spec) : undefined;
}
function badgeFor(r: Rec, view: View): { text: string; color: string } | null {
  const b = view?.badge; if (!b) return null;
  const raw = getPath(r, b.field); if (raw == null || raw === "" || (Array.isArray(raw) && !raw.length)) return null;
  const val = Array.isArray(raw) ? raw[0] : raw;
  const key = String(val).toUpperCase() === "TRUE" || val === true ? "true" : String(val).toUpperCase();
  const colorName = (b.scale && (b.scale[key] || b.scale[String(val)])) || "muted";
  const text = typeof val === "boolean" ? (view.badge.field || "").replace(/_/g, " ") : String(Array.isArray(raw) ? raw.join(", ") : val).toUpperCase();
  return { text, color: COLOR[colorName] || C.muted };
}
function ratingFor(r: Rec, view: View): { n: number; color: string } | null {
  const rt = view?.rating; if (!rt) return null;
  const v = String(getPath(r, rt.field) || "").toUpperCase();
  const n = rt.scale?.[v]; if (n == null) return null;
  const bc = badgeFor(r, view); return { n, color: bc?.color || C.muted };
}
const TYPE_LABEL: Record<string, string> = { "attack-technique": "ATT&CK Techniques", "indicator-of-compromise": "IOCs" };
const plural = (w: string) => (/[sxz]$|[cs]h$/.test(w) ? w + "es" : w.endsWith("y") ? w.slice(0, -1) + "ies" : w.endsWith("s") ? w : w + "s");
const typeLabel = (t: string) => { if (!t) return ""; if (TYPE_LABEL[t]) return TYPE_LABEL[t]; const parts = t.split("-"); parts[parts.length - 1] = plural(parts[parts.length - 1]); return parts.map((p) => p.charAt(0).toUpperCase() + p.slice(1)).join(" "); };
const firstSentence = (s: string) => { const m = String(s || "").match(/^.*?[.](\s|$)/); return m ? m[0].trim() : String(s || "").slice(0, 200); };
function fmtNum(v: number): string { const a = Math.abs(v); if (a >= 1e9) return `${(v / 1e9).toFixed(1)}B`; if (a >= 1e6) return `${(v / 1e6).toFixed(1)}M`; if (a >= 1e3) return `${(v / 1e3).toFixed(0)}K`; return String(v); }
function leadStat(s: string): { big: string; rest: string } { const str = String(s); const m = str.match(/[~<>]?\s*\$?\d[\d,.\-–]*\s*(%|days?|years?|weeks?|months?|hours?|events\/year|x|M|K|B)?/i); if (m && m[0].trim()) return { big: m[0].trim(), rest: str.replace(m[0], "").replace(/^[\s—–,-]+/, "").trim() }; return { big: "", rest: str }; }

function Pill({ text, color = C.muted }: { text: string; color?: string }) { return <span style={{ display: "inline-block", padding: ".18rem .6rem", borderRadius: 9999, fontSize: ".72rem", fontWeight: 600, color, background: `${color}22`, border: `1px solid ${color}55`, whiteSpace: "nowrap" }}>{text}</span>; }
function Card({ children, title }: { children: React.ReactNode; title?: string }) { return <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: ".75rem", padding: "1.5rem", marginBottom: "1.5rem" }}>{title && <h2 style={{ fontSize: "1.05rem", fontWeight: 700, margin: "0 0 1rem", color: C.accent }}>{title}</h2>}{children}</div>; }
function Row({ label, children }: { label: string; children: React.ReactNode }) { return <div style={{ display: "flex", gap: "1rem", alignItems: "flex-start", marginBottom: ".7rem" }}><div style={{ width: 150, flexShrink: 0, fontSize: ".78rem", color: C.muted, fontWeight: 600 }}>{label}</div><div style={{ display: "flex", gap: ".35rem", flexWrap: "wrap" }}>{children}</div></div>; }
function KV({ k, v, color }: { k: string; v: any; color?: string }) { if (!v) return null; return <div style={{ marginBottom: ".8rem" }}><div style={{ fontSize: ".72rem", textTransform: "uppercase", letterSpacing: ".05em", color: color || C.muted, fontWeight: 700, marginBottom: ".2rem" }}>{k.replace(/_/g, " ")}</div><div style={{ fontSize: ".88rem" }}>{String(v)}</div></div>; }
function FairBar({ label, f }: { label: string; f: any }) {
  if (!f || typeof f !== "object" || f.min == null || f.max == null) return null;
  const isMoney = (f.unit || "").toUpperCase() === "USD";
  const fmt = (v: number) => (isMoney ? `$${fmtNum(v)}` : `${fmtNum(v)}${f.unit ? " " + f.unit : ""}`);
  const lo = Math.max(f.min, 1e-6), hi = Math.max(f.max, lo * 1.0001);
  const pos = (v: number) => Math.max(0, Math.min(1, (Math.log(Math.max(v, 1e-6)) - Math.log(lo)) / (Math.log(hi) - Math.log(lo)))) * 100;
  const modePos = f.mode != null ? pos(f.mode) : null;
  return (<div style={{ marginBottom: "1.1rem" }}>
    <div style={{ display: "flex", justifyContent: "space-between", fontSize: ".82rem", marginBottom: ".3rem" }}><span style={{ fontWeight: 600, textTransform: "capitalize" }}>{label.replace(/_/g, " ")}</span>{f.confidence != null && <span style={{ color: C.muted, fontSize: ".7rem" }}>confidence {f.confidence}/3</span>}</div>
    <div style={{ position: "relative", height: "1.5rem", background: C.bg, border: `1px solid ${C.border}`, borderRadius: ".4rem" }}><div style={{ position: "absolute", left: "6%", right: "6%", top: "50%", transform: "translateY(-50%)", height: ".4rem", borderRadius: 9999, background: `linear-gradient(90deg,${C.accent}44,${C.accent})` }} />{modePos != null && <div style={{ position: "absolute", left: `calc(6% + ${modePos * 0.88}%)`, top: "50%", transform: "translate(-50%,-50%)", width: ".7rem", height: ".7rem", borderRadius: "50%", background: "#fff", border: `2px solid ${C.accent}` }} />}</div>
    <div style={{ display: "flex", justifyContent: "space-between", fontSize: ".72rem", color: C.muted, marginTop: ".2rem" }}><span>{fmt(f.min)}</span>{f.mode != null && <span style={{ color: C.accent, fontWeight: 600 }}>mode {fmt(f.mode)}</span>}<span>{fmt(f.max)}</span></div>
    {f.rationale && <p style={{ fontSize: ".72rem", color: C.muted, marginTop: ".3rem", lineHeight: 1.5 }}>{f.rationale}</p>}
  </div>);
}

export function CatalogBrowser({ domainId, onBack, pipelineOnly }: { domainId: string; onBack?: () => void; pipelineOnly?: boolean }) {
  const { authFetch, isAdmin } = useAuth();
  const { pushStep } = useBackStack();
  const [domainName, setDomainName] = useState("");
  const [domainDesc, setDomainDesc] = useState("");
  const [catalog, setCatalog] = useState<any>(null);
  const [taxonomy, setTaxonomy] = useState<any[]>([]);
  const [mode, setMode] = useState<"browse" | "pipeline" | "admin">("browse");
  const [types, setTypes] = useState<TypeCount[]>([]);
  const [active, setActive] = useState("");
  const [records, setRecords] = useState<Rec[]>([]);
  const [loading, setLoading] = useState(false);
  const [nextToken, setNextToken] = useState<string | null>(null);
  const [loadingMore, setLoadingMore] = useState(false);
  const [err, setErr] = useState("");
  const [q, setQ] = useState("");
  const [severity, setSeverity] = useState("");
  const [framework, setFramework] = useState("");
  const [frameworks, setFrameworks] = useState<{ value: string; count: number }[]>([]);
  const [selected, setSelected] = useState<Rec | null>(null);
  // Register a Back step when a record is opened, so browser Back returns to the record
  // list (not out of the catalog). Cleanup disposes it when `selected` clears (in-app
  // "← Back to catalog", or a domain/type switch) or on unmount — keeping history in sync.
  useEffect(() => {
    if (!selected) return;
    const dispose = pushStep(() => setSelected(null));
    return dispose;
  }, [selected, pushStep]);
  const [ingestMsg, setIngestMsg] = useState("");
  const [ingesting, setIngesting] = useState(false);
  const [refresh, setRefresh] = useState<any>(null);
  const [polling, setPolling] = useState(false);
  const [versions, setVersions] = useState<Record<string, any>>({});
  const [activeVersion, setActiveVersion] = useState<string | null>(null);
  const [verMsg, setVerMsg] = useState("");
  const [verBusy, setVerBusy] = useState(false);

  const view = useCallback((t: string): View => (catalog?.view?.[t] || catalog?.view?.default || {}), [catalog]);
  const get = useCallback(async (p: string) => { const r = await authFetch(`${CATALOG_API}${p}`); if (!r.ok) throw new Error(`${r.status}`); return r.json(); }, [authFetch]);

  useEffect(() => {
    setCatalog(null); setActive(""); setRecords([]); setSelected(null); setErr("");
    authFetch(`${API_URL}/api/domains/${encodeURIComponent(domainId)}`).then((r) => r.json()).then((d) => { setCatalog(d.catalog || {}); setTaxonomy(d.taxonomy || []); setDomainName(d.name || domainId); setDomainDesc(d.description || ""); }).catch((e) => setErr("descriptor: " + String(e)));
  }, [authFetch, domainId]);
  useEffect(() => { get(`catalog/types?domain=${domainId}`).then((d) => { setTypes(d.types || []); setActive(d.types?.find((t: TypeCount) => t.type === "risk") ? "risk" : (d.types?.[0]?.type || "")); }).catch((e) => setErr(String(e)));
    // Depend only on domainId: `get` (and authFetch) change identity when the id token
    // loads, which otherwise re-fires this expensive /catalog/types call a second time.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [domainId]);
  const listQ = useCallback(() => { let s = `catalog/${active}?domain=${domainId}&limit=60`; if (q) s += `&q=${encodeURIComponent(q)}`; if (severity) s += `&severity=${encodeURIComponent(severity)}`; if (framework) s += `&framework=${encodeURIComponent(framework)}`; return s; }, [active, domainId, q, severity, framework]);
  // First page (server-side). Debounced on search text so we don't fire per keystroke.
  useEffect(() => {
    if (!active) return;
    let live = true;
    setLoading(true); setErr(""); setSelected(null);
    const run = () => get(listQ()).then((d) => { if (!live) return; setRecords(d.records || []); setNextToken(d.next_token || null); }).catch((e) => { if (live) setErr(String(e)); }).finally(() => { if (live) setLoading(false); });
    const t = setTimeout(run, q ? 350 : 0);
    return () => { live = false; clearTimeout(t); };
  }, [active, domainId, q, severity, framework, get, listQ]);
  // Distinct framework values for the active type -> powers the framework facet.
  useEffect(() => {
    setFramework(""); setFrameworks([]);
    if (!active) return;
    let live = true;
    get(`catalog/${active}?domain=${domainId}&facet=framework`).then((d) => { if (live) setFrameworks(d.values || []); }).catch(() => {});
    return () => { live = false; };
  }, [active, domainId, get]);
  const loadMore = async () => {
    if (!nextToken || loadingMore) return;
    setLoadingMore(true);
    try { const d = await get(`${listQ()}&next_token=${encodeURIComponent(nextToken)}`); setRecords((rs) => [...rs, ...(d.records || [])]); setNextToken(d.next_token || null); }
    catch (e) { setErr(String(e)); } finally { setLoadingMore(false); }
  };

  const runRefresh = async () => {
    setIngestMsg("Starting refresh…");
    try {
      const r = await authFetch(`${CATALOG_API}catalog/refresh`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ domain: domainId }) });
      const d = await r.json();
      if (r.status === 409) { setIngestMsg("A refresh is already running — showing live progress."); setPolling(true); return; }
      if (!r.ok) throw new Error(d.error || r.status);
      setIngestMsg(`✓ Refresh started: ${(d.fetch || []).length} fetch + ${(d.agent || []).length} agent source(s). Incremental — unchanged data is skipped.`);
      setPolling(true);
    } catch (e) { setIngestMsg("Error: " + String(e)); }
  };

  const fetchStatus = useCallback(async () => {
    try { const d = await get(`catalog/refresh/status?domain=${domainId}`); setRefresh(d); return d.state; } catch { return null; }
  }, [get, domainId]);

  // ── Blue/green version registry (list + active pointer) ──────────────────────────
  const fetchVersions = useCallback(async () => {
    try { const d = await get(`catalog/versions?domain=${domainId}`); setVersions(d.versions || {}); setActiveVersion(d.active ?? null); } catch {}
  }, [get, domainId]);
  useEffect(() => { fetchVersions(); }, [fetchVersions]);

  const startBuild = async () => {
    setVerBusy(true); setVerMsg("Starting green build — ingest inputs, then synthesize risks into a fresh version…");
    try {
      const r = await authFetch(`${CATALOG_API}catalog/build`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ domain: domainId }) });
      const d = await r.json(); if (!r.ok) throw new Error(d.error || r.status);
      setVerMsg("✓ Green build started (non-destructive). It validates coverage; promote when ready."); setPolling(true); fetchVersions();
    } catch (e) { setVerMsg("Error: " + String(e)); } finally { setVerBusy(false); }
  };
  const promote = async (version: string) => {
    if (typeof window !== "undefined" && !window.confirm(`Promote ${version} to ACTIVE? Readers will atomically switch to this version.`)) return;
    setVerBusy(true); setVerMsg(`Promoting ${version}…`);
    try {
      const r = await authFetch(`${CATALOG_API}catalog/promote`, { method: "POST", headers: { "content-type": "application/json" }, body: JSON.stringify({ domain: domainId, version }) });
      const d = await r.json(); if (!r.ok) throw new Error(d.error || r.status);
      setVerMsg(`✓ Promoted ${version} (previous: ${d.previous ?? "none"}).`);
      await fetchVersions(); get(`catalog/types?domain=${domainId}`).then((x) => setTypes(x.types || [])).catch(() => {});
    } catch (e) { setVerMsg("Error: " + String(e)); } finally { setVerBusy(false); }
  };

  // Poll refresh status ~2.5s while a run is in flight; refresh counts when it ends.
  useEffect(() => {
    if (!polling) return;
    let live = true;
    const tick = async () => {
      const st = await fetchStatus();
      if (!live) return;
      if (st !== "running") { setPolling(false); get(`catalog/types?domain=${domainId}`).then((d) => setTypes(d.types || [])).catch(() => {}); fetchVersions(); }
    };
    tick();
    const iv = setInterval(tick, 2500);
    return () => { live = false; clearInterval(iv); };
  }, [polling, fetchStatus, get, domainId, fetchVersions]);

  // Reflect an already-in-flight run on load.
  useEffect(() => { fetchStatus().then((st) => { if (st === "running") setPolling(true); }); }, [fetchStatus]);
  const refreshing = polling || refresh?.state === "running";

  const filtered = useMemo(() => { let rs = records; const v = view(active); const sevField = v?.badge?.field; if (severity && sevField) rs = rs.filter((r) => String(getPath(r, sevField) || "").toUpperCase() === severity); if (q) { const ql = q.toLowerCase(); rs = rs.filter((r) => JSON.stringify(r).toLowerCase().includes(ql)); } return rs; }, [records, q, severity, active, view]);
  const activeView = view(active);
  const hasSeverityFilter = !!activeView?.badge && !!activeView?.rating;
  const inputStyle = { background: C.bg, border: `1px solid ${C.border}`, borderRadius: ".5rem", padding: ".5rem .75rem", color: C.text, fontSize: ".85rem", outline: "none" } as const;

  if (pipelineOnly) return (
    <div style={{ background: C.bg, color: C.text, minHeight: "100%", padding: "1rem 1rem 1.5rem", fontFamily: "-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif" }}>
      <div style={{ marginBottom: ".6rem" }}>
        <div style={{ fontSize: "1rem", fontWeight: 700 }}>{domainName || domainId}</div>
        {domainDesc && <div style={{ color: C.muted, fontSize: ".76rem", marginTop: ".1rem", lineHeight: 1.4 }}>{firstSentence(domainDesc)}</div>}
      </div>
      {catalog ? (
        <PipelineView catalog={catalog} taxonomy={taxonomy} types={types} fetchRecords={get} refresh={refresh} />
      ) : (
        <div style={{ display: "flex", alignItems: "center", gap: ".5rem", color: C.muted, fontSize: ".85rem", padding: "1rem 0" }}><TesseraSpinner /> Loading catalog…</div>
      )}
    </div>
  );

  if (selected) return <RiskView r={selected} view={view(active)} onBack={() => setSelected(null)} fetchRecords={get} domainId={domainId} type={active} />;
  return (
    <div style={{ background: C.bg, color: C.text, minHeight: "100vh", fontFamily: "-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif" }}>
      <div style={{ maxWidth: 1160, margin: "0 auto", padding: "2rem 1.5rem" }}>
        {onBack ? <button onClick={onBack} style={{ background: "none", border: "none", color: C.accent, fontSize: ".8rem", cursor: "pointer", padding: 0 }}>← Catalogs</button> : <a href="/" style={{ color: C.accent, fontSize: ".8rem", textDecoration: "none" }}>← Tessera</a>}
        <div style={{ display: "flex", alignItems: "flex-start", justifyContent: "space-between", gap: "1rem", flexWrap: "wrap" }}>
          <div>
            <h1 style={{ fontSize: "1.9rem", fontWeight: 700, margin: ".25rem 0 0" }}>{domainName || "Catalog"}</h1>
            <p style={{ color: C.muted, fontSize: ".85rem", margin: ".15rem 0 0", maxWidth: 720 }}>{domainDesc || "Authoritative reference catalog"}</p>
          </div>
        </div>

        {ingestMsg && <div style={{ marginTop: ".6rem", fontSize: ".8rem", color: ingestMsg.startsWith("Error") ? C.red : C.green }}>{ingestMsg}</div>}
        {refresh?.progress && (refreshing || refresh.state === "done") && <div style={{ marginTop: ".35rem", fontSize: ".76rem", color: C.muted }}>sources {refresh.progress.sources_done}/{refresh.progress.sources_total} · synthesis clusters {refresh.progress.clusters_done}/{refresh.progress.clusters_total} · <b style={{ color: refresh.state === "running" ? C.amber : C.green }}>{refresh.state}</b></div>}
        <div style={{ height: "1.25rem" }} />
        {catalog && (
          <div style={{ display: "flex", gap: ".4rem", marginBottom: "1.25rem" }}>
            {(["browse", "pipeline", ...(isAdmin ? ["admin"] : [])]).map((m) => <button key={m} onClick={() => setMode(m as "browse" | "pipeline" | "admin")} style={{ padding: ".4rem .9rem", borderRadius: ".5rem", cursor: "pointer", fontSize: ".8rem", fontWeight: 600, fontFamily: "inherit", border: `1px solid ${mode === m ? C.accent : C.border}`, background: mode === m ? C.accent : C.card, color: mode === m ? "#fff" : C.muted }}>{m === "pipeline" ? "⛓ Pipeline" : m === "admin" ? "⚙ Admin" : "▤ Browse"}</button>)}
          </div>
        )}
        {mode === "pipeline" ? <PipelineView catalog={catalog} taxonomy={taxonomy} types={types} fetchRecords={get} refresh={refresh} /> : mode === "admin" ? (
        <div>
          {/* Admin: incremental refresh from sources + blue/green version registry
              (build a fresh green non-destructively, review coverage, atomically promote). */}
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: ".6rem", padding: "1rem 1.15rem", marginBottom: "1rem" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "1rem", flexWrap: "wrap" }}>
              <div><div style={{ fontSize: ".92rem", fontWeight: 700 }}>Refresh from sources</div><div style={{ color: C.muted, fontSize: ".76rem", marginTop: ".2rem" }}>Incremental: fetch + agent sources, then synthesize only changed clusters.</div></div>
              {catalog?.ingestion?.sources?.length > 0 && <button onClick={runRefresh} disabled={refreshing} style={{ cursor: refreshing ? "default" : "pointer", background: refreshing ? C.bg : C.accent, color: refreshing ? C.muted : "#fff", border: `1px solid ${C.accent}`, borderRadius: ".5rem", padding: ".55rem 1rem", fontSize: ".82rem", fontWeight: 600, fontFamily: "inherit", whiteSpace: "nowrap" }}>{refreshing ? "⟳ Refreshing…" : "↻ Refresh from sources"}</button>}
            </div>
          </div>
          <div style={{ background: C.card, border: `1px solid ${C.border}`, borderRadius: ".6rem", padding: "1rem 1.15rem" }}>
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: "1rem", flexWrap: "wrap" }}>
              <div style={{ fontSize: ".92rem", fontWeight: 700 }}>Versions <span style={{ color: C.muted, fontWeight: 400, fontSize: ".82rem" }}>· active: {activeVersion || "legacy (unversioned)"}</span></div>
              <button onClick={startBuild} disabled={verBusy || refreshing} title="Build a fresh GREEN version: ingest inputs + synthesize risks into it, non-destructively. Promote when coverage is met." style={{ cursor: verBusy || refreshing ? "default" : "pointer", background: "none", color: C.accent, border: `1px solid ${C.accent}`, borderRadius: ".5rem", padding: ".4rem .8rem", fontSize: ".78rem", fontWeight: 600, fontFamily: "inherit" }}>{refreshing ? "⟳ Building…" : "+ Build green version"}</button>
            </div>
            {verMsg && <div style={{ color: C.muted, fontSize: ".76rem", marginTop: ".5rem" }}>{verMsg}</div>}
            {Object.keys(versions).length > 0 ? (
              <div style={{ marginTop: ".6rem", display: "flex", flexDirection: "column", gap: ".35rem" }}>
                {Object.entries(versions).sort((a, b) => (a[0] < b[0] ? 1 : -1)).map(([v, meta]: [string, any]) => {
                  const isActive = v === activeVersion;
                  const ready = meta?.ready === true;
                  const cov = meta?.coverage_ok;
                  return (
                    <div key={v} style={{ display: "flex", alignItems: "center", gap: ".6rem", fontSize: ".78rem", padding: ".35rem .5rem", borderRadius: ".4rem", background: isActive ? "rgba(80,160,255,.08)" : "transparent", border: `1px solid ${isActive ? C.accent : "transparent"}` }}>
                      <span style={{ fontFamily: "monospace" }}>{v}</span>
                      <span style={{ color: C.muted }}>{meta?.status || "?"}</span>
                      {meta?.identical !== undefined && <span title="inputs reproduce blue exactly" style={{ color: meta.identical ? "#4caf50" : C.muted }}>{meta.identical ? "inputs✓" : "inputs≠"}</span>}
                      {cov !== undefined && <span title="synthesized coverage thresholds met" style={{ color: cov ? "#4caf50" : "#e0a030" }}>{cov ? "coverage✓" : "coverage✗"}</span>}
                      {isActive && <span style={{ color: C.accent, fontWeight: 600 }}>ACTIVE</span>}
                      <span style={{ flex: 1 }} />
                      {!isActive && <button onClick={() => promote(v)} disabled={verBusy} title={ready ? "Promote — atomically switch readers to this version" : "Not marked ready (inputs identical + coverage). Promote only after review."} style={{ cursor: verBusy ? "default" : "pointer", background: ready ? C.accent : "none", color: ready ? "#fff" : C.muted, border: `1px solid ${ready ? C.accent : C.border}`, borderRadius: ".4rem", padding: ".25rem .6rem", fontSize: ".72rem", fontWeight: 600, fontFamily: "inherit" }}>Promote{ready ? "" : "…"}</button>}
                    </div>
                  );
                })}
              </div>
            ) : <div style={{ color: C.muted, fontSize: ".78rem", marginTop: ".5rem" }}>No versioned builds yet. Use “Build green version” to mint one.</div>}
          </div>
        </div>
        ) : (<>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(150px,1fr))", gap: "1rem", marginBottom: "1.5rem" }}>
          {types.map((t) => <button key={t.type} onClick={() => setActive(t.type)} style={{ textAlign: "left", cursor: "pointer", background: C.card, border: `1px solid ${active === t.type ? C.accent : C.border}`, borderRadius: ".75rem", padding: "1rem 1.15rem" }}><div style={{ fontSize: "1.6rem", fontWeight: 700, color: active === t.type ? C.accent : C.text }}>{t.count}</div><div style={{ fontSize: ".78rem", color: C.muted, marginTop: ".15rem" }}>{typeLabel(t.type)}</div></button>)}
          {catalog && types.length === 0 && <p style={{ color: C.muted, fontSize: ".85rem" }}>No catalog records yet for this domain.</p>}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: ".75rem", marginBottom: "1.25rem", flexWrap: "wrap" }}>
          <h2 style={{ fontSize: "1.15rem", fontWeight: 600, margin: 0 }}>{typeLabel(active)}</h2>
          <span style={{ color: C.muted, fontSize: ".8rem" }}>{filtered.length}{(q || severity || framework) ? " match" : " loaded"}{(() => { const tc = types.find((t) => t.type === active)?.count; return tc && !q && !severity && !framework ? ` of ${tc.toLocaleString()}` : ""; })()}</span>
          <div style={{ flex: 1 }} />
          <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="Search…" style={{ ...inputStyle, width: 240 }} />
          {frameworks.length > 1 && <select value={framework} onChange={(e) => setFramework(e.target.value)} title="Filter by framework" style={inputStyle}><option value="">All frameworks ({frameworks.reduce((n, f) => n + f.count, 0)})</option>{frameworks.map((f) => <option key={f.value} value={f.value}>{f.value} ({f.count})</option>)}</select>}
          {hasSeverityFilter && <select value={severity} onChange={(e) => setSeverity(e.target.value)} style={inputStyle}><option value="">All severities</option>{["CRITICAL", "HIGH", "MEDIUM", "LOW"].map((s) => <option key={s} value={s}>{s}</option>)}</select>}
        </div>
        {err && <div style={{ color: C.red, fontSize: ".85rem", marginBottom: "1rem" }}>Error: {err}</div>}
        {loading && <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: ".6rem", padding: "3rem 0", color: C.muted }}><TesseraSpinner size={52} /><span style={{ fontSize: ".82rem" }}>Loading {typeLabel(active)}…</span></div>}
        {!loading && <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill,minmax(340px,1fr))", gap: "1rem" }}>
          {filtered.map((r, i) => { const v = view(active); const badge = badgeFor(r, v); const sub = resolve(r, v.subtitle) || r._ns; const summary = resolve(r, v.summary); const hasTabs = (v.tabs || []).length > 0;
            return (<button key={r._ns || i} onClick={() => setSelected(r)} style={{ textAlign: "left", cursor: "pointer", background: C.card, border: `1px solid ${C.border}`, borderRadius: ".75rem", padding: "1.15rem" }} onMouseEnter={(e) => (e.currentTarget.style.borderColor = C.accent)} onMouseLeave={(e) => (e.currentTarget.style.borderColor = C.border)}>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: ".5rem" }}><span style={{ fontFamily: "monospace", fontSize: ".72rem", color: C.accent }}>{sub}</span>{badge && <Pill text={badge.text} color={badge.color} />}</div>
              <h3 style={{ fontSize: ".92rem", fontWeight: 600, margin: ".5rem 0 .25rem", lineHeight: 1.35 }}>{resolve(r, v.title) || sub}</h3>
              {summary && <p style={{ fontSize: ".78rem", color: C.muted, marginTop: ".6rem", lineHeight: 1.5, display: "-webkit-box", WebkitLineClamp: 3, WebkitBoxOrient: "vertical", overflow: "hidden" }}>{summary}</p>}
              {hasTabs && <div style={{ marginTop: ".7rem", display: "flex", gap: ".4rem", alignItems: "center" }}>{r.methodology && <Pill text="methodology" color={C.green} />}<span style={{ fontSize: ".72rem", color: C.accent, fontWeight: 600 }}>View assessment →</span></div>}
            </button>);
          })}
        </div>}
        {!loading && nextToken && <div style={{ textAlign: "center", marginTop: "1.25rem" }}><button onClick={loadMore} disabled={loadingMore} style={{ cursor: loadingMore ? "default" : "pointer", background: C.card, color: C.text, border: `1px solid ${C.border}`, borderRadius: ".5rem", padding: ".6rem 1.4rem", fontSize: ".82rem", fontWeight: 600, fontFamily: "inherit", display: "inline-flex", alignItems: "center", gap: ".5rem" }}>{loadingMore ? <><TesseraSpinner size={18} /> Loading…</> : "Load more"}</button></div>}
        {!loading && filtered.length === 0 && active && <p style={{ color: C.muted, fontSize: ".85rem" }}>No records.</p>}
        </>)}
      </div>
    </div>
  );
}

function RiskView({ r: r0, view, onBack, fetchRecords, domainId, type }: { r: Rec; view: View; onBack: () => void; fetchRecords?: (p: string) => Promise<any>; domainId?: string; type?: string }) {
  // S3-offloaded records arrive LEAN in the list view (heavy fields — frameworks,
  // methodology, attack_paths, mitigation, key_stats — live only in S3). Fetch the
  // RESOLVED detail so every tab renders. Inline records (already complete) skip the fetch.
  const [full, setFull] = useState<Rec | null>(null);
  useEffect(() => {
    setFull(null);
    if (!fetchRecords || (r0 as Record<string, unknown>).frameworks || (r0 as Record<string, unknown>).methodology) return;
    const key = String((r0 as Record<string, unknown>)._ns || "").split("/").filter(Boolean).pop() || "";
    if (!key || !type) return;
    let live = true;
    fetchRecords(`catalog/${type}/${key}?domain=${domainId || ""}`).then((d) => { if (live && d && !d.error) setFull(d); }).catch(() => {});
    return () => { live = false; };
  }, [r0, type, fetchRecords, domainId]);
  const r = full || r0;
  const badge = badgeFor(r, view); const rating = ratingFor(r, view);
  const tabs = (view.tabs || []).filter((t: any) => t.render === "overview" || t.render === "auto" || getPath(r, t.field) != null);
  const [tab, setTab] = useState(tabs[0]?.id || "Overview");
  const br = r.base_rates || {};
  const accent = badge?.color || C.accent;

  // Traceability: resolve every referenced control id against the security-control
  // catalog (authoritative name + description + framework), independent of what the
  // synthesizer embedded. The risk only needs to carry the id.
  const [detail, setDetail] = useState<Record<string, Record<string, any>>>({});
  const refs = useMemo(() => {
    const need: Record<string, Set<string>> = {};
    const add = (type: string, id: any) => { if (!id) return; (need[type] = need[type] || new Set()).add(String(id)); };
    const mc = r.mapped_controls;
    (Array.isArray(mc) ? mc : []).forEach((c: any) => add("security-control", typeof c === "string" ? c : (c?.control_id || c?.id)));
    const s = r.sources || {};
    (s.controls || []).forEach((id: any) => add("security-control", id));
    (s.attack_techniques || []).forEach((id: any) => add("attack-technique", id));
    (s.vulnerabilities || []).forEach((id: any) => add("vulnerability", id));
    (r.attack_paths || []).forEach((p: any) => { (p.technique_ids || []).forEach((id: any) => add("attack-technique", id)); (p.breaking_controls || []).forEach((id: any) => add("security-control", id)); });
    const mit = r.mitigation || {};
    [...(mit.quick_wins || []), ...(mit.strategic || [])].forEach((m: any) => (m.control_ids || []).forEach((id: any) => add("security-control", id)));
    // any `linked` tab (e.g. attack-technique.mitigations -> security-control)
    (view.tabs || []).forEach((t: any) => { if (t.render === "linked" && t.field && t.resolve) { const val = getPath(r, t.field); (Array.isArray(val) ? val : []).forEach((id: any) => add(t.resolve, id)); } });
    const out: Record<string, string[]> = {}; Object.entries(need).forEach(([k, set]) => out[k] = Array.from(set)); return out;
  }, [r, view]);
  useEffect(() => {
    if (!fetchRecords) return;
    let live = true;
    const slug = (s: string) => s.toLowerCase().replace(/[^a-z0-9]+/g, "-").replace(/^-|-$/g, "");
    const types = Object.keys(refs);
    Promise.all(types.map((type) => Promise.all(refs[type].map((id) =>
      fetchRecords(`catalog/${type}/${slug(id)}?domain=${domainId || ""}`).then((d) => [id, (d && !d.error) ? d : null]).catch(() => [id, null])
    )).then((p) => [type, Object.fromEntries(p as any)]))).then((pairs) => { if (live) setDetail(Object.fromEntries(pairs as any)); });
    return () => { live = false; };
  }, [refs, fetchRecords, domainId]);
  const ctx = { detail, ctrlDetail: detail["security-control"] || {}, techDetail: detail["attack-technique"] || {}, vulnDetail: detail["vulnerability"] || {} };
  return (
    <div style={{ background: C.bg, color: C.text, minHeight: "100vh", fontFamily: "-apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif", lineHeight: 1.6 }}>
      <div style={{ maxWidth: 1100, margin: "0 auto", padding: "2rem 1.5rem" }}>
        <button onClick={onBack} style={{ background: "none", border: "none", color: C.accent, fontSize: ".85rem", cursor: "pointer", padding: 0, marginBottom: "1rem" }}>← Back to catalog</button>
        <div style={{ position: "relative", overflow: "hidden", borderRadius: ".85rem", border: `1px solid ${categoryColor(r.category)}44`, padding: "1.5rem 1.6rem", marginBottom: "1.25rem", background: `linear-gradient(135deg, ${categoryColor(r.category)}1f, ${C.bg})` }}>
          <RiskBanner category={r.category} />
          <div style={{ position: "relative" }}>
            <div style={{ display: "flex", alignItems: "baseline", gap: ".75rem", flexWrap: "wrap" }}><span style={{ fontFamily: "monospace", fontSize: ".85rem", color: C.accent }}>{resolve(r, view.subtitle)}</span>{badge && <Pill text={badge.text} color={badge.color} />}<span style={{ fontSize: ".82rem", color: C.muted }}>{r.category}</span></div>
            <h1 style={{ fontSize: "2rem", fontWeight: 700, margin: ".4rem 0 0", lineHeight: 1.2 }}>{resolve(r, view.title) || resolve(r, view.subtitle)}</h1>
          </div>
        </div>
        {resolve(r, view.summary) && <div style={{ marginTop: "1.25rem", padding: "1.5rem", borderRadius: ".75rem", background: `linear-gradient(135deg, ${C.card}, ${C.bg})`, border: `1px solid ${C.border}` }}><div style={{ fontSize: ".72rem", letterSpacing: ".08em", color: accent, fontWeight: 700, textTransform: "uppercase", marginBottom: ".5rem" }}>◆ In One Line</div><p style={{ fontSize: "1.25rem", fontWeight: 600, margin: 0, lineHeight: 1.4 }}>{firstSentence(resolve(r, view.summary))}</p></div>}
        {rating && <div style={{ display: "flex", alignItems: "center", gap: "1.5rem", padding: "1.25rem", marginTop: "1.5rem", background: `${rating.color}14`, border: `1px solid ${rating.color}40`, borderRadius: ".75rem" }}><div style={{ fontSize: "2.6rem", fontWeight: 800, color: rating.color, lineHeight: 1 }}>{rating.n}/5</div><div><div style={{ fontSize: "1.05rem", fontWeight: 700 }}>Severity Rating: {badge?.text}</div><div style={{ fontSize: ".85rem", color: C.muted }}>{br.annual_event_probability ? `Annualized event probability: ${br.annual_event_probability}` : "Severity from catalog synthesis."}</div></div></div>}
        {tabs.length > 0 && <div style={{ display: "flex", gap: ".4rem", flexWrap: "wrap", margin: "1.5rem 0" }}>{tabs.map((t: any) => <button key={t.id} onClick={() => setTab(t.id)} style={{ padding: ".5rem .9rem", borderRadius: ".5rem", cursor: "pointer", fontSize: ".82rem", fontWeight: 600, fontFamily: "inherit", border: `1px solid ${tab === t.id ? C.accent : C.border}`, background: tab === t.id ? C.accent : C.card, color: tab === t.id ? "#fff" : C.muted }}>{t.id}</button>)}</div>}
        {["Taleb", "Bow-Tie", "Decision Tree", "Pre-Mortem"].includes(tab) && <div style={{ fontSize: ".72rem", color: C.amber, marginBottom: "1rem" }}>⚠ Generic methodology — not contextualized to any organization. The risk-assessment consumer contextualizes these to a specific org &amp; project.</div>}
        {tabs.map((t: any) => tab === t.id && <div key={t.id}>{renderTab(t, r, view, ctx)}</div>)}
        {tabs.length === 0 && <AutoDetail r={r} view={view} />}
      </div>
    </div>
  );
}

function renderTab(t: any, r: Rec, view: View, ctx?: any) {
  if (t.render === "overview") return <OverviewTab r={r} view={view} accent={(badgeFor(r, view)?.color) || C.accent} ctx={ctx} />;
  const data = getPath(r, t.field);
  if (t.render === "range_bars") return <Card title={`${t.id} — estimated ranges (industry-norm)`}>{Object.entries(data || {}).map(([k, v]) => <FairBar key={k} label={k} f={v} />)}</Card>;
  if (t.render === "kv") return <Card title={t.id}>{Object.entries(data || {}).map(([k, v]) => <KV key={k} k={k} v={typeof v === "object" ? JSON.stringify(v) : v} />)}</Card>;
  if (t.render === "bowtie") return <BowTie b={data || {}} title={t.id} />;
  if (t.render === "options") return <Options d={data || {}} title={t.id} />;
  if (t.render === "failures") return <Failures p={data || {}} title={t.id} />;
  if (t.render === "controls") return <ControlsTab list={Array.isArray(data) ? data : []} title={t.id} detail={(ctx && ctx.ctrlDetail) || {}} extraIds={(r.sources || {}).controls || []} />;
  if (t.render === "frameworks") return <FrameworksTab f={data || {}} title={t.id} />;
  if (t.render === "auto") return <AutoDetail r={r} view={view} />;
  if (t.render === "linked") return <LinkedTab title={t.id} ids={Array.isArray(data) ? data : []} detail={(ctx && ctx.detail && ctx.detail[t.resolve]) || {}} link={t.link} />;
  if (t.render === "attack_paths") return <AttackPathsTab steps={Array.isArray(data) ? data : []} tech={(ctx && ctx.techDetail) || {}} ctrl={(ctx && ctx.ctrlDetail) || {}} title={t.id} />;
  if (t.render === "mitigation") return <MitigationTab m={data || {}} ctrl={(ctx && ctx.ctrlDetail) || {}} title={t.id} />;
  if (t.render === "board") return <BoardTab b={data || {}} title={t.id} />;
  return <Card title={t.id}><pre style={{ fontSize: ".7rem", color: C.muted, whiteSpace: "pre-wrap" }}>{JSON.stringify(data, null, 2)}</pre></Card>;
}

function AttackPathsTab({ steps, tech, ctrl, title }: { steps: any[]; tech: Record<string, any>; ctrl: Record<string, any>; title: string }) {
  return <Card title={`${title} — attacker kill-chain (generic)`}>
    {steps.length === 0 && <p style={{ color: C.muted, fontSize: ".85rem" }}>No attack paths.</p>}
    {steps.map((p: any, i: number) => (
      <div key={i} style={{ display: "flex", gap: ".7rem", marginBottom: ".2rem" }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center" }}>
          <div style={{ width: 26, height: 26, borderRadius: 99, background: `${C.accent}22`, border: `1px solid ${C.accent}66`, color: C.accent, fontSize: ".72rem", fontWeight: 700, display: "flex", alignItems: "center", justifyContent: "center" }}>{i + 1}</div>
          {i < steps.length - 1 && <div style={{ width: 2, flex: 1, background: C.border, minHeight: 18 }} />}
        </div>
        <div style={{ flex: 1, paddingBottom: ".8rem" }}>
          <div style={{ fontSize: ".7rem", textTransform: "uppercase", letterSpacing: ".04em", color: C.amber, fontWeight: 700 }}>{p.tactic}</div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: ".35rem", marginTop: ".3rem" }}>
            {(p.technique_ids || []).map((id: string, j: number) => <Pill key={j} text={tech[id]?.name ? `${id} · ${tech[id].name}` : id} color={C.accent} />)}
          </div>
          {(p.breaking_controls || []).length > 0 && <div style={{ marginTop: ".4rem", fontSize: ".78rem", color: C.text }}><span style={{ color: C.green, fontWeight: 600 }}>⛔ broken by: </span>{(p.breaking_controls || []).map((id: string) => ctrl[id]?.name ? `${id} (${ctrl[id].name})` : id).join(", ")}</div>}
        </div>
      </div>
    ))}
  </Card>;
}

function MitigationTab({ m, ctrl, title }: { m: any; ctrl: Record<string, any>; title: string }) {
  const grp = (label: string, list: any[], color: string) => (list && list.length) ? <>
    <div style={{ fontSize: ".72rem", letterSpacing: ".05em", textTransform: "uppercase", color, fontWeight: 700, margin: "1rem 0 .5rem" }}>{label}</div>
    {list.map((x: any, i: number) => <div key={i} style={{ padding: ".7rem .9rem", marginBottom: ".55rem", background: C.bg, border: `1px solid ${C.border}`, borderRadius: ".6rem" }}>
      {(x.control_ids || []).length > 0 && <div style={{ display: "flex", flexWrap: "wrap", gap: ".3rem", marginBottom: ".35rem" }}>{(x.control_ids || []).map((id: string, j: number) => <Pill key={j} text={ctrl[id]?.name ? `${id} · ${ctrl[id].name}` : id} color={color} />)}</div>}
      {x.action && <div style={{ fontSize: ".86rem" }}>{x.action}</div>}
      {x.residual && <div style={{ fontSize: ".78rem", color: C.muted, marginTop: ".25rem" }}>Residual: {x.residual}</div>}
    </div>)}
  </> : null;
  const has = (m.quick_wins || []).length || (m.strategic || []).length;
  return <Card title={title}>{!has && <p style={{ color: C.muted, fontSize: ".85rem" }}>No mitigation plan.</p>}{grp("⚡ Quick wins", m.quick_wins, C.green)}{grp("🏗️ Strategic", m.strategic, C.accent)}</Card>;
}

function BoardTab({ b, title }: { b: any; title: string }) {
  const posture = String(b.recommended_posture || "").toLowerCase();
  const pc = posture === "mitigate" ? C.green : posture === "transfer" ? C.amber : C.muted;
  return <Card title={title}>
    <div style={{ fontSize: ".72rem", color: C.amber, marginBottom: ".8rem" }}>⚠ Generic framing — the org-specific verdict is produced by the risk-assessment consumer.</div>
    {b.one_liner && <p style={{ fontSize: "1.15rem", fontWeight: 600, margin: "0 0 1rem", lineHeight: 1.4 }}>{b.one_liner}</p>}
    {b.decisive_question && <div style={{ padding: "1rem 1.25rem", background: `${C.accent}14`, borderLeft: `3px solid ${C.accent}`, borderRadius: "0 .5rem .5rem 0", marginBottom: "1rem" }}><div style={{ fontSize: ".72rem", color: C.accent, fontWeight: 700, textTransform: "uppercase", marginBottom: ".3rem" }}>The decisive question</div><p style={{ margin: 0, fontSize: ".95rem" }}>{b.decisive_question}</p></div>}
    {b.recommended_posture && <div style={{ marginBottom: ".8rem" }}><Pill text={`recommended posture: ${b.recommended_posture}`} color={pc} /></div>}
    {b.asymmetry && <KV k="Asymmetry" v={b.asymmetry} />}
  </Card>;
}

function LinkedTab({ title, ids, detail, link }: { title: string; ids: any[]; detail: Record<string, any>; link?: string }) {
  return <Card title={`${title} — traceable to the catalog`}>
    {ids.length === 0 && <p style={{ color: C.muted, fontSize: ".85rem" }}>None.</p>}
    {ids.map((id: any, i: number) => {
      const d = detail[String(id)];
      const href = link ? link.replace("{id}", String(id).replace(".", "/")) : "";
      return <div key={i} style={{ padding: ".7rem .9rem", marginBottom: ".55rem", background: C.bg, border: `1px solid ${C.border}`, borderRadius: ".6rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: ".5rem", flexWrap: "wrap" }}>
          {href ? <a href={href} target="_blank" rel="noreferrer" style={{ fontFamily: "monospace", fontSize: ".78rem", color: C.accent, textDecoration: "none" }}>{String(id)}</a> : <span style={{ fontFamily: "monospace", fontSize: ".78rem", color: C.accent }}>{String(id)}</span>}
          {d && d.name && <b style={{ fontSize: ".9rem" }}>{d.name}</b>}
          {d && d.framework && <Pill text={d.framework} color={C.muted} />}
          {!d && <span style={{ fontSize: ".68rem", color: C.amber }}>⚠ not found in catalog</span>}
        </div>
        {d && d.description && <div style={{ fontSize: ".82rem", color: C.text, marginTop: ".35rem" }}>{String(d.description).slice(0, 600)}</div>}
      </div>;
    })}
  </Card>;
}

function ControlsTab({ list, title, detail, extraIds }: { list: any[]; title: string; detail: Record<string, any>; extraIds: string[] }) {
  // Merge risk-specific control refs (mapped_controls) with any source control ids,
  // then resolve each against the security-control catalog for full authoritative detail.
  const byId: Record<string, any> = {};
  list.forEach((c: any) => { const id = typeof c === "string" ? c : (c.control_id || c.id || ""); if (id) byId[id] = { id, efficacy: typeof c === "object" ? (c.efficacy || c.effectiveness || "") : "", note: typeof c === "object" ? (c.note || c.notes || c.rationale || "") : "" }; });
  (extraIds || []).forEach((id: string) => { if (id && !byId[id]) byId[id] = { id, efficacy: "", note: "" }; });
  const rows = Object.values(byId);
  return <Card title={`${title} — traceable to the security-control catalog`}>
    {rows.length === 0 && <p style={{ color: C.muted, fontSize: ".85rem" }}>No mapped controls.</p>}
    {rows.map((c: any, i: number) => {
      const d = detail[c.id];               // authoritative catalog record (if resolved)
      const name = (d && d.name) || "";
      const fw = (d && d.framework) || "";
      const desc = (d && d.description) || "";
      return <div key={i} style={{ padding: ".8rem .9rem", marginBottom: ".6rem", background: C.bg, border: `1px solid ${C.border}`, borderRadius: ".6rem" }}>
        <div style={{ display: "flex", alignItems: "center", gap: ".5rem", flexWrap: "wrap" }}>
          <span style={{ fontFamily: "monospace", fontSize: ".78rem", color: C.accent }}>{c.id}</span>
          {name && <b style={{ fontSize: ".92rem" }}>{name}</b>}
          {fw && <Pill text={fw} color={C.muted} />}
          {c.efficacy && <Pill text={`efficacy: ${c.efficacy}`} color={c.efficacy === "High" ? C.green : c.efficacy === "Medium" ? C.amber : C.muted} />}
          {!d && <span style={{ fontSize: ".68rem", color: C.amber }}>⚠ not found in catalog</span>}
        </div>
        {desc && <div style={{ fontSize: ".82rem", color: C.text, marginTop: ".4rem" }}>{desc}</div>}
        {c.note && <div style={{ fontSize: ".8rem", color: C.muted, marginTop: ".35rem", fontStyle: "italic" }}>Why here: {c.note}</div>}
      </div>;
    })}
  </Card>;
}

function FWValue({ v }: { v: any }): any {
  if (v === null || v === undefined || v === "") return null;
  if (Array.isArray(v)) {
    if (v.every((x) => typeof x !== "object" || x === null)) return <span style={{ fontSize: ".85rem", color: C.text }}>{v.join(", ")}</span>;
    return <div>{v.map((x, i) => <div key={i} style={{ marginBottom: ".3rem" }}><FWValue v={x} /></div>)}</div>;
  }
  if (typeof v === "object") {
    return <div style={{ marginLeft: ".2rem" }}>{Object.entries(v).map(([k, val]) => (
      <div key={k} style={{ marginBottom: ".4rem" }}>
        <span style={{ fontSize: ".68rem", textTransform: "uppercase", letterSpacing: ".04em", color: C.muted, fontWeight: 700 }}>{k.replace(/_/g, " ")}</span>
        <div style={{ fontSize: ".85rem", color: C.text, marginTop: ".1rem" }}><FWValue v={val} /></div>
      </div>
    ))}</div>;
  }
  return <span style={{ fontSize: ".85rem", color: C.text }}>{String(v)}</span>;
}

function FrameworksTab({ f, title }: { f: any; title: string }) {
  const NAMES: Record<string, string> = {
    open_fair: "Open FAIR (O-RT) factors", nist_csf: "NIST CSF 2.0", nist_ai_rmf: "NIST AI RMF",
    nist_ai_600_1: "NIST AI 600-1 (GAI risks)", owasp: "OWASP", nist_800_30_threat_events: "NIST SP 800-30 threat events",
  };
  const H = ({ x }: { x: string }) => <div style={{ fontSize: ".72rem", letterSpacing: ".05em", textTransform: "uppercase", color: C.accent, fontWeight: 700, margin: "1rem 0 .5rem" }}>{x}</div>;
  const keys = Object.keys(f || {});
  return <Card title={title}>
    {keys.length === 0 && <p style={{ color: C.muted, fontSize: ".85rem" }}>No framework mappings.</p>}
    {keys.map((k) => { const v = f[k]; if (v == null || (Array.isArray(v) && v.length === 0) || (typeof v === "object" && !Array.isArray(v) && Object.keys(v).length === 0)) return null; return <div key={k}><H x={NAMES[k] || k.replace(/_/g, " ")} /><FWValue v={v} /></div>; })}
  </Card>;
}

function OverviewTab({ r, view, accent, ctx }: { r: Rec; view: View; accent: string; ctx?: any }) {
  const br = r.base_rates || {}, exp = r.exposure_range, src = r.sources || {};
  const cd = (ctx && ctx.ctrlDetail) || {};
  const td = (ctx && ctx.techDetail) || {};
  const vd = (ctx && ctx.vulnDetail) || {};
  const mc = Array.isArray(r.mapped_controls) ? r.mapped_controls : [];
  const ctrlIds: string[] = Array.from(new Set([
    ...mc.map((c: any) => (typeof c === "string" ? c : (c?.control_id || c?.id))).filter(Boolean),
    ...((r.sources || {}).controls || []),
  ]));
  const frameworks = r.frameworks_applicable || []; const stats = Object.entries(br).slice(0, 4);
  return (<>
    {stats.length > 0 && <Card title="📊 Key Statistics"><div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(200px,1fr))", gap: "1.25rem" }}>{stats.map(([k, v]) => { const { big, rest } = leadStat(String(v)); return (<div key={k} style={{ textAlign: "center" }}>{big ? <div style={{ fontSize: "1.7rem", fontWeight: 800, color: accent }}>{big}</div> : <div style={{ fontSize: ".95rem", fontWeight: 700, color: accent, lineHeight: 1.3 }}>{String(v).split(/[.;(]/)[0].slice(0, 60)}</div>}<div style={{ fontSize: ".8rem", marginTop: ".2rem", textTransform: "capitalize" }}>{k.replace(/_/g, " ")}</div>{big && rest && <div style={{ fontSize: ".72rem", color: C.muted, marginTop: ".2rem" }}>{rest}</div>}</div>); })}</div></Card>}
    {(r.key_stats || []).length > 0 && <Card title="📈 Industry Statistics"><div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))", gap: "1rem" }}>{(r.key_stats || []).map((s: any, i: number) => { const u = sourceUrl(s.source || ""); return <div key={i} style={{ padding: ".8rem", background: C.bg, border: `1px solid ${C.border}`, borderRadius: ".5rem" }}><div style={{ fontSize: ".9rem", fontWeight: 600, color: C.text }}>{s.stat}</div><div style={{ fontSize: ".74rem", color: C.muted, marginTop: ".3rem" }}>{u ? <a href={u} target="_blank" rel="noreferrer" style={{ color: C.accent }}>{s.source}</a> : s.source}{s.year ? ` · ${s.year}` : ""}</div></div>; })}</div></Card>}
    {resolve(r, view.summary) && <Card title="🧠 Narrative"><p style={{ margin: 0, fontSize: ".9rem" }}>{resolve(r, view.summary)}</p></Card>}
    {exp && typeof exp === "object" && <Card title="🎯 Exposure"><table style={{ width: "100%", borderCollapse: "collapse", fontSize: ".85rem" }}><tbody>{Object.entries(exp).map(([k, v]) => <tr key={k} style={{ borderBottom: `1px solid ${C.border}` }}><td style={{ padding: ".6rem .5rem", color: C.accent, fontWeight: 600, textTransform: "capitalize", width: "30%", verticalAlign: "top" }}>{k.replace(/_/g, " ")}</td><td style={{ padding: ".6rem .5rem" }}>{String(v)}</td></tr>)}</tbody></table></Card>}
    {ctrlIds.length > 0 && <Card title="🛡️ Mapped Controls"><div style={{ display: "flex", flexWrap: "wrap", gap: ".5rem" }}>{ctrlIds.map((id: string, i: number) => { const d = cd[id]; return <div key={i} style={{ display: "flex", alignItems: "center", gap: ".4rem", background: C.bg, border: `1px solid ${C.border}`, borderRadius: ".5rem", padding: ".35rem .6rem" }}><span style={{ fontFamily: "monospace", fontSize: ".76rem", color: C.accent }}>{id}</span>{d && d.name && <span style={{ fontSize: ".8rem" }}>{d.name}</span>}{d && d.framework && <span style={{ fontSize: ".66rem", color: C.muted }}>{d.framework}</span>}</div>; })}</div><div style={{ fontSize: ".72rem", color: C.muted, marginTop: ".55rem" }}>→ Full authoritative detail in the <b style={{ color: C.text }}>Controls</b> tab.</div></Card>}
    {(src.attack_techniques?.length || src.vulnerabilities?.length || src.ransomware_families?.length || src.cri_controls?.length) && <Card title="🔎 Evidence & Sources">
      {src.attack_techniques?.length > 0 && <Row label="ATT&CK techniques"><div style={{ display: "flex", flexWrap: "wrap", gap: ".4rem" }}>{src.attack_techniques.map((t: string) => { const d = td[t]; return <a key={t} href={`https://attack.mitre.org/techniques/${String(t).replace(".", "/")}/`} target="_blank" rel="noreferrer" style={{ display: "flex", alignItems: "center", gap: ".35rem", textDecoration: "none", background: C.bg, border: `1px solid ${C.border}`, borderRadius: ".5rem", padding: ".3rem .55rem" }}><span style={{ fontFamily: "monospace", fontSize: ".74rem", color: C.accent }}>{t}</span>{d && d.name && <span style={{ fontSize: ".78rem", color: C.text }}>{d.name}</span>}</a>; })}</div></Row>}
      {src.vulnerabilities?.length > 0 && <Row label="Exploited CVEs"><div style={{ display: "flex", flexWrap: "wrap", gap: ".4rem" }}>{src.vulnerabilities.map((c: string) => { const d = vd[c]; return <div key={c} style={{ display: "flex", alignItems: "center", gap: ".35rem", background: C.bg, border: `1px solid ${C.border}`, borderRadius: ".5rem", padding: ".3rem .55rem" }}><span style={{ fontFamily: "monospace", fontSize: ".74rem", color: C.amber }}>{c}</span>{d && d.name && <span style={{ fontSize: ".76rem", color: C.muted }}>{String(d.name).slice(0, 60)}</span>}</div>; })}</div></Row>}
      {src.ransomware_families?.length > 0 && <Row label="Ransomware families">{src.ransomware_families.map((c: string) => <Pill key={c} text={c} color={C.red} />)}</Row>}
      {src.cri_controls?.length > 0 && <Row label="CRI controls">{src.cri_controls.map((c: string) => <Pill key={c} text={c} color={C.green} />)}</Row>}
    </Card>}
    {Array.isArray(frameworks) && frameworks.length > 0 && <div style={{ borderTop: `1px solid ${C.border}`, paddingTop: "1rem", color: C.muted, fontSize: ".76rem" }}><b style={{ color: C.text }}>Frameworks:</b> {frameworks.join(" · ")}</div>}
  </>);
}

function BowTie({ b, title }: { b: any; title: string }) {
  const col = (t: string, items: any[], color: string) => <div style={{ flex: 1, minWidth: 180 }}><div style={{ fontSize: ".72rem", fontWeight: 700, color, textTransform: "uppercase", marginBottom: ".5rem" }}>{t}</div>{(items || []).map((it: any, i: number) => <div key={i} style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: ".4rem", padding: ".5rem .65rem", marginBottom: ".4rem", fontSize: ".78rem" }}>{typeof it === "object" ? (it.control_id || JSON.stringify(it)) : it}</div>)}</div>;
  return <Card title={`🎀 ${title}`}>{b.top_event && <div style={{ textAlign: "center", margin: "0 0 1.25rem" }}><div style={{ display: "inline-block", padding: ".6rem 1.25rem", background: `${C.red}20`, border: `1px solid ${C.red}55`, borderRadius: ".5rem", fontWeight: 700 }}>⨂ {b.top_event}</div></div>}<div style={{ display: "flex", gap: "1rem", flexWrap: "wrap" }}>{col("Threats", b.threats, C.red)}{col("Preventive barriers", b.preventive_barriers, C.green)}{col("Recovery barriers", b.recovery_barriers, C.amber)}{col("Consequences", b.consequences, C.red)}</div></Card>;
}
function Options({ d, title }: { d: any; title: string }) {
  return <Card title={`🌳 ${title}`}><div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit,minmax(240px,1fr))", gap: "1rem" }}>{(d.options || []).map((o: any, i: number) => <div key={i} style={{ background: C.bg, border: `1px solid ${C.border}`, borderRadius: ".5rem", padding: "1rem" }}><div style={{ fontWeight: 700, color: C.accent, marginBottom: ".5rem", fontSize: ".9rem" }}>{o.option}</div><KV k="Cost class" v={o.generic_cost} /><KV k="Residual risk" v={o.residual_risk} /><KV k="EMV logic" v={o.emv_logic} /></div>)}</div>{d.recommendation_logic && <div style={{ padding: "1rem 1.25rem", background: `${C.accent}14`, borderLeft: `3px solid ${C.accent}`, borderRadius: "0 .5rem .5rem 0", marginTop: "1rem" }}><div style={{ fontSize: ".72rem", color: C.accent, fontWeight: 700, textTransform: "uppercase", marginBottom: ".3rem" }}>Decision rule (generic)</div><p style={{ margin: 0, fontSize: ".88rem" }}>{d.recommendation_logic}</p></div>}</Card>;
}
function Failures({ p, title }: { p: any; title: string }) {
  return <Card title={`💀 ${title}`}>{(p.failure_modes || []).map((f: any, i: number) => <div key={i} style={{ padding: ".85rem 1rem", background: `${C.red}0d`, borderLeft: `2px solid ${C.red}`, borderRadius: "0 .4rem .4rem 0", marginBottom: ".7rem" }}><div style={{ fontWeight: 600, fontSize: ".88rem" }}>{typeof f === "object" ? f.scenario : f}</div>{typeof f === "object" && f.why && <div style={{ color: C.muted, fontSize: ".78rem", marginTop: ".25rem" }}>{f.why}</div>}</div>)}</Card>;
}

// Generic full-record detail for any catalog type that has no bespoke view tabs
// (attack-technique, security-control, vulnerability, breach, agentic-threat, gai-risk,
// ai-attack-technique, …). Renders every meaningful field — no per-type config needed.
function AutoDetail({ r, view }: { r: Rec; view: View }) {
  const skip = new Set<string>(["provenance", "content_hash", "status", "deprecated_at", "references"]);
  [view.title, view.subtitle, view.summary].forEach((f: any) => (Array.isArray(f) ? f : [f]).forEach((x: any) => x && skip.add(x)));
  (view.tabs || []).forEach((t: any) => { if (t.render === "linked" && t.field) skip.add(t.field); });
  const entries = Object.entries(r).filter(([k, v]) => !skip.has(k) && v != null && v !== "" && !(Array.isArray(v) && v.length === 0));
  const refs = (r.references || []).filter(Boolean);
  const isURL = (s: any) => typeof s === "string" && /^https?:\/\//.test(s);
  return <>
    <Card title="Details">
      {entries.length === 0 && <p style={{ color: C.muted, fontSize: ".85rem" }}>No additional fields.</p>}
      {entries.map(([k, v]) => (
        <div key={k} style={{ marginBottom: ".8rem" }}>
          <div style={{ fontSize: ".68rem", textTransform: "uppercase", letterSpacing: ".04em", color: C.muted, fontWeight: 700, marginBottom: ".2rem" }}>{k.replace(/_/g, " ")}</div>
          <div style={{ fontSize: ".85rem", color: C.text }}>
            {Array.isArray(v)
              ? (v.every((x: any) => typeof x !== "object") ? <div style={{ display: "flex", flexWrap: "wrap", gap: ".35rem" }}>{v.map((x: any, i: number) => <Pill key={i} text={String(x)} color={C.accent} />)}</div> : <FWValue v={v} />)
              : (typeof v === "object" ? <FWValue v={v} /> : <span style={{ whiteSpace: "pre-wrap" }}>{String(v)}</span>)}
          </div>
        </div>
      ))}
    </Card>
    {refs.length > 0 && <Card title="🔗 References">{refs.map((u: string, i: number) => <div key={i} style={{ marginBottom: ".3rem", fontSize: ".82rem" }}>{isURL(u) ? <a href={u} target="_blank" rel="noreferrer" style={{ color: C.accent }}>{u}</a> : <span style={{ color: C.muted }}>{u}</span>}</div>)}</Card>}
  </>;
}

function PipelineView({ catalog, taxonomy, types, fetchRecords, refresh }: { catalog: any; taxonomy: any[]; types: TypeCount[]; fetchRecords: (p: string) => Promise<any>; refresh?: any }) {
  const [sel, setSel] = useState<PNode | null>(null);
  const [sample, setSample] = useState<any>(null);
  const [sampleLoading, setSampleLoading] = useState(false);

  // Lazy-load a representative record when an entity node is selected — gives real
  // provenance (last update, contributing sources) without a dedicated status API.
  useEffect(() => {
    setSample(null);
    const t = sel?.kind === "entity" ? sel?.meta?.type : null;
    if (!t) return;
    let live = true; setSampleLoading(true);
    fetchRecords(`catalog/${encodeURIComponent(t)}?limit=1`)
      .then((d) => { if (live) setSample((d.records || [])[0] || null); })
      .catch(() => { if (live) setSample(null); })
      .finally(() => { if (live) setSampleLoading(false); });
    return () => { live = false; };
  }, [sel, fetchRecords]);

  if (!catalog) return <div style={{ display: "flex", justifyContent: "center", padding: "3rem" }}><TesseraSpinner size={48} /></div>;
  const countOf = (t: string) => types.find((x) => x.type === t)?.count ?? 0;
  const RC: Record<string, string> = { source: "#22d3ee", ingest: "#8b5cf6", raw: "#3b82f6", cluster: "#f59e0b", synth: "#a855f7", derived: "#ef4444", run: "#f59e0b", compound: "#64748b" };
  const sources: any[] = catalog.ingestion?.sources || [];
  const schedule = catalog.ingestion?.schedule;
  const ingestedTypes: string[] = Array.from(new Set(sources.flatMap((s: any) => s.produces || [])));
  const synthList: any[] = catalog.synthesis || [];
  const synthesizedTypes: string[] = synthList.map((s) => s.produces);
  const taxTypes: string[] = (taxonomy || []).map((t) => t.type).filter(Boolean);
  const compounding = taxTypes.filter((t) => !ingestedTypes.includes(t) && !synthesizedTypes.includes(t));
  const hasIngestPipe = sources.length > 0 || synthList.length > 0;
  const isAgent = (s: any) => (s.mode || "fetch") === "agent";
  const typesOf = (arr: any[]) => Array.from(new Set(arr.flatMap((s: any) => s.produces || [])));
  const producersOf = (t: string) => sources.filter((s) => (s.produces || []).includes(t));

  // ── lane 1: ingested -> synthesized (top-down layers) ─────────────────────────
  const layers1: PNode[][] = []; const edges1: PEdge[] = [];
  if (sources.length) {
    const fetchSrc = sources.filter((s) => !isAgent(s));
    const agentSrc = sources.filter((s) => isAgent(s));
    layers1.push(sources.map((s) => {
      const rs = refresh?.sources?.[s.id];
      let delta = "";
      if (rs && rs.status !== "pending") {
        if (rs.added || rs.updated) delta = `+${rs.added || 0} new · ${rs.updated || 0} upd${rs.skipped ? ` · ${rs.skipped} unch` : ""}`;
        else if (rs.status === "done") delta = "unchanged";
        else if (rs.status === "aborted") delta = "aborted (kept prior)";
        else if (rs.status === "failed") delta = "failed";
      }
      return {
        id: "src:" + s.id, title: s.id, kind: "source" as const,
        icon: isAgent(s) ? "\ud83e\udd16" : "\ud83c\udf10",
        sub: isAgent(s) ? "agent \u00b7 web-search" : "fetched feed",
        color: isAgent(s) ? RC.synth : RC.source, planned: s.status === "planned", meta: s,
        status: rs?.status, delta,
      };
    }));
    const anyRunning = refresh?.state === "running";
    const ingestLayer: PNode[] = [];
    if (fetchSrc.length) ingestLayer.push({ id: "ingest:fetch", title: "catalog-ingest", sub: "Lambda \u00b7 fetch", icon: "\u2699", color: RC.ingest, kind: "machinery",
      status: anyRunning ? "running" : undefined,
      meta: { desc: "Deterministic AWS Lambda. Fetches bulk structured feeds (STIX/OSCAL/JSON) and normalises them into catalog records.", inputs: fetchSrc.map((s) => s.id), produces: typesOf(fetchSrc) } });
    if (agentSrc.length) ingestLayer.push({ id: "ingest:agent", title: "catalog-agent-ingest", sub: "eco \u00b7 web-search + LLM \u00b7 grounded", icon: "\u2726", color: RC.synth, kind: "machinery", planned: agentSrc.every((s) => s.status === "planned"),
      status: anyRunning ? "running" : undefined,
      meta: { desc: "Grounded LLM ecosystem (Step Functions). A web-search agent reads the authoritative source and extracts the taxonomy; a challenger verifies completeness/fidelity. One parallel execution per source.", inputs: agentSrc.map((s) => s.id), produces: typesOf(agentSrc) } });
    layers1.push(ingestLayer);
    fetchSrc.forEach((s) => edges1.push({ from: "src:" + s.id, to: "ingest:fetch" }));
    agentSrc.forEach((s) => edges1.push({ from: "src:" + s.id, to: "ingest:agent" }));
    layers1.push(ingestedTypes.map((t) => ({ id: "raw:" + t, title: t, sub: typeLabel(t), icon: "\u25a6", count: countOf(t), color: RC.raw, kind: "entity" as const, meta: { type: t } })));
    typesOf(fetchSrc).forEach((t) => edges1.push({ from: "ingest:fetch", to: "raw:" + t }));
    typesOf(agentSrc).forEach((t) => edges1.push({ from: "ingest:agent", to: "raw:" + t }));
  }
  if (synthList.length) {
    const cb = synthList[0].cluster_by || "group";
    layers1.push([{ id: "cluster", title: "deterministic cluster", sub: "by " + cb, icon: "\u25f5", color: RC.cluster, kind: "machinery", meta: { desc: `Deterministic clustering (by ${cb}) — groups raw entities into bounded inputs for synthesis.`, from: (synthList[0].from || []) } }]);
    const fromTypes: string[] = (synthList[0].from || []).filter((t: string) => ingestedTypes.includes(t));
    (fromTypes.length ? fromTypes : ingestedTypes).forEach((t) => edges1.push({ from: "raw:" + t, to: "cluster" }));
    layers1.push([{ id: "synth", title: synthList[0].method || "synthesis", sub: "LLM eco", icon: "\u2726", color: RC.synth, kind: "machinery",
      status: (refresh?.state === "running" && refresh?.progress && refresh.progress.clusters_done < refresh.progress.clusters_total) ? "running" : undefined,
      delta: refresh?.progress ? `clusters ${refresh.progress.clusters_done}/${refresh.progress.clusters_total}` : "",
      meta: { desc: "LLM ecosystem that reasons each cluster into durable risk records (grounded, challenger-reviewed). Only CHANGED clusters re-run.", method: synthList[0].method, from: synthList[0].from } }]);
    edges1.push({ from: "cluster", to: "synth" });
    layers1.push(synthList.map((s) => ({ id: "der:" + s.produces, title: s.produces, sub: typeLabel(s.produces), icon: "\u25c8", count: countOf(s.produces), color: RC.derived, kind: "entity" as const, meta: { type: s.produces, synthesized: true } })));
    synthList.forEach((s) => edges1.push({ from: "synth", to: "der:" + s.produces }));
  }

  // ── lane 2: compounding (filled by ecosystem runs) ────────────────────────────
  const layers2: PNode[][] = [
    [{ id: "runs", title: "Ecosystem runs", sub: "from runs", icon: "\u2b21", color: RC.run, kind: "machinery", meta: { desc: "No external feed — these entities accumulate as the domain's ecosystems run and persist durable dossiers." } }],
    compounding.map((t) => ({ id: "comp:" + t, title: t, sub: typeLabel(t), icon: "\u25a6", count: countOf(t), color: RC.compound, kind: "entity" as const, meta: { type: t, compounding: true } })),
  ];
  const edges2: PEdge[] = compounding.map((t) => ({ from: "runs", to: "comp:" + t }));

  const h1 = Math.max(300, layers1.length * 150 + 40);
  const h2 = 240;

  return (
    <div style={{ position: "relative" }}>
      {hasIngestPipe && (
        <div style={{ background: "linear-gradient(135deg,rgba(59,130,246,.08),rgba(168,85,247,.05))", border: `1px solid ${C.border}`, borderRadius: ".9rem", padding: "1.2rem", marginBottom: "1.5rem" }}>
          <h2 style={{ fontSize: "1.05rem", fontWeight: 700, margin: "0 0 .3rem", color: C.accent }}>&#9741; Refresh pipeline &mdash; ingested &rarr; synthesized</h2>
          <p style={{ fontSize: ".74rem", color: C.muted, margin: "0 0 .6rem" }}>Two ingestion machineries: <b style={{ color: RC.ingest }}>&#9881; deterministic Lambda</b> for bulk feeds and a <b style={{ color: RC.synth }}>&#10022; grounded agent eco</b> for prose taxonomies. Click any node for details.</p>
          <PipelineGraph layers={layers1} edges={edges1} height={h1} onSelect={setSel} selectedId={sel?.id} />
          {schedule && <div style={{ fontSize: ".72rem", color: C.muted, marginTop: ".4rem" }}>&#9201; Scheduled refresh: <b style={{ color: C.text }}>{schedule}</b></div>}
        </div>
      )}
      {compounding.length > 0 && (
        <div style={{ background: "linear-gradient(135deg,rgba(245,158,11,.07),rgba(100,116,139,.04))", border: `1px solid ${C.border}`, borderRadius: ".9rem", padding: "1.2rem", marginBottom: "1.5rem" }}>
          <h2 style={{ fontSize: "1.05rem", fontWeight: 700, margin: "0 0 .6rem", color: C.amber }}>&#128257; Compounding &mdash; accumulated by ecosystem runs</h2>
          <PipelineGraph layers={layers2} edges={edges2} height={h2} onSelect={setSel} selectedId={sel?.id} />
        </div>
      )}
      {!hasIngestPipe && compounding.length === 0 && <p style={{ color: C.muted, fontSize: ".85rem" }}>No pipeline configured for this domain.</p>}
      {sel && <NodeDrawer node={sel} sample={sample} sampleLoading={sampleLoading} producersOf={producersOf} countOf={countOf} onClose={() => setSel(null)} />}
    </div>
  );
}

function DRow({ label, children }: { label: string; children: React.ReactNode }) {
  if (children == null || children === "" || (Array.isArray(children) && !children.length)) return null;
  return <div style={{ marginBottom: ".7rem" }}><div style={{ fontSize: ".66rem", textTransform: "uppercase", letterSpacing: ".05em", color: C.muted, fontWeight: 700, marginBottom: ".18rem" }}>{label}</div><div style={{ fontSize: ".84rem", color: C.text, wordBreak: "break-word" }}>{children}</div></div>;
}

function NodeDrawer({ node, sample, sampleLoading, producersOf, countOf, onClose }: { node: PNode; sample: any; sampleLoading: boolean; producersOf: (t: string) => any[]; countOf: (t: string) => number; onClose: () => void }) {
  const c = node.planned ? C.muted : node.color;
  const m = node.meta || {};
  const prov = (sample && sample.provenance) || {};
  return (
    <div style={{ position: "absolute", top: 0, right: 0, width: 340, maxWidth: "90%", maxHeight: "100%", overflowY: "auto", background: "#0b1220f2", backdropFilter: "blur(6px)", border: `1px solid ${c}66`, borderRadius: ".8rem", boxShadow: "0 12px 40px rgba(0,0,0,.6)", padding: "1.1rem", zIndex: 20 }}>
      <div style={{ display: "flex", justifyContent: "space-between", alignItems: "flex-start", gap: ".5rem" }}>
        <div>
          <div style={{ fontSize: ".6rem", fontWeight: 800, letterSpacing: ".05em", textTransform: "uppercase", color: c }}>{node.icon} {node.sub}</div>
          <div style={{ fontSize: "1.05rem", fontWeight: 700, color: C.text, marginTop: ".15rem" }}>{node.title}</div>
        </div>
        <button onClick={onClose} style={{ background: "transparent", border: "none", color: C.muted, cursor: "pointer", fontSize: "1.1rem", lineHeight: 1 }} aria-label="Close">&times;</button>
      </div>
      <div style={{ height: 1, background: C.border, margin: ".7rem 0 .9rem" }} />

      {node.kind === "source" && <>
        <DRow label="Ingestion mode">{(m.mode || "fetch") === "agent" ? "🤖 Agent · grounded web-search + LLM" : "⚙ Deterministic fetch (Lambda)"}</DRow>
        <DRow label="Status"><span style={{ color: node.planned ? C.amber : C.green }}>{m.status || "active"}</span></DRow>
        <DRow label="Produces">{(m.produces || []).map((p: string) => typeLabel(p)).join(", ")}</DRow>
        <DRow label="Key field">{m.key_field}</DRow>
        <DRow label="Id scheme">{m.id_scheme}</DRow>
        <DRow label="Authoritative source">{m.authoritative_url ? <a href={m.authoritative_url} target="_blank" rel="noreferrer" style={{ color: C.accent }}>{m.authoritative_url}</a> : (m.mode === "agent" ? "—" : "code-defined feed")}</DRow>
        <DRow label="Scope">{m.scope}</DRow>
      </>}

      {node.kind === "machinery" && <>
        <DRow label="What it is">{m.desc}</DRow>
        <DRow label="Inputs">{(m.inputs || []).join(", ")}</DRow>
        <DRow label="Produces">{(m.produces || []).map((p: string) => typeLabel(p)).join(", ")}</DRow>
        <DRow label="Clusters from">{(m.from || []).map((p: string) => typeLabel(p)).join(", ")}</DRow>
        <DRow label="Method">{m.method}</DRow>
      </>}

      {node.kind === "entity" && <>
        <DRow label="Records">{countOf(m.type).toLocaleString()}</DRow>
        <DRow label="Fed by">{m.synthesized ? "synthesis (derived)" : m.compounding ? "ecosystem runs (compounding)" : producersOf(m.type).map((s: any) => `${s.id}${(s.mode || "fetch") === "agent" ? " 🤖" : ""}`).join(", ")}</DRow>
        <DRow label="Last update">{sampleLoading ? "loading…" : (prov.refresh_date || prov.refresh || "—")}</DRow>
        <DRow label="Ingested by">{sampleLoading ? "" : prov.ingested_by}</DRow>
        <DRow label="Provenance sources">{sampleLoading ? "" : (prov.sources || []).join(", ")}</DRow>
        <DRow label="Grounded against">{prov.authoritative_url ? <a href={prov.authoritative_url} target="_blank" rel="noreferrer" style={{ color: C.accent }}>{prov.authoritative_url}</a> : null}</DRow>
      </>}
    </div>
  );
}
