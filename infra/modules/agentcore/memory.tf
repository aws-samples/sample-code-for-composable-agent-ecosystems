# ─────────────────────────────────────────────────────────────────────────────
# AgentCore Memory (AWSCC) — a single shared, namespace-isolated Memory resource.
#
# Two strategies:
#   • semantic — model-extracted findings indexed for similarity search.
#   • custom (self-managed) — durable catalog dossiers written directly via
#     BatchCreateMemoryRecords. Requires an invocation config (SNS topic + S3 payload
#     bucket); the trigger pipeline is not used (ingestion is 100% direct), so no
#     trigger conditions are set.
# ─────────────────────────────────────────────────────────────────────────────

resource "awscc_bedrockagentcore_memory" "main" {
  name                      = "${replace(var.name_prefix, "-", "_")}_memory"
  description               = "Tessera shared memory — namespace-isolated cross-ecosystem store"
  event_expiry_duration     = var.event_expiry_days
  memory_execution_role_arn = aws_iam_role.memory_execution.arn

  # NOTE: element order must match the order the AgentCore API returns strategies
  # (custom first, then semantic). AWSCC matches list-of-object elements positionally,
  # so a mismatched order makes Terraform layer a second strategy key onto each element,
  # producing "MemoryStrategies/N: maximum size: [1], found: [2]" on update.
  memory_strategies = [
    {
      custom_memory_strategy = {
        name        = "tessera_catalog"
        description = "Self-managed: durable entity dossiers ingested directly as memory records"
        configuration = {
          self_managed_configuration = {
            invocation_configuration = {
              topic_arn                    = aws_sns_topic.memory_jobs.arn
              payload_delivery_bucket_name = var.memory_payloads_bucket_name
            }
          }
        }
      }
    },
    {
      semantic_memory_strategy = {
        name                = "tessera_semantic"
        description         = "Semantic similarity search over agent-emitted findings and tool results"
        namespace_templates = ["/tessera/{actorId}/{sessionId}"]
      }
    },
  ]

  tags = var.tags

  lifecycle {
    # RETAIN semantics: never let a stack change clobber stored events.
    prevent_destroy = true
  }

  depends_on = [
    aws_iam_role_policy.memory_execution,
    aws_sns_topic.memory_jobs,
  ]
}
