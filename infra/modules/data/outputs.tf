# ── DynamoDB ──
output "connections_table_name" {
  description = "WebSocket connections table name."
  value       = aws_dynamodb_table.ws_connections.name
}

output "connections_table_arn" {
  description = "WebSocket connections table ARN."
  value       = aws_dynamodb_table.ws_connections.arn
}

output "attention_table_name" {
  description = "Attention inbox table name."
  value       = aws_dynamodb_table.attention.name
}

output "attention_table_arn" {
  description = "Attention inbox table ARN."
  value       = aws_dynamodb_table.attention.arn
}

output "catalog_refresh_table_name" {
  description = "Catalog refresh state table name."
  value       = aws_dynamodb_table.catalog_refresh.name
}

output "catalog_refresh_table_arn" {
  description = "Catalog refresh state table ARN."
  value       = aws_dynamodb_table.catalog_refresh.arn
}

# ── EventBridge ──
output "signals_bus_name" {
  description = "Signals event bus name."
  value       = aws_cloudwatch_event_bus.signals.name
}

output "signals_bus_arn" {
  description = "Signals event bus ARN."
  value       = aws_cloudwatch_event_bus.signals.arn
}

output "regrounding_bus_name" {
  description = "Re-grounding event bus name."
  value       = aws_cloudwatch_event_bus.regrounding.name
}

output "regrounding_bus_arn" {
  description = "Re-grounding event bus ARN."
  value       = aws_cloudwatch_event_bus.regrounding.arn
}
