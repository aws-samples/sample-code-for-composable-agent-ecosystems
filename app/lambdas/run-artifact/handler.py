"""GET /api/runs/{sessionId}/artifacts/{artifactKey+} — fetch a typed
run artifact from Amazon S3.
artifact produced by an agent run.

Phase 5f.3 (MVP). Returns either:
  - The file CONTENT directly (?inline=true, useful for small text/json/yaml
    artifacts that the frontend wants to display without a second round-trip)
  - A presigned S3 URL (?inline=false, default — works for any size; the
    browser does the actual download)

The artifactKey is a relative path within the run's S3 prefix:
    s3://{RUNS_BUCKET}/runs/{session_id}/{artifactKey}

Examples:
    /api/runs/platform-optimizer-abc123/artifacts/patch_synthesizer/proposed_patch.yaml
    /api/runs/buyerside-def456/artifacts/buyer_brief_composer/output.txt

Path traversal protection: artifactKey is validated to contain no ".."
segments and no leading "/" — strictly relative within the run's prefix.
"""
import json
import os
import re
from typing import Dict

import boto3
from botocore.config import Config

REGION = os.environ.get("AWS_REGION", "us-east-1")
RUNS_BUCKET = os.environ["TESSERA_RUNS_BUCKET"]
PRESIGN_EXPIRY_S = int(os.environ.get("PRESIGN_EXPIRY_S", "3600"))   # 1 hour
INLINE_MAX_BYTES = int(os.environ.get("INLINE_MAX_BYTES", "1048576"))  # 1 MB

# Amazon S3 client
s3 = boto3.client(
    "s3", region_name=REGION,
    config=Config(signature_version="s3v4"),
)


# Map common file extensions to Content-Type. Used both for inline responses
# and for the response-content-type override on presigned URLs (so the
# browser knows what to do with the file).
_CONTENT_TYPES = {
    ".txt":   "text/plain; charset=utf-8",
    ".md":    "text/markdown; charset=utf-8",
    ".json":  "application/json; charset=utf-8",
    ".yaml":  "application/x-yaml; charset=utf-8",
    ".yml":   "application/x-yaml; charset=utf-8",
    ".html":  "text/html; charset=utf-8",
    ".csv":   "text/csv; charset=utf-8",
    ".pdf":   "application/pdf",
    ".docx":  "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".png":   "image/png",
    ".svg":   "image/svg+xml",
    ".jpg":   "image/jpeg",
    ".jpeg":  "image/jpeg",
    ".zip":   "application/zip",
}


def _content_type_for(key: str) -> str:
    for ext, ct in _CONTENT_TYPES.items():
        if key.lower().endswith(ext):
            return ct
    return "application/octet-stream"


def _is_text_inline_safe(key: str) -> bool:
    """Decide whether this file type can be safely returned inline as a
    JSON-string in the response body. Binary types must use presigned URLs."""
    return any(key.lower().endswith(ext) for ext in (
        ".txt", ".md", ".json", ".yaml", ".yml", ".html", ".csv", ".svg",
    ))


# Path-traversal guard. Allow only ASCII alphanumerics, hyphens, underscores,
# slashes, and dots. Reject ".." and absolute paths.
_SAFE_KEY_RE = re.compile(r"^[A-Za-z0-9._/\-]+$")


def _validate_artifact_key(key: str) -> str | None:
    """Return None if valid, otherwise an error message."""
    if not key or not key.strip():
        return "artifactKey is required"
    if key.startswith("/") or key.startswith("\\"):
        return "artifactKey must be a relative path (no leading slash)"
    if not _SAFE_KEY_RE.match(key):
        return "artifactKey contains invalid characters"
    if ".." in key.split("/"):
        return "artifactKey must not contain '..' segments"
    if key.endswith("/"):
        return "artifactKey must point to a file, not a directory"
    return None


def _validate_session_id(session_id: str) -> str | None:
    """Session IDs are <ecosystem-id>-<8hex>; validate basic shape."""
    if not session_id:
        return "sessionId is required"
    if not re.match(r"^[A-Za-z0-9_\-]+$", session_id):
        return "sessionId contains invalid characters"
    if len(session_id) > 200:
        return "sessionId too long"
    return None


# ─── Phase 5f.5: bundle .zip download ───────────────────────────────────────

