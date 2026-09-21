r"""Decision engine — see docs/spec-decision-engine.md.

Pipeline:  items[] --(dimensions)--> scored items --(rubric)--> labels --(rule)--> outcome
                                             \-------------- trace --------------/

Public API:
    validate_config(cfg)  -> list[str]        # compile-time errors ([] == valid)
    evaluate(cfg, items)  -> dict             # {items:[{id,label,trace,borderline}], outcome, outcome_trace}

The `when:` expressions are a tiny, SAFE DSL parsed with the stdlib `ast` module and
evaluated by a restricted walker (NO Python eval/exec). Supported:
    - names: declared dimensions, `label`, `status`, level/category/label/outcome identifiers
    - literals: numbers, strings, true/false
    - operators: == != < <= > >= (ordinals compare by declared order), and/or/not, in
    - aggregates (decision rule only): count(<per-item predicate>), any(...), all(...)
"""
from __future__ import annotations

import ast
from typing import Any

# ── platform-provided common dimensions (opt-in via {use: <name>}) ──────────────
COMMON_DIMENSIONS: dict[str, dict] = {
    # the memo's confidence-basis legend, ordered weakest -> strongest
    "verifiability": {"type": "ordinal", "levels": ["reasoning", "assertion", "verified", "grounded"]},
}

# built-in item lifecycle vocabulary (an item is `open` unless marked otherwise)
STATUS_VALUES = ["open", "closed", "resolved", "mitigated", "accepted"]

_AGGREGATES = {"count", "any", "all"}
_BOOLS = {"true": True, "false": False, "True": True, "False": False}


class DecisionConfigError(ValueError):
    """Raised for a structurally invalid `decision:` config (fail loud at compile time)."""


# ─────────────────────────────── config resolution ──────────────────────────────
def resolve_dimensions(cfg: dict) -> dict[str, dict]:
    """Return {name: meta} with `{use: X}` resolved against COMMON_DIMENSIONS."""
    out: dict[str, dict] = {}
    for name, meta in (cfg.get("dimensions") or {}).items():
        if isinstance(meta, dict) and "use" in meta:
            common = COMMON_DIMENSIONS.get(meta["use"])
            if not common:
                raise DecisionConfigError(f"dimension {name!r} uses unknown common dimension {meta['use']!r}")
            out[name] = dict(common)
        else:
            out[name] = dict(meta or {})
    return out


def _allowed_identifiers(cfg: dict, dims: dict[str, dict]) -> set[str]:
    """Every identifier that may legitimately appear in a `when:` expression."""
    ids: set[str] = set(dims) | {"label", "status"} | _AGGREGATES | set(STATUS_VALUES) | set(_BOOLS)
    for meta in dims.values():
        ids |= set(meta.get("levels", []) or [])
        ids |= set(meta.get("values", []) or [])
    ids |= set(cfg.get("labels", []) or [])
    ids |= set((cfg.get("decision", {}) or {}).get("outcomes", []) or [])
    return ids


# ──────────────────────────────── expression DSL ────────────────────────────────
class _Scope:
    """Resolves names for one evaluation context."""

    def __init__(self, dims: dict[str, dict], item: dict | None = None, item_scopes: list["_Scope"] | None = None):
        self.dims = dims
        self.item = item or {}
        self.item_scopes = item_scopes  # only set for the decision-rule (set) scope

    def dim_meta(self, name: str) -> dict | None:
        return self.dims.get(name)

    def dim_value(self, name: str):
        return (self.item.get("dimensions") or {}).get(name)

    def special(self, name: str):
        if name == "label":
            return self.item.get("label")
        if name == "status":
            return self.item.get("status", "open")
        return None


def _parse(expr: str) -> ast.AST:
    try:
        return ast.parse(expr, mode="eval").body
    except SyntaxError as e:
        raise DecisionConfigError(f"invalid expression {expr!r}: {e}")


