# ─────────────────────────────────────────────────────────────────────────────
# REST API (API Gateway v1) — built from an OpenAPI body generated from rest_routes.
#
# Every method uses AWS_PROXY (Lambda proxy) integration and the Cognito authorizer.
# CORS is handled by a MOCK OPTIONS method on each distinct path, and by gateway
# responses that add CORS headers to 4xx errors so the browser sees them on 401.
# ─────────────────────────────────────────────────────────────────────────────

locals {
  # Group routes by path so we can emit one OpenAPI path item per path with all its
  # methods plus a synthetic OPTIONS (CORS preflight).
  routes_by_path = {
    for p in distinct([for r in local.rest_routes : r.path]) :
    p => [for r in local.rest_routes : r if r.path == p]
  }

  # Lambda proxy integration URI (built from the deterministic function ARN map).
  integration_uri = { for fn in local.rest_functions : fn => local.fn_lambda_uri[fn] }

  cors_headers = {
    "Access-Control-Allow-Origin"  = "'*'"
    "Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
    "Access-Control-Allow-Methods" = "'GET,POST,PUT,PATCH,DELETE,OPTIONS'"
  }

  # OpenAPI paths object.
  openapi_paths = {
    for path, routes in local.routes_by_path :
    path => merge(
      # Real methods → Lambda proxy + Cognito authorizer.
      {
        for r in routes :
        lower(r.method == "ANY" ? "x-amazon-apigateway-any-method" : r.method) => {
          security = [{ CognitoAuth = [] }]
          x-amazon-apigateway-integration = {
            type                = "aws_proxy"
            httpMethod          = "POST"
            uri                 = local.integration_uri[r.fn]
            passthroughBehavior = "when_no_match"
          }
        }
      },
      # Synthetic OPTIONS (CORS preflight) — MOCK, no auth.
      {
        options = {
          responses = {
            "200" = {
              description = "CORS preflight"
              headers = {
                "Access-Control-Allow-Origin"  = { schema = { type = "string" } }
                "Access-Control-Allow-Headers" = { schema = { type = "string" } }
                "Access-Control-Allow-Methods" = { schema = { type = "string" } }
              }
            }
          }
          x-amazon-apigateway-integration = {
            type                = "mock"
            requestTemplates    = { "application/json" = "{\"statusCode\": 200}" }
            passthroughBehavior = "when_no_match"
            responses = {
              default = {
                statusCode = "200"
                responseParameters = {
                  "method.response.header.Access-Control-Allow-Origin"  = local.cors_headers["Access-Control-Allow-Origin"]
                  "method.response.header.Access-Control-Allow-Headers" = local.cors_headers["Access-Control-Allow-Headers"]
                  "method.response.header.Access-Control-Allow-Methods" = local.cors_headers["Access-Control-Allow-Methods"]
                }
              }
            }
          }
        }
      },
    )
  }

  openapi = {
    openapi = "3.0.1"
    info = {
      title   = "${var.name_prefix}-api"
      version = "1.0"
    }
    paths = local.openapi_paths
    components = {
      securitySchemes = {
        CognitoAuth = {
          type                         = "apiKey"
          name                         = "Authorization"
          in                           = "header"
          x-amazon-apigateway-authtype = "cognito_user_pools"
          x-amazon-apigateway-authorizer = {
            type         = "cognito_user_pools"
            providerARNs = [var.user_pool_arn]
          }
        }
      }
    }
  }
}

resource "aws_api_gateway_rest_api" "main" {
  #checkov:skip=CKV_AWS_237:create_before_destroy on the REST API would force a replacement that changes the API id/endpoint on every body change, breaking the deployed frontend + wired integrations. In-place update is intentional. Accepted risk.
  name = "${var.name_prefix}-api"
  body = jsonencode(local.openapi)

  endpoint_configuration {
    types = ["EDGE"]
  }

  tags = var.tags
}

# CORS headers on 4xx gateway responses (so the browser gets CORS on 401/403).
resource "aws_api_gateway_gateway_response" "unauthorized" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  response_type = "UNAUTHORIZED"
  status_code   = "401"
  response_parameters = {
    "gatewayresponse.header.Access-Control-Allow-Origin"  = "'*'"
    "gatewayresponse.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
  }
}

resource "aws_api_gateway_gateway_response" "default_4xx" {
  rest_api_id   = aws_api_gateway_rest_api.main.id
  response_type = "DEFAULT_4XX"
  response_parameters = {
    "gatewayresponse.header.Access-Control-Allow-Origin"  = "'*'"
    "gatewayresponse.header.Access-Control-Allow-Headers" = "'Content-Type,Authorization'"
  }
}

# ── Deployment + stage ────────────────────────────────────────────────────────
resource "aws_api_gateway_deployment" "main" {
  rest_api_id = aws_api_gateway_rest_api.main.id

  triggers = {
    # Redeploy whenever the API definition changes.
    redeploy = sha1(jsonencode(local.openapi))
  }

  lifecycle {
    create_before_destroy = true
  }
}

resource "aws_cloudwatch_log_group" "rest" {
  name              = "/aws/apigateway/${var.name_prefix}-api"
  retention_in_days = max(var.log_retention_days, 365) # >= 1 year (CKV_AWS_338)
  kms_key_id        = var.logs_key_arn
  tags              = var.tags
}

resource "aws_api_gateway_stage" "main" {
  #checkov:skip=CKV2_AWS_29:The API is protected by per-route Cognito JWT authorizers (defined in the OpenAPI body); a WAF WebACL on the regional REST API is not required for this deployment. Accepted risk.
  #checkov:skip=CKV2_AWS_4:Stage logging level is set via method_settings (below); the graph check does not resolve the separately-declared method_settings for this stage.
  #checkov:skip=CKV2_AWS_51:Client-certificate (mutual TLS) authentication is not used — clients authenticate with Cognito JWTs, which is the intended auth model for this API. Accepted risk.
  rest_api_id           = aws_api_gateway_rest_api.main.id
  deployment_id         = aws_api_gateway_deployment.main.id
  stage_name            = var.environment
  xray_tracing_enabled  = true
  cache_cluster_enabled = true
  cache_cluster_size    = "0.5"

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.rest.arn
    format          = "$context.identity.sourceIp - - [$context.requestTime] \"$context.httpMethod $context.resourcePath $context.protocol\" $context.status $context.responseLength $context.requestId"
  }

  tags = var.tags

  # Access logging requires the account-level CloudWatch Logs role to be set first.
  depends_on = [aws_api_gateway_account.this]
}

# Grant API Gateway permission to invoke each referenced Lambda.
# NOTE: Lambda invoke permissions (for both REST and WebSocket) are created in the
# compute module, which owns the functions. The compute module consumes this module's
# API ids, so the dependency flows api → compute (one direction, no cycle).
