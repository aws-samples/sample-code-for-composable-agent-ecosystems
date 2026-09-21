// terraform-tessera-eco :: WebSocket API Gateway (the run "front door")
//
// The websocket Lambda handles $connect/$disconnect/$default (dispatching on `action`:
// register/launch/chat/quorum/…) and starts the Step Functions run. agent-invoker,
// grounding-check and websocket all post events back to clients via WS_ENDPOINT (the
// @connections management endpoint). No cycle: the API resource itself doesn't reference
// the Lambda (only the integration does), so api → lambda(env) → integration is linear.

resource "aws_apigatewayv2_api" "ws" {
  name                       = "${local.name}-ws"
  protocol_type              = "WEBSOCKET"
  route_selection_expression = "$request.body.action"
  tags                       = local.tags
}

resource "aws_apigatewayv2_integration" "ws" {
  api_id             = aws_apigatewayv2_api.ws.id
  integration_type   = "AWS_PROXY"
  integration_uri    = aws_lambda_function.fn["websocket"].invoke_arn
  integration_method = "POST"
}

// $connect REQUEST authorizer — verifies the eco Cognito ID token passed as ?token= on
// the handshake (CKV_AWS_309). $disconnect/$default are authorized by the established
// connection (WS APIs only support an authorizer on $connect); the handler re-verifies
// the JWT per message as defense in depth.
resource "aws_apigatewayv2_authorizer" "ws_connect" {
  api_id           = aws_apigatewayv2_api.ws.id
  authorizer_type  = "REQUEST"
  authorizer_uri   = aws_lambda_function.fn["ws-authorizer"].invoke_arn
  identity_sources = ["route.request.querystring.token"]
  name             = "${local.name}-ws-connect"
}

resource "aws_lambda_permission" "ws_authorizer" {
  statement_id  = "AllowWsAuthorizerInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["ws-authorizer"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.ws.execution_arn}/authorizers/${aws_apigatewayv2_authorizer.ws_connect.id}"
}

resource "aws_apigatewayv2_route" "connect" {
  api_id             = aws_apigatewayv2_api.ws.id
  route_key          = "$connect"
  target             = "integrations/${aws_apigatewayv2_integration.ws.id}"
  authorization_type = "CUSTOM"
  authorizer_id      = aws_apigatewayv2_authorizer.ws_connect.id
}

resource "aws_apigatewayv2_route" "disconnect" {
  #checkov:skip=CKV_AWS_309:AWS allows a WebSocket authorizer ONLY on $connect (which IS authorized above). $disconnect cannot take one; the connection is gated at $connect and the handler re-verifies the JWT per message.
  api_id    = aws_apigatewayv2_api.ws.id
  route_key = "$disconnect"
  target    = "integrations/${aws_apigatewayv2_integration.ws.id}"
}

resource "aws_apigatewayv2_route" "default" {
  #checkov:skip=CKV_AWS_309:AWS allows a WebSocket authorizer ONLY on $connect (which IS authorized above). $default cannot take one; the connection is gated at $connect and the handler re-verifies the JWT per message.
  api_id    = aws_apigatewayv2_api.ws.id
  route_key = "$default"
  target    = "integrations/${aws_apigatewayv2_integration.ws.id}"
}

resource "aws_cloudwatch_log_group" "ws_access" {
  name              = "/aws/apigateway/${local.name}-ws"
  retention_in_days = max(var.log_retention_days, 365) # >= 1 year (CKV_AWS_338)
  kms_key_id        = local.kms_key_arn
  tags              = local.tags
}

resource "aws_apigatewayv2_stage" "ws" {
  #checkov:skip=CKV2_AWS_51:Client-certificate (mutual TLS) is not used — WebSocket clients authenticate via the Cognito JWT $connect authorizer. Accepted risk.
  api_id      = aws_apigatewayv2_api.ws.id
  name        = "prod"
  auto_deploy = true
  tags        = local.tags

  # Access logging (CKV_AWS_76).
  access_log_settings {
    destination_arn = aws_cloudwatch_log_group.ws_access.arn
    format = jsonencode({
      requestId   = "$context.requestId"
      ip          = "$context.identity.sourceIp"
      requestTime = "$context.requestTime"
      routeKey    = "$context.routeKey"
      status      = "$context.status"
    })
  }
}

resource "aws_lambda_permission" "ws" {
  statement_id  = "AllowWebSocketInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.fn["websocket"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "${aws_apigatewayv2_api.ws.execution_arn}/*/*"
}
