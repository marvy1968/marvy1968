"""Quarantined library helper. Not a supported simulated-fill path.

`insert` persists only after `policy.decide_fill`, the same authorization and
identity decision the integrated writers use. It still does not run the
exported stake, edge, cap, or dedupe code. Supported fills go through
`poly_paper.integrated`.
"""
from __future__ import annotations

from .authorize import authorize_and_commit

CALLER_WRITER = {
    "runner": "runner",
    "pm_fill": "pm_fill",
    "pm_nba_live": "pm_fill",
    "pm_me_track": "pm_fill",
    "pm_me_track.marv_cbb": "pm_fill",
    "live_props.poly_fill": "pm_fill",
}


def insert(caller, db_path, request, *, policy_dir, switch_dir, env=None):
    if caller not in CALLER_WRITER:
        raise ValueError(f"unknown fill entry point: {caller}")
    writer = CALLER_WRITER[caller]
    body = dict(request)
    supplied = body.get("writer")
    body["writer_conflict"] = supplied not in (None, writer)
    body["writer"] = writer
    body["caller"] = caller
    return authorize_and_commit(
        db_path,
        body,
        policy_dir=policy_dir,
        switch_dir=switch_dir,
        env=env,
    )
