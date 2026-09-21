variable "name_prefix" {
  description = "Resource name prefix (<project>-<environment>)."
  type        = string
}

variable "environment" {
  description = "Deployment environment (used as the API stage name)."
  type        = string
}

variable "tags" {
  description = "Common tags."
  type        = map(string)
  default     = {}
}

variable "account_id" {
  type = string
}
variable "region" {
  type = string
}
variable "partition" {
  type = string
}

variable "logs_key_arn" {
  description = "KMS key ARN for CloudWatch log group encryption."
  type        = string
}

variable "log_retention_days" {
  type    = number
  default = 30
}

variable "user_pool_arn" {
  description = "Cognito user pool ARN for the REST authorizer."
  type        = string
}

# The API module references Lambda functions only by their deterministic names
# (${name_prefix}-<fn>), so it does NOT depend on the compute module. The dependency
# flows api → compute: the compute module consumes this module's API ids to create the
# Lambda invoke permissions and to set the WebSocket callback URL (WS_ENDPOINT).

variable "websocket_function_name" {
  description = "Short key of the WebSocket handler function (default: websocket)."
  type        = string
  default     = "websocket"
}
