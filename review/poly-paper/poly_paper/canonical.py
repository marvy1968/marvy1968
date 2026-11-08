"""Canonical JSON, hashes, and name/date normalization for the review boundary.

Name normalization used for identity is intentionally stricter about hyphens than the
baseline mapper: hyphens and slashes become spaces so "Samford-Bulldogs" and
"Samford Bulldogs" are the same tokens. It does not invent team ids or expand
abbreviations.
"""
from __future__ import annotations

import hashlib
import json
import re
import unicodedata
from datetime import datetime
from zoneinfo import ZoneInfo

CT = ZoneInfo("America/Chicago")


def canonical_json(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=_reject)


def _reject(obj):
    raise TypeError(f"not JSON-serializable: {type(obj).__name__}")


def sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def sha256_obj(value) -> str:
    return sha256_text(canonical_json(value))


def normalize_name(value) -> str:
    text = unicodedata.normalize("NFKC", "" if value is None else str(value))
    text = text.replace("-", " ").replace("/", " ").replace("&", " and ")
    text = text.lower()
    text = re.sub(r"[^a-z0-9 ]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def ct_date(value):
    """America/Chicago calendar date for an aware ISO timestamp. Naive values are unknown."""
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return None
    return parsed.astimezone(CT).date().isoformat()


def slug_date(slug):
    if not isinstance(slug, str):
        return None
    match = re.search(r"(20\d{2}-\d{2}-\d{2})$", slug.strip())
    return match.group(1) if match else None
