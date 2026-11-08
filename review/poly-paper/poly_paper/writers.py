"""Entry points that correspond to the baseline fill writers.

runner.py and pm_fill.py are the only modules in the screened tree that insert
into fills. pm_nba_live, pm_me_track (including marv_cbb), and live_props call
pm_fill.fill and do not insert themselves. Those callers are routed here. Their
model and network code is not imported.
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
