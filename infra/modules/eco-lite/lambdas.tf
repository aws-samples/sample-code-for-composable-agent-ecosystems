// terraform-tessera-eco :: Lambda artifacts (shared layer + zip uploads)
//
// The export generator lays the real deployment artifacts into the package:
//   layer/shared-layer.zip        — auth/pricing/tessera_platform.{catalog,decision,export,quorum}
//   lambdas/<name>.zip            — handler + pip-installed deps (Linux/arm64 wheels)
//   ecosystem.yaml                — this eco's definition
// This file uploads them to the (created or referenced) artifacts bucket and publishes the
// shared layer. path.root is the ROOT package dir (the exported package), so filemd5/
// filebase64sha256 read the generator-built zips shipped inside the package.

resource "aws_s3_object" "layer" {
  bucket = local.artifacts_bucket
  key    = "layers/${local.name}-shared-layer.zip"
  source = "${path.root}/layer/shared-layer.zip"
  etag   = filemd5("${path.root}/layer/shared-layer.zip")
  tags   = local.tags
}

resource "aws_lambda_layer_version" "shared" {
  layer_name               = "${local.name}-shared"
  s3_bucket                = local.artifacts_bucket
  s3_key                   = aws_s3_object.layer.key
  source_code_hash         = filebase64sha256("${path.root}/layer/shared-layer.zip")
  compatible_runtimes      = ["python3.12"]
  compatible_architectures = ["x86_64"] # must match aws_lambda_function.fn (x86_64 wheels)
  depends_on               = [aws_s3_object.layer]
}

resource "aws_s3_object" "lambda" {
  for_each = var.lambdas
  bucket   = local.artifacts_bucket
  key      = each.value.s3_key
  source   = "${path.root}/lambdas/${each.key}.zip"
  etag     = filemd5("${path.root}/lambdas/${each.key}.zip")
  tags     = local.tags
}

// The ecosystem YAML — get-ecosystem serves it to the UI at ecosystems/<id>.yaml.
resource "aws_s3_object" "eco_yaml" {
  bucket = local.artifacts_bucket
  key    = "ecosystems/${var.eco_id}.yaml"
  source = "${path.root}/ecosystem.yaml"
  etag   = filemd5("${path.root}/ecosystem.yaml")
  tags   = local.tags
}
