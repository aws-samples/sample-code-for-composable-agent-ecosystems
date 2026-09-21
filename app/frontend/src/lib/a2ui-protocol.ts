/**
 * A2UI Protocol v0.9 — Basic Catalog types and data-binding helpers.
 *
 * Spec: https://a2ui.org/specification/v0.9-a2ui/
 *
 * The protocol defines a flat list of components linked by ID references
 * (adjacency list). The renderer resolves the tree starting from `id === "root"`.
 * Component properties may be literal values or `Dynamic*` types containing a
 * `path` (JSON Pointer) or `FunctionCall`.
 */

export type DynamicValue<T> = T | { path: string } | FunctionCall;

export interface FunctionCall {
  call: string;
  args?: Record<string, unknown>;
  returnType?: string;
}

/** A single component definition in the adjacency list. */
export interface A2UIComponent {
  id: string;
  component: string;
  [key: string]: unknown;
}

/** A complete UI surface payload. */
export interface A2UISurface {
  surfaceId: string;
  catalogId?: string;
  theme?: Record<string, unknown>;
  components: A2UIComponent[];
  dataModel?: Record<string, unknown>;
}

/** ChildList — either an array of component IDs or a template binding. */
export type ChildList = string[] | { path: string; componentId: string };

/** Resolve a JSON Pointer path (RFC 6901) against a data object. */
export function resolvePath(data: unknown, path: string, scope?: unknown): unknown {
  if (!path) return undefined;
  // Relative paths (no leading /) resolve against scope, absolute against root
  const isRelative = !path.startsWith("/");
  const base = isRelative ? scope ?? {} : data ?? {};
  const segments = path.replace(/^\//, "").split("/").filter(Boolean).map((s) =>
    s.replace(/~1/g, "/").replace(/~0/g, "~")
  );
  let current: unknown = base;
  for (const seg of segments) {
    if (current == null) return undefined;
    if (Array.isArray(current)) {
      const idx = parseInt(seg, 10);
      if (Number.isNaN(idx)) return undefined;
      current = current[idx];
    } else if (typeof current === "object") {
      current = (current as Record<string, unknown>)[seg];
    } else {
      return undefined;
    }
  }
  return current;
}

/** Resolve a Dynamic* value to a concrete value. */
export function resolveDynamic<T>(
  value: DynamicValue<T> | undefined,
  data: unknown,
  scope?: unknown
): T | undefined {
  if (value === undefined || value === null) return undefined;
  if (typeof value === "object" && value !== null) {
    if ("path" in value && typeof (value as { path: string }).path === "string") {
      const resolved = resolvePath(data, (value as { path: string }).path, scope);
      return resolved as T | undefined;
    }
    if ("call" in value) {
      return runFunction(value as FunctionCall, data, scope) as T | undefined;
    }
  }
  return value as T;
}

/** Convert any resolved value to a display string. */
export function toDisplayString(v: unknown): string {
  if (v === null || v === undefined) return "";
  if (typeof v === "string") return v;
  if (typeof v === "number" || typeof v === "boolean") return String(v);
  return JSON.stringify(v);
}

/** Run a registered client-side function. Used by `formatString`, `formatNumber`, etc. */
export function runFunction(fn: FunctionCall, data: unknown, scope?: unknown): unknown {
  const args = fn.args ?? {};
  const resolveArg = (a: unknown): unknown =>
    typeof a === "object" && a !== null && ("path" in a || "call" in a)
      ? resolveDynamic(a as DynamicValue<unknown>, data, scope)
      : a;

  switch (fn.call) {
    case "formatString": {
      const value = resolveArg(args.value) as string | undefined;
      return formatString(value ?? "", data, scope);
    }
    case "formatNumber": {
      const value = resolveArg(args.value) as number | undefined;
      const precision = (resolveArg(args.precision) as number) ?? 2;
      if (value === undefined || value === null) return "";
      return value.toLocaleString(undefined, {
        minimumFractionDigits: precision,
        maximumFractionDigits: precision,
      });
    }
    case "formatCurrency": {
      const value = resolveArg(args.value) as number | undefined;
      const currency = (resolveArg(args.currency) as string) ?? "USD";
      if (value === undefined || value === null) return "";
      return value.toLocaleString(undefined, { style: "currency", currency });
    }
    case "formatDate": {
      const value = resolveArg(args.value) as string | number | Date | undefined;
      if (!value) return "";
      try {
        return new Date(value).toLocaleString();
      } catch {
        return String(value);
      }
    }
    case "now":
      return new Date().toISOString();
    case "upper":
      return String(resolveArg(args.value) ?? "").toUpperCase();
    case "lower":
      return String(resolveArg(args.value) ?? "").toLowerCase();
    case "concat": {
      const values = (args.values as unknown[] | undefined) ?? [];
      return values.map((v) => toDisplayString(resolveArg(v))).join("");
    }
    case "and":
      return ((args.values as unknown[] | undefined) ?? []).every((v) => Boolean(resolveArg(v)));
    case "or":
      return ((args.values as unknown[] | undefined) ?? []).some((v) => Boolean(resolveArg(v)));
    case "required": {
      const v = resolveArg(args.value);
      return v !== null && v !== undefined && v !== "";
    }
    case "regex": {
      const v = String(resolveArg(args.value) ?? "");
      const pattern = String(resolveArg(args.pattern) ?? "");
      try {
        return new RegExp(pattern).test(v);
      } catch {
        return false;
      }
    }
    case "email": {
      const v = String(resolveArg(args.value) ?? "");
      return /^[^\s@]+@[^\s@]+\.[^\s@]+$/.test(v);
    }
    case "length": {
      const v = String(resolveArg(args.value) ?? "");
      const min = (resolveArg(args.min) as number) ?? 0;
      const max = (resolveArg(args.max) as number) ?? Infinity;
      return v.length >= min && v.length <= max;
    }
    case "numeric": {
      const v = Number(resolveArg(args.value));
      const min = (resolveArg(args.min) as number) ?? -Infinity;
      const max = (resolveArg(args.max) as number) ?? Infinity;
      return Number.isFinite(v) && v >= min && v <= max;
    }
    default:
      return undefined;
  }
}

