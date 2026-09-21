"""Terraform package generator — turn a BoM + ecosystem config into a self-contained,
customer-deployable Terraform package (self-serve / Model 1).

Layout produced:
    main.tf / variables.tf / versions.tf / terraform.tfvars   Terraform root
    asl.json                                                  compiled Step Functions (orchestrated)
    bom.json                                                  the Bill of Materials
    ecosystem.yaml                                            the source definition
    modules/eco-lite/                                         vendored single-eco module (Slice 2)
    runtime/ + build-and-push-runtime.sh                      AgentCore runtime image build
    README.md                                                 deploy runbook (Model 1)

Ported from Tessera-Org and adapted to this project's layout. Model 2 (delegated deploy)
and the built /solo web UI are intentionally NOT emitted here — this is the source-package,
self-serve model. When the eco-lite module isn't present yet, a PENDING placeholder is
written instead of failing, so the generator (and its tests) run offline.
"""
from __future__ import annotations

import json
import re
import shutil
from pathlib import Path

from .bom import BoM

_APP_ROOT = Path(__file__).resolve().parents[2]                 # .../app
_MODULE_SRC = _APP_ROOT.parent / "infra" / "modules" / "eco-lite"   # created in Slice 2
_RUNTIME_SRC = _APP_ROOT / "agent"                              # AgentCore runtime source
_SAVE_ECOSYSTEM = _APP_ROOT / "lambdas" / "save-ecosystem" / "handler.py"
_IDENT_OK = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def compile_asl(config: dict, eco_id: str, save_ecosystem_path=None) -> dict:
    """Compile the ecosystem to Step Functions ASL using the SAME compiler the platform
    uses (save-ecosystem::compile_to_asl), loaded via importlib. Emits placeholder ARN
    tokens that the eco-lite module substitutes at apply.

    save_ecosystem_path overrides the repo location of save-ecosystem/handler.py. A Lambda
    (no repo tree) fetches that file as an export input and passes its /tmp path (Option A).
    """
    import importlib.util
    import os

    path = str(save_ecosystem_path) if save_ecosystem_path else str(_SAVE_ECOSYSTEM)
    os.environ.setdefault("ECOSYSTEMS_BUCKET", "export-placeholder")
    os.environ.setdefault("AWS_DEFAULT_REGION", "us-east-1")
    # save-ecosystem/handler.py reads the lambda ARNs from these env vars AT IMPORT and
    # bakes them straight into the ASL's FunctionName fields. For an export we don't know
    # the target account's ARNs, so we set them to the PLACEHOLDER TOKENS that the eco-lite
    # module substitutes (via replace()) at apply against the deployed eco's own functions.
    # Without this the vars default to "" → "FunctionName": "" in the ASL → every task fails
    # at run time with "Lambda.SdkClientException: FunctionName cannot be empty". Set BEFORE
    # exec_module because the compiler binds these at import, not at call time.
    os.environ["AGENT_INVOKER_ARN"] = "__AGENT_INVOKER_ARN__"
    os.environ["GROUNDING_CHECK_ARN"] = "__GROUNDING_CHECK_ARN__"
    # external-grounding (web-search enrichment) is NOT shipped in the eco-lite closure, so
    # the deploy has no such lambda. The compiler gates it on bool(EXTERNAL_GROUNDING_ARN)
    # (handler line ~1170), so we leave it EMPTY on purpose: a non-empty token would make the
    # compiler emit External_* tasks whose FunctionName then substitutes to "" at apply
    # (eco-lite has no external-grounding fn) → the run fails with "FunctionName cannot be
    # empty" on every External_* state. Empty here => no External_* tasks are emitted; the
    # run still gets INTERNAL grounding (grounding-check). If external grounding is ever added
    # to the eco-lite closure, switch this back to the "__EXTERNAL_GROUNDING_ARN__" token.
    os.environ["EXTERNAL_GROUNDING_ARN"] = ""
    spec = importlib.util.spec_from_file_location("_tessera_save_ecosystem_export", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.compile_to_asl(config, eco_id)


def _hcl(value, indent: int = 0) -> str:
    """Render a Python value as idiomatic HCL."""
    pad = "  " * indent
    pad1 = "  " * (indent + 1)
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return str(value)
    if value is None:
        return "null"
    if isinstance(value, str):
        return json.dumps(value)  # JSON string == valid HCL string (handles escaping)
    if isinstance(value, (list, tuple)):
        if not value:
            return "[]"
        items = ",\n".join(f"{pad1}{_hcl(v, indent + 1)}" for v in value)
        return "[\n" + items + f"\n{pad}]"
    if isinstance(value, dict):
        if not value:
            return "{}"
        lines = []
        for k, v in value.items():
            key = k if _IDENT_OK.match(str(k)) else json.dumps(str(k))
            lines.append(f"{pad1}{key} = {_hcl(v, indent + 1)}")
        return "{\n" + "\n".join(lines) + f"\n{pad}}}"
    raise TypeError(f"cannot render {type(value)} as HCL")


def _tfvars(bom: BoM) -> str:
    model_ids = {role: v["default"] for role, v in bom.models.roles.items()}
    lambdas = {name: {"s3_key": f"lambdas/{name}.zip"} for name in bom.lambdas.ships}
    lines = [
        "# terraform.tfvars — rendered from the ecosystem Bill of Materials.",
        "# BoM-derived values (do not hand-edit); customer infra is left as TODO below.",
        "",
        f"eco_id      = {_hcl(bom.eco_id)}",
        f"eco_version = {_hcl(bom.version or '0.0.0')}",
        f"region      = {_hcl(bom.region_gate.target)}",
        f"strategy    = {_hcl(bom.strategy)}",
        "",
        f"model_ids = {_hcl(model_ids)}",
        "",
        f"lambdas = {_hcl(lambdas)}",
        "",
        f"business_tools    = {_hcl(bom.tools.business)}",
        f"grounding_enabled = {_hcl(bool(bom.quality.get('grounding')))}",
        f"provision_web_search = {_hcl('web_search' in bom.tools.platform_inline)}",
        "",
        "# ── Infra provenance — DEFAULT: create dedicated infra (deployable in a bare account).",
        "#    To reference an existing landing zone instead, flip a toggle off + supply the id.",
        "create_networking       = true    # false → set vpc_id + private_subnet_ids",
        "create_kms              = true    # false → set kms_key_arn",
        "create_artifacts_bucket = true    # false → set artifacts_bucket",
        "create_ecr              = true    # runtime image repo (build-and-push script pushes here)",
        "# vpc_id                = \"\"",
        "# private_subnet_ids    = []",
        "# kms_key_arn           = \"\"",
        "# artifacts_bucket      = \"\"",
        "# runtime_image_uri     = \"\"   # set after build-and-push-runtime.sh (or leave blank pre-plan)",
        "",
        "# ── Access + misc ──",
        "# allowed_groups          = [\"ecosystem-users\"]   # default restrictive",
        "# allow_all_authenticated = false                 # explicit opt-in only",
        "# tool_secret_arns        = {}",
        "# federated_idp           = { enabled = false }",
        "# tags                    = { CostCenter = \"...\", Environment = \"...\" }",
        "",
        "# ── Web search (REQUIRED only if provision_web_search = true) ──",
        "#   You (the exporter) supply your OWN web-search API key — Tessera ships none.",
        '# websearch_api_key = "<your-web-search-api-key>"',
        "",
        "# ── Teardown posture — safe prod defaults shown ──",
        "# force_destroy_data          = false      # true only for demo/dev teardown",
        "# cognito_deletion_protection = \"ACTIVE\"   # blocks accidental pool deletion in prod",
        "# log_retention_days          = 30",
        "",
    ]
    return "\n".join(lines)


_ROOT_VARIABLES = '''// root variable declarations. BoM-derived values arrive via terraform.tfvars;
// customer-supplied values are filled at deploy time (validate does not require them).

variable "eco_id" { type = string }
variable "eco_version" { type = string }
variable "region" { type = string }
variable "strategy" { type = string }
variable "model_ids" { type = map(string) }

variable "lambdas" {
  type = map(object({
    s3_key  = string
    handler = optional(string, "handler.handler")
    timeout = optional(number, 120)
    memory  = optional(number, 512)
  }))
}

variable "business_tools" {
  type    = list(string)
  default = []
}
variable "grounding_enabled" {
  type    = bool
  default = false
}

// infra provenance: create dedicated infra, or reference existing ids (create-or-reference)
variable "create_networking" {
  type    = bool
  default = true
}
variable "vpc_cidr" {
  type    = string
  default = "10.20.0.0/16"
}
variable "vpc_id" {
  type    = string
  default = ""
}
variable "private_subnet_ids" {
  type    = list(string)
  default = []
}
variable "create_kms" {
  type    = bool
  default = true
}
variable "kms_key_arn" {
  type    = string
  default = ""
}
variable "create_artifacts_bucket" {
  type    = bool
  default = true
}
variable "artifacts_bucket" {
  type    = string
  default = ""
}
variable "create_ecr" {
  type    = bool
  default = true
}
variable "runtime_image_uri" {
  type    = string
  default = ""
}
// web_search (real credential provider; customer supplies the key — see README)
variable "provision_web_search" {
  type    = bool
  default = false
}
variable "websearch_api_key" {
  type      = string
  default   = "REPLACE_ME_SEE_README"
  sensitive = true
}
variable "websearch_api_url" {
  type    = string
  default = "https://api.tavily.com/search"
}

variable "tool_secret_arns" {
  type      = map(string)
  default   = {}
  sensitive = true
}

variable "federated_idp" {
  type = object({
    enabled       = bool
    provider_name = optional(string, "CorporateSSO")
    oidc_issuer   = optional(string, "")
    client_id     = optional(string, "")
    secret_arn    = optional(string, "")
  })
  default = { enabled = false }
}

variable "allowed_groups" {
  type    = list(string)
  default = []
}
variable "allow_all_authenticated" {
  type    = bool
  default = false
}
variable "tags" {
  type    = map(string)
  default = {}
}

// teardown / decommissioning
variable "force_destroy_data" {
  type    = bool
  default = false
}
variable "log_retention_days" {
  type    = number
  default = 30
}
variable "cognito_deletion_protection" {
  type    = string
  default = "INACTIVE"
}
'''

_ROOT_VERSIONS = '''terraform {
  required_version = ">= 1.6.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = ">= 5.60.0"
    }
    awscc = {
      source  = "hashicorp/awscc"
      version = ">= 1.0.0"
    }
  }

  // Backend-agnostic: wire YOUR state backend here, e.g.
  //   backend "s3" {}            # + -backend-config on init
  // Intentionally left unset.
}

provider "aws" {
  region = var.region
}

provider "awscc" {
  region = var.region
}
'''

_ROOT_MAIN = '''// root — vendors the eco-lite module and wires BoM + customer inputs.

module "eco" {
  source = "./modules/eco-lite"

  eco_id                   = var.eco_id
  eco_version              = var.eco_version
  region                   = var.region
  strategy                 = var.strategy
  state_machine_definition = try(file("${path.module}/asl.json"), "")

  model_ids         = var.model_ids
  lambdas           = var.lambdas
  business_tools    = var.business_tools
  grounding_enabled = var.grounding_enabled
  artifacts_bucket  = var.artifacts_bucket
  runtime_image_uri = var.runtime_image_uri

  # infra provenance (create-or-reference)
  create_networking       = var.create_networking
  vpc_cidr                = var.vpc_cidr
  create_kms              = var.create_kms
  create_artifacts_bucket = var.create_artifacts_bucket
  create_ecr              = var.create_ecr

  # web_search credential provider (customer supplies the key)
  provision_web_search = var.provision_web_search
  websearch_api_key    = var.websearch_api_key
  websearch_api_url    = var.websearch_api_url

  vpc_id             = var.vpc_id
  private_subnet_ids = var.private_subnet_ids
  kms_key_arn        = var.kms_key_arn

  tool_secret_arns        = var.tool_secret_arns
  federated_idp           = var.federated_idp
  allowed_groups          = var.allowed_groups
  allow_all_authenticated = var.allow_all_authenticated
  tags                    = var.tags

  # teardown / decommissioning
  force_destroy_data          = var.force_destroy_data
  log_retention_days          = var.log_retention_days
  cognito_deletion_protection = var.cognito_deletion_protection
}

output "runs_bucket" { value = module.eco.runs_bucket }
output "cognito_user_pool_id" { value = module.eco.cognito_user_pool_id }
output "cognito_client_id" { value = module.eco.cognito_client_id }
output "agent_runtime_id" { value = module.eco.agent_runtime_id }
output "state_machine_arn" { value = module.eco.state_machine_arn }
output "ecr_repository_url" { value = module.eco.ecr_repository_url }

# customer-facing endpoints (what you hand to end users / wire into CI)
output "frontend_url" { value = module.eco.frontend_url }
output "rest_api_url" { value = module.eco.rest_api_url }
output "ws_url" { value = module.eco.ws_url }
output "cognito_domain" { value = module.eco.cognito_domain }

# resolved infra provenance (created or referenced)
output "vpc_id" { value = module.eco.vpc_id }
output "kms_key_arn" { value = module.eco.kms_key_arn }
'''


def _readme(bom: BoM) -> str:
    uses_web_search = "web_search" in bom.tools.platform_inline
    if uses_web_search:
        ws_note = (
            "This ecosystem's agents use the **`web_search`** tool to do live research during a run.\n"
            "\n"
            "**What the key is:** `websearch_api_key` is a **third-party web-search API key that YOU "
            "provide** — it is **NOT an AWS credential**. By default the runtime calls "
            "**[Tavily](https://tavily.com)** (`websearch_api_url` defaults to "
            "`https://api.tavily.com/search`), which offers a free tier. Tessera ships no key of its own.\n"
            "\n"
            "**How to get one:** sign up at https://tavily.com, create an API key (it looks like "
            "`tvly-xxxxxxxxxxxxxxxx`), and use it below. On `apply`, the key is stored in an AgentCore "
            "API-key credential provider (backed by AWS Secrets Manager) and read by the runtime at run "
            "time. The `websearch_api_key` variable is `sensitive` and write-only — passing it via `-var` "
            "keeps it OUT of the Terraform state file.\n"
            "\n"
            "**Provide it** either inline on the final apply:\n"
            "```bash\n"
            "terraform apply -var websearch_api_key=tvly-xxxxxxxxxxxxxxxx -auto-approve\n"
            "```\n"
            "or by uncommenting `websearch_api_key` in `terraform.tfvars`.\n"
            "\n"
            "**Don't have a key / don't want web search yet?** Set `provision_web_search = false` in "
            "`terraform.tfvars` and no key is needed (`terraform apply -auto-approve`). The ecosystem "
            "still runs — its agents just won't verify facts against the live web (assumptions stay "
            "unverified instead of grounded). You can flip it back on and re-apply with a key later.\n"
            "\n"
            "**Using a different search provider?** Override `websearch_api_url`, but the request/response "
            "shape and key format follow Tavily's API, so Tavily is the path of least resistance."
        )
    else:
        ws_note = "This ecosystem does not use `web_search`; no web-search key is needed."
    region_warn = (
        " ⚠ Web Search is NOT available in this region — supply a customer-provided search key/tool."
        if "agentcore-web-search" in bom.region_gate.unavailable else ""
    )
    return f"""# {bom.eco_id} — standalone deployment package (v{bom.version or '1.0'})

A **self-contained, customer-deployable** package for the **{bom.eco_id}** ecosystem, exported
from Tessera. It contains ONLY this ecosystem's run-plane + a purpose-built single-ecosystem UI
— **none of the Tessera platform** (no builder, catalog, admin, or other ecosystems). Deploy it
into your own AWS account with Terraform. **You** run the deploy — nothing of Tessera's runs in
your account.

---

## What's in this package
```
main.tf, variables.tf, versions.tf, terraform.tfvars   Terraform root (vendors the module)
modules/eco-lite/                                       the eco-lite Terraform module
asl.json                                                compiled Step Functions definition
ecosystem.yaml                                          this ecosystem's definition
lambdas/*.zip                                           prebuilt run-plane Lambda functions (arm64)
layer/shared-layer.zip                                  the shared Lambda layer
frontend/                                               the single-ecosystem web UI (built static site)
runtime/ + build-and-push-runtime.sh + Dockerfile       AgentCore runtime image (build + push)
README.md                                               this file
```
Everything except the runtime container image is prebuilt and shipped in this package — Terraform
uploads the Lambda zips, layer, and web UI to your account on `apply`. The ONE thing Terraform
can't build is the runtime container image (AgentCore Runtime is container-based), so there is a
single `docker build` + push step (step 2 below). All steps are listed explicitly.

## Prerequisites
1. **AWS account + credentials** for the target account, exported as `AWS_PROFILE` / `AWS_REGION`
   (verify with `aws sts get-caller-identity`). The credentials need permission to create IAM,
   Lambda, API Gateway, S3, Step Functions, Cognito, ECR, and Bedrock AgentCore resources.
2. **Amazon Bedrock model access** enabled in region **{bom.region_gate.target}** for the models
   this ecosystem pins (see `terraform.tfvars` → `model_ids`), plus **Bedrock AgentCore**
   available in that region.{region_warn}
3. **Terraform ≥ 1.6**.
4. **AWS CLI v2**.
5. **Docker installed AND running** — required for the one runtime-image build step (step 2).
   Verify with `docker info` (it must succeed, not just `docker --version`).{(chr(10) + "6. **A web-search API key** — this ecosystem uses `web_search`. See the **Web search** section below; you'll pass it on step 3 (or set `provision_web_search = false` to skip).") if uses_web_search else ""}

---

## Deploy (self-serve)

Deploy in **this order** — it is staged on purpose. The AgentCore runtime resource requires an
ECR image URI, which doesn't exist until you build + push the image. So you first create *only*
the ECR repo (step 1, using `-target`), then build + push the image (step 2, which writes
`image.auto.tfvars` setting `runtime_image_uri`), then apply everything (step 3). Running a plain
`terraform plan`/`apply` before step 2 will fail with a `container_uri` validation error — that
is expected; just follow the steps in order.

```bash
export AWS_PROFILE=<your-profile> AWS_REGION={bom.region_gate.target}

terraform init
terraform apply -auto-approve -target=module.eco.aws_ecr_repository.runtime   # 1) create ECR repo
bash build-and-push-runtime.sh                                                # 2) build+push runtime image
terraform apply {"-var websearch_api_key=<YOUR_KEY> " if uses_web_search else ""}-auto-approve            # 3) deploy everything
```
{"> Step 3 needs your web-search API key — see the **Web search** section below for what it is and how to get one (or how to skip it)." if uses_web_search else ""}

Then create your first user and open the UI (`terraform output frontend_url`):
```bash
POOL=$(terraform output -raw cognito_user_pool_id)
aws cognito-idp admin-create-user  --user-pool-id "$POOL" --username you@example.com \\
  --message-action SUPPRESS --user-attributes Name=email,Value=you@example.com Name=email_verified,Value=true
aws cognito-idp admin-set-user-password --user-pool-id "$POOL" --username you@example.com \\
  --password '<StrongPassw0rd!>' --permanent
```

## Web search
{ws_note}

## Auth & access
- Cognito direct login is always available (a public app-client, USER_PASSWORD_AUTH).
- Restrict access with `allowed_groups` (default restrictive) or set
  `allow_all_authenticated = true` for an open pilot.

## Attach to an existing landing zone (optional)
Create-mode is the default (own VPC/KMS/artifacts bucket/ECR). To attach to existing infra,
set the `create_*` toggles to `false` in `terraform.tfvars` and supply the ids.

## Teardown
```bash
terraform destroy -var force_destroy_data=true -var cognito_deletion_protection=INACTIVE
```
- **Back up first:** the runs bucket + AgentCore Memory hold run transcripts/deliverables.
- Prod defaults make destroy **refuse** to delete non-empty data / protected pool until flipped.
"""


_RUNTIME_BUILD_SH = r'''#!/usr/bin/env bash
# build-and-push-runtime.sh — build the AgentCore runtime image from the vendored source
# (runtime/Dockerfile) and push it to this ecosystem's ECR repo, then write image.auto.tfvars
# so the next `terraform apply` wires runtime_image_uri automatically.
set -Eeuo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
REGION="${AWS_REGION:-us-east-1}"
ECR_REPO_NAME="__ECR_REPO_NAME__"

REPO_URL="$(aws ecr describe-repositories --region "$REGION" \
  --repository-names "$ECR_REPO_NAME" --query 'repositories[0].repositoryUri' --output text 2>/dev/null || true)"
if [ -z "$REPO_URL" ] || [ "$REPO_URL" = "None" ]; then
  echo "ECR repo '$ECR_REPO_NAME' not found — create it first:" >&2
  echo "  terraform apply -target=module.eco.aws_ecr_repository.runtime" >&2
  exit 1
fi
REGISTRY="${REPO_URL%%/*}"
echo "→ pushing to $REPO_URL"
aws ecr get-login-password --region "$REGION" | docker login --username AWS --password-stdin "$REGISTRY"

cd "$HERE/runtime"
[ -f Dockerfile ] || { echo "no runtime/Dockerfile to build" >&2; exit 1; }
TAG="$(date +%Y%m%d%H%M%S)"
docker build --platform linux/arm64 -t "$REPO_URL:$TAG" .
docker push "$REPO_URL:$TAG"
DIGEST="$(aws ecr describe-images --region "$REGION" --repository-name "$ECR_REPO_NAME" \
  --image-ids imageTag="$TAG" --query 'imageDetails[0].imageDigest' --output text)"
printf 'runtime_image_uri = "%s@%s"\n' "$REPO_URL" "$DIGEST" > "$HERE/image.auto.tfvars"
echo "✓ wrote image.auto.tfvars → ${REPO_URL}@${DIGEST}"
'''


def _vendor_module(out: Path, module_src: Path | None = None) -> None:
    """Vendor the eco-lite Terraform module. When module_src is given (e.g. a Lambda that
    fetched the module to /tmp) it is used instead of the repo path. If neither exists,
    write a PENDING placeholder so the generator still produces a package."""
    src = Path(module_src) if module_src else _MODULE_SRC
    dst = out / "modules" / "eco-lite"
    if src.exists():
        shutil.copytree(src, dst)
    else:
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "PENDING.txt").write_text(
            "The eco-lite Terraform module has not been vendored into this package.\n"
            "It is authored in a later slice of the export feature. Until then this\n"
            "package's Terraform root references ./modules/eco-lite but the module body\n"
            "is not present — `terraform init` will fail here by design.\n"
        )


