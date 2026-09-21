# ─────────────────────────────────────────────────────────────────────────────
# WebSocket API (API Gateway v2)
#
# Routes: $connect, $disconnect, $default, launch, chat, register — all integrated
# to the single WebSocket handler Lambda. A single wildcard invoke permission covers
# every stage/route so no route ever fails with "internal server error" from a missing
# per-route permission.
# ─────────────────────────────────────────────────────────────────────────────

locals {
  # Function names/ARNs derived from the deterministic convention (no dependency on the
  # compute module → avoids a cycle with the WS callback URL wiring).
  fn_name       = { for fn in local.all_referenced_fns : fn => "${var.name_prefix}-${fn}" }
  fn_arn        = { for fn in local.all_referenced_fns : fn => "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-${fn}" }
  fn_lambda_uri = { for fn in local.all_referenced_fns : fn => "arn:${var.partition}:apigateway:${var.region}:lambda:path/2015-03-31/functions/${local.fn_arn[fn]}/invocations" }

  ws_fn_invoke_arn = local.fn_lambda_uri[var.websocket_function_name]
  ws_fn_name       = local.fn_name[var.websocket_function_name]
  ws_routes        = ["$connect", "$disconnect", "$default", "launch", "chat", "register"]
}

resource "aws_apigatewayv2_api" "ws" {
  name                       = "${var.name_prefix}-ws"
  protocol_type              = "WEBSOCKET"
  route_selection_expression = "$request.body.action"
  tags                       = var.tags
}

resource "aws_apigatewayv2_integration" "ws" {
  api_id                    = aws_apigatewayv2_api.ws.id
  integration_type          = "AWS_PROXY"
  integration_uri           = local.ws_fn_invoke_arn
  content_handling_strategy = "CONVERT_TO_TEXT"
  passthrough_behavior      = "WHEN_NO_MATCH"
}

# $connect REQUEST authorizer — verifies the Cognito ID token passed as ?token= on the
# handshake (CKV_AWS_309). The authorizer Lambda is created in the compute module; it is
# referenced here by its deterministic ARN to avoid an api->compute dependency cycle.
locals {
  ws_authorizer_fn_arn = "arn:${var.partition}:lambda:${var.region}:${var.account_id}:function:${var.name_prefix}-ws-authorizer"
  ws_authorizer_uri    = "arn:${var.partition}:apigateway:${var.region}:lambda:path/2015-03-31/functions/${local.ws_authorizer_fn_arn}/invocations"
}

resource "aws_apigatewayv2_authorizer" "ws_connect" {
  api_id           = aws_apigatewayv2_api.ws.id
  authorizer_type  = "REQUEST"
  authorizer_uri   = local.ws_authorizer_uri
  identity_sources = ["route.request.querystring.token"]
  name             = "${var.name_prefix}-ws-connect"
}

# NOTE: the permission letting API Gateway invoke the ws-authorizer Lambda is created in
# the COMPUTE module (compute/api_permissions.tf), because that module owns the Lambda
# functions. Creating it there lets the permission take an implicit dependency on the
# actual aws_lambda_function.this["ws-authorizer"] resource, so Terraform orders it AFTER
# the function exists (avoiding the 404 "Function not found" race), while keeping the
# module dependency one-directional (api → compute) and cycle-free. The authorizer id is
# exported via the websocket_authorizer_id output.

# $connect carries the CUSTOM authorizer; the other routes ($disconnect/$default and the
# message routes) are authorized by the connection established at $connect (API Gateway v2
# WebSocket only supports an authorizer on $connect), with per-message JWT re-verification
# in the handler as defense in depth.
resource "aws_apigatewayv2_route" "ws_connect" {
  api_id             = aws_apigatewayv2_api.ws.id
  route_key          = "$connect"
  target             = "integrations/${aws_apigatewayv2_integration.ws.id}"
  authorization_type = "CUSTOM"
  authorizer_id      = aws_apigatewayv2_authorizer.ws_connect.id
}

resource "aws_apigatewayv2_route" "ws" {
  #checkov:skip=CKV_AWS_309:AWS API Gateway v2 WebSocket allows an authorizer ONLY on $connect (see $connect route above, which IS authorized). $disconnect/$default/message routes cannot take an authorizer by AWS design; the connection is gated at $connect and the handler re-verifies the JWT per message.
  for_each  = toset([for r in local.ws_routes : r if r != "$connect"])
  api_id    = aws_apigatewayv2_api.ws.id
  route_key = each.value
  target    = "integrations/${aws_apigatewayv2_integration.ws.id}"
}

resource "aws_cloudwatch_log_group" "ws" {
  name              = "/aws/apigateway/${var.name_prefix}-ws"
  retention_in_days = max(var.log_retention_days, 365) # >= 1 year (CKV_AWS_338)
  kms_key_id        = var.logs_key_arn
  tags              = var.tags
}

resource "aws_apigatewayv2_stage" "ws" {
  #checkov:skip=CKV2_AWS_51:Client-certificate (mutual TLS) is not used — WebSocket clients authenticate via the Cognito JWT $connect authorizer. Accepted risk.
  api_id      = aws_apigatewayv2_api.ws.id
  name        = var.environment
  auto_deploy = true

  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.ws.arn
    format = jsonencode({
      requestId   = "$context.requestId"
      ip          = "$context.identity.sourceIp"
      caller      = "$context.identity.caller"
      user        = "$context.identity.user"
      requestTime = "$context.requestTime"
      routeKey    = "$context.routeKey"
      status      = "$context.status"
    })
  }

  tags = var.tags

  # Access logging requires the account-level CloudWatch Logs role to be set first.
  depends_on = [aws_api_gateway_account.this]
}

# NOTE: the wildcard WebSocket invoke permission is created in the compute module
# (which owns the handler function), using this API's id — api → compute direction.
