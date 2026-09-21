# ─────────────────────────────────────────────────────────────────────────────
# Import shim (test environment only).
#
# The platform package lives at app/platform/ but is imported as `tessera_platform`
# (the name it ships under in the Lambda shared layer — see scripts/package-lambdas.sh
# and app/lambdas/shared_layer_manifest.txt). Tests import `tessera_platform.*` and a
# couple of Lambda shared modules (`auth`, `pricing`).
#
# We register these on sys.path/sys.modules explicitly rather than putting app/ on
# sys.path. Adding app/ would let `import platform` resolve to app/platform/ and shadow
# the STDLIB `platform` module, which botocore/moto import at runtime — the cause of the
# "module 'platform' has no attribute 'system'" collection failures. Mapping the package
# under its real import name (tessera_platform) sidesteps the shadow entirely.
import importlib.util
import os as _os
import sys as _sys

_APP_DIR = _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__)))  # .../app
_PLATFORM_DIR = _os.path.join(_APP_DIR, "platform")
_LAMBDAS_DIR = _os.path.join(_APP_DIR, "lambdas")

# Purge app/ (and cwd "") from sys.path so `import platform` resolves to the STDLIB
# module, not app/platform/. pytest inserts the rootdir (app/) because tests/ is a
# package (has __init__.py); that insertion is what shadows stdlib `platform`.
_sys.path[:] = [p for p in _sys.path if _os.path.abspath(p or ".") != _APP_DIR]
# Drop any already-imported wrong `platform` (app/platform) so the stdlib re-imports.
_pl = _sys.modules.get("platform")
if _pl is not None and getattr(_pl, "__file__", "").startswith(_PLATFORM_DIR):
    del _sys.modules["platform"]

# Register `tessera_platform` as a package pointing at app/platform/ (without adding
# app/ to sys.path, which would shadow the stdlib `platform` module).
if "tessera_platform" not in _sys.modules and _os.path.isdir(_PLATFORM_DIR):
    _spec = importlib.util.spec_from_file_location(
        "tessera_platform",
        _os.path.join(_PLATFORM_DIR, "__init__.py"),
        submodule_search_locations=[_PLATFORM_DIR],
    )
    _mod = importlib.util.module_from_spec(_spec)
    _sys.modules["tessera_platform"] = _mod
    _spec.loader.exec_module(_mod)

# Shared Lambda modules (auth, pricing) live in app/lambdas/ and are imported by name.
# Safe to add: app/lambdas has no `platform` module, so no stdlib shadow.
if _os.path.isdir(_LAMBDAS_DIR) and _LAMBDAS_DIR not in _sys.path:
    _sys.path.insert(0, _LAMBDAS_DIR)

import json
import os
import pytest
import boto3
from moto import mock_aws

os.environ["AWS_DEFAULT_REGION"] = "us-east-1"
os.environ["AWS_ACCESS_KEY_ID"] = "testing"  # nosec B105 — mock credential for offline tests
os.environ["AWS_SECRET_ACCESS_KEY"] = "testing"  # nosec B105  # pragma: allowlist secret


@pytest.fixture
def aws_env(monkeypatch):
    """Mock AWS environment for all services."""
    with mock_aws():
        # Create DynamoDB tables
        dynamodb = boto3.resource("dynamodb", region_name="us-east-1")

        dynamodb.create_table(
            TableName="tessera-dev-memory",
            KeySchema=[
                {"AttributeName": "namespace", "KeyType": "HASH"},
                {"AttributeName": "entry_id", "KeyType": "RANGE"},
            ],
            AttributeDefinitions=[
                {"AttributeName": "namespace", "AttributeType": "S"},
                {"AttributeName": "entry_id", "AttributeType": "S"},
                {"AttributeName": "session_id", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "session-index",
                "KeySchema": [
                    {"AttributeName": "session_id", "KeyType": "HASH"},
                    {"AttributeName": "entry_id", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )

        dynamodb.create_table(
            TableName="tessera-dev-registry",
            KeySchema=[{"AttributeName": "agent_id", "KeyType": "HASH"}],
            AttributeDefinitions=[
                {"AttributeName": "agent_id", "AttributeType": "S"},
                {"AttributeName": "ecosystem", "AttributeType": "S"},
            ],
            GlobalSecondaryIndexes=[{
                "IndexName": "ecosystem-index",
                "KeySchema": [
                    {"AttributeName": "ecosystem", "KeyType": "HASH"},
                    {"AttributeName": "agent_id", "KeyType": "RANGE"},
                ],
                "Projection": {"ProjectionType": "ALL"},
            }],
            BillingMode="PAY_PER_REQUEST",
        )

        monkeypatch.setenv("MEMORY_TABLE", "tessera-dev-memory")
        monkeypatch.setenv("REGISTRY_TABLE", "tessera-dev-registry")
        monkeypatch.setenv("TOOLS_TABLE", "tessera-dev-registry")

        yield dynamodb
