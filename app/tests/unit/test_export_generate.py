"""Unit tests for the export Terraform generator (app/platform/export/generate.py).
Offline (build_artifacts=False): no Docker, no Node, no AWS."""
import json

from tessera_platform.export import bom as _bom
from tessera_platform.export import generate as _gen


# ── _hcl renderer (pure) ──────────────────────────────────────────────────────

def test_hcl_scalars():
    assert _gen._hcl(True) == "true"
    assert _gen._hcl(False) == "false"
    assert _gen._hcl(7) == "7"
    assert _gen._hcl(None) == "null"
    assert _gen._hcl("a\"b") == '"a\\"b"'  # JSON-escaped string


def test_hcl_list_and_map():
    assert _gen._hcl([]) == "[]"
    assert _gen._hcl({}) == "{}"
    out = _gen._hcl({"eco_id": "x", "n": 3})
    assert "eco_id = \"x\"" in out and "n = 3" in out
    # a key that isn't a bare identifier is quoted
    out2 = _gen._hcl({"s3_key": "lambdas/agent-invoker.zip"})
    assert 's3_key = "lambdas/agent-invoker.zip"' in out2


def test_tfvars_contains_bom_values():
    cfg = _bom.load_config("agentic-ai-sirs")
    b = _bom.analyze(cfg, region="us-east-1")
    tfvars = _gen._tfvars(b)
    assert 'eco_id      = "agentic-ai-sirs"' in tfvars
    assert 'region      = "us-east-1"' in tfvars
    assert "model_ids =" in tfvars and "lambdas =" in tfvars
    assert "create_networking       = true" in tfvars


# ── generate() package contents (source mode) ────────────────────────────────

def test_generate_writes_package(tmp_path):
    cfg = _bom.load_config("agentic-ai-sirs")
    b = _bom.analyze(cfg, region="us-east-1")
    asl = _gen.compile_asl(cfg, b.eco_id)
    out = _gen.generate(b, tmp_path / "pkg", asl=asl, build_artifacts=False)

    # Terraform root files present.
    for f in ("main.tf", "variables.tf", "versions.tf", "terraform.tfvars", "README.md",
              "bom.json", "asl.json"):
        assert (out / f).exists(), f"missing {f}"

    # main.tf vendors the eco-lite module and exposes customer endpoints.
    main = (out / "main.tf").read_text()
    assert 'source = "./modules/eco-lite"' in main
    assert "output \"frontend_url\"" in main

    # bom.json parses and matches.
    bj = json.loads((out / "bom.json").read_text())
    assert bj["eco_id"] == "agentic-ai-sirs" and bj["ok"] is True

    # asl.json is a real compiled state machine for an orchestrated eco.
    asl_written = json.loads((out / "asl.json").read_text())
    assert asl_written.get("StartAt") == "Start"

    # runtime source vendored + build script emitted and executable-flagged.
    assert (out / "build-and-push-runtime.sh").exists()
    assert (out / "runtime").is_dir()

    # eco-lite module: the real module is now vendored (Slice 2), not a PENDING placeholder.
    eco_lite = out / "modules" / "eco-lite"
    assert eco_lite.is_dir()
    assert (eco_lite / "main.tf").exists(), "eco-lite module not vendored"
    assert (eco_lite / "variables.tf").exists()
    assert not (eco_lite / "PENDING.txt").exists()


def test_generate_no_model2(tmp_path):
    # Model 1 / self-serve package: NO delegated-deploy (Model 2) artifacts.
    cfg = _bom.load_config("agentic-ai-sirs")
    b = _bom.analyze(cfg, region="us-east-1")
    out = _gen.generate(b, tmp_path / "pkg", asl=None, build_artifacts=False)
    assert not (out / "delivery").exists()


def test_generate_without_artifact_inputs_omits_them(tmp_path):
    # When no prebuilt-artifact inputs are passed, the package ships no lambdas/layer/frontend
    # (a "partial" export — the handler flags complete=False). generate() must not fabricate them.
    cfg = _bom.load_config("agentic-ai-sirs")
    b = _bom.analyze(cfg, region="us-east-1")
    out = _gen.generate(b, tmp_path / "pkg", asl=None, build_artifacts=False)
    assert not (out / "frontend").exists()
    assert not (out / "layer").exists()
    assert not (out / "lambdas").exists()


def test_generate_lays_prebuilt_artifacts(tmp_path):
    # With prebuilt-artifact inputs supplied (as the export Lambda does via Option A), the
    # package SHIPS the per-lambda zips, the shared layer, and the frontend so it is directly
    # deployable (the eco-lite module uploads them via aws_s3_object at apply).
    cfg = _bom.load_config("agentic-ai-sirs")
    b = _bom.analyze(cfg, region="us-east-1")

    # Fake prebuilt inputs (content is irrelevant to generate() — it just lays the files).
    lam_src = tmp_path / "lam"; lam_src.mkdir()
    for name in b.lambdas.ships:
        (lam_src / f"{name}.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)  # empty-zip stub
    layer_src = tmp_path / "layer"; layer_src.mkdir()
    (layer_src / "shared-layer.zip").write_bytes(b"PK\x05\x06" + b"\x00" * 18)
    fe_src = tmp_path / "fe"; fe_src.mkdir()
    (fe_src / "index.html").write_text("<html></html>")
    (fe_src / "_next").mkdir()
    (fe_src / "_next" / "app.js").write_text("//")

    out = _gen.generate(
        b, tmp_path / "pkg", asl=None, build_artifacts=False,
        lambdas_src=lam_src, layer_src=layer_src / "shared-layer.zip", frontend_src=fe_src,
    )

    # A zip per shipped lambda + the shared layer + the flattened frontend are present.
    for name in b.lambdas.ships:
        assert (out / "lambdas" / f"{name}.zip").exists(), f"missing lambdas/{name}.zip"
    assert (out / "layer" / "shared-layer.zip").exists()
    assert (out / "frontend" / "index.html").exists()
    assert (out / "frontend" / "_next" / "app.js").exists()

    # The eco-lite module uploads them via aws_s3_object (deployable, not out-of-band).
    lambdas_tf = (out / "modules" / "eco-lite" / "lambdas.tf").read_text()
    assert 'resource "aws_s3_object" "lambda"' in lambdas_tf
    assert 'resource "aws_s3_object" "layer"' in lambdas_tf
    frontend_tf = (out / "modules" / "eco-lite" / "frontend.tf").read_text()
    assert 'resource "aws_s3_object" "web"' in frontend_tf


def test_generate_ships_runtime_dockerfile(tmp_path):
    # The runtime build step (build-and-push-runtime.sh) requires runtime/Dockerfile — it must
    # be vendored into every package.
    cfg = _bom.load_config("agentic-ai-sirs")
    b = _bom.analyze(cfg, region="us-east-1")
    out = _gen.generate(b, tmp_path / "pkg", asl=None, build_artifacts=False)
    assert (out / "runtime" / "Dockerfile").exists(), "runtime/Dockerfile not vendored"


def test_generate_non_orchestrated_empty_asl(tmp_path):
    # An eco with no compiled ASL passed -> asl.json is empty (quorum/choreography).
    cfg = _bom.load_config("agentic-ai-sirs")
    b = _bom.analyze(cfg, region="us-east-1")
    out = _gen.generate(b, tmp_path / "pkg", asl=None, build_artifacts=False)
    assert (out / "asl.json").read_text() == ""
