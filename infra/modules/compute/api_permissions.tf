# ─────────────────────────────────────────────────────────────────────────────
# API Gateway → Lambda invoke permissions.
#
# Created here (not in the api module) because this module owns the functions, and it
# receives the API ids from the api module (api → compute). This keeps the dependency
# one-directional and cycle-free.
# ─────────────────────────────────────────────────────────────────────────────

# REST API: one permission per function referenced by a REST route.
#
# The for_each set is keyed on function names — both `rest_function_keys` and
# `platform_lambda_dirs` are known at plan time (derived from config / the source tree),
# so the instance keys are static. The unknown-at-plan `rest_api_id` appears only inside
# `source_arn`, which Terraform permits.
resource "aws_lambda_permission" "rest_invoke" {
  for_each = setintersection(var.rest_function_keys, local.platform_lambda_dirs)

  statement_id  = "AllowRestApiInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this[each.value].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "arn:${var.partition}:execute-api:${var.region}:${var.account_id}:${var.rest_api_id}/*/*"
}

# WebSocket API: single wildcard invoke permission for all routes/stages. The websocket
# handler is always part of the platform function set, so this resource always exists;
# the unknown `websocket_api_id` is used only in `source_arn`.
resource "aws_lambda_permission" "ws_invoke" {
  statement_id  = "AllowWsInvokeAllRoutes"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this["websocket"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "arn:${var.partition}:execute-api:${var.region}:${var.account_id}:${var.websocket_api_id}/*"
}

# WebSocket $connect REQUEST authorizer invoke permission. Created here (not in the api
# module) so it can reference the actual ws-authorizer function resource — this gives
# Terraform an implicit dependency that orders the permission AFTER the function is
# created, eliminating the "Function not found" (404) race seen on a from-scratch apply.
resource "aws_lambda_permission" "ws_authorizer" {
  statement_id  = "AllowWsAuthorizerInvoke"
  action        = "lambda:InvokeFunction"
  function_name = aws_lambda_function.this["ws-authorizer"].function_name
  principal     = "apigateway.amazonaws.com"
  source_arn    = "arn:${var.partition}:execute-api:${var.region}:${var.account_id}:${var.websocket_api_id}/authorizers/${var.websocket_authorizer_id}"
}