def _name_or_str(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Constant):
        return str(node.value)
    raise DecisionConfigError("expected an identifier or literal on the right-hand side")


def _apply_cmp(op: ast.AST, a, b) -> bool:
    if isinstance(op, ast.Eq):
        return a == b
    if isinstance(op, ast.NotEq):
        return a != b
    if isinstance(op, ast.Lt):
        return a < b
    if isinstance(op, ast.LtE):
        return a <= b
    if isinstance(op, ast.Gt):
        return a > b
    if isinstance(op, ast.GtE):
        return a >= b
    raise DecisionConfigError(f"unsupported comparison operator {type(op).__name__}")


def _value(node: ast.AST, scope: _Scope):
    """Value of a scalar node (LHS or numeric/aggregate operand)."""
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Call):
        return _aggregate(node, scope)
    if isinstance(node, ast.Name):
        if scope.dim_meta(node.id) is not None:
            return scope.dim_value(node.id)
        if node.id in ("label", "status"):
            return scope.special(node.id)
        if node.id in _BOOLS:
            return _BOOLS[node.id]
        # bare identifier used as a value (level/category/label literal)
        return node.id
    raise DecisionConfigError(f"unsupported expression node {type(node).__name__}")


def _compare(node: ast.Compare, scope: _Scope) -> bool:
    if len(node.ops) != 1 or len(node.comparators) != 1:
        raise DecisionConfigError("chained comparisons are not supported")
    left, op, right = node.left, node.ops[0], node.comparators[0]

    # membership: `label in [a, b]`
    if isinstance(op, (ast.In, ast.NotIn)):
        if not isinstance(right, (ast.List, ast.Tuple)):
            raise DecisionConfigError("`in` requires a list on the right-hand side")
        lval = _value(left, scope)
        rlist = [_name_or_str(e) for e in right.elts]
        hit = lval in rlist
        return (not hit) if isinstance(op, ast.NotIn) else hit

    # ordinal-aware comparison when the LHS is an ordinal dimension
    if isinstance(left, ast.Name):
        meta = scope.dim_meta(left.id)
        if meta and meta.get("type") == "ordinal":
            levels = meta["levels"]
            lv = scope.dim_value(left.id)
            rv = _name_or_str(right)
            if lv not in levels:
                raise DecisionConfigError(f"{left.id}={lv!r} is not a declared level of {levels}")
            if rv not in levels:
                raise DecisionConfigError(f"{rv!r} is not a level of ordinal dimension {left.id!r}")
            return _apply_cmp(op, levels.index(lv), levels.index(rv))

    lval = _value(left, scope)
    # RHS: a bare Name is a string literal (category/label/level/status value)
    rval = _name_or_str(right) if isinstance(right, ast.Name) else _value(right, scope)
    return _apply_cmp(op, lval, rval)


def _aggregate(node: ast.Call, scope: _Scope):
    if not isinstance(node.func, ast.Name) or node.func.id not in _AGGREGATES:
        raise DecisionConfigError("only count()/any()/all() calls are allowed")
    if scope.item_scopes is None:
        raise DecisionConfigError(f"{node.func.id}() is only valid in a decision rule (over the item set)")
    if len(node.args) != 1:
        raise DecisionConfigError(f"{node.func.id}() takes exactly one predicate argument")
    pred = node.args[0]
    results = [_eval_bool(pred, s) for s in scope.item_scopes]
    if node.func.id == "count":
        return sum(1 for r in results if r)
    if node.func.id == "any":
        return any(results)
    return all(results)


