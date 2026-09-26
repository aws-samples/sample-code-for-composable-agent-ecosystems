"use client";

/**
 * A2UI Surface — renders a complete A2UI v0.9 Basic Catalog surface.
 *
 * Takes a flat list of components and a data model, walks from `id: "root"`,
 * resolves data bindings, and renders the standard 18 Basic Catalog widgets.
 *
 * Spec: https://a2ui.org/specification/v0.9-a2ui/
 */

import { useCallback, useMemo, useState } from "react";
import ReactMarkdown from "react-markdown";
import { LineChart, Line, BarChart, Bar, PieChart, Pie, Cell, XAxis, YAxis, Tooltip, ResponsiveContainer, Legend } from "recharts";
import {
  A2UIComponent,
  A2UISurface,
  ChildList,
  DynamicValue,
  resolveDynamic,
  toDisplayString,
  resolvePath,
} from "@/lib/a2ui-protocol";

interface SurfaceProps {
  surface: A2UISurface;
  onAction?: (action: { name: string; surfaceId: string; sourceComponentId: string; context: Record<string, unknown> }) => void;
}

interface RenderContext {
  components: Map<string, A2UIComponent>;
  data: Record<string, unknown>;
  setData: (path: string, value: unknown) => void;
  scope?: unknown;
  surfaceId: string;
  onAction?: SurfaceProps["onAction"];
}

/** Set a value at a JSON Pointer path on the data model (immutable update). */
function setAtPath(data: Record<string, unknown>, path: string, value: unknown): Record<string, unknown> {
  if (!path || path === "/") return value as Record<string, unknown>;
  const segments = path.replace(/^\//, "").split("/").filter(Boolean).map((s) =>
    s.replace(/~1/g, "/").replace(/~0/g, "~")
  );
  const next = { ...data };
  let current: Record<string, unknown> | unknown[] = next;
  for (let i = 0; i < segments.length - 1; i++) {
    const seg = segments[i];
    const isArray = Array.isArray(current);
    const key = isArray ? Number(seg) : seg;
    const child = (current as Record<string, unknown>)[key as string];
    const cloned = Array.isArray(child) ? [...(child as unknown[])] : { ...((child as Record<string, unknown>) ?? {}) };
    (current as Record<string, unknown>)[key as string] = cloned;
    current = cloned as Record<string, unknown> | unknown[];
  }
  const last = segments[segments.length - 1];
  if (Array.isArray(current)) {
    current[Number(last)] = value;
  } else {
    current[last] = value;
  }
  return next;
}

export function A2UISurfaceRenderer({ surface, onAction }: SurfaceProps) {
  const [dataModel, setDataModel] = useState<Record<string, unknown>>(surface.dataModel ?? {});

  const components = useMemo(() => {
    const map = new Map<string, A2UIComponent>();
    for (const c of surface.components ?? []) map.set(c.id, c);
    return map;
  }, [surface.components]);

  const setData = useCallback((path: string, value: unknown) => {
    setDataModel((prev) => setAtPath(prev, path, value));
  }, []);

  const ctx: RenderContext = { components, data: dataModel, setData, surfaceId: surface.surfaceId, onAction };

  const root = components.get("root");
  if (!root) {
    return <div className="text-xs text-gray-500">A2UI surface missing root component</div>;
  }

  return <div className="a2ui-surface">{renderComponent(root, ctx)}</div>;
}

/** Render a child reference (a component ID or template). */
function renderChildren(children: ChildList | undefined, ctx: RenderContext): React.ReactNode[] {
  if (!children) return [];
  if (Array.isArray(children)) {
    return children
      .map((id) => {
        const child = ctx.components.get(id);
        return child ? <RenderedComponent key={id} component={child} ctx={ctx} /> : null;
      })
      .filter(Boolean) as React.ReactNode[];
  }
  // Template binding — iterate the bound array, render `componentId` for each item with a child scope
  const list = resolvePath(ctx.data, children.path, ctx.scope);
  if (!Array.isArray(list)) return [];
  const template = ctx.components.get(children.componentId);
  if (!template) return [];
  return list.map((item, idx) => (
    <RenderedComponent key={`${children.componentId}-${idx}`} component={template} ctx={{ ...ctx, scope: item }} />
  ));
}

/** Resolve a single child ID reference. */
function renderChild(childId: string | undefined, ctx: RenderContext): React.ReactNode {
  if (!childId) return null;
  const child = ctx.components.get(childId);
  return child ? <RenderedComponent component={child} ctx={ctx} /> : null;
}

function RenderedComponent({ component, ctx }: { component: A2UIComponent; ctx: RenderContext }) {
  return <>{renderComponent(component, ctx)}</>;
}

/** Resolve a Dynamic property. */
function dyn<T>(value: unknown, ctx: RenderContext): T | undefined {
  return resolveDynamic<T>(value as DynamicValue<T>, ctx.data, ctx.scope);
}

/**
 * Allowlist a URL before it is placed in an href/src attribute. Surface content is
 * agent-authored, so an unchecked URL could carry a `javascript:` (script execution on
 * click) or hostile `data:` scheme. Only return the URL when it parses to an allowed
 * scheme; otherwise return "" so the attribute is inert.
 *
 * `allowData` permits `data:image/...` for media <img>/<video>/<audio> src, where inline
 * image data is legitimate; it is never allowed for anchor href.
 */