import io
import zipfile

# Cap individual object size (skip huge files to keep zip generation fast).
# Most agent outputs are well under 10MB; tool_results.json can be larger
# but is also rarely useful in a download bundle.
_BUNDLE_MAX_OBJECT_BYTES = 50 * 1024 * 1024   # 50 MB per file
# Cap total zip size so a runaway run can't OOM the Lambda.
_BUNDLE_MAX_TOTAL_BYTES = 200 * 1024 * 1024   # 200 MB total
# Max objects to include (defensive against weird run prefixes).
_BUNDLE_MAX_OBJECTS = 500


def _bundle_run(session_id: str) -> Dict:
    """Stream the run's S3 prefix into a zip, upload to a bundles/ prefix,
    return a presigned URL.

    Layout inside the zip:
      <session_id>/
        <agent_id>/output.txt
        <agent_id>/tool_results.json
        <agent_id>/<typed_artifact>.<ext>
        ...
    """
    prefix = f"runs/{session_id}/"

    # List all objects under the run's prefix
    objects = []
    total_bytes = 0
    paginator = s3.get_paginator("list_objects_v2")
    try:
        for page in paginator.paginate(Bucket=RUNS_BUCKET, Prefix=prefix):
            for obj in page.get("Contents", []) or []:
                size = int(obj.get("Size", 0))
                if size > _BUNDLE_MAX_OBJECT_BYTES:
                    print(f"bundle: skipping {obj['Key']} ({size} bytes > cap)")
                    continue
                if total_bytes + size > _BUNDLE_MAX_TOTAL_BYTES:
                    print(f"bundle: total cap reached at {total_bytes} bytes")
                    break
                if len(objects) >= _BUNDLE_MAX_OBJECTS:
                    break
                objects.append((obj["Key"], size))
                total_bytes += size
            if len(objects) >= _BUNDLE_MAX_OBJECTS:
                break
    except Exception as e:
        return _response(500, {"error": f"S3 list failed: {e}"})

    if not objects:
        return _response(404, {"error": f"No objects found for session {session_id}"})

    # Build the zip in memory
    buf = io.BytesIO()
    try:
        with zipfile.ZipFile(buf, mode="w", compression=zipfile.ZIP_DEFLATED) as zf:
            for key, _size in objects:
                # Strip the "runs/" prefix so the zip starts at the
                # session_id directory — cleaner for the operator.
                rel_path = key[len("runs/"):] if key.startswith("runs/") else key
                try:
                    obj = s3.get_object(Bucket=RUNS_BUCKET, Key=key)
                    body = obj["Body"].read()
                    zf.writestr(rel_path, body)
                except Exception as e:
                    print(f"bundle: failed to add {key}: {e}")
                    # Skip rather than fail entire bundle
                    continue
            # Small manifest for the operator
            manifest = (
                f"# Tessera run bundle\n"
                f"session_id: {session_id}\n"
                f"objects_included: {len(objects)}\n"
                f"total_uncompressed_bytes: {total_bytes}\n"
                f"generated_at: {context_now_iso()}\n"
            )
            zf.writestr(f"{session_id}/MANIFEST.txt", manifest)
    except Exception as e:
        return _response(500, {"error": f"Zip generation failed: {e}"})

    # Upload to a bundles/ prefix with a short-lived TTL via S3 lifecycle
    # (lifecycle policy is a separate CDK concern; we just write here).
    bundle_key = f"bundles/{session_id}.zip"
    buf.seek(0)
    try:
        s3.put_object(
            Bucket=RUNS_BUCKET,
            Key=bundle_key,
            Body=buf.getvalue(),
            ContentType="application/zip",
        )
    except Exception as e:
        return _response(500, {"error": f"Bundle upload failed: {e}"})

    # Return a presigned URL with download disposition
    try:
        presigned = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": RUNS_BUCKET,
                "Key": bundle_key,
                "ResponseContentType": "application/zip",
                "ResponseContentDisposition": f'attachment; filename="{session_id}.zip"',
            },
            ExpiresIn=PRESIGN_EXPIRY_S,
        )
    except Exception as e:
        return _response(500, {"error": f"Presign failed: {e}"})

    return _response(200, {
        "session_id": session_id,
        "objects_included": len(objects),
        "uncompressed_bytes": total_bytes,
        "compressed_bytes": len(buf.getvalue()),
        "filename": f"{session_id}.zip",
        "download_url": presigned,
        "expires_in_s": PRESIGN_EXPIRY_S,
    })


