# Runtime-resolved context. The account id and region are read from the active
# credentials/provider rather than being written into any file, so ARNs built in the
# modules are always correct for whatever account/region the deploy targets.
data "aws_caller_identity" "current" {}

data "aws_region" "current" {}

data "aws_partition" "current" {}