/**
 * formatString — interpolate `${expr}` expressions in a template.
 * Supports both data paths (`${/user/name}`) and function calls (`${formatDate(value: ${/date}, format: 'yyyy-MM-dd')}`).
 */
export function formatString(template: string, data: unknown, scope?: unknown): string {
  if (!template) return "";
  let result = "";
  let i = 0;
  while (i < template.length) {
    if (template[i] === "\\" && template[i + 1] === "$") {
      result += "$";
      i += 2;
      continue;
    }
    if (template[i] === "$" && template[i + 1] === "{") {
      // Find matching closing brace, handling nested ${...}
      let depth = 1;
      let j = i + 2;
      while (j < template.length && depth > 0) {
        if (template[j] === "$" && template[j + 1] === "{") {
          depth++;
          j += 2;
        } else if (template[j] === "}") {
          depth--;
          if (depth === 0) break;
          j++;
        } else {
          j++;
        }
      }
      const expr = template.slice(i + 2, j);
      result += toDisplayString(evaluateExpression(expr, data, scope));
      i = j + 1;
    } else {
      result += template[i];
      i++;
    }
  }
  return result;
}

/** Evaluate a `formatString` inner expression — either a JSON Pointer path or `funcName(arg:..., arg:...)`. */
function evaluateExpression(expr: string, data: unknown, scope?: unknown): unknown {
  const trimmed = expr.trim();
  // Function call: `name(arg1: val, arg2: val)` — detect by parenthesis
  const fnMatch = trimmed.match(/^([a-zA-Z_]\w*)\s*\(([\s\S]*)\)$/);
  if (fnMatch) {
    const [, name, argsStr] = fnMatch;
    const args = parseFnArgs(argsStr, data, scope);
    return runFunction({ call: name, args }, data, scope);
  }
  // Otherwise treat as a path
  if (trimmed.startsWith("/") || /^[a-zA-Z_]/.test(trimmed)) {
    return resolvePath(data, trimmed, scope);
  }
  return trimmed;
}

/** Parse `arg1: value, arg2: 'literal', arg3: ${path}` style argument lists. */
function parseFnArgs(s: string, data: unknown, scope?: unknown): Record<string, unknown> {
  const args: Record<string, unknown> = {};
  let i = 0;
  while (i < s.length) {
    // Skip whitespace and commas
    while (i < s.length && /[\s,]/.test(s[i])) i++;
    // Read key
    const keyStart = i;
    while (i < s.length && s[i] !== ":") i++;
    if (i >= s.length) break;
    const key = s.slice(keyStart, i).trim();
    i++; // skip colon
    while (i < s.length && /\s/.test(s[i])) i++;
    // Read value
    let value: string;
    if (s[i] === "'" || s[i] === '"') {
      const quote = s[i];
      const start = ++i;
      while (i < s.length && s[i] !== quote) i++;
      value = s.slice(start, i);
      i++;
      args[key] = value;
    } else if (s[i] === "$" && s[i + 1] === "{") {
      // Nested ${...}
      let depth = 1;
      let j = i + 2;
      while (j < s.length && depth > 0) {
        if (s[j] === "$" && s[j + 1] === "{") {
          depth++;
          j += 2;
        } else if (s[j] === "}") {
          depth--;
          if (depth === 0) break;
          j++;
        } else {
          j++;
        }
      }
      const expr = s.slice(i + 2, j);
      args[key] = evaluateExpression(expr, data, scope);
      i = j + 1;
    } else {
      const start = i;
      let depth = 0;
      while (i < s.length && (depth > 0 || s[i] !== ",")) {
        if (s[i] === "(") depth++;
        else if (s[i] === ")") depth--;
        i++;
      }
      const literal = s.slice(start, i).trim();
      // Try parsing as number / boolean
      if (literal === "true") args[key] = true;
      else if (literal === "false") args[key] = false;
      else if (!Number.isNaN(Number(literal)) && literal !== "") args[key] = Number(literal);
      else args[key] = literal;
    }
  }
  return args;
}
