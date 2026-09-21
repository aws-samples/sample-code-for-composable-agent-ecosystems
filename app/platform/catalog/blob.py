"""tessera_platform.catalog.blob — S3 overflow for oversized catalog records.

AgentCore Memory rejects any record whose content.text exceeds 16,000 chars
(BatchCreateMemoryRecords ValidationException). Rich, growing dossiers (e.g. enriched
`risk` records) exceed that. Rather than TRUNCATE (lossy) we keep FULL fidelity:

  • the COMPLETE dossier is written to S3 (no size ceiling), and
  • a LEAN, filterable record stays in Memory — scalars + short strings + excerpts of long
    prose + a `content_ref` (s3://…) pointer + the full record's `content_hash`.

Readers call `resolve(record)` to reconstitute the full dossier (fetch S3 when a
`content_ref` is present; otherwise the record is already whole). Records that fit inline
are stored whole — nothing regresses. This is the AWS-endorsed pattern (a `source_url`-style
pointer in the record + full content in external storage) and the standard "retrieve, don't
load large docs into memory" knowledge-base approach.

Shared by the engine + agent-invoker + catalog-api (vendored). The AgentCore runtime
container inlines an equivalent `resolve` (it doesn't package this module).
"""
import json
import os

# Keep well under the 16,000 hard cap (JSON separators + base64 headroom + safety).
INLINE_LIMIT = int(os.environ.get("CATALOG_INLINE_LIMIT", "15000"))
_MAX_INLINE_STR = 400            # long string leaves become excerpts of this length in the lean record
_PREFIX = "catalog-content"


def _bucket():
    return os.environ.get("CATALOG_CONTENT_BUCKET") or os.environ.get("ECOSYSTEMS_BUCKET") or ""


def _s3():
    import boto3
    return boto3.client("s3", region_name=os.environ.get("AWS_REGION", "us-east-1"))


def blob_key(namespace_leaf: str) -> str:
    """/tessera/catalog/<version>/<type>/<key> -> catalog-content/tessera/catalog/<version>/<type>/<key>.json"""
    return f"{_PREFIX}/{namespace_leaf.strip('/')}.json"


def _lean(record: dict, chash: str, ref: str) -> dict:
    """Lean Memory record: keep scalars + short strings; excerpt long prose; add ref+hash."""
    out = {}
    for k, v in record.items():
        if isinstance(v, (int, float, bool)) or v is None:
            out[k] = v
        elif isinstance(v, str):
            out[k] = v if len(v) <= _MAX_INLINE_STR else (v[: _MAX_INLINE_STR - 1] + "\u2026")
        # long/complex fields (lists, dicts, big prose) live in the S3 full record only
    out["content_hash"] = chash
    out["content_ref"] = ref
    return out


def pack(record: dict, namespace_leaf: str, chash: str, inline_limit: int = None):
    """Prepare a record for Memory storage. Returns (record_to_store, offloaded: bool).

    Fits inline  -> return the full record (with content_hash), offloaded=False.
    Too large     -> PUT the full dossier to S3, return a lean record (+content_ref), True.
    Never truncates: if it can't offload (no bucket) it RAISES rather than lose content."""
    limit = inline_limit or INLINE_LIMIT
    whole = dict(record)
    whole["content_hash"] = chash
    if len(json.dumps(whole, separators=(",", ":"), ensure_ascii=False, default=str)) <= limit:
        return whole, False
    bucket = _bucket()
    if not bucket:
        raise RuntimeError("cannot offload oversized catalog record: "
                           "CATALOG_CONTENT_BUCKET / ECOSYSTEMS_BUCKET not configured")
    key = blob_key(namespace_leaf)
    body = json.dumps(whole, separators=(",", ":"), ensure_ascii=False, default=str).encode("utf-8")
    _s3().put_object(Bucket=bucket, Key=key, Body=body, ContentType="application/json")
    return _lean(record, chash, f"s3://{bucket}/{key}"), True


def resolve(record: dict) -> dict:
    """Reconstitute the FULL dossier: fetch S3 when the record carries a content_ref;
    otherwise return the record unchanged. Best-effort (falls back to the lean record)."""
    if not isinstance(record, dict):
        return record
    ref = record.get("content_ref")
    if not isinstance(ref, str) or not ref.startswith("s3://"):
        return record
    b, _, k = ref[5:].partition("/")
    try:
        return json.loads(_s3().get_object(Bucket=b, Key=k)["Body"].read())
    except Exception:
        return record


def delete_ref(record: dict) -> None:
    """Delete the S3 backing object for an offloaded record (retire/tombstone cleanup)."""
    ref = (record or {}).get("content_ref")
    if isinstance(ref, str) and ref.startswith("s3://"):
        b, _, k = ref[5:].partition("/")
        try:
            _s3().delete_object(Bucket=b, Key=k)
        except Exception:
            pass
