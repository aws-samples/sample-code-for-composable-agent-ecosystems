# ─────────────────────────────────────────────────────────────────────────────
# Lambda packaging + shared layer
#
# Dependency-bearing Lambdas are packaged by scripts/package-lambdas.sh into
# `build_dir/<name>.zip` (Linux x86_64 wheels), run before `terraform apply` by the
# Makefile and CI. Terraform uses that prebuilt zip when present. For Lambdas with no
# third-party dependencies, Terraform zips the raw source itself via archive_file, so
# a plain `terraform apply` still works for them without a separate build step.
#
# The shared layer (auth.py, pricing.py, tessera_platform.catalog/.decision/.quorum)
# is likewise built by the packaging script into `build_dir/shared_layer.zip` from the
# manifest (app/lambdas/shared_layer_manifest.txt) — the single source of truth.
# ─────────────────────────────────────────────────────────────────────────────

locals {
  # Discover every Lambda source directory under app/lambdas, excluding shared modules
  # and scratch dirs. Each becomes a function named tessera-<env>-<dir>.
  lambda_dirs = toset([
    for d in fileset(var.lambdas_src_dir, "*/handler.py") :
    dirname(d)
  ])

  # Prebuilt package path per function (produced by scripts/package-lambdas.sh).
  prebuilt_zip = { for name in local.lambda_dirs : name => "${var.build_dir}/${name}.zip" }

  shared_layer_zip = "${var.build_dir}/shared_layer.zip"
}

# Fallback source-only archives — used only when a prebuilt zip is absent. archive_file
# reads at plan time; if build_dir is empty this still produces a valid (deps-less) zip.
data "archive_file" "lambda_src" {
  for_each    = local.lambda_dirs
  type        = "zip"
  source_dir  = "${var.lambdas_src_dir}/${each.value}"
  output_path = "${path.module}/.src-archives/${each.value}.zip"
  excludes    = ["__pycache__", "requirements.txt"]
}

# Shared layer — prefer the prebuilt (dependency-complete) zip; the module requires the
# packaging script to have produced it, since the shared modules are pure-python and the
# layer must contain the tessera_platform packages.
resource "aws_lambda_layer_version" "shared" {
  layer_name          = "${var.name_prefix}-shared"
  description         = "Shared modules (auth, pricing, tessera_platform.catalog/.decision/.quorum) for Tessera Lambdas"
  filename            = fileexists(local.shared_layer_zip) ? local.shared_layer_zip : data.archive_file.shared_layer_placeholder.output_path
  source_code_hash    = fileexists(local.shared_layer_zip) ? filebase64sha256(local.shared_layer_zip) : data.archive_file.shared_layer_placeholder.output_base64sha256
  compatible_runtimes = [var.lambda_runtime]
}

# Placeholder empty layer so `terraform validate`/plan succeed before the packaging
# script has run. The real layer content comes from build_dir/shared_layer.zip.
data "archive_file" "shared_layer_placeholder" {
  type        = "zip"
  output_path = "${path.module}/.src-archives/shared_layer_placeholder.zip"

  source {
    content  = "# placeholder — run scripts/package-lambdas.sh to build the real shared layer"
    filename = "python/.placeholder"
  }
}

# Helper: resolve the deployment package + hash for a given function name.
locals {
  lambda_package = {
    for name in local.lambda_dirs : name => {
      filename = fileexists(local.prebuilt_zip[name]) ? local.prebuilt_zip[name] : data.archive_file.lambda_src[name].output_path
      hash     = fileexists(local.prebuilt_zip[name]) ? filebase64sha256(local.prebuilt_zip[name]) : data.archive_file.lambda_src[name].output_base64sha256
    }
  }
}
