"""Productized ecosystem export API.

POST /api/ecosystems/{id}/export  ->  runs the export toolchain (BoM analyze + Terraform
generate) on the saved ecosystem YAML, zips the resulting self-contained Terraform package
(root + vendored eco-lite module + runtime source + build scripts + README), uploads it to
the export bucket, and returns a presigned download URL (valid 1h). The package is a
self-serve (Model 1) source package — the customer runs `terraform apply` themselves.

Option A (inputs from S3): a Lambda has no repo tree, so the eco-lite Terraform module and
the AgentCore runtime source are published (as .zip) to the ecosystems bucket at deploy time
under export-inputs/. This handler fetches + unpacks them to /tmp and passes their paths to
the generator (module_src / runtime_src).
"""
import json
import os
import re
import sys
import time
import zipfile
from pathlib import Path

import boto3

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from auth import require_auth, is_admin

from tessera_platform.export import bom as _bom
from tessera_platform.export import generate as _gen

ECOSYSTEMS_BUCKET = os.environ["ECOSYSTEMS_BUCKET"]
EXPORT_BUCKET = os.environ["EXPORT_BUCKET"]
DEFAULT_REGION = os.environ.get("EXPORT_REGION") or os.environ.get("AWS_REGION", "us-east-1")
# S3 keys of the zipped export inputs (published at deploy time).
INPUTS_PREFIX = os.environ.get("EXPORT_INPUTS_PREFIX", "export-inputs").rstrip("/")
# How long the presigned download URL is valid (seconds).
PRESIGN_EXPIRY = int(os.environ.get("EXPORT_PRESIGN_EXPIRY", "3600"))

s3 = boto3.client("s3")


def _resp(body, status=200):
    return {
        "statusCode": status,
        "headers": {
            "Content-Type": "application/json",
            "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
        },
        "body": json.dumps(body, default=str),
    }


def _fetch_input(key_stem: str, dest_dir: Path) -> Path | None:
    """Fetch export-inputs/<key_stem>.zip from the ecosystems bucket and unpack it into
    dest_dir. Returns the unpacked directory, or None if the input isn't published."""
    key = f"{INPUTS_PREFIX}/{key_stem}.zip"
    zpath = dest_dir.parent / f"{key_stem}.zip"
    try:
        s3.download_file(ECOSYSTEMS_BUCKET, key, str(zpath))
    except Exception as e:
        print(f"[export] input '{key}' not available: {type(e).__name__}: {e}")
        return None
    dest_dir.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(zpath) as zf:
        zf.extractall(dest_dir)
    return dest_dir


def _zipdir(base: Path, zpath: Path) -> None:
    with zipfile.ZipFile(zpath, "w", zipfile.ZIP_DEFLATED) as zf:
        for f in base.rglob("*"):
            if f.is_file():
                zf.write(f, f.relative_to(base))


