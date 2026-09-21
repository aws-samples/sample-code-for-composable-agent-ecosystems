# ─────────────────────────────────────────────────────────────────────────────
# VPC for Lambda functions.
#
# Mirrors the original topology: one VPC with paired public/private subnets across
# `vpc_max_azs` AZs, and `vpc_nat_gateways` NAT gateway(s) providing egress for the
# private subnets (Lambdas run in the private subnets; NAT gives them internet
# access for e.g. Bedrock/agent calls).
# ─────────────────────────────────────────────────────────────────────────────

data "aws_availability_zones" "available" {
  state = "available"
}

locals {
  azs = slice(data.aws_availability_zones.available.names, 0, var.vpc_max_azs)

  # /16 VPC carved into /24 subnets: public = 10.0.0.0/24.., private = 10.0.128.0/24..
  public_subnet_cidrs  = [for i in range(var.vpc_max_azs) : cidrsubnet("10.0.0.0/16", 8, i)]
  private_subnet_cidrs = [for i in range(var.vpc_max_azs) : cidrsubnet("10.0.0.0/16", 8, i + 128)]
}

resource "aws_vpc" "main" {
  cidr_block           = "10.0.0.0/16"
  enable_dns_support   = true
  enable_dns_hostnames = true
  tags                 = merge(var.tags, { Name = "${var.name_prefix}-lambda-vpc" })
}

# ── Default security group: deny all (CKV2_AWS_12) ────────────────────────────
# Managing the VPC's default SG with no ingress/egress rules ensures nothing can
# inadvertently use it. All workloads use the explicit egress-only SG below.
resource "aws_default_security_group" "main" {
  vpc_id = aws_vpc.main.id
  tags   = merge(var.tags, { Name = "${var.name_prefix}-default-sg-deny-all" })
}

# ── VPC flow logs (CKV2_AWS_11) ───────────────────────────────────────────────
# Capture all traffic metadata to an encrypted CloudWatch log group for audit.
resource "aws_cloudwatch_log_group" "flow_logs" {
  name              = "/aws/vpc/${var.name_prefix}-flow-logs"
  retention_in_days = max(var.log_retention_days, 365) # >= 1 year (CKV_AWS_338)
  kms_key_id        = aws_kms_key.logs.arn             # CMK encryption (CKV_AWS_158)
  tags              = var.tags
}

data "aws_iam_policy_document" "flow_logs_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["vpc-flow-logs.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "flow_logs" {
  name               = "${var.name_prefix}-vpc-flow-logs"
  assume_role_policy = data.aws_iam_policy_document.flow_logs_assume.json
  tags               = var.tags
}

data "aws_iam_policy_document" "flow_logs" {
  statement {
    effect = "Allow"
    actions = [
      "logs:CreateLogStream",
      "logs:PutLogEvents",
      "logs:DescribeLogGroups",
      "logs:DescribeLogStreams",
    ]
    resources = ["${aws_cloudwatch_log_group.flow_logs.arn}:*"]
  }
}

resource "aws_iam_role_policy" "flow_logs" {
  name   = "flow-logs"
  role   = aws_iam_role.flow_logs.id
  policy = data.aws_iam_policy_document.flow_logs.json
}

resource "aws_flow_log" "main" {
  vpc_id                   = aws_vpc.main.id
  traffic_type             = "ALL"
  log_destination_type     = "cloud-watch-logs"
  log_destination          = aws_cloudwatch_log_group.flow_logs.arn
  iam_role_arn             = aws_iam_role.flow_logs.arn
  max_aggregation_interval = 600
  tags                     = merge(var.tags, { Name = "${var.name_prefix}-vpc-flow-logs" })
}

resource "aws_internet_gateway" "main" {
  vpc_id = aws_vpc.main.id
  tags   = merge(var.tags, { Name = "${var.name_prefix}-igw" })
}

resource "aws_subnet" "public" {
  count             = var.vpc_max_azs
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.public_subnet_cidrs[count.index]
  availability_zone = local.azs[count.index]
  # No auto-assigned public IPs (CKV_AWS_130). These subnets only host the NAT gateway,
  # which uses an explicit EIP — nothing here relies on auto-assign, so egress is unaffected.
  map_public_ip_on_launch = false
  tags                    = merge(var.tags, { Name = "${var.name_prefix}-public-${local.azs[count.index]}" })
}

resource "aws_subnet" "private" {
  count             = var.vpc_max_azs
  vpc_id            = aws_vpc.main.id
  cidr_block        = local.private_subnet_cidrs[count.index]
  availability_zone = local.azs[count.index]
  tags              = merge(var.tags, { Name = "${var.name_prefix}-private-${local.azs[count.index]}" })
}

# Public route table → internet gateway.
resource "aws_route_table" "public" {
  vpc_id = aws_vpc.main.id
  route {
    cidr_block = "0.0.0.0/0"
    gateway_id = aws_internet_gateway.main.id
  }
  tags = merge(var.tags, { Name = "${var.name_prefix}-public-rt" })
}

resource "aws_route_table_association" "public" {
  count          = var.vpc_max_azs
  subnet_id      = aws_subnet.public[count.index].id
  route_table_id = aws_route_table.public.id
}

# NAT gateways (one per NAT, placed in the first N public subnets).
resource "aws_eip" "nat" {
  count  = var.vpc_nat_gateways
  domain = "vpc"
  tags   = merge(var.tags, { Name = "${var.name_prefix}-nat-eip-${count.index}" })
}

resource "aws_nat_gateway" "main" {
  count         = var.vpc_nat_gateways
  allocation_id = aws_eip.nat[count.index].id
  subnet_id     = aws_subnet.public[count.index].id
  tags          = merge(var.tags, { Name = "${var.name_prefix}-nat-${count.index}" })
  depends_on    = [aws_internet_gateway.main]
}

# Private route tables → NAT. Each private subnet routes to a NAT gateway; if there
# are fewer NATs than AZs, private subnets share NATs round-robin.
resource "aws_route_table" "private" {
  count  = var.vpc_max_azs
  vpc_id = aws_vpc.main.id
  route {
    cidr_block     = "0.0.0.0/0"
    nat_gateway_id = aws_nat_gateway.main[count.index % var.vpc_nat_gateways].id
  }
  tags = merge(var.tags, { Name = "${var.name_prefix}-private-rt-${count.index}" })
}

resource "aws_route_table_association" "private" {
  count          = var.vpc_max_azs
  subnet_id      = aws_subnet.private[count.index].id
  route_table_id = aws_route_table.private[count.index].id
}

# Security group for Lambda functions — egress-only (all outbound, no inbound).
resource "aws_security_group" "lambda" {
  #checkov:skip=CKV_AWS_382:Lambdas need outbound egress to reach Bedrock/AgentCore and public source feeds via NAT; there is no fixed egress CIDR/port set to restrict to. Egress-only, no inbound. Accepted risk.
  #checkov:skip=CKV2_AWS_5:This SG IS attached — it is referenced by the VPC-attached Lambda functions (var.lambda_security_group_id → aws_lambda_function vpc_config). Checkov cannot statically trace the cross-module attachment.
  name        = "${var.name_prefix}-lambda-sg"
  description = "Egress-only security group for Tessera Lambda functions"
  vpc_id      = aws_vpc.main.id

  egress {
    description = "All outbound"
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(var.tags, { Name = "${var.name_prefix}-lambda-sg" })
}
