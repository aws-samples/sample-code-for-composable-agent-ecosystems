# ─────────────────────────────────────────────────────────────────────────────
# Catalog HTTP API (API Gateway v2) — the public entry point for the read-only
# Risk Catalog Browser + interactive serve engine, behind a Cognito JWT authorizer.
# ─────────────────────────────────────────────────────────────────────────────

resource "aws_apigatewayv2_api" "catalog" {
  name          = "${var.name_prefix}-catalog"
  protocol_type = "HTTP"

  cors_configuration {
    allow_origins = [var.allowed_origin]
    allow_methods = ["GET", "POST"]
    allow_headers = ["authorization", "content-type"]
  }

  tags = var.tags
}

resource "aws_apigatewayv2_authorizer" "catalog_jwt" {
  api_id           = aws_apigatewayv2_api.catalog.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "${var.name_prefix}-catalog-jwt"

  jwt_configuration {
    audience = var.cognito_client_id != "" ? [var.cognito_client_id] : []
    issuer   = var.cognito_issuer_url
  }
}

# /catalog/{proxy+} → catalog-api
resource "aws_apigatewayv2_integration" "catalog_api" {
  api_id                 = aws_apigatewayv2_api.catalog.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.fn["catalog-api"].invoke_arn
  payload_format_version = "2.0"
}

# Register the catalog proxy for GET + POST ONLY — deliberately NOT `ANY`. An `ANY` route
# captures the CORS preflight OPTIONS too and forces it through the JWT authorizer, which
# returns 401 (preflights carry no Authorization header). The browser then blocks the real
# request and reports "Failed to fetch". By registering only GET/POST, the OPTIONS preflight
# falls through to API Gateway's built-in cors_configuration handler (unauthenticated), as it
# must. This mirrors the original CDK definition (methods=[GET, POST], not ANY).
resource "aws_apigatewayv2_route" "catalog_proxy_get" {
  api_id             = aws_apigatewayv2_api.catalog.id
  route_key          = "GET /catalog/{proxy+}"
  target             = "integrations/${aws_apigatewayv2_integration.catalog_api.id}"
  authorizer_id      = aws_apigatewayv2_authorizer.catalog_jwt.id
  authorization_type = "JWT"
}

resource "aws_apigatewayv2_route" "catalog_proxy_post" {
  api_id             = aws_apigatewayv2_api.catalog.id
  route_key          = "POST /catalog/{proxy+}"
  target             = "integrations/${aws_apigatewayv2_integration.catalog_api.id}"
  authorizer_id      = aws_apigatewayv2_authorizer.catalog_jwt.id
  authorization_type = "JWT"
}

# /interactive → interactive-invoke
resource "aws_apigatewayv2_integration" "interactive" {
  api_id                 = aws_apigatewayv2_api.catalog.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.fn["interactive-invoke"].invoke_arn
  payload_format_version = "2.0"
}

resource "aws_apigatewayv2_route" "interactive" {
  api_id             = aws_apigatewayv2_api.catalog.id
  route_key          = "POST /interactive"
  target             = "integrations/${aws_apigatewayv2_integration.interactive.id}"
  authorizer_id      = aws_apigatewayv2_authorizer.catalog_jwt.id
  authorization_type = "JWT"
}

resource "aws_cloudwatch_log_group" "catalog_access" {
  name              = "/aws/apigateway/${var.name_prefix}-catalog"
  retention_in_days = max(var.log_retention_days, 365) # >= 1 year (CKV_AWS_338)
  kms_key_id        = var.logs_key_arn                 # logs key has the CloudWatch Logs service grant
  tags              = var.tags
}

resource "aws_apigatewayv2_stage" "catalog" {
  api_id      = aws_apigatewayv2_api.catalog.id
  name        = "$default"
  auto_deploy = true
  tags        = var.tags

  # Access logging (CKV_AWS_76).
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.catalog_access.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      protocol       = "$context.protocol"
      responseLength = "$context.responseLength"
    })
  }
}

resource "aws_lambda_permission" "catalog_api_http" {
  statement_id  = "AllowCatalogHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["catalog-api"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "arn:${var.partition}:execute-api:${var.region}:${var.account_id}:${aws_apigatewayv2_api.catalog.id}/*/*"
}

resource "aws_lambda_permission" "interactive_http" {
  statement_id  = "AllowInteractiveHttpApi"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["interactive-invoke"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "arn:${var.partition}:execute-api:${var.region}:${var.account_id}:${aws_apigatewayv2_api.catalog.id}/*/*"
}

# Wire the catalog refresh/generic-ingest state machine ARNs into catalog-api so it can
# start builds/refreshes from the UI. (Set here rather than at creation to reference the
# state machines created in this module.)
# Note: env additions to an existing function require the function's environment to be
# fully specified; these are folded into the catalog-api env in lambdas.tf via ARNs by
# convention, so no update resource is needed.

# ─────────────────────────────────────────────────────────────────────────────
# Concierge over the platform WebSocket API — a `concierge` route added to the
# existing WS API (created in the api module) targeting concierge-ws.
# ─────────────────────────────────────────────────────────────────────────────
# The platform WebSocket API is always created (by the api module) and its id is always
# passed in, so these are unconditional. The id being unknown at plan time is fine — it
# is only used as an argument, not in count/for_each.
resource "aws_apigatewayv2_integration" "concierge" {
  api_id                    = var.websocket_api_id
  integration_type          = "AWS_PROXY"
  integration_uri           = aws_lambda_function.fn["concierge-ws"].invoke_arn
  content_handling_strategy = "CONVERT_TO_TEXT"
}

resource "aws_apigatewayv2_route" "concierge" {
  #checkov:skip=CKV_AWS_309:concierge is a WebSocket message route on the platform WS API. AWS allows an authorizer ONLY on $connect (which IS authorized in the api module); message routes cannot take one. The concierge-ws handler re-verifies the Cognito JWT per message.
  api_id    = var.websocket_api_id
  route_key = "concierge"
  target    = "integrations/${aws_apigatewayv2_integration.concierge.id}"
}

resource "aws_lambda_permission" "concierge_ws" {
  statement_id  = "AllowConciergeWsInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["concierge-ws"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "arn:${var.partition}:execute-api:${var.region}:${var.account_id}:${var.websocket_api_id}/*"
}
