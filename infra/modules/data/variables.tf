variable "name_prefix" {
  description = "Resource name prefix (<project>-<environment>)."
  type        = string
}

variable "tags" {
  description = "Common tags applied to all resources."
  type        = map(string)
  default     = {}
}

variable "data_key_arn" {
  type        = string
  description = "KMS CMK ARN for encrypting DynamoDB tables at rest."
}