def handler(event, context):
    eco_id = (event.get("pathParameters") or {}).get("id")
    if not eco_id:
        return _resp({"error": "Missing ecosystem id"}, 400)

    try:
        user = require_auth(event)
    except PermissionError:
        return _resp({"error": "Unauthorized"}, 401)

    # Load the saved ecosystem YAML.
    try:
        raw = s3.get_object(Bucket=ECOSYSTEMS_BUCKET, Key=f"ecosystems/{eco_id}.yaml")["Body"].read()
        import yaml
        config = yaml.safe_load(raw) or {}
    except Exception as e:
        return _resp({"error": f"Ecosystem '{eco_id}' not found: {e}"}, 404)

    if not isinstance(config, dict) or not config.get("agents"):
        return _resp({"error": f"Ecosystem '{eco_id}' has no agents / is not exportable"}, 400)

    # Export access. Default: OPEN to any authenticated user (EXPORT_REQUIRE_OWNER unset/false),
    # per current product decision — export produces a read-only source package, it doesn't
    # mutate the ecosystem. Set EXPORT_REQUIRE_OWNER=true to restrict exporting to the
    # ecosystem's owner or an admin (the earlier behavior) if we tighten this later.
    if os.environ.get("EXPORT_REQUIRE_OWNER", "").strip().lower() in ("1", "true", "yes", "on"):
        owner = config.get("owner", "")
        if owner and owner != user["user_id"] and not is_admin(user):
            return _resp({"error": "Only the owner or an admin can export this ecosystem"}, 403)

    region = DEFAULT_REGION
    work = Path("/tmp") / f"export-{eco_id}-{int(time.time())}"
    work.mkdir(parents=True, exist_ok=True)

    # Fetch the export inputs (Option A). If unavailable, the generator falls back to a
    # PENDING placeholder for that piece (the package still generates, flagged incomplete).
    module_src = _fetch_input("eco-lite", work / "eco-lite")
    runtime_src = _fetch_input("agent-runtime", work / "agent-runtime")
    # save-ecosystem/handler.py holds the ASL compiler — fetched as a file input so the
    # exported orchestrated eco gets its compiled Step Functions definition.
    save_eco_dir = _fetch_input("save-ecosystem", work / "save-ecosystem")
    save_eco_path = (save_eco_dir / "handler.py") if save_eco_dir else None

    # Prebuilt, eco-agnostic deployment artifacts (published once by `make package` + the
    # frontend build via seed.sh). The package ships these so it is directly deployable:
    #   lambdas.zip   → dir of per-lambda <name>.zip (arm64-compatible)
    #   layer.zip     → dir containing shared-layer.zip
    #   frontend.zip  → dir of the built single-eco /solo static site
    lambdas_dir = _fetch_input("lambdas", work / "lambdas-src")
    layer_dir = _fetch_input("layer", work / "layer-src")
    layer_path = (layer_dir / "shared-layer.zip") if layer_dir else None
    frontend_dir = _fetch_input("frontend", work / "frontend-src")

    # BoM + ASL compile (best-effort ASL for orchestrated ecos).
    bom = _bom.analyze(config, region=region)
    asl = None
    if bom.strategy == "orchestrated" and save_eco_path and save_eco_path.exists():
        try:
            asl = _gen.compile_asl(config, eco_id, save_ecosystem_path=save_eco_path)
        except Exception as e:
            print(f"[export] ASL compile failed (continuing without ASL): {type(e).__name__}: {e}")
    elif bom.strategy == "orchestrated":
        print("[export] save-ecosystem compiler input not available — exporting without compiled ASL")

    # Write the eco YAML to a temp file for vendoring into the package.
    eco_yaml_path = work / "ecosystem.yaml"
    eco_yaml_path.write_bytes(raw)

    pkg = work / "pkg"
    try:
        _gen.generate(
            bom, pkg, asl=asl, build_artifacts=False, eco_yaml_path=eco_yaml_path,
            module_src=module_src, runtime_src=runtime_src,
            lambdas_src=lambdas_dir, layer_src=layer_path, frontend_src=frontend_dir,
        )
    except Exception as e:
        return _resp({"error": f"Package generation failed: {type(e).__name__}: {e}"}, 500)

    # A package is COMPLETE (directly deployable) only when every artifact the eco-lite module
    # reads at apply is present: the module, the runtime source, the compiled ASL (orchestrated
    # ecos), the prebuilt Lambda zips, the shared layer, and the frontend static site.
    complete = (
        module_src is not None
        and runtime_src is not None
        and lambdas_dir is not None
        and layer_path is not None and layer_path.exists()
        and frontend_dir is not None
        and (bom.strategy != "orchestrated" or asl is not None)
    )

    # Zip + upload to the export bucket (lifecycle-expired after 7 days).
    zpath = work / f"{eco_id}.zip"
    _zipdir(pkg, zpath)
    ts = time.strftime("%Y%m%d%H%M%S", time.gmtime())
    # Human-readable, self-describing download name: tessera-<eco>-v<version>-<ts>.zip.
    # Previously the browser named the download from the S3 key's last segment (just the
    # timestamp, e.g. 20260828121609.zip) — impossible to tell which ecosystem it was.
    # We now (a) embed the eco (+ version) in the S3 KEY so the stored object is meaningful,
    # and (b) set Content-Disposition on the presigned URL so the BROWSER uses this name
    # regardless of the key. Slugify version (may contain dots) for a safe filename.
    _ver = re.sub(r"[^A-Za-z0-9._-]", "-", str(bom.version or "0.0.0"))
    download_name = f"tessera-{eco_id}-v{_ver}-{ts}.zip"
    key = f"exports/{user['user_id']}/{eco_id}/{download_name}"
    try:
        s3.upload_file(str(zpath), EXPORT_BUCKET, key)
    except Exception as e:
        return _resp({"error": f"Upload failed: {type(e).__name__}: {e}"}, 500)

    try:
        url = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": EXPORT_BUCKET,
                "Key": key,
                "ResponseContentDisposition": f'attachment; filename="{download_name}"',
            },
            ExpiresIn=PRESIGN_EXPIRY,
        )
    except Exception as e:
        return _resp({"error": f"Presign failed: {type(e).__name__}: {e}"}, 500)

    return _resp({
        "ecosystem_id": eco_id,
        "version": bom.version,
        "region": region,
        "download_url": url,
        "key": key,
        "bytes": zpath.stat().st_size,
        "complete": complete,
        "note": (
            "Complete source package." if complete
            else "Partial package — export inputs (eco-lite module / runtime) were not "
                 "available; some sections are placeholders."
        ),
        # The bucket lifecycle deletes exports after 7 days.
        "expires_days": 7,
        "bom": bom.to_dict(),
    })
