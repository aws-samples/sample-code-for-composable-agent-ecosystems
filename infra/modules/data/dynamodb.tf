# ─────────────────────────────────────────────────────────────────────────────
# DynamoDB tables
# ─────────────────────────────────────────────────────────────────────────────

# WebSocket connections registry. Partition key = connectionId. PITR on.
resource "aws_dynamodb_table" "ws_connections" {
  name         = "${var.name_prefix}-ws-connections"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "connectionId"

  attribute {
    name = "connectionId"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  # Customer-managed KMS encryption at rest (CKV_AWS_119).
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.data_key_arn
  }

  tags = var.tags
}

# Attention inbox — async human-in-the-loop queue. Partition by owner, sort by item
# id so a single Query answers "what needs me, anywhere?". `ttl` is housekeeping only
# (auto-purge resolved/expired items); deadline/expiry semantics are handled in code.
resource "aws_dynamodb_table" "attention" {
  name         = "${var.name_prefix}-attention"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "ownerId"
  range_key    = "itemId"

  attribute {
    name = "ownerId"
    type = "S"
  }

  attribute {
    name = "itemId"
    type = "S"
  }

  point_in_time_recovery {
    enabled = true
  }

  # Customer-managed KMS encryption at rest (CKV_AWS_119).
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.data_key_arn
  }

  ttl {
    attribute_name = "ttl"
    enabled        = true
  }

  tags = var.tags
}

# Catalog refresh state store — single-table design keyed (pk, sk). Holds regenerable
# ingest cursor/checkpoint/manifest state; rebuilt on the next ingest.
resource "aws_dynamodb_table" "catalog_refresh" {
  name         = "${var.name_prefix}-catalog-refresh"
  billing_mode = "PAY_PER_REQUEST"
  hash_key     = "pk"
  range_key    = "sk"

  attribute {
    name = "pk"
    type = "S"
  }

  attribute {
    name = "sk"
    type = "S"
  }

  # Point-in-time recovery (CKV_AWS_28).
  point_in_time_recovery {
    enabled = true
  }

  # Customer-managed KMS encryption at rest (CKV_AWS_119).
  server_side_encryption {
    enabled     = true
    kms_key_arn = var.data_key_arn
  }

  tags = var.tags
}
