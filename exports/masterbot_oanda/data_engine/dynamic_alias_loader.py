from __future__ import annotations

import json
import os

CACHE_PATH = "data_engine/alias_lookup_cache.json"


def load_dynamic_aliases(path: str = CACHE_PATH) -> dict[str, str]:
    if not os.path.exists(path):
        return {}

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)

        aliases = data.get("aliases", {})
        if not isinstance(aliases, dict):
            return {}

        return aliases

    except Exception:
        return {}
	
