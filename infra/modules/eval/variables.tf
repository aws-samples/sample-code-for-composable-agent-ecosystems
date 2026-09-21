variable "name_prefix" {
  type = string
}
variable "project" {
  type = string
}
variable "environment" {
  type = string
}
variable "tags" {
  type    = map(string)
  default = {}
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
variable "lambda_runtime" {
  type    = string
  default = "python3.12"
}
variable "lambdas_src_dir" {
  type = string
}
variable "build_dir" {
  type = string
}
variable "shared_layer_arn" {
  description = "ARN of the shared Lambda layer (tessera_platform.*), built by the compute module."
  type        = string
}
variable "ecosystems_bucket_name" {
  type = string
}
variable "ecosystems_bucket_arn" {
  type = string
}
variable "runs_bucket_name" {
  type = string
}
variable "runs_bucket_arn" {
  type = string
}

variable "lambda_max_memory" {
  type        = number
  default     = 512
  description = "Per-function Lambda memory ceiling (clamps eval function memory to the account limit)."
}

variable "lambda_env_key_arn" {
  description = "KMS key ARN for Lambda environment variable encryption (CKV_AWS_173)."
  type        = string
}

variable "lambda_dlq_arn" {
  description = "Shared Lambda dead-letter queue ARN (CKV_AWS_116)."
  type        = string
}
