// terraform-tessera-eco :: versions
// Backend-agnostic (spec §8.2): NO backend block here — the consuming customer wires
// their own state backend (S3+DynamoDB / Terraform Cloud / Spacelift) in the root.
// CHILD MODULE: no provider blocks either.
terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.60.0"
    }
    // AgentCore resources are exposed via the AWS Cloud Control provider (awscc),
    // generated from the CloudFormation registry (AWS::BedrockAgentCore::*).
    awscc = {
      source  = "hashicorp/awscc"
      version = ">= 1.0.0"
    }
  }
}
