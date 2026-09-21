"""Shared model price table + cost estimation (spec-run-tuning §5.1).

INTERNAL COST ESTIMATE ONLY — not authoritative billing. For real charges consult
the AWS Billing console / Bedrock pricing. Prices are list prices per 1,000 tokens
and are easy to edit as models/pricing change. Unknown models cost 0 (so the
manifest degrades gracefully rather than guessing).

Packaged into each Lambda by scripts/deploy-lambdas.sh (copied like auth.py).
"""
from __future__ import annotations

# Per-1,000-token list prices (USD). Keyed by the resolved Bedrock model id and by
# the platform's short aliases. Keep in sync with agentcore MODEL_ALIASES.
# Source: public Anthropic-on-Bedrock list pricing; treat as editable estimates.
_PER_1K = {
    # Opus 4.x — premium tier
    "us.anthropic.claude-opus-4-7":                  {"in": 0.015, "out": 0.075},
    "opus":                                          {"in": 0.015, "out": 0.075},
    # Sonnet 4.5 — workhorse
    "us.anthropic.claude-sonnet-4-5-20250929-v1:0":  {"in": 0.003, "out": 0.015},
    "sonnet":                                        {"in": 0.003, "out": 0.015},
    # Haiku 4.5 — fast/cheap
    "us.anthropic.claude-haiku-4-5-20251001-v1:0":   {"in": 0.001, "out": 0.005},
    "haiku":                                         {"in": 0.001, "out": 0.005},
    # --- Non-Claude (Bedrock on-demand list prices, US East, per 1K tokens) ---
    # Sourced 2026-07-06. DeepSeek V3.2 confirmed $0.62/$1.85 per 1M (pricepertoken).
    # Keyed by both resolved model id and the agentcore MODEL_ALIASES short name.
    "us.amazon.nova-micro-v1:0":                     {"in": 0.000035, "out": 0.00014},
    "nova-micro":                                    {"in": 0.000035, "out": 0.00014},
    "us.amazon.nova-lite-v1:0":                      {"in": 0.00006, "out": 0.00024},
    "nova-lite":                                     {"in": 0.00006, "out": 0.00024},
    "us.amazon.nova-pro-v1:0":                       {"in": 0.0008, "out": 0.0032},
    "nova-pro":                                      {"in": 0.0008, "out": 0.0032},
    "us.meta.llama3-3-70b-instruct-v1:0":            {"in": 0.00072, "out": 0.00072},
    "llama-70b":                                     {"in": 0.00072, "out": 0.00072},
    "us.meta.llama4-maverick-17b-instruct-v1:0":     {"in": 0.00024, "out": 0.00097},
    "llama4-maverick":                               {"in": 0.00024, "out": 0.00097},
    "us.deepseek.r1-v1:0":                           {"in": 0.00135, "out": 0.0054},
    "deepseek-r1":                                   {"in": 0.00135, "out": 0.0054},
    "deepseek.v3.2":                                 {"in": 0.00062, "out": 0.00185},
    "deepseek-v3":                                   {"in": 0.00062, "out": 0.00185},
}


def price_for(model_id: str) -> dict | None:
    """Return {'in','out'} per-1k prices for a model id/alias, or None if unknown."""
    if not model_id:
        return None
    if model_id in _PER_1K:
        return _PER_1K[model_id]
    # Tolerate version-suffix drift: match by family prefix (e.g. a future
    # opus-4-8 still scores as opus pricing) before giving up.
    low = str(model_id).lower()
    for fam, alias in (("opus", "opus"), ("sonnet", "sonnet"), ("haiku", "haiku")):
        if fam in low:
            return _PER_1K[alias]
    return None


def estimate_cost(model_id: str, tokens_in: int = 0, tokens_out: int = 0) -> float:
    """Estimated USD cost for one model call. Unknown model or missing tokens → 0.0."""
    p = price_for(model_id)
    if not p:
        return 0.0
    return round((int(tokens_in or 0) / 1000.0) * p["in"]
                 + (int(tokens_out or 0) / 1000.0) * p["out"], 6)


def is_priced(model_id: str) -> bool:
    """Whether we have a price for this model (for surfacing 'cost is an estimate')."""
    return price_for(model_id) is not None