def _eval_bool(node: ast.AST, scope: _Scope) -> bool:
    if isinstance(node, ast.BoolOp):
        vals = [_eval_bool(v, scope) for v in node.values]
        return all(vals) if isinstance(node.op, ast.And) else any(vals)
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.Not):
        return not _eval_bool(node.operand, scope)
    if isinstance(node, ast.Compare):
        return _compare(node, scope)
    if isinstance(node, ast.Call):
        return bool(_aggregate(node, scope))
    if isinstance(node, ast.Constant):
        return bool(node.value)
    if isinstance(node, ast.Name):
        # bare identifier in boolean context -> a boolean dimension's value
        meta = scope.dim_meta(node.id)
        if meta and meta.get("type") == "boolean":
            return bool(scope.dim_value(node.id))
        if node.id in _BOOLS:
            return _BOOLS[node.id]
        raise DecisionConfigError(f"identifier {node.id!r} is not usable as a boolean")
    raise DecisionConfigError(f"unsupported expression node {type(node).__name__}")


def _eval_when(expr: str, scope: _Scope) -> bool:
    return _eval_bool(_parse(expr), scope)


# ─────────────────────────────────── validation ─────────────────────────────────
def _collect_names(node: ast.AST) -> set[str]:
    return {n.id for n in ast.walk(node) if isinstance(n, ast.Name)}


def validate_config(cfg: dict) -> list[str]:
    """Return a list of human-readable errors ([] means valid). Used by save-ecosystem."""
    errs: list[str] = []
    if not isinstance(cfg, dict):
        return ["decision config must be a mapping"]
    try:
        dims = resolve_dimensions(cfg)
    except DecisionConfigError as e:
        return [str(e)]

    # dimensions
    for name, meta in dims.items():
        t = meta.get("type")
        if t not in ("ordinal", "boolean", "number", "category"):
            errs.append(f"dimension {name!r}: type must be ordinal|boolean|number|category (got {t!r})")
        if t == "ordinal" and not meta.get("levels"):
            errs.append(f"dimension {name!r}: ordinal requires non-empty `levels`")
        if t == "category" and not meta.get("values"):
            errs.append(f"dimension {name!r}: category requires non-empty `values`")

    labels = cfg.get("labels") or []
    if not labels:
        errs.append("`labels` must be a non-empty list")
    allowed = _allowed_identifiers(cfg, dims)

    def _check_expr(expr: str, where: str):
        try:
            tree = _parse(expr)
        except DecisionConfigError as e:
            errs.append(f"{where}: {e}")
            return
        unknown = _collect_names(tree) - allowed
        if unknown:
            errs.append(f"{where}: unknown identifier(s) {sorted(unknown)}")

    # rubric
    rubric = cfg.get("rubric") or []
    if not rubric:
        errs.append("`rubric` must be a non-empty list")
    saw_default = False
    for i, rule in enumerate(rubric):
        lbl = rule.get("label")
        if lbl not in labels:
            errs.append(f"rubric[{i}]: label {lbl!r} is not in labels {labels}")
        if rule.get("default"):
            saw_default = True
        elif "when" in rule:
            _check_expr(rule["when"], f"rubric[{i}].when")
        else:
            errs.append(f"rubric[{i}]: needs either `when` or `default: true`")
    if not saw_default:
        errs.append("rubric must include a `default: true` rule (else some items are unlabelled)")

    # decision
    decision = cfg.get("decision") or {}
    outcomes = decision.get("outcomes") or []
    if not outcomes:
        errs.append("`decision.outcomes` must be a non-empty list")
    rules = decision.get("rule") or []
    if not rules:
        errs.append("`decision.rule` must be a non-empty list")
    saw_out_default = False
    for i, rule in enumerate(rules):
        out = rule.get("outcome")
        if out not in outcomes:
            errs.append(f"decision.rule[{i}]: outcome {out!r} is not in outcomes {outcomes}")
        if rule.get("default"):
            saw_out_default = True
        elif "when" in rule:
            _check_expr(rule["when"], f"decision.rule[{i}].when")
        else:
            errs.append(f"decision.rule[{i}]: needs either `when` or `default: true`")
    if not saw_out_default:
        errs.append("decision.rule must include a `default: true` outcome")

    for lbl in decision.get("conditions_from", []) or []:
        if lbl not in labels:
            errs.append(f"decision.conditions_from: {lbl!r} is not a declared label")
    return errs


