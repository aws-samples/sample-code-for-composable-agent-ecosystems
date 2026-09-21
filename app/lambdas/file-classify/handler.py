"""File classify — validates, classifies (Haiku), extracts if large, stores .meta.json.

Triggered by S3 event (or invoked directly after presign upload completes).
Handles both ecosystem files and run attachments.
"""
import os, json, boto3

REGION = os.environ.get("AWS_REGION", "us-east-1")
MODEL_ID = os.environ.get("CLASSIFY_MODEL_ID", "us.anthropic.claude-sonnet-4-5-20250929-v1:0")
MAX_SIZE = 4_500_000  # 4.5MB Bedrock limit
MAX_PAGES_FOR_RAW = 50  # Above this, extract text instead of passing raw PDF

# Amazon S3 client
s3 = boto3.client("s3", region_name=REGION)
bedrock = boto3.client("bedrock-runtime", region_name=REGION)

# Format groups. Bedrock Converse only accepts specific *document* formats and
# *image* formats; everything else that's still human-readable (json/yaml/xml/…)
# is passed to the classifier as a decoded **text** block instead.
# NOTE: keep the union below in sync with ALLOWED_FORMATS in lambdas/presign-upload/handler.py.
DOC_FORMATS = {"pdf", "csv", "doc", "docx", "xls", "xlsx", "html", "txt", "md"}
IMAGE_FORMATS = {"png", "jpg", "jpeg", "gif", "webp"}
TEXT_FORMATS = {"json", "yaml", "yml", "xml", "tsv", "log", "rtf", "ini", "toml"}
SUPPORTED_FORMATS = DOC_FORMATS | IMAGE_FORMATS | TEXT_FORMATS


def handler(event, context):
    """Handle S3 event or direct invocation."""
    # Support both S3 event trigger and direct invocation
    if "Records" in event:
        # S3 event
        record = event["Records"][0]
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]
    else:
        # Direct invocation
        bucket = event.get("bucket", "")
        key = event.get("key", "")

    if not bucket or not key:
        return {"error": "No bucket/key provided"}

    # Skip if this is already a .meta.json or .extracted.txt
    if key.endswith(".meta.json") or key.endswith(".extracted.txt"):
        return {"skipped": True}

    # Skip if not in a files/ or inputs/ path
    if "/files/" not in key and "/inputs/" not in key:
        return {"skipped": True}

    filename = key.split("/")[-1]
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    # Step 1: Validate
    if ext not in SUPPORTED_FORMATS:
        return store_meta(bucket, key, {"status": "failed", "error": f"Unsupported format: .{ext}", "filename": filename})

    head = s3.head_object(Bucket=bucket, Key=key)
    size = head["ContentLength"]

    if size > MAX_SIZE:
        return store_meta(bucket, key, {"status": "failed", "error": f"File too large: {size} bytes (max {MAX_SIZE})", "filename": filename})

    # Step 2: Classify with Haiku
    # Read first ~100KB for classification (enough for first page of most docs)
    sample_size = min(size, 100_000)
    obj = s3.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{sample_size - 1}")
    sample_bytes = obj["Body"].read()

    # For text-based formats, decode and use text. For binary (PDF), use document block.
    tag, summary, pages = classify_file(filename, ext, sample_bytes, bucket, key, size)

    # Step 3: Extract FULL text so agents can quote EXACT evidence (not just the
    # one-line classify summary). For PDFs we extract with pypdf and store
    # <key>.extracted.txt; the run-attachment loader + resolve_files_for_agent
    # read this. Best-effort — a failed extraction leaves extracted=False.
    extracted = False
    extracted_chars = 0
    if ext == "pdf":
        extracted_chars = _extract_pdf_text(bucket, key)
        extracted = extracted_chars > 0

    # Step 4: Store .meta.json
    meta = {
        "status": "ready",
        "filename": filename,
        "format": ext,
        "size": size,
        "pages": pages,
        "tag": tag,
        "summary": summary,
        "extracted": extracted,
        "extracted_chars": extracted_chars,
        "s3Uri": f"s3://{bucket}/{key}",
    }

    return store_meta(bucket, key, meta)


