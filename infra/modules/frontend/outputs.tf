output "frontend_bucket_name" {
  description = "Frontend site S3 bucket name."
  value       = aws_s3_bucket.frontend.id
}

output "distribution_id" {
  description = "CloudFront distribution id."
  value       = aws_cloudfront_distribution.frontend.id
}

output "distribution_domain_name" {
  description = "CloudFront distribution domain name."
  value       = aws_cloudfront_distribution.frontend.domain_name
}

output "frontend_url" {
  description = "Public frontend URL."
  value       = "https://${aws_cloudfront_distribution.frontend.domain_name}"
}

output "waf_web_acl_arn" {
  value = aws_wafv2_web_acl.cloudfront.arn
}
