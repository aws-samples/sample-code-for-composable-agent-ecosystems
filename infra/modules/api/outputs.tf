output "rest_api_id" {
  description = "REST API id."
  value       = aws_api_gateway_rest_api.main.id
}

output "rest_api_url" {
  description = "REST API invoke URL."
  value       = aws_api_gateway_stage.main.invoke_url
}

output "websocket_api_id" {
  description = "WebSocket API id."
  value       = aws_apigatewayv2_api.ws.id
}

output "websocket_url" {
  description = "WebSocket API invoke URL (wss)."
  value       = aws_apigatewayv2_stage.ws.invoke_url
}

output "websocket_callback_url" {
  description = "WebSocket management (callback) endpoint (https) for pushing to connections."
  value       = "https://${aws_apigatewayv2_api.ws.id}.execute-api.${var.region}.amazonaws.com/${var.environment}"
}

output "websocket_authorizer_id" {
  description = "WebSocket $connect REQUEST authorizer id (for the ws-authorizer invoke permission, created in the compute module which owns the function)."
  value       = aws_apigatewayv2_authorizer.ws_connect.id
}

output "rest_functions" {
  description = "Set of short function keys referenced by REST routes (for invoke permissions)."
  value       = local.rest_functions
}

output "websocket_handler_key" {
  description = "Short key of the WebSocket handler function."
  value       = var.websocket_function_name
}
