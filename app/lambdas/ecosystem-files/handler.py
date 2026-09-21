"""Ecosystem Files API — list, delete, update metadata for ecosystem file library."""
import os, json, boto3

BUCKET = os.environ.get("ECOSYSTEMS_BUCKET", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")
# Amazon S3 client
s3 = boto3.client("s3", region_name=REGION)

CORS = {"Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"), "Access-Control-Allow-Methods": "GET,PUT,DELETE,OPTIONS", "Access-Control-Allow-Headers": "Content-Type"}


def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return resp({}, 200)

    method = event.get("httpMethod", "GET")
    path_params = event.get("pathParameters") or {}
    eco_id = path_params.get("id", "")
    filename = path_params.get("filename")

    if not eco_id:
        return resp({"error": "ecosystem id required"}, 400)

    prefix = f"ecosystems/{eco_id}/files/"

    try:
        if method == "GET" and not filename:
            return list_files(prefix)
        elif method == "PUT" and filename:
            body = json.loads(event.get("body", "{}"))
            return update_meta(prefix, filename, body)
        elif method == "DELETE" and filename:
            return delete_file(prefix, filename)
        else:
            return resp({"error": "Not found"}, 404)
    except Exception as e:
        return resp({"error": str(e)[:300]}, 500)


def list_files(prefix):
    """List all files with their metadata."""
    objects = s3.list_objects_v2(Bucket=BUCKET, Prefix=prefix)
    files = []
    meta_cache = {}

    # First pass: collect all .meta.json
    for obj in objects.get("Contents", []):
        key = obj["Key"]
        if key.endswith(".meta.json"):
            body = s3.get_object(Bucket=BUCKET, Key=key)["Body"].read()
            meta = json.loads(body)
            # The meta belongs to the file without .meta.json suffix
            file_key = key[:-len(".meta.json")]
            meta_cache[file_key] = meta

    # Second pass: list actual files (not .meta.json, not .extracted.txt)
    for obj in objects.get("Contents", []):
        key = obj["Key"]
        if key.endswith(".meta.json") or key.endswith(".extracted.txt"):
            continue
        filename = key.split("/")[-1]
        meta = meta_cache.get(key, {})
        files.append({
            "filename": filename,
            "key": key,
            "size": obj["Size"],
            "lastModified": str(obj["LastModified"]),
            "tag": meta.get("tag", "unclassified"),
            "summary": meta.get("summary", ""),
            "status": meta.get("status", "processing"),
            "pages": meta.get("pages"),
            "format": meta.get("format", filename.rsplit(".", 1)[-1] if "." in filename else ""),
            "s3Uri": f"s3://{BUCKET}/{key}",
        })

    return resp(files)


def update_meta(prefix, filename, body):
    """Update tag/summary in .meta.json (operator override)."""
    meta_key = f"{prefix}{filename}.meta.json"
    # Read existing meta
    try:
        existing = json.loads(s3.get_object(Bucket=BUCKET, Key=meta_key)["Body"].read())
    except Exception:
        existing = {"filename": filename, "status": "ready"}

    # Update fields
    if "tag" in body:
        existing["tag"] = body["tag"]
    if "summary" in body:
        existing["summary"] = body["summary"]

    s3.put_object(Bucket=BUCKET, Key=meta_key, Body=json.dumps(existing, indent=2), ContentType="application/json")
    return resp({"updated": True, "filename": filename, "meta": existing})


def delete_file(prefix, filename):
    """Delete file + its .meta.json + .extracted.txt."""
    keys_to_delete = [
        f"{prefix}{filename}",
        f"{prefix}{filename}.meta.json",
        f"{prefix}{filename}.extracted.txt",
    ]
    for key in keys_to_delete:
        try:
            s3.delete_object(Bucket=BUCKET, Key=key)
        except Exception:
            pass
    return resp({"deleted": True, "filename": filename})


def resp(data, status=200):
    return {"statusCode": status, "headers": CORS, "body": json.dumps(data, default=str)}