def context_now_iso() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).isoformat()


def _response(status: int, body: Dict) -> Dict:
    return {
        "statusCode": status,
        "headers": {
            "Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"),
            "Content-Type": "application/json",
        },
        "body": json.dumps(body, default=str),
    }


def _handle_save(event, session_id: str, artifact_key: str) -> Dict:
    """Beautify Phase 2: persist an edited/beautified deliverable.

    Writes a NEW immutable version under `runs/{sid}/{agent}/beautified/` plus a
    `latest-<file>` pointer and a `manifest.json` — the original artifact is never
    overwritten (audit-grade provenance). Body: {content: <html>, provenance}.
    """
    err = _validate_session_id(session_id) or _validate_artifact_key(artifact_key)
    if err:
        return _response(400, {"error": err})
    if not artifact_key.lower().endswith(".html"):
        return _response(400, {"error": "Only .html deliverables can be saved via beautify."})
    try:
        body = json.loads(event.get("body") or "{}")
    except Exception:
        return _response(400, {"error": "Invalid JSON body"})
    content = body.get("content")
    if not isinstance(content, str) or not content.strip():
        return _response(400, {"error": "content (html string) is required"})
    content_bytes = content.encode("utf-8")
    if len(content_bytes) > INLINE_MAX_BYTES * 8:   # ~8 MB cap
        return _response(413, {"error": "content too large"})
    provenance = body.get("provenance", "beautified")
    if provenance not in ("beautified", "human-edited", "machine-edited"):
        provenance = "beautified"
    # NOTE: saves are FLATTENED — the posted HTML already contains every edit (CSS +
    # diagrams + added sections) baked in. We deliberately do NOT store a transform layer:
    # re-applying one on top of an already-flattened document is what produced duplicate
    # diagrams. The pristine original artifact is always kept untouched alongside.

    parts = artifact_key.rsplit("/", 1)
    dir_prefix = parts[0] if len(parts) == 2 else ""
    basename = parts[-1]
    ts = re.sub(r"[^0-9T]", "", context_now_iso())[:15]  # e.g. 20260731T104500
    base_pfx = f"runs/{session_id}/" + (f"{dir_prefix}/" if dir_prefix else "") + "beautified/"
    version_key = f"{base_pfx}{ts}-{basename}"
    latest_key = f"{base_pfx}latest-{basename}"
    manifest_key = f"{base_pfx}manifest.json"
    meta = {"provenance": provenance, "source-artifact": artifact_key}
    rel = lambda k: k.split(f"runs/{session_id}/", 1)[-1]

    try:
        for k in (version_key, latest_key):
            s3.put_object(Bucket=RUNS_BUCKET, Key=k, Body=content_bytes,
                          ContentType="text/html; charset=utf-8", Metadata=meta)
        try:
            manifest = json.loads(s3.get_object(Bucket=RUNS_BUCKET, Key=manifest_key)["Body"].read())
        except Exception:
            manifest = {"source_artifact": artifact_key, "versions": []}
        manifest.setdefault("versions", []).append({
            "key": rel(version_key), "saved_at": context_now_iso(),
            "provenance": provenance, "bytes": len(content_bytes),
        })
        manifest["published"] = rel(version_key)
        manifest["latest"] = rel(latest_key)
        s3.put_object(Bucket=RUNS_BUCKET, Key=manifest_key,
                      Body=json.dumps(manifest, default=str).encode("utf-8"),
                      ContentType="application/json; charset=utf-8")
    except Exception as e:
        return _response(500, {"error": f"Save failed: {e}"})

    return _response(200, {
        "saved": True,
        "version_key": rel(version_key),
        "latest_key": rel(latest_key),
        "provenance": provenance,
        "versions": len(manifest["versions"]),
    })