# ─────────────────────────────────── evaluation ─────────────────────────────────
def _label_for(cfg: dict, dims: dict, item: dict) -> tuple[str, str]:
    """Return (label, rule_id) — first matching rubric rule; else the default."""
    scope = _Scope(dims, item=item)
    for i, rule in enumerate(cfg.get("rubric") or []):
        if rule.get("default"):
            continue
        if _eval_when(rule["when"], scope):
            return rule["label"], f"rubric#{i}:{rule['label']}"
    for rule in cfg.get("rubric") or []:
        if rule.get("default"):
            return rule["label"], "rubric:default"
    raise DecisionConfigError("no rubric rule matched and no default is defined")


def _is_borderline(cfg: dict, dims: dict, item: dict, label: str) -> bool:
    """A label is borderline if nudging any single ordinal dimension by one level would
    change the label, or the item's `verifiability` is at/below `assertion`."""
    base = dict(item)
    for name, meta in dims.items():
        if meta.get("type") != "ordinal":
            continue
        levels = meta["levels"]
        cur = (item.get("dimensions") or {}).get(name)
        if cur not in levels:
            continue
        idx = levels.index(cur)
        for j in (idx - 1, idx + 1):
            if 0 <= j < len(levels):
                probe = {**base, "dimensions": {**base.get("dimensions", {}), name: levels[j]}}
                if _label_for(cfg, dims, probe)[0] != label:
                    return True
    v = (item.get("dimensions") or {}).get("verifiability")
    if v in ("reasoning", "assertion"):
        return True
    return False


def evaluate(cfg: dict, items: list[dict]) -> dict:
    """Compute per-item labels + the outcome, with a trace. Deterministic.

    items: [{id, title?, dimensions:{...}, status?, evidence?, justification?}]
    """
    errs = validate_config(cfg)
    if errs:
        raise DecisionConfigError("invalid decision config: " + "; ".join(errs))
    dims = resolve_dimensions(cfg)

    scored: list[dict] = []
    for it in items:
        label, rule_id = _label_for(cfg, dims, it)
        enriched = {**it, "label": label, "status": it.get("status", "open")}
        scored.append({
            "id": it.get("id"),
            "title": it.get("title"),
            "label": label,
            "status": enriched["status"],
            "rule": rule_id,
            "dimensions": it.get("dimensions", {}),
            "evidence": it.get("evidence", []),
            "justification": it.get("justification"),
            "borderline": _is_borderline(cfg, dims, it, label),
        })

    # decision over the labelled set
    item_scopes = [_Scope(dims, item=s) for s in scored]
    set_scope = _Scope(dims, item_scopes=item_scopes)
    decision = cfg.get("decision") or {}
    outcome, outcome_rule = None, None
    for i, rule in enumerate(decision.get("rule") or []):
        if rule.get("default"):
            outcome, outcome_rule = rule["outcome"], f"decision#{i}:default"
            break
        if _eval_when(rule["when"], set_scope):
            outcome, outcome_rule = rule["outcome"], f"decision#{i}:{rule['outcome']}"
            break

    conditions_from = set(decision.get("conditions_from", []) or [])
    conditions = [s for s in scored if s["label"] in conditions_from and s["status"] == "open"]

    return {
        "items": scored,
        "outcome": outcome,
        "outcome_trace": {
            "rule": outcome_rule,
            "label_counts": _counts(scored),
            "open_by_label": _counts([s for s in scored if s["status"] == "open"]),
        },
        "conditions": [{"id": c["id"], "label": c["label"], "title": c.get("title")} for c in conditions],
        "borderline": [s["id"] for s in scored if s["borderline"]],
    }


def _counts(items: list[dict]) -> dict[str, int]:
    out: dict[str, int] = {}
    for s in items:
        out[s["label"]] = out.get(s["label"], 0) + 1
    return out
