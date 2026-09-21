"""Presign upload — generates presigned PUT URLs for file uploads."""
import os, json, boto3, uuid

ECOSYSTEMS_BUCKET = os.environ.get("ECOSYSTEMS_BUCKET", "")
RUNS_BUCKET = os.environ.get("RUNS_BUCKET", "")
REGION = os.environ.get("AWS_REGION", "us-east-1")
EXPIRY = 300  # 5 minutes

# Amazon S3 client
s3 = boto3.client("s3", region_name=REGION)

CORS = {"Access-Control-Allow-Origin": os.environ.get("CORS_ORIGIN", "*"), "Access-Control-Allow-Methods": "POST,OPTIONS", "Access-Control-Allow-Headers": "Content-Type"}

# Max file size: 4.5MB (Bedrock document block limit)
MAX_SIZE = 4_500_000
# Keep this in sync with file-classify (DOC_FORMATS | IMAGE_FORMATS | TEXT_FORMATS).
# Documents + images go to Bedrock as document/image blocks; the text/data formats
# (json/yaml/xml/…) are decoded and passed as text by the classifier and the run.
ALLOWED_FORMATS = {
    # documents (Bedrock-native document blocks)
    "pdf", "csv", "doc", "docx", "xls", "xlsx", "html", "txt", "md",
    # images
    "png", "jpg", "jpeg", "gif", "webp",
    # text / data / config (decoded as text)
    "json", "yaml", "yml", "xml", "tsv", "log", "rtf", "ini", "toml",
}


def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS, "body": ""}

    body = json.loads(event.get("body", "{}"))
    filename = body.get("filename", "")
    ecosystem_id = body.get("ecosystemId", "")
    session_id = body.get("sessionId", "")  # if per-run attachment
    file_type = body.get("type", "ecosystem")  # "ecosystem" or "run"

    if not filename:
        return resp({"error": "filename is required"}, 400)

    # Validate extension
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if ext not in ALLOWED_FORMATS:
        return resp({"error": f"Unsupported format: .{ext}. Allowed: {', '.join(sorted(ALLOWED_FORMATS))}"}, 400)

    # Determine S3 key
    if file_type == "ecosystem" and ecosystem_id:
        bucket = ECOSYSTEMS_BUCKET
        key = f"ecosystems/{ecosystem_id}/files/{filename}"
    elif file_type == "run" and session_id:
        bucket = RUNS_BUCKET
        key = f"{session_id}/inputs/{filename}"
    else:
        return resp({"error": "ecosystemId (for ecosystem files) or sessionId (for run attachments) is required"}, 400)

    if not bucket:
        return resp({"error": "Storage bucket not configured"}, 500)

    # Generate presigned PUT URL
    presigned_url = s3.generate_presigned_url(
        "put_object",
        Params={"Bucket": bucket, "Key": key, "ContentType": body.get("contentType", "application/octet-stream")},
        ExpiresIn=EXPIRY,
    )

    return resp({
        "uploadUrl": presigned_url,
        "s3Uri": f"s3://{bucket}/{key}",
        "key": key,
        "expiresIn": EXPIRY,
    })


def resp(data, status=200):
    return {"statusCode": status, "headers": CORS, "body": json.dumps(data)}