def handler(event, context):
    method = (event.get("httpMethod") or "GET").upper()
    path_params = event.get("pathParameters") or {}
    session_id = (path_params.get("sessionId") or "").strip()
    raw_path = event.get("path") or event.get("rawPath") or ""

    # Beautify Phase 2: POST /api/runs/{sessionId}/artifacts/{artifactKey+} saves an
    # edited/beautified version of a deliverable — versioned, never overwriting the
    # original, provenance-tagged (see docs/spec-beautify-standardize.md).
    if method == "POST":
        return _handle_save(event, session_id, (path_params.get("artifactKey") or "").strip())
    if method != "GET":
        return _response(405, {"error": "Method not allowed"})

    # Phase 5f.5: bundle path — /api/runs/{sessionId}/bundle returns a
    # presigned URL for a zip of the run's full S3 prefix.
    if raw_path.endswith("/bundle"):
        err = _validate_session_id(session_id)
        if err:
            return _response(400, {"error": err})
        return _bundle_run(session_id)

    # Default path — artifact fetch
    artifact_key = (path_params.get("artifactKey") or "").strip()
    err = _validate_session_id(session_id)
    if err:
        return _response(400, {"error": err})
    err = _validate_artifact_key(artifact_key)
    if err:
        return _response(400, {"error": err})

    s3_key = f"runs/{session_id}/{artifact_key}"

    # Verify the object exists + capture metadata
    try:
        head = s3.head_object(Bucket=RUNS_BUCKET, Key=s3_key)
    except s3.exceptions.NoSuchKey:
        return _response(404, {"error": f"Artifact not found: {artifact_key}"})
    except s3.exceptions.ClientError as e:
        code = (e.response or {}).get("Error", {}).get("Code", "")
        if code in ("404", "NoSuchKey", "NotFound"):
            return _response(404, {"error": f"Artifact not found: {artifact_key}"})
        return _response(500, {"error": f"S3 head failed: {e}"})

    size = int(head.get("ContentLength", 0))
    content_type = _content_type_for(artifact_key)
    download_filename = artifact_key.rsplit("/", 1)[-1]

    # Mode selection: ?inline=true returns content directly (small text only);
    # default returns a presigned URL.
    qs = event.get("queryStringParameters") or {}
    inline = (qs.get("inline") or "").lower() in ("1", "true", "yes")

    if inline and _is_text_inline_safe(artifact_key) and size <= INLINE_MAX_BYTES:
        try:
            obj = s3.get_object(Bucket=RUNS_BUCKET, Key=s3_key)
            body_bytes = obj["Body"].read()
            # If an edited (flattened) copy of this deliverable exists, point the caller at
            # it so the studio reopens what the user last saved instead of the pristine
            # original. Only a POINTER — the content itself is fetched with a second GET.
            saved_latest_key, saved_versions = None, 0
            if artifact_key.lower().endswith(".html"):
                dirp = artifact_key.rsplit("/", 1)[0] if "/" in artifact_key else ""
                mkey = f"runs/{session_id}/" + (f"{dirp}/" if dirp else "") + "beautified/manifest.json"
                try:
                    m = json.loads(s3.get_object(Bucket=RUNS_BUCKET, Key=mkey)["Body"].read())
                    cand = m.get("published") or m.get("latest")
                    if cand:
                        saved_latest_key = cand
                        saved_versions = len(m.get("versions") or [])
                except Exception:
                    saved_latest_key = None
            return _response(200, {
                "artifact_key": artifact_key,
                "session_id": session_id,
                "size_bytes": size,
                "content_type": content_type,
                "content": body_bytes.decode("utf-8", errors="replace"),
                "filename": download_filename,
                "saved_latest_key": saved_latest_key,
                "saved_versions": saved_versions,
            })
        except Exception as e:
            return _response(500, {"error": f"S3 get failed: {e}"})

    # Default: presigned URL with content-disposition for download
    try:
        presigned = s3.generate_presigned_url(
            "get_object",
            Params={
                "Bucket": RUNS_BUCKET,
                "Key": s3_key,
                "ResponseContentType": content_type,
                "ResponseContentDisposition": f'attachment; filename="{download_filename}"',
            },
            ExpiresIn=PRESIGN_EXPIRY_S,
        )
    except Exception as e:
        return _response(500, {"error": f"Presign failed: {e}"})

    return _response(200, {
        "artifact_key": artifact_key,
        "session_id": session_id,
        "size_bytes": size,
        "content_type": content_type,
        "filename": download_filename,
        "download_url": presigned,
        "expires_in_s": PRESIGN_EXPIRY_S,
    })
