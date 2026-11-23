"""Pacer overwrite.

Stand-in for ``pacer_hook.apply``. It replaces the calibrated ``fair_p``
with the raw pacer probability, so the 65% cap from ``calib`` does not
survive this step.
"""

from __future__ import annotations


def apply(finding: dict, pacer_p: float) -> dict:
    finding["fair_p"] = float(pacer_p)
    extra = finding.setdefault("extra", {})
    extra["pacer_p"] = float(pacer_p)
    return finding