function safeUrl(raw: string | undefined | null, opts?: { allowData?: boolean }): string {
  const u = (raw ?? "").trim();
  if (!u) return "";
  const RELATIVE_BASE = "http://relative.invalid/";
  let parsed: URL;
  try {
    parsed = new URL(u, RELATIVE_BASE);
  } catch {
    return "";
  }
  const scheme = parsed.protocol.toLowerCase();
  // Return values DERIVED FROM THE PARSED URL (not the raw input), so the sanitisation is
  // observable to static analysis and a dangerous scheme (javascript:/vbscript:/untrusted
  // data:) can never reach the href/src sink.
  if (scheme === "http:" || scheme === "https:") {
    // If it was a relative URL, hand back the original relative form (same-origin, safe);
    // otherwise the normalised absolute URL from the parser.
    return parsed.origin === RELATIVE_BASE.slice(0, -1)
      ? parsed.pathname + parsed.search + parsed.hash
      : parsed.href;
  }
  if (scheme === "mailto:") return parsed.href;
  if (opts?.allowData && scheme === "data:" && parsed.href.toLowerCase().startsWith("data:image/")) {
    return parsed.href;
  }
  return "";
}

/** Dispatch the main render based on `component` type. */
function renderComponent(c: A2UIComponent, ctx: RenderContext): React.ReactNode {
  switch (c.component) {
    case "Text":
      return <TextWidget c={c} ctx={ctx} />;
    case "Image":
      return <ImageWidget c={c} ctx={ctx} />;
    case "Icon":
      return <IconWidget c={c} ctx={ctx} />;
    case "Video":
      return <VideoWidget c={c} ctx={ctx} />;
    case "AudioPlayer":
      return <AudioWidget c={c} ctx={ctx} />;
    case "Row":
      return <RowWidget c={c} ctx={ctx} />;
    case "Column":
      return <ColumnWidget c={c} ctx={ctx} />;
    case "List":
      return <ListWidget c={c} ctx={ctx} />;
    case "Card":
      return <CardWidget c={c} ctx={ctx} />;
    case "Tabs":
      return <TabsWidget c={c} ctx={ctx} />;
    case "Divider":
      return <DividerWidget c={c} ctx={ctx} />;
    case "Modal":
      return <ModalWidget c={c} ctx={ctx} />;
    case "Button":
      return <ButtonWidget c={c} ctx={ctx} />;
    case "CheckBox":
      return <CheckBoxWidget c={c} ctx={ctx} />;
    case "TextField":
      return <TextFieldWidget c={c} ctx={ctx} />;
    case "DateTimeInput":
      return <DateTimeWidget c={c} ctx={ctx} />;
    case "ChoicePicker":
      return <ChoicePickerWidget c={c} ctx={ctx} />;
    case "Slider":
      return <SliderWidget c={c} ctx={ctx} />;
    // ─── Tessera extensions (data viz) ───────────────────────────────────
    case "Table":
      return <TableWidget c={c} ctx={ctx} />;
    case "Chart":
      return <ChartWidget c={c} ctx={ctx} />;
    case "Metric":
      return <MetricWidget c={c} ctx={ctx} />;
    case "Gauge":
      return <GaugeWidget c={c} ctx={ctx} />;
    case "Heatmap":
      return <HeatmapWidget c={c} ctx={ctx} />;
    case "Badge":
      return <BadgeWidget c={c} ctx={ctx} />;
    case "AlertBanner":
      return <AlertBannerWidget c={c} ctx={ctx} />;
    case "CitationCard":
      return <CitationCardWidget c={c} ctx={ctx} />;
    // ─── Tessera extensions: Quorum deliberation (docs/spec-quorum-archetype.md §8) ──
    case "MemberBench":
      return <MemberBenchWidget c={c} ctx={ctx} />;
    case "ArgumentThread":
      return <ArgumentThreadWidget c={c} ctx={ctx} />;
    case "DissentRail":
      return <DissentRailWidget c={c} ctx={ctx} />;
    case "ExclusionTray":
      return <ExclusionTrayWidget c={c} ctx={ctx} />;
    case "CoverageMeter":
      return <CoverageMeterWidget c={c} ctx={ctx} />;
    case "VerdictRuling":
      return <VerdictRulingWidget c={c} ctx={ctx} />;
    default:
      return <div className="text-xs text-amber-400">Unknown component: {c.component}</div>;
  }
}

// ─── Display ────────────────────────────────────────────────────────────────

function TextWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const text = toDisplayString(dyn<unknown>(c.text, ctx));
  const variant = (c.variant as string) ?? "body";
  const cls: Record<string, string> = {
    h1: "text-2xl font-bold",
    h2: "text-xl font-bold",
    h3: "text-lg font-semibold",
    body: "text-sm",
    caption: "text-xs text-gray-400",
  };
  return (
    <div className={`prose prose-invert prose-sm max-w-none ${cls[variant] ?? cls.body}`}>
      <ReactMarkdown>{text}</ReactMarkdown>
    </div>
  );
}

function ImageWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const url = safeUrl(dyn<string>(c.url, ctx), { allowData: true });
  const alt = dyn<string>(c.alt, ctx) ?? "";
  return <img src={url} alt={alt} className="max-w-full rounded" />;
}

function IconWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const name = dyn<string>(c.name, ctx) ?? "";
  // Simple emoji icon mapping for the common cases
  const map: Record<string, string> = {
    mail: "✉️", check: "✓", warning: "⚠️", info: "ℹ️", error: "❌",
    success: "✅", search: "🔍", user: "👤", calendar: "📅", chart: "📊",
  };
  return <span className="text-base">{map[name] ?? "●"}</span>;
}

function VideoWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const url = safeUrl(dyn<string>(c.url, ctx), { allowData: true });
  return <video src={url} controls className="max-w-full rounded" />;
}

function AudioWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const url = safeUrl(dyn<string>(c.url, ctx), { allowData: true });
  return <audio src={url} controls className="w-full" />;
}

