"""Unit tests for the run quality feedback loop (spec-quality-feedback-loop).

Covers the runs-api feedback sidecar + per-ecosystem guidance store + promotion,
and the websocket launch-time STANDING GUIDANCE block composition. Uses an
in-memory S3 fake — no boto3/network.
"""
import importlib.util, io, json, os, sys, types

_HERE = os.path.dirname(__file__)


class FakeS3:
    """Minimal in-memory S3 supporting get_object/put_object used by the handlers."""
    def __init__(self):
        self.store = {}

    def get_object(self, Bucket, Key):
        if Key not in self.store:
            raise Exception("NoSuchKey")
        return {"Body": io.BytesIO(self.store[Key])}

    def put_object(self, Bucket, Key, Body, **kw):
        self.store[Key] = Body if isinstance(Body, (bytes, bytearray)) else str(Body).encode()


def _stub_boto3():
    if "boto3" not in sys.modules:
        b = types.ModuleType("boto3")
        b.client = lambda *a, **k: None
        b.resource = lambda *a, **k: types.SimpleNamespace(Table=lambda *a, **k: None)
        sys.modules["boto3"] = b


def _load(name, rel_path, env):
    _stub_boto3()
    for k, v in env.items():
        os.environ.setdefault(k, v)
    # yaml is imported by runs-api; stub if unavailable
    if "yaml" not in sys.modules:
        try:
            import yaml  # noqa: F401
        except Exception:
            y = types.ModuleType("yaml"); y.safe_load = lambda *a, **k: {}
            sys.modules["yaml"] = y
    path = os.path.abspath(os.path.join(_HERE, "..", "..", "lambdas", *rel_path))
    spec = importlib.util.spec_from_file_location(name, path)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _runs_api():
    m = _load("runs_api_qfl", ["runs-api", "handler.py"], {
        "TESSERA_RUNS_BUCKET": "amzn-s3-demo-runs-bkt", "ECOSYSTEMS_BUCKET": "amzn-s3-demo-eco-bkt",
        "AWS_REGION": "us-east-1",
    })
    m.s3 = FakeS3()
    m.RUNS_BUCKET = "amzn-s3-demo-runs-bkt"
    return m


def _body(resp):
    return json.loads(resp["body"])


# ─── run feedback sidecar ────────────────────────────────────────────────────

def test_feedback_empty_read():
    m = _runs_api()
    r = m._get_feedback("eco1", "eco1-run1")
    assert r["statusCode"] == 200
    b = _body(r)
    assert b["run_feedback"] == "" and b["sections"] == []


def test_feedback_upsert_roundtrip():
    m = _runs_api()
    r = m._put_feedback("eco1", "eco1-run1", {"run_feedback": "insurance section thin"})
    assert r["statusCode"] == 200
    got = _body(m._get_feedback("eco1", "eco1-run1"))
    assert got["run_feedback"] == "insurance section thin"
    assert got["ecosystem_id"] == "eco1"


def test_sections_clamp_and_ids():
    m = _runs_api()
    r = m._put_sections("eco1", "eco1-run1", {"sections": [
        {"artifact_key": "d/pack.html", "excerpt": "NBA-05", "score": 9, "feedback": "shallow"},
        {"artifact_key": "d/pack.html", "excerpt": "NBA-01", "score": 3, "feedback": "good"},
    ]})
    assert r["statusCode"] == 200
    secs = _body(r)["sections"]
    assert len(secs) == 2
    assert secs[0]["score"] is None  # 9 clamped out of range → None
    assert secs[1]["score"] == 3
    assert all(s["id"].startswith("sec_") for s in secs)


def test_sections_rejects_non_list():
    m = _runs_api()
    assert m._put_sections("eco1", "r", {"sections": "nope"})["statusCode"] == 400


# ─── per-ecosystem guidance store ────────────────────────────────────────────

def test_guidance_crud_lifecycle():
    m = _runs_api()
    user = {"user_id": "u1"}
    # add
    add = m._add_guidance("eco1", {"text": "Quantify premium drag in dollars."}, user)
    assert add["statusCode"] == 201
    gid = _body(add)["guidance"]["id"]
    # list
    lst = _body(m._list_guidance("eco1"))
    assert len(lst["entries"]) == 1 and lst["entries"][0]["enabled"] is True
    # patch → disable + edit
    p = m._patch_guidance("eco1", gid, {"enabled": False, "text": "edited"})
    assert p["statusCode"] == 200
    e = _body(m._list_guidance("eco1"))["entries"][0]
    assert e["enabled"] is False and e["text"] == "edited"
    # delete
    assert m._delete_guidance("eco1", gid)["statusCode"] == 200
    assert _body(m._list_guidance("eco1"))["entries"] == []


def test_guidance_add_requires_text():
    m = _runs_api()
    assert m._add_guidance("eco1", {"text": "  "}, {"user_id": "u1"})["statusCode"] == 400


