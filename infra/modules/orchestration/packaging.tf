# Package resolution for this module's Lambdas — same convention as the compute module:
# prefer the prebuilt zip in build_dir (produced by scripts/package-lambdas.sh), else
# fall back to a source-only archive.
locals {
  owned_lambdas = [
    "catalog-ingest",
    "catalog-ingest-generic",
    "catalog-api",
    "catalog-write",
    "catalog-regrounding",
    "interactive-invoke",
    "concierge-ws",
    "quorum-run",
  ]
}

data "archive_file" "src" {
  for_each    = toset(local.owned_lambdas)
  type        = "zip"
  source_dir  = "${var.lambdas_src_dir}/${each.value}"
  output_path = "${path.module}/.src-archives/${each.value}.zip"
  excludes    = ["__pycache__", "requirements.txt"]
}

locals {
  package = {
    for name in local.owned_lambdas : name => {
      filename = fileexists("${var.build_dir}/${name}.zip") ? "${var.build_dir}/${name}.zip" : data.archive_file.src[name].output_path
      hash     = fileexists("${var.build_dir}/${name}.zip") ? filebase64sha256("${var.build_dir}/${name}.zip") : data.archive_file.src[name].output_base64sha256
    }
  }
}
