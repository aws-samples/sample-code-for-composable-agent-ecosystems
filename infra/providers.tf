# Provider configuration.
#
# The AWS account and region are NEVER hardcoded. Region comes from the `region`
# input variable; the account id is discovered at runtime from the caller identity
# (see data.tf). This keeps the entire project config-driven and portable across
# accounts and environments.

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

# CloudFront and its WAFv2 web ACL (scope = CLOUDFRONT) must be created in us-east-1
# regardless of the primary deployment region. This aliased provider is passed to the
# frontend module for those global resources.
provider "aws" {
  alias  = "us_east_1"
  region = "us-east-1"

  default_tags {
    tags = {
      Project     = var.project
      Environment = var.environment
      ManagedBy   = "terraform"
    }
  }
}

provider "awscc" {
  region = var.region
}
