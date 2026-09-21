variable "name_prefix" {
  description = "Resource name prefix (<project>-<environment>)."
  type        = string
}

variable "tags" {
  description = "Common tags applied to all resources."
  type        = map(string)
  default     = {}
}

variable "vpc_max_azs" {
  description = "Number of Availability Zones for the Lambda VPC."
  type        = number
  default     = 2
}

variable "vpc_nat_gateways" {
  description = "Number of NAT gateways for the private subnets."
  type        = number
  default     = 1
}

variable "access_logs_expiration_days" {
  description = "Lifecycle expiration (days) for objects in the access-logs bucket."
  type        = number
  default     = 90
}

variable "export_expiration_days" {
  description = "Lifecycle expiration (days) for generated export packages in the exports bucket."
  type        = number
  default     = 7
}

variable "log_retention_days" {
  description = "Retention (days) for foundation-owned CloudWatch log groups (e.g. VPC flow logs). Clamped to >= 365."
  type        = number
  default     = 365
}
