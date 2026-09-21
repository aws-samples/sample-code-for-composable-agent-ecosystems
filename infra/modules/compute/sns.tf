# ─────────────────────────────────────────────────────────────────────────────
# SNS topics
# ─────────────────────────────────────────────────────────────────────────────

# Human-in-the-loop Attention topic — published to when a conversation/run needs a
# human (gate fired, builder question, etc). Operators subscribe out of band.
resource "aws_sns_topic" "attention" {
  name              = "${var.name_prefix}-attention"
  kms_master_key_id = "alias/aws/sns"
  tags              = var.tags
}

resource "aws_sns_topic_subscription" "attention_email" {
  count     = var.attention_email != "" ? 1 : 0
  topic_arn = aws_sns_topic.attention.arn
  protocol  = "email"
  endpoint  = var.attention_email
}

# Signup-approval topic — the post-confirmation trigger publishes here when a new user
# signs up and is auto-disabled pending approval. Recipients come from a variable
# (replaces the hardcoded addresses in the original).
resource "aws_sns_topic" "signup_approvals" {
  name              = "${var.name_prefix}-signup-approvals"
  display_name      = "Tessera Signups"
  kms_master_key_id = "alias/aws/sns"
  tags              = var.tags
}

resource "aws_sns_topic_subscription" "signup_email" {
  for_each  = toset(var.admin_notify_emails)
  topic_arn = aws_sns_topic.signup_approvals.arn
  protocol  = "email"
  endpoint  = each.value
}
