# ── KMS ──
output "lambda_env_key_arn" {
  description = "KMS key ARN for Lambda environment variable encryption."
  value       = aws_kms_key.lambda_env.arn
}

output "logs_key_arn" {
  description = "KMS key ARN for CloudWatch Logs encryption."
  value       = aws_kms_key.logs.arn
}

output "data_key_arn" {
  description = "KMS key ARN for data-at-rest (S3/DynamoDB/Secrets Manager/ECR) encryption."
  value       = aws_kms_key.data.arn
}

# ── VPC ──
output "vpc_id" {
  description = "Lambda VPC id."
  value       = aws_vpc.main.id
}

output "private_subnet_ids" {
  description = "Private subnet ids (where Lambdas run)."
  value       = aws_subnet.private[*].id
}

output "public_subnet_ids" {
  description = "Public subnet ids."
  value       = aws_subnet.public[*].id
}

output "lambda_security_group_id" {
  description = "Egress-only security group id for Lambda functions."
  value       = aws_security_group.lambda.id
}

# ── SQS ──
output "lambda_dlq_arn" {
  description = "Shared Lambda dead-letter queue ARN."
  value       = aws_sqs_queue.lambda_dlq.arn
}

output "lambda_dlq_url" {
  description = "Shared Lambda dead-letter queue URL."
  value       = aws_sqs_queue.lambda_dlq.url
}

# ── S3 buckets ──
output "access_logs_bucket_id" {
  description = "Access-logs bucket name."
  value       = aws_s3_bucket.access_logs.id
}

output "access_logs_bucket_arn" {
  description = "Access-logs bucket ARN."
  value       = aws_s3_bucket.access_logs.arn
}

output "ecosystems_bucket_name" {
  description = "Ecosystems bucket name."
  value       = aws_s3_bucket.ecosystems.id
}

output "ecosystems_bucket_arn" {
  description = "Ecosystems bucket ARN."
  value       = aws_s3_bucket.ecosystems.arn
}

output "runs_bucket_name" {
  description = "Runs bucket name."
  value       = aws_s3_bucket.runs.id
}

output "runs_bucket_arn" {
  description = "Runs bucket ARN."
  value       = aws_s3_bucket.runs.arn
}

output "memory_payloads_bucket_name" {
  description = "Memory payloads (ephemeral) bucket name."
  value       = aws_s3_bucket.memory_payloads.id
}

output "memory_payloads_bucket_arn" {
  description = "Memory payloads (ephemeral) bucket ARN."
  value       = aws_s3_bucket.memory_payloads.arn
}

output "exports_bucket_name" {
  description = "Exports (productized export packages) bucket name."
  value       = aws_s3_bucket.exports.id
}

output "exports_bucket_arn" {
  description = "Exports (productized export packages) bucket ARN."
  value       = aws_s3_bucket.exports.arn
}