def _extract_pdf_text(bucket, key, max_chars=200_000):
    """Extract full text from a PDF and store it at <key>.extracted.txt.
    Returns the number of characters written (0 on failure/empty)."""
    try:
        import io
        from pypdf import PdfReader
        raw = s3.get_object(Bucket=bucket, Key=key)["Body"].read()
        reader = PdfReader(io.BytesIO(raw))
        parts = []
        for page in reader.pages:
            try:
                t = page.extract_text() or ""
            except Exception:
                t = ""
            if t.strip():
                parts.append(t)
        text = "\n\n".join(parts).strip()
        if not text:
            return 0
        text = text[:max_chars]
        s3.put_object(Bucket=bucket, Key=key + ".extracted.txt",
                      Body=text.encode("utf-8"), ContentType="text/plain; charset=utf-8")
        return len(text)
    except Exception as e:
        print(f"[file-classify] PDF extract failed for {key}: {type(e).__name__}: {str(e)[:160]}")
        return 0


def classify_file(filename, ext, sample_bytes, bucket, key, size):
    """Use Haiku to classify the file and produce a tag + summary."""
    # Build the prompt
    prompt = (
        f"You are classifying a file for a multi-agent platform. "
        f"Filename: {filename}, Format: {ext}, Size: {size} bytes.\n\n"
        f"Based on the filename and content, respond with EXACTLY this JSON (no other text):\n"
        f'{{"tag": "<one_word_category>", "summary": "<one sentence describing what this file contains>", "pages": <estimated_page_count_or_null>}}\n\n'
        f"Common tags: survey, contour_map, title_search, bank_statement, loan_statement, "
        f"portfolio, tax_return, contract, policy, soc2_report, architecture_diagram, "
        f"floor_plan, da_application, precedent_da, council_dcp, financial_report, invoice, "
        f"identity_doc, insurance_policy, meeting_minutes, correspondence\n\n"
        f"Pick the most specific tag that fits."
    )

    # Build message content
    content = [{"text": prompt}]

    # For supported document formats, include as document block (bytes source for Haiku compat)
    if ext in DOC_FORMATS:
        # Read full file for classification
        if size <= MAX_SIZE:
            full_obj = s3.get_object(Bucket=bucket, Key=key)
            file_bytes = full_obj["Body"].read()
        else:
            file_bytes = sample_bytes
        import re
        safe_name = re.sub(r'[^a-zA-Z0-9\s\-\(\)\[\]]', '_', filename.rsplit('.', 1)[0])[:60]
        content.append({
            "document": {
                "format": ext,
                "name": safe_name,
                "source": {"bytes": file_bytes},
            }
        })
    elif ext in IMAGE_FORMATS:
        # Use image block for images
        fmt_map = {"jpg": "jpeg", "jpeg": "jpeg", "png": "png", "gif": "gif", "webp": "webp"}
        content.append({
            "image": {
                "format": fmt_map.get(ext, "png"),
                "source": {"bytes": sample_bytes},
            }
        })
    elif ext in TEXT_FORMATS:
        # Bedrock has no document block for these — decode and pass as plain text.
        text = sample_bytes.decode("utf-8", errors="replace")
        content.append({"text": f"\n\n--- FILE CONTENT ({filename}, .{ext}) ---\n{text}"})

    try:
        response = bedrock.converse(
            modelId=MODEL_ID,
            messages=[{"role": "user", "content": content}],
            inferenceConfig={"maxTokens": 200, "temperature": 0},
        )
        result_text = response["output"]["message"]["content"][0]["text"].strip()
        # Strip markdown code fences if present
        if result_text.startswith("```"):
            result_text = result_text.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
        # Find JSON object in response
        start = result_text.find("{")
        end = result_text.rfind("}") + 1
        if start >= 0 and end > start:
            parsed = json.loads(result_text[start:end])
            return parsed.get("tag", "unknown"), parsed.get("summary", ""), parsed.get("pages")
        return "document", f"File: {filename}", None
    except Exception as e:
        # Fallback: use filename as tag
        print(f"Classification failed for {filename}: {e}")
        tag = ext if ext in ("pdf", "csv") else "document"
        return tag, f"File: {filename} ({size} bytes)", None


def store_meta(bucket, key, meta):
    """Write .meta.json alongside the file."""
    meta_key = key + ".meta.json"
    s3.put_object(
        Bucket=bucket,
        Key=meta_key,
        Body=json.dumps(meta, indent=2),
        ContentType="application/json",
    )
    return meta
