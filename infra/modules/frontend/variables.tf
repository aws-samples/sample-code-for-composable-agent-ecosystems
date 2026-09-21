variable "name_prefix" {
  type = string
}
variable "tags" {
  type    = map(string)
  default = {}
}

variable "access_logs_bucket_id" {
  description = "Access-logs bucket name (for CloudFront + S3 server-access logging)."
  type        = string
}
variable "access_logs_bucket_arn" {
  description = "Access-logs bucket ARN."
  type        = string
}

variable "cloudfront_acm_certificate_arn" {
  type        = string
  default     = ""
  description = "Optional us-east-1 ACM certificate ARN for a custom domain on the CloudFront distribution. When set (with cloudfront_aliases), CloudFront presents this cert and enforces TLSv1.2_2021; when empty, the default CloudFront certificate is used."
}

variable "cloudfront_aliases" {
  type        = list(string)
  default     = []
  description = "Optional custom domain name(s) (CNAMEs) for the CloudFront distribution. Required alongside cloudfront_acm_certificate_arn."
}
