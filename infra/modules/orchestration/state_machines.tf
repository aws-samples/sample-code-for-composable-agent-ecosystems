# ─────────────────────────────────────────────────────────────────────────────
# Catalog state machines (declarative ingest engine).
#
# Per domain (declarative): a REFRESH state machine (ingest_all → [plan_synthesis] →
# mark_run) and a versioned BUILD state machine (new_version → ingest_all →
# [plan_synthesis] → validate_version). Plus one generic, domain-parameterised BUILD
# state machine driven entirely by the {domain} in its input. Synthesis fan-out is
# gated per domain by `synthesis` in the domains map.
#
# Domains are declared in `var`-free locals here (config: which domains to provision an
# ingest pipeline for, and whether each runs synthesis + on what schedule), mirroring
# the two DomainIngest instances in the original (cyber-security-risk with synthesis,
# schedule off; au-financial-advice, 30-day schedule, no synthesis).
# ─────────────────────────────────────────────────────────────────────────────

locals {
  # Config-driven domain ingest pipelines. schedule_days = 0 disables the schedule.
  # synthesis = true domains run the synthesis fan-out (plan_synthesis -> per-cluster Map
  # invoking the `<name_prefix>-<synthesis_method>` ecosystem state machine) in the build.
  ingest_domains = {
    "cyber-security-risk" = { synthesis = true, synthesis_method = "risk-synthesis", schedule_days = 0 }
    "au-financial-advice" = { synthesis = false, synthesis_method = "", schedule_days = 30 }
    # Persona pool for Quorum ecosystems (e.g. architecture-review-board). Deterministic
    # map ingest, no synthesis; provisioning it gives scrutineer its build/refresh SMs so
    # `make ingest` can populate + promote it like any other domain.
    "scrutineer" = { synthesis = false, synthesis_method = "", schedule_days = 0 }
  }

  generic_fn_arn = aws_lambda_function.fn["catalog-ingest-generic"].arn

  # Standard transient-error retry for Lambda-invoke tasks in the catalog SMs (mirrors the
  # original CDK definitions so a cold Lambda / throttle doesn't fail a long build).
  sfn_lambda_retry = [{
    ErrorEquals     = ["Lambda.ClientExecutionTimeoutException", "Lambda.ServiceException", "Lambda.AWSLambdaException", "Lambda.SdkClientException"]
    IntervalSeconds = 2
    MaxAttempts     = 6
    BackoffRate     = 2
  }]
}

# CloudWatch log groups for state-machine execution history (CKV_AWS_285).
resource "aws_cloudwatch_log_group" "sfn_refresh" {
  for_each          = local.ingest_domains
  name              = "/aws/vendedlogs/states/${var.name_prefix}-catalog-refresh-${each.key}"
  retention_in_days = max(var.log_retention_days, 365) # >= 1 year (CKV_AWS_338)
  kms_key_id        = var.logs_key_arn                 # CMK encryption (CKV_AWS_158)
  tags              = var.tags
}

resource "aws_cloudwatch_log_group" "sfn_build" {
  for_each          = local.ingest_domains
  name              = "/aws/vendedlogs/states/${var.name_prefix}-catalog-build-${each.key}"
  retention_in_days = max(var.log_retention_days, 365) # >= 1 year (CKV_AWS_338)
  kms_key_id        = var.logs_key_arn                 # CMK encryption (CKV_AWS_158)
  tags              = var.tags
}

resource "aws_cloudwatch_log_group" "sfn_generic_refresh" {
  name              = "/aws/vendedlogs/states/${var.name_prefix}-catalog-refresh"
  retention_in_days = max(var.log_retention_days, 365) # >= 1 year (CKV_AWS_338)
  kms_key_id        = var.logs_key_arn                 # CMK encryption (CKV_AWS_158)
  tags              = var.tags
}

resource "aws_cloudwatch_log_group" "sfn_generic_build" {
  name              = "/aws/vendedlogs/states/${var.name_prefix}-catalog-build"
  retention_in_days = max(var.log_retention_days, 365) # >= 1 year (CKV_AWS_338)
  kms_key_id        = var.logs_key_arn                 # CMK encryption (CKV_AWS_158)
  tags              = var.tags
}

