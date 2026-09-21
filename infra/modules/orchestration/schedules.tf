# ─────────────────────────────────────────────────────────────────────────────
# Schedules + event wiring.
# ─────────────────────────────────────────────────────────────────────────────

# Weekly legacy catalog refresh (mitre-attack source) → catalog-ingest.
resource "aws_cloudwatch_event_rule" "catalog_ingest_weekly" {
  name                = "${var.name_prefix}-catalog-ingest-schedule"
  schedule_expression = "rate(7 days)"
  tags                = var.tags
}

resource "aws_cloudwatch_event_target" "catalog_ingest_weekly" {
  rule      = aws_cloudwatch_event_rule.catalog_ingest_weekly.name
  target_id = "catalog-ingest"
  arn       = aws_lambda_function.fn["catalog-ingest"].arn
  input     = jsonencode({ source = "mitre-attack", limit = 0 })
}

resource "aws_lambda_permission" "catalog_ingest_weekly" {
  statement_id  = "AllowScheduleInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["catalog-ingest"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.catalog_ingest_weekly.arn
}

# Per-domain scheduled declarative ingest (only for domains with schedule_days > 0).
resource "aws_cloudwatch_event_rule" "domain_ingest" {
  for_each            = { for d, cfg in local.ingest_domains : d => cfg if cfg.schedule_days > 0 }
  name                = "${var.name_prefix}-catalog-ingest-${each.key}-schedule"
  schedule_expression = "rate(${each.value.schedule_days} days)"
  tags                = var.tags
}

resource "aws_cloudwatch_event_target" "domain_ingest" {
  for_each  = { for d, cfg in local.ingest_domains : d => cfg if cfg.schedule_days > 0 }
  rule      = aws_cloudwatch_event_rule.domain_ingest[each.key].name
  target_id = "ingest"
  arn       = aws_lambda_function.fn["catalog-ingest-generic"].arn
  input     = jsonencode({ action = "ingest_all", domain = each.key })
}

resource "aws_lambda_permission" "domain_ingest" {
  for_each      = { for d, cfg in local.ingest_domains : d => cfg if cfg.schedule_days > 0 }
  statement_id  = "AllowScheduleInvoke-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["catalog-ingest-generic"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.domain_ingest[each.key].arn
}

# Re-grounding bus: ChangeSignal → catalog-regrounding.
resource "aws_cloudwatch_event_rule" "regrounding" {
  name           = "${var.name_prefix}-regrounding-change-signal"
  event_bus_name = var.regrounding_bus_name
  event_pattern  = jsonencode({ detail-type = ["ChangeSignal"] })
  tags           = var.tags
}

resource "aws_cloudwatch_event_target" "regrounding" {
  rule           = aws_cloudwatch_event_rule.regrounding.name
  event_bus_name = var.regrounding_bus_name
  target_id      = "catalog-regrounding"
  arn            = aws_lambda_function.fn["catalog-regrounding"].arn
}

resource "aws_lambda_permission" "regrounding" {
  statement_id  = "AllowRegroundingInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["catalog-regrounding"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.regrounding.arn
}
