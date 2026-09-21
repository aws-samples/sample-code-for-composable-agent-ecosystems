"""Unit tests for the `browser` platform tool, its SSRF guard, the server-side
screenshot-ref inlining in agent-invoker, and the stage.yaml webapp_explorer wiring.

Runtime deps ag_ui / ag_ui_strands are not installed in the test venv, so we stub
just those two before importing the runtime module (strands / fastapi ARE real)."""
import importlib.util
import io
import json
import os
import re
import sys
import types
from unittest.mock import MagicMock

import pytest
import yaml

_HERE = os.path.dirname(__file__)
_ROOT = os.path.abspath(os.path.join(_HERE, "..", "..", ".."))
_MAIN = os.path.join(_ROOT, "agent", "src", "main.py")
_INVOKER = os.path.join(_ROOT, "lambdas", "agent-invoker", "handler.py")
_STAGE = os.path.join(_ROOT, "content", "ecosystems", "samples", "stage.yaml")


def _load_runtime():
    """Import agent/src/main.py with ag_ui* stubbed out.

    The runtime module imports the full agent stack (uvicorn, fastapi, strands,
    strands_tools, httpx, bedrock_agentcore). Those are intentionally NOT in the
    offline unit-test environment (tests/requirements.txt), so skip these runtime
    tests when the stack isn't importable rather than failing on a missing dep.
    """
    import importlib.util as _ilu
    for _dep in ("uvicorn", "fastapi", "httpx", "strands", "strands_tools", "bedrock_agentcore"):
        if _ilu.find_spec(_dep) is None:
            import pytest
            pytest.skip(f"agent runtime dependency '{_dep}' not installed (offline unit env)")
    for name in ("ag_ui", "ag_ui.core", "ag_ui.encoder", "ag_ui_strands"):
        if name not in sys.modules:
            mod = types.ModuleType(name)
            sys.modules[name] = mod
    sys.modules["ag_ui.core"].RunAgentInput = MagicMock()
    sys.modules["ag_ui.core"].StateDeltaEvent = MagicMock()
    sys.modules["ag_ui.core"].EventType = MagicMock()
    sys.modules["ag_ui.encoder"].EventEncoder = MagicMock()
    sys.modules["ag_ui_strands"].StrandsAgent = MagicMock()
    os.environ.setdefault("AWS_REGION", "us-east-1")
    spec = importlib.util.spec_from_file_location("tessera_runtime_main", _MAIN)
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def _load_invoker():
    for k in ("TESSERA_MEMORY_ID", "TESSERA_AGENT_RUNTIME_ARN", "TESSERA_RUNS_BUCKET",
              "WS_ENDPOINT", "CONNECTIONS_TABLE", "AWS_REGION"):
        os.environ.setdefault(k, "test")
    # Force a boto3 stub for the import: the invoker builds real clients (e.g.
    # boto3.client("bedrock-agentcore")) at module scope, and the pinned boto3 doesn't
    # know that newer service. Swap in a MagicMock boto3 for the import, then restore.
    _real_boto3 = sys.modules.get("boto3")
    b = types.ModuleType("boto3")
    b.client = lambda *a, **k: MagicMock()
    b.resource = lambda *a, **k: MagicMock()
    sys.modules["boto3"] = b
    try:
        spec = importlib.util.spec_from_file_location("agent_invoker_browser", _INVOKER)
        m = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(m)
    finally:
        if _real_boto3 is not None:
            sys.modules["boto3"] = _real_boto3
        else:
            sys.modules.pop("boto3", None)
    return m


# ─── SSRF guard ─────────────────────────────────────────────────────────────

def test_ssrf_guard_allows_public_https():
    m = _load_runtime()
    ok, reason = m._is_safe_public_url("https://example.com/path")
    assert ok, reason


@pytest.mark.parametrize("url", [
    "http://localhost/admin",
    "http://127.0.0.1:8080",
    "http://169.254.169.254/latest/meta-data/",   # cloud metadata endpoint
    "http://10.0.0.5/internal",
    "http://192.168.1.1",
    "http://foo.internal/",
    "ftp://example.com/file",                       # non-http scheme
    "file:///etc/passwd",
    "javascript:alert(1)",
    "",
])
def test_ssrf_guard_blocks_dangerous_urls(url):
    m = _load_runtime()
    ok, _ = m._is_safe_public_url(url)
    assert ok is False, f"expected {url!r} to be blocked"