# ── Per-domain REFRESH state machine ──────────────────────────────────────────
resource "aws_sfn_state_machine" "refresh" {
  for_each = local.ingest_domains
  name     = "${var.name_prefix}-catalog-refresh-${each.key}"
  role_arn = aws_iam_role.ingest_sfn.arn
  tags     = var.tags

  # X-Ray tracing (CKV_AWS_284) + execution history logging (CKV_AWS_285).
  tracing_configuration {
    enabled = true
  }
  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn_refresh[each.key].arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  definition = jsonencode({
    Comment = "Declarative refresh for ${each.key}"
    StartAt = "IngestAll"
    States = merge(
      {
        IngestAll = {
          Type       = "Task"
          Resource   = "arn:${var.partition}:states:::lambda:invoke"
          Parameters = { FunctionName = local.generic_fn_arn, Payload = { action = "ingest_all", domain = each.key } }
          ResultPath = null
          Next       = each.value.synthesis ? "PlanSynthesis" : "MarkRun"
        }
      },
      each.value.synthesis ? {
        PlanSynthesis = {
          Type       = "Task"
          Resource   = "arn:${var.partition}:states:::lambda:invoke"
          Parameters = { FunctionName = local.generic_fn_arn, Payload = { action = "plan_synthesis", domain = each.key } }
          ResultPath = "$.plan"
          Next       = "MarkRun"
        }
      } : {},
      {
        MarkRun = {
          Type       = "Task"
          Resource   = "arn:${var.partition}:states:::lambda:invoke"
          Parameters = { FunctionName = local.generic_fn_arn, Payload = { action = "mark_run", domain = each.key, state = "done" } }
          End        = true
        }
      },
    )
  })
}

# ── Per-domain versioned BUILD state machine ─────────────────────────────────
# new_version -> ingest_all -> [plan_synthesis -> Map(per cluster: get_mission ->
# synthesis ecosystem SM, .sync)] -> validate_version. The synthesis fan-out is included
# only for synthesis-enabled domains; it invokes the `<name_prefix>-<synthesis_method>`
# ecosystem state machine (created by ecosystem seeding) once per planned cluster, which
# is what produces the synthesized types (e.g. `risk`). Without this stage a build yields
# a coverage-incomplete version (no risks) — the bug this restores from the CDK original.
resource "aws_sfn_state_machine" "build" {
  for_each = local.ingest_domains
  name     = "${var.name_prefix}-catalog-build-${each.key}"
  role_arn = aws_iam_role.ingest_sfn.arn
  tags     = var.tags

  # X-Ray tracing (CKV_AWS_284) + execution history logging (CKV_AWS_285).
  tracing_configuration {
    enabled = true
  }
  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn_build[each.key].arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  definition = jsonencode({
    Comment        = "Versioned blue/green build for ${each.key}"
    TimeoutSeconds = 21600
    StartAt        = "NewVersion"
    States = merge(
      {
        NewVersion = {
          Type       = "Task"
          Resource   = "arn:${var.partition}:states:::lambda:invoke"
          Parameters = { FunctionName = local.generic_fn_arn, Payload = { action = "new_version", domain = each.key } }
          ResultPath = "$.ver"
          Retry      = local.sfn_lambda_retry
          Next       = "IngestVersion"
        }
        IngestVersion = {
          Type     = "Task"
          Resource = "arn:${var.partition}:states:::lambda:invoke"
          Parameters = {
            FunctionName = local.generic_fn_arn
            Payload      = { action = "ingest_all", domain = each.key, "version.$" = "$.ver.Payload.version" }
          }
          ResultPath = null
          Retry      = local.sfn_lambda_retry
          Next       = each.value.synthesis ? "PlanVersion" : "ValidateVersion"
        }
      },
      merge([for _ in(each.value.synthesis ? [1] : []) : {
        PlanVersion = {
          Type     = "Task"
          Resource = "arn:${var.partition}:states:::lambda:invoke"
          Parameters = {
            FunctionName = local.generic_fn_arn
            Payload      = { action = "plan_synthesis", domain = each.key, "version.$" = "$.ver.Payload.version" }
          }
          ResultPath = "$.plan"
          Retry      = local.sfn_lambda_retry
          Next       = "MapClustersV"
        }
        MapClustersV = {
          Type           = "Map"
          ItemsPath      = "$.plan.Payload.to_run"
          ItemSelector   = { "cluster_id.$" = "$$.Map.Item.Value.cluster_id", domain = each.key }
          MaxConcurrency = 5
          ResultPath     = null
          Next           = "ValidateVersion"
          Iterator = {
            StartAt = "GetMissionV"
            States = {
              GetMissionV = {
                Type     = "Task"
                Resource = "arn:${var.partition}:states:::lambda:invoke"
                Parameters = {
                  FunctionName = local.generic_fn_arn
                  Payload      = { action = "get_mission", domain = each.key, "cluster_id.$" = "$.cluster_id" }
                }
                ResultPath = "$.m"
                Retry      = local.sfn_lambda_retry
                Next       = "SynthExecV"
              }
              SynthExecV = {
                Type     = "Task"
                Resource = "arn:${var.partition}:states:::states:startExecution.sync:2"
                Parameters = {
                  StateMachineArn = "arn:${var.partition}:states:${var.region}:${var.account_id}:stateMachine:${var.name_prefix}-${each.value.synthesis_method}"
                  Input = {
                    "mission.$"        = "$.m.Payload.mission"
                    "session_id.$"     = "$.cluster_id"
                    user_id            = "system-catalog-ingest"
                    dry_run            = false
                    run_config         = {}
                    dependency_context = ""
                    "input_context.$"  = "$.m.Payload.mission"
                    results            = {}
                  }
                }
                ResultPath = null
                End        = true
              }
            }
          }
        }
      }]...),
      {
        ValidateVersion = {
          Type     = "Task"
          Resource = "arn:${var.partition}:states:::lambda:invoke"
          Parameters = {
            FunctionName = local.generic_fn_arn
            Payload      = { action = "validate_version", domain = each.key, "version.$" = "$.ver.Payload.version" }
          }
          ResultPath = "$.validate"
          Retry      = local.sfn_lambda_retry
          End        = true
        }
      },
    )
  })
}

