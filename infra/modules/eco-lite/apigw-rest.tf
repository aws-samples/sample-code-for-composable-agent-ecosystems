// terraform-tessera-eco :: REST API (HTTP API v2) for the single-eco UI
//
// Cognito JWT authorizer; payload format 1.0 so the platform lambdas' event contract
// (httpMethod/path/pathParameters/body) is unchanged. $default stage → clean paths
// (no /stage prefix). Routes mirror the platform subset the run/report/HITL UI needs.

resource "aws_apigatewayv2_api" "rest" {
  name          = "${local.name}-rest"
  protocol_type = "HTTP"
  cors_configuration {
    allow_origins = ["*"]
    allow_methods = ["GET", "POST", "PUT", "DELETE", "OPTIONS"]
    allow_headers = ["authorization", "content-type"]
    max_age       = 3600
  }
  tags = local.tags
}

resource "aws_apigatewayv2_authorizer" "cognito" {
  api_id           = aws_apigatewayv2_api.rest.id
  authorizer_type  = "JWT"
  identity_sources = ["$request.header.Authorization"]
  name             = "${local.name}-cognito"
  jwt_configuration {
    audience = [aws_cognito_user_pool_client.eco.id]
    issuer   = "https://cognito-idp.${var.region}.amazonaws.com/${aws_cognito_user_pool.eco.id}"
  }
}

resource "aws_cloudwatch_log_group" "rest_access" {
  name              = "/aws/apigateway/${local.name}-rest"
  retention_in_days = max(var.log_retention_days, 365) # >= 1 year (CKV_AWS_338)
  kms_key_id        = local.kms_key_arn
  tags              = local.tags
}

resource "aws_apigatewayv2_stage" "rest" {
  api_id      = aws_apigatewayv2_api.rest.id
  name        = "$default"
  auto_deploy = true
  tags        = local.tags

  # Access logging (CKV_AWS_76).
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.rest_access.arn
    format = jsonencode({
      requestId      = "$context.requestId"
      ip             = "$context.identity.sourceIp"
      requestTime    = "$context.requestTime"
      httpMethod     = "$context.httpMethod"
      routeKey       = "$context.routeKey"
      status         = "$context.status"
      protocol       = "$context.protocol"
      responseLength = "$context.responseLength"
      integrationErr = "$context.integrationErrorMessage"
    })
  }
}

// Integrations (payload 1.0) for the lambdas the UI needs.
locals {
  rest_fns = toset([
    for f in ["get-ecosystem", "runs-api", "run-artifact", "gate-decision", "mission-console", "presign-upload"] : f
    if contains(keys(var.lambdas), f)
  ])
}

resource "aws_apigatewayv2_integration" "rest" {
  for_each               = local.rest_fns
  api_id                 = aws_apigatewayv2_api.rest.id
  integration_type       = "AWS_PROXY"
  integration_uri        = aws_lambda_function.fn[each.key].invoke_arn
  integration_method     = "POST"
  payload_format_version = "1.0"
}

resource "aws_lambda_permission" "rest" {
  for_each      = local.rest_fns
  statement_id  = "AllowRestInvoke-${each.key}"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn[each.key].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.rest.execution_arn}/*/*"
}

// Route table: route_key -> integration function (filtered to fns present).
locals {
  rest_routes = merge(
    {
      "GET /api/ecosystems/{id}"                      = "get-ecosystem"
      "GET /api/ecosystems/{id}/runs"                 = "runs-api"
      "GET /api/ecosystems/{id}/runs/{proxy+}"        = "runs-api"
      "GET /api/attention"                            = "runs-api"
      "GET /api/runs/{sessionId}/artifacts/{proxy+}"  = "run-artifact"
      "POST /api/runs/{sessionId}/artifacts/{proxy+}" = "run-artifact"
      "GET /api/runs/{sessionId}/bundle"              = "run-artifact"
      // Conversational mission (run-planner, mode=mission) — the UI's mission front door.
      "POST /api/ecosystems/suggest" = "mission-console"
    },
    contains(keys(var.lambdas), "gate-decision") ? {
      "POST /api/runs/{sessionId}/gate-decision" = "gate-decision"
    } : {},
    contains(keys(var.lambdas), "presign-upload") ? {
      "POST /api/uploads/presign" = "presign-upload"
    } : {},
  )
  // Only keep routes whose target fn is actually present in the closure.
  rest_routes_present = { for k, v in local.rest_routes : k => v if contains(local.rest_fns, v) }
}

resource "aws_apigatewayv2_route" "rest" {
  for_each           = local.rest_routes_present
  api_id             = aws_apigatewayv2_api.rest.id
  route_key          = each.key
  target             = "integrations/${aws_apigatewayv2_integration.rest[each.value].id}"
  authorization_type = "JWT"
  authorizer_id      = aws_apigatewayv2_authorizer.cognito.id
}
