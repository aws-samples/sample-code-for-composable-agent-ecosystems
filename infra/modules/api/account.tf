# ─────────────────────────────────────────────────────────────────────────────
# Account-level API Gateway CloudWatch Logs role.
#
# API Gateway refuses to enable stage ACCESS LOGGING unless the account has a
# CloudWatch Logs role ARN configured (a fresh account has none, which otherwise fails
# stage creation with "CloudWatch Logs role ARN must be set in account settings"). This
# is an ACCOUNT-WIDE setting (one per account/region), set here once. The REST and
# WebSocket stages depend on it so it is applied first.
# ─────────────────────────────────────────────────────────────────────────────

data "aws_iam_policy_document" "apigw_cloudwatch_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["apigateway.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "apigw_cloudwatch" {
  name               = "${var.name_prefix}-apigw-cloudwatch"
  assume_role_policy = data.aws_iam_policy_document.apigw_cloudwatch_assume.json
  tags               = var.tags
}

resource "aws_iam_role_policy_attachment" "apigw_cloudwatch" {
  role       = aws_iam_role.apigw_cloudwatch.name
  policy_arn = "arn:${var.partition}:iam::aws:policy/service-role/AmazonAPIGatewayPushToCloudWatchLogs"
}

# Account-wide setting. NOTE: this affects the whole account/region, not just Tessera.
resource "aws_api_gateway_account" "this" {
  cloudwatch_role_arn = aws_iam_role.apigw_cloudwatch.arn

  depends_on = [aws_iam_role_policy_attachment.apigw_cloudwatch]
}