# ─── browser tool registration ──────────────────────────────────────────────

def test_browser_is_opt_in_not_ambient():
    """browser must NOT be handed to an agent that didn't declare it, but the
    other (ambient) platform tools still are."""
    m = _load_runtime()
    tools, _cs, _art, _res = m.build_tools([], [], agent_id="t_agent")
    names = {getattr(t, "tool_name", getattr(t, "name", None)) for t in tools}
    assert "browser" not in names, "browser leaked into an agent that didn't declare it"
    # ambient platform tools are still present
    assert "memory_search" in names
    assert "web_search" in names
    assert "code_interpreter" in names


def test_browser_granted_only_when_declared(monkeypatch):
    """An agent that lists `browser` in its tools gets it."""
    m = _load_runtime()
    monkeypatch.setattr(m, "discover_gateway_tools", lambda allowed: [])
    tools, _cs, _art, _res = m.build_tools(["browser"], [], agent_id="t_agent")
    names = {getattr(t, "tool_name", getattr(t, "name", None)) for t in tools}
    assert "browser" in names
    # declaring browser must NOT be flagged as an unregistered/missing tool
    assert "browser" not in _res.get("missing", [])


def test_browser_worker_class_exists(monkeypatch):
    m = _load_runtime()
    assert hasattr(m, "_BrowserWorker")
    monkeypatch.setattr(m, "discover_gateway_tools", lambda allowed: [])
    # exercises the SSRF-guard blocked-navigate path without touching a real browser
    tools, _cs, _art, _res = m.build_tools(["browser"], [], agent_id="t_agent")
    browser = next(t for t in tools if getattr(t, "tool_name", None) == "browser")
    import json
    # Strands @tool objects are invocable; call the underlying function directly.
    fn = getattr(browser, "_tool_func", None) or getattr(browser, "func", None) or getattr(browser, "original_function", None)
    if fn is None:
        pytest.skip("cannot access underlying tool function on this strands version")
    out = json.loads(fn(action="navigate", url="http://169.254.169.254/"))
    assert out.get("error") == "url_blocked"


# ─── screenshot-ref inlining (agent-invoker) ────────────────────────────────

def test_inline_screenshot_refs_replaces_token_with_data_uri():
    m = _load_invoker()
    png = b"\xff\xd8\xffFAKEJPEGBYTES"
    m.RUNS_BUCKET = "amzn-s3-demo-test-bucket"
    m.s3 = types.SimpleNamespace(
        get_object=lambda Bucket, Key: {"Body": io.BytesIO(png)}
    )
    html = '<img src="tessera-screenshot://webapp_explorer/screenshot-01-login.jpg" alt="Login">'
    out = m._inline_screenshot_refs(html, "stage-abc12345")
    import base64
    expect = "data:image/jpeg;base64," + base64.b64encode(png).decode()
    assert expect in out
    assert "tessera-screenshot://" not in out


def test_inline_screenshot_refs_degrades_on_missing_object():
    m = _load_invoker()
    m.RUNS_BUCKET = "amzn-s3-demo-test-bucket"

    def _boom(Bucket, Key):
        raise RuntimeError("NoSuchKey")

    m.s3 = types.SimpleNamespace(get_object=_boom)
    html = '<img src="tessera-screenshot://webapp_explorer/missing.jpg">'
    out = m._inline_screenshot_refs(html, "stage-abc12345")
    # token replaced with an empty data URI (img falls back to alt), no crash
    assert "tessera-screenshot://" not in out
    assert "data:image/jpeg;base64," in out


def test_inline_screenshot_refs_noop_without_token():
    m = _load_invoker()
    m.RUNS_BUCKET = "amzn-s3-demo-test-bucket"
    html = "<html><body>no screenshots here</body></html>"
    assert m._inline_screenshot_refs(html, "stage-abc12345") == html