def _vendor_runtime(bom: BoM, out: Path, runtime_src: Path | None = None) -> None:
    """Vendor the AgentCore runtime source + emit the build-and-push script. runtime_src
    overrides the repo path (e.g. a Lambda that fetched the runtime to /tmp)."""
    src = Path(runtime_src) if runtime_src else _RUNTIME_SRC
    dst = out / "runtime"
    if dst.exists():
        shutil.rmtree(dst)
    if src.exists():
        shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
            "__pycache__", "*.pyc", ".bedrock_agentcore", ".venv", "*.egg-info"))
    else:
        dst.mkdir(parents=True, exist_ok=True)
        (dst / "PENDING.txt").write_text("AgentCore runtime source (app/agent) not found at export time.\n")
    script = out / "build-and-push-runtime.sh"
    ecr_repo_name = f"tessera-{bom.eco_id}-runtime"
    script.write_text(_RUNTIME_BUILD_SH.replace("__ECR_REPO_NAME__", ecr_repo_name))
    script.chmod(0o755)


def _lay_lambdas(bom: BoM, out: Path, lambdas_src: Path | None) -> list[str]:
    """Lay one prebuilt Lambda zip per BoM-shipped Lambda into out/lambdas/<name>.zip.

    The zips are eco-agnostic and prebuilt by `make package` (arm64-compatible wheels),
    published to export-inputs/ and fetched by the export Lambda (Option A). lambdas_src is
    the unpacked dir of prebuilt <name>.zip files. Returns the names actually laid; a missing
    zip is skipped (the package is then flagged incomplete by the caller)."""
    if not lambdas_src:
        return []
    lambdas_src = Path(lambdas_src)
    lam_out = out / "lambdas"
    lam_out.mkdir(parents=True, exist_ok=True)
    laid: list[str] = []
    for name in bom.lambdas.ships:
        z = lambdas_src / f"{name}.zip"
        if z.exists():
            shutil.copy(z, lam_out / f"{name}.zip")
            laid.append(name)
        else:
            print(f"[export] WARNING: prebuilt lambda zip missing for '{name}' ({z})")
    return laid


