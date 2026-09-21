# Step Functions execution role for the ingest/build/refresh state machines. Grants
# invoke on this module's ingest Lambdas and start/describe on the child state machines
# used in RUN_JOB (.sync) integrations (agent-ingest, risk-synthesis).
data "aws_iam_policy_document" "sfn_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["states.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "ingest_sfn" {
  name               = "${var.name_prefix}-ingest-sfn"
  assume_role_policy = data.aws_iam_policy_document.sfn_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy" "ingest_sfn" {
  name = "grants"
  role = aws_iam_role.ingest_sfn.id
  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect = "Allow"
        Action = ["lambda:InvokeFunction"]
        Resource = [
          aws_lambda_function.fn["catalog-ingest"].arn,
          aws_lambda_function.fn["catalog-ingest-generic"].arn,
        ]
      },
      {
        Effect   = "Allow"
        Action   = ["states:StartExecution", "states:DescribeExecution", "states:StopExecution"]
        Resource = ["arn:${var.partition}:states:${var.region}:${var.account_id}:stateMachine:${var.name_prefix}-*", "arn:${var.partition}:states:${var.region}:${var.account_id}:execution:${var.name_prefix}-*:*"]
      },
      # RUN_JOB (.sync) child executions require these on the events managed rule.
      {
        Effect   = "Allow"
        Action   = ["events:PutTargets", "events:PutRule", "events:DescribeRule"]
        Resource = ["arn:${var.partition}:events:${var.region}:${var.account_id}:rule/StepFunctionsGetEventsForStepFunctionsExecutionRule"]
      },
      # X-Ray tracing (CKV_AWS_284). X-Ray API actions do not support resource-level scoping.
      {
        Effect   = "Allow"
        Action   = ["xray:PutTraceSegments", "xray:PutTelemetryRecords", "xray:GetSamplingRules", "xray:GetSamplingTargets"]
        Resource = ["*"]
      },
      # Execution history logging to CloudWatch Logs (CKV_AWS_285). Step Functions logging
      # requires these log-delivery/log-group actions on "*" (service limitation).
      {
        Effect = "Allow"
        Action = [
          "logs:CreateLogDelivery", "logs:GetLogDelivery", "logs:UpdateLogDelivery", "logs:DeleteLogDelivery",
          "logs:ListLogDeliveries", "logs:PutResourcePolicy", "logs:DescribeResourcePolicies", "logs:DescribeLogGroups",
        ]
        Resource = ["*"]
      },
    ]
  })
}
