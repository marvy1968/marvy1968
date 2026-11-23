"""Pacer shrink.

Stand-in for ``me_shrink.apply_pacer``. The fitted NBA slope is -0.047.
A slope that flat would pin the probability on the ~60-64% lines, so the
live function skips it, tags ``pacer:nba:flat_skip``, and leaves the raw
pacer probability in place. This review copy does not change that model.
"""

from __future__ import annotations

NBA_SLOPE = -0.047


def apply_pacer(finding: dict, sport: str) -> dict:
    if sport == "nba" and NBA_SLOPE <= 0.0:
        finding.setdefault("tags", []).append("pacer:nba:flat_skip")
        finding["shrink"] = "flat_skip"
        return finding
    finding["shrink"] = "unchanged"
    return finding
