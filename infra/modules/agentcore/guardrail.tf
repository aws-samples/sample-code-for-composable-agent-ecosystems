# Bedrock Guardrail — contextual grounding for agent outputs against upstream + tool
# results. Thresholds are config-driven (default 0.7 for both filters).
resource "aws_bedrock_guardrail" "grounding" {
  name                      = "${var.name_prefix}-grounding"
  description               = "Contextual grounding for Tessera agent outputs against upstream + tool results"
  blocked_input_messaging   = "Input blocked by guardrail."
  blocked_outputs_messaging = "Output not grounded in source material."

  contextual_grounding_policy_config {
    filters_config {
      type      = "GROUNDING"
      threshold = var.grounding_threshold
    }
    filters_config {
      type      = "RELEVANCE"
      threshold = var.relevance_threshold
    }
  }

  tags = var.tags
}
