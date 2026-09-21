# Shared Dead Letter Queue for all Lambda functions. 14-day retention, KMS-managed
# (SSE-SQS) encryption — matches the original.
resource "aws_sqs_queue" "lambda_dlq" {
  name                      = "${var.name_prefix}-lambda-dlq"
  message_retention_seconds = 1209600 # 14 days
  sqs_managed_sse_enabled   = true
  tags                      = var.tags
}