// ─── Layout ─────────────────────────────────────────────────────────────────

function alignClass(align: string | undefined): string {
  switch (align) {
    case "start": return "items-start";
    case "center": return "items-center";
    case "end": return "items-end";
    case "stretch": return "items-stretch";
    default: return "";
  }
}

function justifyClass(justify: string | undefined): string {
  switch (justify) {
    case "start": return "justify-start";
    case "center": return "justify-center";
    case "end": return "justify-end";
    case "spaceBetween": return "justify-between";
    case "spaceAround": return "justify-around";
    default: return "";
  }
}

function RowWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const align = c.align as string | undefined;
  const justify = c.justify as string | undefined;
  const weight = c.weight as number | undefined;
  return (
    <div className={`flex flex-row gap-2 ${alignClass(align)} ${justifyClass(justify)}`} style={weight ? { flex: weight } : undefined}>
      {renderChildren(c.children as ChildList, ctx)}
    </div>
  );
}

function ColumnWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const align = c.align as string | undefined;
  const justify = c.justify as string | undefined;
  const weight = c.weight as number | undefined;
  return (
    <div className={`flex flex-col gap-2 ${alignClass(align)} ${justifyClass(justify)}`} style={weight ? { flex: weight } : undefined}>
      {renderChildren(c.children as ChildList, ctx)}
    </div>
  );
}

function ListWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  return (
    <div className="flex flex-col gap-1.5 max-h-96 overflow-y-auto">
      {renderChildren(c.children as ChildList, ctx)}
    </div>
  );
}

function CardWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-4">
      {c.child ? renderChild(c.child as string, ctx) : renderChildren(c.children as ChildList, ctx)}
    </div>
  );
}

function TabsWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const tabs = (c.tabs as Array<{ title: string; child: string }>) ?? [];
  const [active, setActive] = useState(0);
  return (
    <div>
      <div className="flex border-b border-gray-700">
        {tabs.map((tab, i) => (
          <button
            key={i}
            onClick={() => setActive(i)}
            className={`px-3 py-1.5 text-xs ${
              active === i ? "text-tessera-accent border-b-2 border-tessera-accent" : "text-gray-400"
            }`}
          >
            {tab.title}
          </button>
        ))}
      </div>
      <div className="p-3">{tabs[active] ? renderChild(tabs[active].child, ctx) : null}</div>
    </div>
  );
}

function DividerWidget({ c }: { c: A2UIComponent; ctx: RenderContext }) {
  const horizontal = (c.axis as string | undefined) !== "vertical";
  return horizontal ? (
    <div className="border-t border-gray-700 my-2" />
  ) : (
    <div className="border-l border-gray-700 mx-2 self-stretch" />
  );
}

function ModalWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const open = dyn<boolean>(c.open, ctx) ?? false;
  if (!open) return null;
  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50">
      <div className="bg-gray-900 border border-gray-700 rounded-lg p-4 max-w-2xl w-full">
        {c.child ? renderChild(c.child as string, ctx) : renderChildren(c.children as ChildList, ctx)}
      </div>
    </div>
  );
}

// ─── Interactive ────────────────────────────────────────────────────────────

interface ActionDef {
  event?: { name: string; context?: Record<string, unknown> };
  functionCall?: { call: string; args?: Record<string, unknown> };
}

function dispatchAction(
  action: ActionDef | undefined,
  componentId: string,
  ctx: RenderContext
) {
  if (!action) return;
  if (action.event) {
    // Resolve any dynamic values in context
    const resolvedContext: Record<string, unknown> = {};
    for (const [k, v] of Object.entries(action.event.context ?? {})) {
      resolvedContext[k] = resolveDynamic(v as DynamicValue<unknown>, ctx.data, ctx.scope);
    }
    ctx.onAction?.({
      name: action.event.name,
      surfaceId: ctx.surfaceId,
      sourceComponentId: componentId,
      context: resolvedContext,
    });
  }
  // functionCall actions could be implemented (e.g. openUrl) but skipped for now
}

function ButtonWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const variant = (c.variant as string) ?? "primary";
  const text = c.text ? toDisplayString(dyn<unknown>(c.text, ctx)) : undefined;
  const cls = variant === "primary"
    ? "bg-tessera-gradient text-white shadow-lg shadow-purple-500/20"
    : variant === "borderless"
    ? "text-tessera-accent hover:bg-white/5"
    : "border border-gray-700 text-gray-300 hover:border-gray-500";
  return (
    <button
      onClick={() => dispatchAction(c.action as ActionDef | undefined, c.id, ctx)}
      className={`px-4 py-2 rounded-lg text-sm font-medium transition ${cls}`}
    >
      {text ?? (c.child ? renderChild(c.child as string, ctx) : "Submit")}
    </button>
  );
}

function CheckBoxWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const label = dyn<string>(c.label, ctx) ?? "";
  const value = c.value as { path: string } | boolean | undefined;
  const path = typeof value === "object" && value && "path" in value ? value.path : undefined;
  const checked = path ? Boolean(resolvePath(ctx.data, path, ctx.scope)) : Boolean(value);
  return (
    <label className="flex items-center gap-2 text-sm text-gray-300">
      <input
        type="checkbox"
        checked={checked}
        onChange={(e) => path && ctx.setData(path, e.target.checked)}
        className="rounded"
      />
      {label}
    </label>
  );
}

function TextFieldWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const label = dyn<string>(c.label, ctx) ?? "";
  const variant = (c.variant as string) ?? "shortText";
  const value = c.value as { path: string } | string | undefined;
  const path = typeof value === "object" && value && "path" in value ? value.path : undefined;
  const current = path ? toDisplayString(resolvePath(ctx.data, path, ctx.scope)) : (value as string) ?? "";
  const onChange = (val: string) => path && ctx.setData(path, val);
  if (variant === "longText") {
    return (
      <textarea
        value={current}
        onChange={(e) => onChange(e.target.value)}
        placeholder={label}
        rows={4}
        className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white"
      />
    );
  }
  return (
    <input
      type="text"
      value={current}
      onChange={(e) => onChange(e.target.value)}
      placeholder={label}
      className="w-full bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white"
    />
  );
}

function DateTimeWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const value = c.value as { path: string } | string | undefined;
  const path = typeof value === "object" && value && "path" in value ? value.path : undefined;
  const current = path ? toDisplayString(resolvePath(ctx.data, path, ctx.scope)) : (value as string) ?? "";
  const variant = (c.variant as string) ?? "datetime";
  const type = variant === "date" ? "date" : variant === "time" ? "time" : "datetime-local";
  return (
    <input
      type={type}
      value={current}
      onChange={(e) => path && ctx.setData(path, e.target.value)}
      className="bg-gray-900 border border-gray-700 rounded px-3 py-2 text-sm text-white"
    />
  );
}

function ChoicePickerWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const variant = (c.variant as string) ?? "mutuallyExclusive";
  const options = (c.options as Array<{ label: string; value: string }>) ?? [];
  const value = c.value as { path: string } | string[] | string | undefined;
  const path = typeof value === "object" && value && "path" in value ? (value as { path: string }).path : undefined;
  const raw = path ? resolvePath(ctx.data, path, ctx.scope) : value;
  const isMulti = variant === "multipleSelect";
  const selected: string[] = Array.isArray(raw) ? (raw as string[]) : raw ? [raw as string] : [];

  const toggle = (opt: string) => {
    if (!path) return;
    if (isMulti) {
      const next = selected.includes(opt) ? selected.filter((s) => s !== opt) : [...selected, opt];
      ctx.setData(path, next);
    } else {
      ctx.setData(path, opt);
    }
  };

  return (
    <div className="flex flex-col gap-1">
      {options.map((opt) => (
        <label key={opt.value} className="flex items-center gap-2 text-sm text-gray-300">
          <input
            type={isMulti ? "checkbox" : "radio"}
            checked={selected.includes(opt.value)}
            onChange={() => toggle(opt.value)}
          />
          {opt.label}
        </label>
      ))}
    </div>
  );
}

function SliderWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const min = (c.min as number) ?? 0;
  const max = (c.max as number) ?? 100;
  const step = (c.step as number) ?? 1;
  const value = c.value as { path: string } | number | undefined;
  const path = typeof value === "object" && value && "path" in value ? (value as { path: string }).path : undefined;
  const current = path ? Number(resolvePath(ctx.data, path, ctx.scope) ?? min) : (value as number) ?? min;
  return (
    <div className="flex items-center gap-2">
      <input
        type="range"
        min={min}
        max={max}
        step={step}
        value={current}
        onChange={(e) => path && ctx.setData(path, Number(e.target.value))}
        className="flex-1"
      />
      <span className="text-xs text-gray-400 w-10 text-right">{current}</span>
    </div>
  );
}

// ─── Tessera extensions: data viz ───────────────────────────────────────────

const CHART_COLORS = ["#a855f7", "#3b82f6", "#10b981", "#f59e0b", "#ef4444", "#06b6d4", "#ec4899"];

function TableWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const title = c.title ? toDisplayString(dyn<unknown>(c.title, ctx)) : undefined;
  // Columns: array of strings or array of { key, label }
  const rawColumns = dyn<unknown>(c.columns, ctx) ?? c.columns;
  const columns: { key: string; label: string }[] = Array.isArray(rawColumns)
    ? (rawColumns as unknown[]).map((col, i) =>
        typeof col === "string"
          ? { key: col, label: col }
          : { key: (col as any).key ?? `col${i}`, label: (col as any).label ?? (col as any).key ?? `Col ${i}` }
      )
    : [];
  // Rows: array of arrays OR array of objects
  const rawRows = dyn<unknown>(c.rows, ctx) ?? c.rows;
  const rows: unknown[][] = Array.isArray(rawRows)
    ? (rawRows as unknown[]).map((row) => {
        if (Array.isArray(row)) return row;
        if (typeof row === "object" && row !== null) {
          return columns.map((col) => (row as Record<string, unknown>)[col.key]);
        }
        return [row];
      })
    : [];
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-4">
      {title && <h3 className="font-semibold text-white mb-3 text-sm">{title}</h3>}
      <div className="overflow-x-auto">
        <table className="w-full text-xs">
          <thead>
            <tr className="border-b border-gray-700">
              {columns.map((col, i) => (
                <th key={i} className="text-left py-2 px-3 text-gray-400 font-medium">
                  {col.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, i) => (
              <tr key={i} className="border-b border-gray-800">
                {row.map((cell, j) => (
                  <td key={j} className="py-2 px-3 text-gray-300">
                    {toDisplayString(cell)}
                  </td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function ChartWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const title = c.title ? toDisplayString(dyn<unknown>(c.title, ctx)) : undefined;
  const chartType = (dyn<string>(c.chartType, ctx) ?? c.chartType ?? "bar") as string;
  const rawData = dyn<unknown>(c.data, ctx) ?? c.data ?? {};
  const data = rawData as { labels?: string[]; values?: number[]; series?: { name: string; values: number[] }[] };

  // Single-series shape → array of {name, value}
  const singleSeries = (data.labels ?? []).map((label, i) => ({
    name: label,
    value: data.values?.[i] ?? 0,
  }));

  // Multi-series shape → array of {name, [seriesName]: value}
  const multiSeries = data.series && data.labels
    ? data.labels.map((label, i) => {
        const point: Record<string, string | number> = { name: label };
        data.series!.forEach((s) => { point[s.name] = s.values[i] ?? 0; });
        return point;
      })
    : [];

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-4">
      {title && <h3 className="font-semibold text-white mb-3 text-sm">{title}</h3>}
      <ResponsiveContainer width="100%" height={220}>
        {chartType === "line" ? (
          data.series ? (
            <LineChart data={multiSeries}>
              <XAxis dataKey="name" stroke="#64748b" tick={{ fontSize: 11 }} />
              <YAxis stroke="#64748b" tick={{ fontSize: 11 }} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 12 }} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {data.series.map((s, i) => (
                <Line key={s.name} type="monotone" dataKey={s.name} stroke={CHART_COLORS[i % CHART_COLORS.length]} strokeWidth={2} dot={false} />
              ))}
            </LineChart>
          ) : (
            <LineChart data={singleSeries}>
              <XAxis dataKey="name" stroke="#64748b" tick={{ fontSize: 11 }} />
              <YAxis stroke="#64748b" tick={{ fontSize: 11 }} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 12 }} />
              <Line type="monotone" dataKey="value" stroke={CHART_COLORS[0]} strokeWidth={2} />
            </LineChart>
          )
        ) : chartType === "pie" ? (
          <PieChart>
            <Pie data={singleSeries} dataKey="value" nameKey="name" cx="50%" cy="50%" outerRadius={80} label={{ fontSize: 11 }}>
              {singleSeries.map((_, i) => (
                <Cell key={i} fill={CHART_COLORS[i % CHART_COLORS.length]} />
              ))}
            </Pie>
            <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 12 }} />
          </PieChart>
        ) : (
          // bar (default)
          data.series ? (
            <BarChart data={multiSeries}>
              <XAxis dataKey="name" stroke="#64748b" tick={{ fontSize: 11 }} />
              <YAxis stroke="#64748b" tick={{ fontSize: 11 }} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 12 }} />
              <Legend wrapperStyle={{ fontSize: 11 }} />
              {data.series.map((s, i) => (
                <Bar key={s.name} dataKey={s.name} fill={CHART_COLORS[i % CHART_COLORS.length]} />
              ))}
            </BarChart>
          ) : (
            <BarChart data={singleSeries}>
              <XAxis dataKey="name" stroke="#64748b" tick={{ fontSize: 11 }} />
              <YAxis stroke="#64748b" tick={{ fontSize: 11 }} />
              <Tooltip contentStyle={{ background: "#0f172a", border: "1px solid #334155", fontSize: 12 }} />
              <Bar dataKey="value" fill={CHART_COLORS[0]} />
            </BarChart>
          )
        )}
      </ResponsiveContainer>
    </div>
  );
}

function MetricWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const label = toDisplayString(dyn<unknown>(c.label, ctx));
  const value = toDisplayString(dyn<unknown>(c.value, ctx));
  const trend = c.trend ? toDisplayString(dyn<unknown>(c.trend, ctx)) : undefined;
  const trendDirection = (dyn<string>(c.trendDirection, ctx) ?? c.trendDirection) as string | undefined;
  const trendColor = trendDirection === "up" ? "text-green-400" : trendDirection === "down" ? "text-red-400" : "text-gray-400";
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-3">
      <div className="text-xs text-gray-400 mb-1">{label}</div>
      <div className="text-xl font-semibold text-white">{value}</div>
      {trend && <div className={`text-xs mt-1 ${trendColor}`}>{trend}</div>}
    </div>
  );
}

// ─── Tessera extensions: storytelling widgets ───────────────────────────────

interface GaugeZone { from: number; to: number; color: "red" | "amber" | "green" | string }

function GaugeWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const label = c.label ? toDisplayString(dyn<unknown>(c.label, ctx)) : undefined;
  const value = Number(dyn<number>(c.value, ctx) ?? c.value ?? 0);
  const min = Number(dyn<number>(c.min, ctx) ?? c.min ?? 0);
  const max = Number(dyn<number>(c.max, ctx) ?? c.max ?? 100);
  const target = c.target !== undefined ? Number(dyn<number>(c.target, ctx) ?? c.target) : undefined;
  const unit = c.unit ? toDisplayString(dyn<unknown>(c.unit, ctx)) : "";
  const zones = (dyn<GaugeZone[]>(c.zones, ctx) ?? c.zones ?? []) as GaugeZone[];

  const range = max - min || 1;
  const pct = Math.max(0, Math.min(1, (value - min) / range));

  // Determine fill color from zones (or default green→amber→red mapping)
  let fillColor = "#10b981";
  if (zones.length > 0) {
    const zone = zones.find((z) => value >= z.from && value <= z.to);
    if (zone) {
      const map: Record<string, string> = { red: "#ef4444", amber: "#f59e0b", green: "#10b981" };
      fillColor = map[zone.color] ?? zone.color;
    }
  } else {
    if (pct < 0.4) fillColor = "#10b981";
    else if (pct < 0.7) fillColor = "#f59e0b";
    else fillColor = "#ef4444";
  }

  // Semicircle gauge. Width 200, height 110. Stroke 14.
  const cx = 100, cy = 100, r = 80, stroke = 14;
  const startAngle = Math.PI; // 180°
  const endAngle = 0;          // 0°
  const angle = startAngle + (endAngle - startAngle) * pct;
  const arcPath = (a0: number, a1: number) => {
    const x0 = cx + r * Math.cos(a0);
    const y0 = cy + r * Math.sin(a0);
    const x1 = cx + r * Math.cos(a1);
    const y1 = cy + r * Math.sin(a1);
    const large = Math.abs(a1 - a0) > Math.PI ? 1 : 0;
    const sweep = a1 > a0 ? 1 : 0;
    return `M ${x0} ${y0} A ${r} ${r} 0 ${large} ${sweep} ${x1} ${y1}`;
  };

  const targetMarker = target !== undefined ? (() => {
    const tPct = Math.max(0, Math.min(1, (target - min) / range));
    const tAngle = startAngle + (endAngle - startAngle) * tPct;
    const tx = cx + (r + 8) * Math.cos(tAngle);
    const ty = cy + (r + 8) * Math.sin(tAngle);
    const tx2 = cx + (r - 8) * Math.cos(tAngle);
    const ty2 = cy + (r - 8) * Math.sin(tAngle);
    return <line x1={tx} y1={ty} x2={tx2} y2={ty2} stroke="#fbbf24" strokeWidth={2} />;
  })() : null;

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-3 flex flex-col items-center">
      {label && <div className="text-xs text-gray-400 mb-1 self-start">{label}</div>}
      <svg viewBox="0 0 200 110" className="w-full max-w-[220px]">
        {/* track */}
        <path d={arcPath(startAngle, endAngle)} fill="none" stroke="#1f2937" strokeWidth={stroke} strokeLinecap="round" />
        {/* fill */}
        {pct > 0 && (
          <path d={arcPath(startAngle, angle)} fill="none" stroke={fillColor} strokeWidth={stroke} strokeLinecap="round" />
        )}
        {targetMarker}
        <text x="100" y="92" textAnchor="middle" fontSize="22" fontWeight="600" fill="#f9fafb">{value}{unit}</text>
        {target !== undefined && (
          <text x="100" y="106" textAnchor="middle" fontSize="9" fill="#9ca3af">target {target}{unit}</text>
        )}
      </svg>
    </div>
  );
}

function HeatmapWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const title = c.title ? toDisplayString(dyn<unknown>(c.title, ctx)) : undefined;
  const xLabels = (dyn<string[]>(c.xLabels, ctx) ?? c.xLabels ?? []) as string[];
  const yLabels = (dyn<string[]>(c.yLabels, ctx) ?? c.yLabels ?? []) as string[];
  const values = (dyn<number[][]>(c.values, ctx) ?? c.values ?? []) as number[][];
  const colorScale = (dyn<string>(c.colorScale, ctx) ?? c.colorScale ?? "risk") as string; // "risk" or "heat"

  // Determine min/max for normalization
  const flat = values.flat().filter((v) => typeof v === "number");
  const vmin = flat.length ? Math.min(...flat) : 0;
  const vmax = flat.length ? Math.max(...flat) : 1;
  const range = vmax - vmin || 1;

  const cellColor = (v: number): string => {
    const t = (v - vmin) / range;
    if (colorScale === "heat") {
      // blue → green → yellow → red
      const r = Math.round(255 * Math.min(1, t * 1.6));
      const g = Math.round(255 * Math.min(1, 1.6 - Math.abs(t - 0.5) * 3.2));
      const b = Math.round(255 * Math.max(0, 1 - t * 1.6));
      return `rgb(${r},${g},${b})`;
    }
    // risk scale: green (low) → amber → red (high)
    if (t < 0.34) return `rgba(16,185,129,${0.3 + t * 1.5})`;
    if (t < 0.67) return `rgba(245,158,11,${0.4 + (t - 0.34) * 1.2})`;
    return `rgba(239,68,68,${0.5 + (t - 0.67) * 1.5})`;
  };

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-3">
      {title && <h3 className="font-semibold text-white mb-2 text-sm">{title}</h3>}
      <div className="overflow-x-auto">
        <table className="text-[11px] border-separate" style={{ borderSpacing: 2 }}>
          <thead>
            <tr>
              <th className="px-1"></th>
              {xLabels.map((x, i) => (
                <th key={i} className="px-2 py-1 text-gray-400 font-normal text-center">{x}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {yLabels.map((y, ri) => (
              <tr key={ri}>
                <td className="px-2 py-1 text-gray-400 text-right whitespace-nowrap">{y}</td>
                {xLabels.map((_, ci) => {
                  const v = values[ri]?.[ci];
                  const numeric = typeof v === "number" ? v : 0;
                  return (
                    <td
                      key={ci}
                      className="text-center font-medium text-gray-900"
                      style={{
                        backgroundColor: typeof v === "number" ? cellColor(numeric) : "#1f2937",
                        minWidth: 36,
                        height: 28,
                        borderRadius: 4,
                      }}
                      aria-label={typeof v === "number" ? `${y} × ${xLabels[ci]}: ${v}` : undefined}
                    >
                      {typeof v === "number" ? v : ""}
                    </td>
                  );
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  );
}

function BadgeWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const text = toDisplayString(dyn<unknown>(c.text, ctx));
  const severity = (dyn<string>(c.severity, ctx) ?? c.severity ?? "neutral") as string;
  const styles: Record<string, string> = {
    success: "bg-green-500/15 text-green-400 border-green-500/30",
    warning: "bg-amber-500/15 text-amber-400 border-amber-500/30",
    danger: "bg-red-500/15 text-red-400 border-red-500/30",
    info: "bg-blue-500/15 text-blue-400 border-blue-500/30",
    neutral: "bg-gray-700/40 text-gray-300 border-gray-600",
  };
  return (
    <span className={`inline-flex items-center px-2 py-0.5 rounded-full border text-[11px] font-medium ${styles[severity] ?? styles.neutral}`}>
      {text}
    </span>
  );
}

function AlertBannerWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const severity = (dyn<string>(c.severity, ctx) ?? c.severity ?? "info") as string;
  const title = c.title ? toDisplayString(dyn<unknown>(c.title, ctx)) : undefined;
  const body = c.body ? toDisplayString(dyn<unknown>(c.body, ctx)) : undefined;
  const styles: Record<string, { wrap: string; icon: string }> = {
    success: { wrap: "bg-green-500/10 border-green-500/30 text-green-200", icon: "✓" },
    warning: { wrap: "bg-amber-500/10 border-amber-500/30 text-amber-200", icon: "⚠" },
    danger: { wrap: "bg-red-500/10 border-red-500/30 text-red-200", icon: "✕" },
    info: { wrap: "bg-blue-500/10 border-blue-500/30 text-blue-200", icon: "ℹ" },
  };
  const s = styles[severity] ?? styles.info;
  return (
    <div className={`flex gap-3 border rounded-lg px-4 py-3 ${s.wrap}`}>
      <span className="text-lg leading-tight">{s.icon}</span>
      <div className="flex-1 min-w-0">
        {title && <div className="font-semibold text-sm">{title}</div>}
        {body && (
          <div className="text-xs mt-1 prose prose-invert prose-sm max-w-none">
            <ReactMarkdown>{body}</ReactMarkdown>
          </div>
        )}
      </div>
    </div>
  );
}

function CitationCardWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const title = toDisplayString(dyn<unknown>(c.title, ctx));
  const snippet = c.snippet ? toDisplayString(dyn<unknown>(c.snippet, ctx)) : undefined;
  const rawUrl = c.url ? toDisplayString(dyn<unknown>(c.url, ctx)) : undefined;
  // Agent-supplied URL: allowlist the scheme before it reaches the href sink so a
  // javascript:/data: URL can't execute or redirect on click.
  const url = safeUrl(rawUrl) || undefined;
  const source = c.source ? toDisplayString(dyn<unknown>(c.source, ctx)) : undefined;
  const retrievedAt = c.retrievedAt ? toDisplayString(dyn<unknown>(c.retrievedAt, ctx)) : undefined;

  let host = "";
  if (url) {
    try { host = new URL(url).hostname.replace(/^www\./, ""); } catch { host = url; }
  }

  const TitleEl = url ? (
    <a href={url} target="_blank" rel="noopener noreferrer" className="text-blue-400 hover:underline font-medium text-sm">
      {title}
    </a>
  ) : (
    <span className="text-gray-200 font-medium text-sm">{title}</span>
  );

  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-3">
      <div className="flex items-start gap-2">
        <span className="text-gray-500 text-xs mt-0.5">🔗</span>
        <div className="flex-1 min-w-0">
          <div>{TitleEl}</div>
          {snippet && <div className="text-xs text-gray-400 mt-1 line-clamp-3">{snippet}</div>}
          <div className="flex items-center gap-2 mt-2 text-[10px] text-gray-500">
            {source && <span>{source}</span>}
            {host && <span>· {host}</span>}
            {retrievedAt && <span>· {retrievedAt}</span>}
          </div>
        </div>
      </div>
    </div>
  );
}


// ─── Tessera extensions: Quorum deliberation widgets ────────────────────────
// Reusable across ANY Quorum ecosystem — the panel/argument/dissent/verdict vocabulary
// the quorum agents emit via generateUI. No per-ecosystem code.

const _SEV_ICON: Record<string, string> = { blocker: "■", material: "▲", note: "·" };
const _ENTRY_COLOR: Record<string, string> = {
  objection: "text-amber-300", dissent: "text-red-300", rebuttal: "text-blue-300",
  concession: "text-green-300", question: "text-purple-300", claim: "text-gray-300",
};
const _MEMBER_STATE: Record<string, string> = {
  speaking: "border-tessera-accent", dissenting: "border-red-500/50",
  thinking: "border-amber-500/40", silent: "border-gray-700",
};

function MemberBenchWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const members = (dyn<any[]>(c.members, ctx) ?? (c.members as any[]) ?? []) as any[];
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-4">
      <h3 className="font-semibold text-white text-sm mb-3">Convened panel ({members.length})</h3>
      <div className="flex flex-wrap gap-2">
        {members.map((m, i) => (
          <div key={i} className={`min-w-[150px] rounded-lg border ${_MEMBER_STATE[m.state] ?? "border-gray-700"} bg-gray-900/60 p-2`}>
            <div className="text-sm text-gray-100 font-medium">{m.name ?? m.persona_id}</div>
            <div className="text-[11px] text-gray-400 line-clamp-2">{m.stance ?? ""}</div>
            <div className="mt-1 flex items-center gap-1">
              {m.summoned && (
                <span className="text-[9px] uppercase tracking-wide bg-tessera-accent/15 text-tessera-accent border border-tessera-accent/30 rounded px-1">summoned</span>
              )}
              {m.state && <span className="text-[10px] text-gray-500">{m.state}</span>}
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

function ArgumentThreadWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const entries = (dyn<any[]>(c.entries, ctx) ?? (c.entries as any[]) ?? []) as any[];
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-4">
      <h3 className="font-semibold text-white text-sm mb-3">Deliberation ({entries.length})</h3>
      <div className="flex flex-col gap-1.5">
        {entries.map((e, i) => (
          <div key={i} className="text-xs" style={{ marginLeft: e.target ? 18 : 0 }}>
            <span className={`mr-1 ${e.severity === "blocker" ? "text-red-400" : e.severity === "material" ? "text-amber-400" : "text-gray-500"}`}>
              {_SEV_ICON[e.severity] ?? "·"}
            </span>
            <span className="font-mono text-gray-500">[{e.member}]</span>{" "}
            <span className={`font-medium ${_ENTRY_COLOR[e.type] ?? "text-gray-300"}`}>{e.type}</span>
            <span className="text-gray-300">: {e.text}</span>
            {Array.isArray(e.grounding) && e.grounding.length > 0 && (
              <span className="ml-1 text-[10px] text-gray-500">[{e.grounding.join(", ")}]</span>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}

function DissentRailWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const dissents = (dyn<any[]>(c.dissents, ctx) ?? (c.dissents as any[]) ?? []) as any[];
  if (dissents.length === 0) return null;
  return (
    <div className="bg-red-500/5 border border-red-500/30 rounded-lg p-4">
      <h3 className="font-semibold text-red-300 text-sm mb-2">Dissent — preserved ({dissents.length})</h3>
      <ul className="flex flex-col gap-1.5">
        {dissents.map((d, i) => (
          <li key={i} className="text-xs text-red-200">
            <span className="font-medium">{d.by}:</span> {d.position}
          </li>
        ))}
      </ul>
    </div>
  );
}

function ExclusionTrayWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const excluded = (dyn<any[]>(c.excluded, ctx) ?? (c.excluded as any[]) ?? []) as any[];
  const justification = c.justification ? toDisplayString(dyn<unknown>(c.justification, ctx)) : "";
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-4">
      <h3 className="font-semibold text-white text-sm mb-1">Why this panel</h3>
      {justification && <p className="text-xs text-gray-400 mb-3">{justification}</p>}
      <div className="text-[11px] text-gray-500 uppercase tracking-wide mb-1">Considered but not seated ({excluded.length})</div>
      <ul className="flex flex-col gap-1">
        {excluded.map((e, i) => (
          <li key={i} className="text-xs text-gray-400">
            <span className="text-gray-300">{e.persona_id}</span> — {e.reason}
          </li>
        ))}
      </ul>
    </div>
  );
}

function CoverageMeterWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const covered = (dyn<string[]>(c.covered, ctx) ?? (c.covered as string[]) ?? []) as string[];
  const missing = (dyn<string[]>(c.missing, ctx) ?? (c.missing as string[]) ?? []) as string[];
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-3">
      <div className="text-xs text-gray-400 mb-2">Scrutiny coverage</div>
      <div className="flex flex-wrap gap-1">
        {covered.map((d) => (
          <span key={d} className="text-[11px] bg-green-500/15 text-green-300 border border-green-500/30 rounded-full px-2 py-0.5">{d}</span>
        ))}
        {missing.map((d) => (
          <span key={d} className="text-[11px] bg-red-500/15 text-red-300 border border-red-500/30 rounded-full px-2 py-0.5">{d} (gap)</span>
        ))}
      </div>
    </div>
  );
}

function VerdictRulingWidget({ c, ctx }: { c: A2UIComponent; ctx: RenderContext }) {
  const grade = (dyn<string>(c.grade, ctx) ?? c.grade ?? "") as string;
  const summary = c.summary ? toDisplayString(dyn<unknown>(c.summary, ctx)) : "";
  const against = (dyn<string[]>(c.against_whom, ctx) ?? (c.against_whom as string[]) ?? []) as string[];
  const elements = (dyn<any[]>(c.indefensible_elements, ctx) ?? (c.indefensible_elements as any[]) ?? []) as any[];
  const dissent = (dyn<any[]>(c.dissent_preserved, ctx) ?? (c.dissent_preserved as any[]) ?? []) as any[];
  const composition = c.composition_summary ? toDisplayString(dyn<unknown>(c.composition_summary, ctx)) : "";
  const gradeStyle: Record<string, string> = {
    defensible: "bg-green-500/15 text-green-300 border-green-500/40",
    defensible_with_conditions: "bg-amber-500/15 text-amber-300 border-amber-500/40",
    indefensible: "bg-red-500/15 text-red-300 border-red-500/40",
  };
  return (
    <div className="bg-gray-900 border border-gray-700 rounded-lg p-4">
      <div className="flex items-center gap-3 mb-2">
        <span className={`px-3 py-1 rounded-full border text-sm font-semibold uppercase tracking-wide ${gradeStyle[grade] ?? "bg-gray-700/40 text-gray-300 border-gray-600"}`}>
          {grade.replace(/_/g, " ") || "verdict"}
        </span>
        {against.length > 0 && (
          <span className="text-xs text-gray-400">fails against: {against.join(", ")}</span>
        )}
      </div>
      {summary && <p className="text-sm text-gray-300 mb-3">{summary}</p>}
      {elements.length > 0 && (
        <div className="mb-3">
          <div className="text-[11px] uppercase tracking-wide text-gray-500 mb-1">Indefensible elements</div>
          <ul className="flex flex-col gap-1">
            {elements.map((el, i) => (
              <li key={i} className="text-xs text-gray-300">
                <span className="text-red-300">■</span> {el.element ?? el.argument} <span className="text-gray-500">— {el.by}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
      {dissent.length > 0 && (
        <div className="mb-2">
          <div className="text-[11px] uppercase tracking-wide text-gray-500 mb-1">Dissent preserved</div>
          <ul className="flex flex-col gap-1">
            {dissent.map((d, i) => (
              <li key={i} className="text-xs text-red-200"><span className="font-medium">{d.by}:</span> {d.position}</li>
            ))}
          </ul>
        </div>
      )}
      {composition && <div className="text-[11px] text-gray-500 mt-2">composition: {composition}</div>}
    </div>
  );
}
