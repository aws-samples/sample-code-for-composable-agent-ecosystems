# ─────────────────────────────────────────────────────────────────────────────
# Event wiring: S3 upload → file-classify, and terminal SFN state → gate-cleanup.
# ─────────────────────────────────────────────────────────────────────────────

# file-classify is triggered by object-created events on both buckets.
resource "aws_lambda_permission" "file_classify_ecosystems" {
  count         = contains(local.platform_lambda_dirs, "file-classify") ? 1 : 0
  statement_id  = "AllowS3InvokeEcosystems"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this["file-classify"].function_name
  principal     = "s3.amazonaws.com"
  source_arn    = var.ecosystems_bucket_arn
}

resource "aws_lambda_permission" "file_classify_runs" {
  count         = contains(local.platform_lambda_dirs, "file-classify") ? 1 : 0
  statement_id  = "AllowS3InvokeRuns"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this["file-classify"].function_name
  principal     = "s3.amazonaws.com"
  source_arn    = var.runs_bucket_arn
}

# S3 bucket notifications. Ecosystems: classify on ecosystems/ prefix. Runs: classify
# document suffixes (the leading session id varies, so we filter by suffix).
resource "aws_s3_bucket_notification" "ecosystems" {
  count  = contains(local.platform_lambda_dirs, "file-classify") ? 1 : 0
  bucket = var.ecosystems_bucket_name

  lambda_function {
    lambda_function_arn = aws_lambda_function.this["file-classify"].arn
    events              = ["s3:ObjectCreated:*"]
    filter_prefix       = "ecosystems/"
  }

  depends_on = [aws_lambda_permission.file_classify_ecosystems]
}

resource "aws_s3_bucket_notification" "runs" {
  count  = contains(local.platform_lambda_dirs, "file-classify") ? 1 : 0
  bucket = var.runs_bucket_name

  dynamic "lambda_function" {
    for_each = toset([".pdf", ".doc", ".docx", ".xls", ".xlsx"])
    content {
      lambda_function_arn = aws_lambda_function.this["file-classify"].arn
      events              = ["s3:ObjectCreated:*"]
      filter_suffix       = lambda_function.value
    }
  }

  depends_on = [aws_lambda_permission.file_classify_runs]
}

# gate-cleanup fires on terminal Step Functions execution status changes for this
# project/env's state machines.
resource "aws_cloudwatch_event_rule" "gate_cleanup" {
  count       = contains(local.platform_lambda_dirs, "gate-cleanup") ? 1 : 0
  name        = "${var.name_prefix}-gate-cleanup-on-terminal"
  description = "Fire gate-cleanup on terminal Step Functions execution status changes"

  event_pattern = jsonencode({
    source      = ["aws.states"]
    detail-type = ["Step Functions Execution Status Change"]
    detail = {
      status = ["ABORTED", "FAILED", "TIMED_OUT", "SUCCEEDED"]
      stateMachineArn = [{
        prefix = "arn:${var.partition}:states:${var.region}:${var.account_id}:stateMachine:${var.name_prefix}-"
      }]
    }
  })

  tags = var.tags
}

resource "aws_cloudwatch_event_target" "gate_cleanup" {
  count     = contains(local.platform_lambda_dirs, "gate-cleanup") ? 1 : 0
  rule      = aws_cloudwatch_event_rule.gate_cleanup[0].name
  target_id = "gate-cleanup"
  arn       = aws_lambda_function.this["gate-cleanup"].arn
}

resource "aws_lambda_permission" "gate_cleanup_events" {
  count         = contains(local.platform_lambda_dirs, "gate-cleanup") ? 1 : 0
  statement_id  = "AllowEventBridgeInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this["gate-cleanup"].function_name
  principal     = "events.amazonaws.com"
  source_arn    = aws_cloudwatch_event_rule.gate_cleanup[0].arn
}
