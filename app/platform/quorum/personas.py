"""Persona provider — loads the scrutineer pool the quorum convenes from.

Two sources, both returning `list[Persona]`:
  * `from_seed_file()` — reads catalog-content/scrutineers.json (offline / tests / CLI).
  * `from_catalog()` — reads the live scrutineer catalog via CatalogStore (production),
    resolving the active version. Import of CatalogStore is lazy so this module has no AWS
    dependency when only the seed file is used.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from .models import Persona


def from_seed_file(path: str | None = None) -> list[Persona]:
    if path is None:
        # Default seed location. The catalog content lives under app/content/catalog-content/
        # in the current layout; fall back to a repo-root/catalog-content/ copy if present
        # (older layout) so the resolver is robust to either.
        app_dir = Path(__file__).resolve().parents[2]  # .../app
        candidates = [
            app_dir / "content" / "catalog-content" / "scrutineers.json",
            app_dir.parent / "catalog-content" / "scrutineers.json",
            app_dir / "catalog-content" / "scrutineers.json",
        ]
        path = str(next((c for c in candidates if c.exists()), candidates[0]))
    data = json.loads(Path(path).read_text())
    return [Persona.from_record(r) for r in data.get("scrutineers", [])]


def from_catalog(version: str | None = None) -> list[Persona]:
    """Load personas from the live scrutineer catalog (AgentCore Memory)."""
    from tessera_platform.catalog.engine import CatalogStore  # lazy — avoids AWS import offline
    store = CatalogStore(version=version)
    recs = store.load_type("scrutineer", resolve=True)
    return [Persona.from_record(r) for r in recs]


def load_pool(source: str = "seed", version: str | None = None, path: str | None = None) -> list[Persona]:
    if source == "catalog":
        return from_catalog(version=version)
    return from_seed_file(path=path)
