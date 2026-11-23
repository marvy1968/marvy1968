"""Live win-probability cap.

Stand-in for ``live_wincap``. Game lines are capped at 65%. Props are
returned unchanged, which is why a pacer prop can still print 78% after
``calib`` has already capped the consensus.
"""

from __future__ import annotations

from .calib import FAIR_CAP


def cap(probability: float, kind: str) -> float:
    if kind == "prop":
        return probability
    if kind != "game":
        raise ValueError(f"unknown line kind: {kind}")
    if probability > FAIR_CAP:
        return FAIR_CAP
    return probability