def _lay_layer(out: Path, layer_src: Path | None) -> bool:
    """Lay the prebuilt shared-layer zip into out/layer/shared-layer.zip. layer_src is the
    path to the prebuilt shared_layer.zip (from make package). Returns True if laid."""
    if not layer_src:
        return False
    layer_src = Path(layer_src)
    if not layer_src.exists():
        print(f"[export] WARNING: prebuilt shared layer missing ({layer_src})")
        return False
    ldir = out / "layer"
    ldir.mkdir(parents=True, exist_ok=True)
    shutil.copy(layer_src, ldir / "shared-layer.zip")
    return True


def _lay_frontend(out: Path, frontend_src: Path | None) -> bool:
    """Lay the prebuilt single-eco frontend static site into out/frontend/. frontend_src is
    the unpacked dir of the built /solo export (index.html + _next + assets). Returns True if
    laid. The eco-lite frontend.tf uploads out/frontend/** to the web bucket at apply."""
    if not frontend_src:
        return False
    frontend_src = Path(frontend_src)
    if not frontend_src.exists() or not any(frontend_src.iterdir()):
        print(f"[export] WARNING: prebuilt frontend missing/empty ({frontend_src})")
        return False
    dst = out / "frontend"
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(frontend_src, dst)
    return True


def generate(bom: BoM, out_dir, *, asl: dict | None = None,
             build_artifacts: bool = False, eco_yaml_path=None,
             module_src=None, runtime_src=None,
             lambdas_src=None, layer_src=None, frontend_src=None) -> Path:
    """Render the Terraform package for one ecosystem into out_dir.

    Source-package, offline. build_artifacts is accepted for API parity but the real
    Lambda-zip / shared-layer / web-UI builds (Docker + Node) are NOT performed here —
    the package ships source + build scripts (self-serve). No Model 2 artifacts.

    module_src / runtime_src override the repo paths for the vendored eco-lite module and
    AgentCore runtime. A Lambda invocation (which has no repo tree) fetches those inputs to
    /tmp and passes them here (Option A).
    """
    out = Path(out_dir)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)

    (out / "bom.json").write_text(json.dumps(bom.to_dict(), indent=2, default=str))
    (out / "terraform.tfvars").write_text(_tfvars(bom))
    (out / "variables.tf").write_text(_ROOT_VARIABLES)
    (out / "versions.tf").write_text(_ROOT_VERSIONS)
    (out / "main.tf").write_text(_ROOT_MAIN)
    (out / "README.md").write_text(_readme(bom))

    if asl and isinstance(asl.get("States"), dict):
        (out / "asl.json").write_text(json.dumps(asl, indent=2))
    else:
        (out / "asl.json").write_text("")  # quorum/choreography: no compiled ASL

    _vendor_module(out, module_src)
    _vendor_runtime(bom, out, runtime_src)

    # Lay the prebuilt, eco-agnostic deployment artifacts into the package so it is directly
    # deployable: the run-plane Lambda zips (lambdas/<name>.zip), the shared layer
    # (layer/shared-layer.zip), and the single-eco frontend static site (frontend/). These are
    # built once by `make package` / the frontend build and fetched from export-inputs/ by the
    # export Lambda (Option A) — the eco-lite module uploads them via aws_s3_object at apply.
    _lay_lambdas(bom, out, lambdas_src)
    _lay_layer(out, layer_src)
    _lay_frontend(out, frontend_src)

    if eco_yaml_path and Path(eco_yaml_path).exists():
        shutil.copy(eco_yaml_path, out / "ecosystem.yaml")

    # Canonicalize HCL formatting (best-effort; no-op if terraform isn't installed).
    # Static command list only: a literal executable name ("terraform") and fixed flags,
    # with the sole dynamic value being the output directory this function itself built.
    # No shell=True and no external/user input, so there is no command-injection surface.
    if shutil.which("terraform"):
        import subprocess

        subprocess.run(  # nosec B603,B607
            ["terraform", "fmt", "-recursive", str(out)],
            check=False,
            capture_output=True,
        )

    return out