# ── Generic (domain-parameterised) REFRESH state machine ─────────────────────
# catalog-api's "Refresh from sources" (POST /catalog/refresh) starts ONE refresh SM and
# passes {domain, ...} in the input — it is NOT per-domain. The original CDK created a
# single generic `catalog-refresh` SM and wired its ARN into catalog-api
# (CATALOG_REFRESH_STATE_MACHINE_ARN). The per-domain refresh SMs above serve the scheduled
# eco-lite path; the UI-driven refresh needs this generic one, which reads $.domain so it
# works for EVERY catalog, not just the two scheduled domains.
resource "aws_sfn_state_machine" "generic_refresh" {
  name     = "${var.name_prefix}-catalog-refresh"
  role_arn = aws_iam_role.ingest_sfn.arn
  tags     = var.tags

  # X-Ray tracing (CKV_AWS_284) + execution history logging (CKV_AWS_285).
  tracing_configuration {
    enabled = true
  }
  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn_generic_refresh.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  definition = jsonencode({
    Comment = "Generic domain-parameterised refresh (domain from input)"
    StartAt = "GRIngest"
    States = {
      GRIngest = {
        Type       = "Task"
        Resource   = "arn:${var.partition}:states:::lambda:invoke"
        Parameters = { FunctionName = local.generic_fn_arn, Payload = { action = "ingest_all", "domain.$" = "$.domain" } }
        ResultPath = null
        Next       = "GRPlanSynthesis"
      }
      # plan_synthesis is a no-op for domains without synthesis (mirrors the generic build,
      # which likewise does not gate synthesis per domain).
      GRPlanSynthesis = {
        Type       = "Task"
        Resource   = "arn:${var.partition}:states:::lambda:invoke"
        Parameters = { FunctionName = local.generic_fn_arn, Payload = { action = "plan_synthesis", "domain.$" = "$.domain" } }
        ResultPath = "$.plan"
        Next       = "GRMarkRun"
      }
      GRMarkRun = {
        Type       = "Task"
        Resource   = "arn:${var.partition}:states:::lambda:invoke"
        Parameters = { FunctionName = local.generic_fn_arn, Payload = { action = "mark_run", "domain.$" = "$.domain", state = "done" } }
        End        = true
      }
    }
  })
}

# ── Generic (domain-parameterised) BUILD state machine ───────────────────────
# One state machine, domain comes from the execution input — lets a UI-created domain
# run its declarative build with no per-domain infrastructure.
resource "aws_sfn_state_machine" "generic_build" {
  name     = "${var.name_prefix}-catalog-build"
  role_arn = aws_iam_role.ingest_sfn.arn
  tags     = var.tags

  # X-Ray tracing (CKV_AWS_284) + execution history logging (CKV_AWS_285).
  tracing_configuration {
    enabled = true
  }
  logging_configuration {
    log_destination        = "${aws_cloudwatch_log_group.sfn_generic_build.arn}:*"
    include_execution_data = true
    level                  = "ALL"
  }

  definition = jsonencode({
    Comment = "Generic domain-parameterised build (domain from input)"
    StartAt = "GNewVersion"
    States = {
      GNewVersion = {
        Type       = "Task"
        Resource   = "arn:${var.partition}:states:::lambda:invoke"
        Parameters = { FunctionName = local.generic_fn_arn, "Payload" = { action = "new_version", "domain.$" = "$.domain" } }
        ResultPath = "$.ver"
        Next       = "GIngest"
      }
      GIngest = {
        Type     = "Task"
        Resource = "arn:${var.partition}:states:::lambda:invoke"
        Parameters = {
          FunctionName = local.generic_fn_arn
          Payload      = { action = "ingest_all", "domain.$" = "$.domain", "version.$" = "$.ver.Payload.version" }
        }
        ResultPath = null
        Next       = "GValidate"
      }
      GValidate = {
        Type     = "Task"
        Resource = "arn:${var.partition}:states:::lambda:invoke"
        Parameters = {
          FunctionName = local.generic_fn_arn
          Payload      = { action = "validate_version", "domain.$" = "$.domain", "version.$" = "$.ver.Payload.version" }
        }
        ResultPath = "$.validate"
        End        = true
      }
    }
  })
}
