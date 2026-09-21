# ─────────────────────────────────────────────────────────────────────────────
# EventBridge event buses
# ─────────────────────────────────────────────────────────────────────────────

# Signal bus — general platform signals.
resource "aws_cloudwatch_event_bus" "signals" {
  name = "${var.name_prefix}-signals"
  tags = var.tags
}

# Re-grounding bus — sources emit `ChangeSignal`; a router+worker (in the
# orchestration/compute modules) re-grounds subscribing catalogs. The rule + target
# that consume this bus are defined alongside the regrounding Lambda.
resource "aws_cloudwatch_event_bus" "regrounding" {
  name = "${var.name_prefix}-regrounding"
  tags = var.tags
}
