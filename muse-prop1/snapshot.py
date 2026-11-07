"""Compatibility shim. The snapshot schema is marv.prop_snapshot.v1.

The previous muse-prop1.shared-snapshot.v1 draft is not a comparable input.
Use prop_snapshot.py.
"""
from prop_snapshot import (  # noqa: F401
    SCHEMA_IN, SCHEMA_OUT, ReplayError, load_snapshot, replay_file, replay_rows,
)

def load_legacy(_payload: dict) -> dict:
    raise ReplayError("snapshot schema must be marv.prop_snapshot.v1")
