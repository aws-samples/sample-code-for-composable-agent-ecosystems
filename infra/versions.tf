# Terraform and provider version constraints.
#
# Terraform >= 1.11 is required for S3-native state locking via the `use_lockfile`
# backend argument. This removes the need for a DynamoDB lock table entirely — the
# lock is held as an object in the same S3 state bucket.
terraform {
  required_version = ">= 1.11.0"

  required_providers {
    # Primary AWS provider — used for all classic services (Lambda, API Gateway,
    # DynamoDB, S3, CloudFront, Cognito, KMS, SQS, SNS, VPC, EventBridge, Step
    # Functions, IAM, WAFv2, Bedrock Guardrail, Verified Permissions, Secrets Manager).
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.70"
    }

    # AWS Cloud Control provider — used for services the classic provider does not yet
    # model natively. Here: Amazon Bedrock AgentCore Memory, Gateway, and Workload
    # Identity. AWSCC is generated from the AWS Cloud Control API, so it tracks new
    # services sooner than the classic provider.
    awscc = {
      source  = "hashicorp/awscc"
      version = "~> 1.20"
    }

    # Packages Lambda source directories into deployment zips at plan/apply time.
    archive = {
      source  = "hashicorp/archive"
      version = "~> 2.4"
    }

    # Drives the small number of imperative steps that have no declarative resource
    # (e.g. the AgentCore Runtime deploy via the agentcore CLI).
    null = {
      source  = "hashicorp/null"
      version = "~> 3.2"
    }
  }
}