def test_patch_missing_guidance_404():
    m = _runs_api()
    assert m._patch_guidance("eco1", "g_missing", {"enabled": False})["statusCode"] == 404


# ─── promotion: run/section feedback → guidance entry ────────────────────────

def test_promote_run_feedback():
    m = _runs_api()
    m._put_feedback("eco1", "eco1-run1", {"run_feedback": "always model reduced need"})
    r = m._promote("eco1", "eco1-run1", {"scope": "run"}, {"user_id": "u1"})
    assert r["statusCode"] == 200
    entries = _body(m._list_guidance("eco1"))["entries"]
    assert len(entries) == 1
    assert entries[0]["text"] == "always model reduced need"
    assert entries[0]["source"]["type"] == "run"


def test_promote_section_feedback():
    m = _runs_api()
    secs = _body(m._put_sections("eco1", "eco1-run1", {"sections": [
        {"artifact_key": "d/pack.html", "excerpt": "NBA-05", "score": 2, "feedback": "quantify the drag"},
    ]}))["sections"]
    sid = secs[0]["id"]
    r = m._promote("eco1", "eco1-run1", {"scope": "section", "section_id": sid}, {"user_id": "u1"})
    assert r["statusCode"] == 200
    e = _body(m._list_guidance("eco1"))["entries"][0]
    assert e["text"] == "quantify the drag"
    assert e["source"] == {"type": "section", "session_id": "eco1-run1", "section_id": sid}


def test_promote_empty_is_400():
    m = _runs_api()
    assert m._promote("eco1", "eco1-run1", {"scope": "run"}, {"user_id": "u1"})["statusCode"] == 400


# ─── websocket launch: STANDING GUIDANCE block composition ───────────────────

def _websocket():
    m = _load("websocket_qfl", ["websocket", "handler.py"], {
        "CONNECTIONS_TABLE": "conns", "STATE_MACHINE_PREFIX": "tessera-dev",
        "TESSERA_AGENT_RUNTIME_ARN": "arn:aws:bedrock-agentcore:us-east-1:1:runtime/x",
        "AWS_REGION": "us-east-1", "TESSERA_RUNS_BUCKET": "amzn-s3-demo-runs-bkt",
    })
    m.s3 = FakeS3()
    m.RUNS_BUCKET = "amzn-s3-demo-runs-bkt"
    return m


def _seed_guidance(m, eco, entries):
    m.s3.put_object(Bucket="amzn-s3-demo-runs-bkt", Key=f"guidance/{eco}.json",
                    Body=json.dumps({"entries": entries}).encode())


def test_standing_guidance_empty_when_none():
    m = _websocket()
    assert m._read_standing_guidance("eco1") == ""


def test_standing_guidance_only_enabled():
    m = _websocket()
    _seed_guidance(m, "eco1", [
        {"text": "Quantify premium drag.", "enabled": True},
        {"text": "Disabled note.", "enabled": False},
        {"text": "  ", "enabled": True},
    ])
    block = m._read_standing_guidance("eco1")
    assert "STANDING QUALITY GUIDANCE" in block
    assert "Quantify premium drag." in block
    assert "Disabled note." not in block


def test_standing_guidance_caps_entries():
    m = _websocket()
    _seed_guidance(m, "eco1", [{"text": f"rule {i}", "enabled": True} for i in range(40)])
    block = m._read_standing_guidance("eco1")
    # capped at 20 bullets (+ header lines); rule 25 must not appear
    assert block.count("\n- ") <= 21
    assert "rule 39" not in block


# ─── proxy routing: execName derived from raw path (behavior-preserving trim) ─

def test_exec_name_from_path():
    m = _runs_api()
    f = m._exec_name_from_path
    # collection + recommendation sibling → not a run
    assert f("/api/ecosystems/eco1/runs") is None
    assert f("/api/ecosystems/eco1/runs/") is None
    assert f("/api/ecosystems/eco1/runs/recommendation") is None
    # a run detail + every sub-route resolves to the same execName
    assert f("/api/ecosystems/eco1/runs/eco1-run1") == "eco1-run1"
    assert f("/api/ecosystems/eco1/runs/eco1-run1/manifest") == "eco1-run1"
    assert f("/api/ecosystems/eco1/runs/eco1-run1/rating") == "eco1-run1"
    assert f("/api/ecosystems/eco1/runs/eco1-run1/feedback") == "eco1-run1"
    assert f("/api/ecosystems/eco1/runs/eco1-run1/redrive") == "eco1-run1"
    # url-encoded segment is decoded
    assert f("/api/ecosystems/eco1/runs/eco1%2Drun1") == "eco1-run1"
    # guidance path (no /runs/) → not a run
    assert f("/api/ecosystems/eco1/guidance") is None
    assert f("/api/ecosystems/eco1/guidance/g_1") is None
