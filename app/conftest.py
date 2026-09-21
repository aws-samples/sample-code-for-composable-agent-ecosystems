# Root conftest (pytest rootdir = app/, where pytest.ini lives).
#
# pytest imports the rootdir conftest BEFORE pytest_sessionstart, which is where the
# terminal reporter calls platform.python_version(). Because tests/ is a package,
# pytest inserts app/ onto sys.path, and app/platform/ then shadows the STDLIB
# `platform` module — breaking the reporter (and botocore/moto) with
# "module 'platform' has no attribute ...". Purge app/ here, at the earliest hook
# point, so the stdlib wins regardless of which tests are selected.
#
# The richer shim (registering `tessera_platform` -> app/platform, adding app/lambdas
# for auth/pricing) lives in tests/conftest.py; this file only removes the shadow.
import os as _os
import sys as _sys

_APP_DIR = _os.path.dirname(_os.path.abspath(__file__))  # .../app

_sys.path[:] = [p for p in _sys.path if _os.path.abspath(p or ".") != _APP_DIR]

_pl = _sys.modules.get("platform")
if _pl is not None and getattr(_pl, "__file__", "").startswith(_os.path.join(_APP_DIR, "platform")):
    del _sys.modules["platform"]