# ─── stage.yaml wiring ──────────────────────────────────────────────────────

def test_stage_yaml_has_webapp_explorer_wired():
    cfg = yaml.safe_load(open(_STAGE))
    agents = {a["id"]: a for a in cfg["agents"]}
    assert "webapp_explorer" in agents
    we = agents["webapp_explorer"]
    assert "browser" in we["tools"]
    assert we["depends_on"] == ["presentation_intake_analyst"]
    # downstream consumers of the screenshot manifest depend on it
    for consumer in ("slide_outline_synthesizer", "presentation_finalizer", "presentation_drafter"):
        assert "webapp_explorer" in agents[consumer]["depends_on"], consumer
    # drafter embeds screenshots via the ref token scheme
    assert "tessera-screenshot://" in agents["presentation_drafter"]["prompt"]


def test_stage_yaml_compiles():
    sys.path.insert(0, _ROOT)
    from tessera_platform.compiler.yaml_to_asl import compile_ecosystem
    asl = compile_ecosystem(_STAGE)
    assert asl["States"], "stage.yaml produced no states"
    # webapp_explorer runs in the first studio layer (Layer1)
    import json as _json
    assert "webapp_explorer" in _json.dumps(asl["States"]["Layer1"])


# ─── Authenticated-browser: profiles + request_login ────────────────────────

def test_profile_name_is_stable_and_valid():
    m = _load_runtime()
    a = m._derive_browser_profile_name("b4f86448-90b1-707d-be95", "d1rassu49g0dum.cloudfront.net")
    b = m._derive_browser_profile_name("b4f86448-90b1-707d-be95", "d1rassu49g0dum.cloudfront.net")
    assert a == b, "profile name must be deterministic (runtime lookup == lambda create)"
    assert re.match(r"^[A-Za-z][A-Za-z0-9_]{0,47}$", a), f"invalid profile name: {a!r}"
    assert len(a) <= 48
    # different site -> different profile
    assert m._derive_browser_profile_name("b4f86448", "other.example.com") != a


def test_request_login_records_to_sink(monkeypatch):
    m = _load_runtime()
    monkeypatch.setattr(m, "discover_gateway_tools", lambda allowed: [])
    tools, code_holder, _art, _res = m.build_tools(
        ["browser"], [], agent_id="webapp_explorer", user_id="user-1234-abcd")
    browser = next(t for t in tools if getattr(t, "tool_name", None) == "browser")
    fn = getattr(browser, "_tool_func", None) or getattr(browser, "func", None) or getattr(browser, "original_function", None)
    if fn is None:
        pytest.skip("cannot access underlying tool function on this strands version")
    import json
    # request_login with a url (no live browser needed) records the site + profile
    out = json.loads(fn(action="request_login", url="https://portal.example.com/login"))
    assert out.get("status") == "login_requested"
    assert out.get("domain") == "portal.example.com"
    sink = code_holder.get("login_sink")
    assert sink and sink[0]["domain"] == "portal.example.com"
    assert re.match(r"^[A-Za-z][A-Za-z0-9_]{0,47}$", sink[0]["profile_name"])


def _load_gate_decision():
    for k in ("TESSERA_RUNS_BUCKET", "ATTENTION_TABLE", "AWS_REGION"):
        os.environ.setdefault(k, "test")
    if "boto3" not in sys.modules:
        b = types.ModuleType("boto3")
        b.client = lambda *a, **k: MagicMock()
        b.resource = lambda *a, **k: MagicMock()
        sys.modules["boto3"] = b
    spec = importlib.util.spec_from_file_location(
        "gate_decision_h", os.path.join(_ROOT, "lambdas", "gate-decision", "handler.py"))
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def test_browser_login_folded_into_gate_decision():
    m = _load_gate_decision()
    # key helper matches the agent-invoker's record path
    assert m._bl_key("stage-abc12345", "webapp_explorer") == "runs/stage-abc12345/webapp_explorer/browser_login.json"
    # browser_login dispatch requires session_id/agent_id
    resp = m.handler({"body": json.dumps({"action": "browser_login_open"})}, None)
    assert resp["statusCode"] == 400

