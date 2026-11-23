"""Consensus calibration.

Stand-in for ``calib.adjust_finding`` on the VM. That function calibrates
the consensus ``fair_p`` and caps it at 65%. ``pacer_hook.apply`` then
overwrites ``fair_p``, which is how the cap is lost on pacer props.

The shrink below is a review stand-in, not a fitted copy of the VM map.
"""

from __future__ import annotations

FAIR_CAP = 0.65


def calibrate(probability: float) -> float:
    """Pull a consensus probability a fifth of the way toward 0.55, then cap."""
    shrunk = probability + 0.20 * (0.55 - probability)
    if shrunk < 0.0:
        return 0.0
    if shrunk > FAIR_CAP:
        return FAIR_CAP
    return shrunk


def adjust_finding(finding: dict) -> dict:
    """Calibrate ``finding['fair_p']`` and cap it at 65%."""
    finding["fair_p"] = calibrate(float(finding["fair_p"]))
    finding["calibrated_by"] = "calib"
    return finding
