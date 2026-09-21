# This module needs a provider aliased to us-east-1 for the CLOUDFRONT-scoped WAFv2
# web ACL (which must live in us-east-1 regardless of the deployment region). The root
# passes it as `aws.us_east_1`.
terraform {
  required_providers {
    aws = {
      source                = "hashicorp/aws"
      configuration_aliases = [aws.us_east_1]
    }
  }
}
