"""Paper confidence for a prop alert.

Stand-in for ``paper_conf.py`` on the VM. The live docstring at lines 44-48
says the honest footnote is display-only. This module keeps that split:
``p_cal`` is the pacer probability plus a paper shift, and ``real_bit`` is
never copied into ``p_cal``.

The paper shift cannot be more negative than ``MAX_SHIFT`` (-5 percentage
points). ``real_hit`` is the real-hit map when one exists, typically about
55-58% on the alerts whose header prints much higher. ``real_bit`` is the
footnote for that map. Callers that honor ``AJ_HONEST_PCT`` read ``real_hit``
into ``p_final`` themselves. This module does not.
"""

from __future__ import annotations

from dataclasses import dataclass

# -5 percentage points. The paper shift cannot go below this.
MAX_SHIFT = -0.05


@dataclass(frozen=True)
class Assessment:
    """Card probability, plus the honest footnote today's gates do not read.

    ``real_bit`` is the only honest number on today's alert. It is never
    used by a gate, a stake, or ``p_cal``.
    """

    p_cal: float
    real_hit: float | None
    real_bit: str
    mapped: bool


def _clip(probability: float) -> float:
    if probability < 0.0:
        return 0.0
    if probability > 1.0:
        return 1.0
    return probability


def assess(pacer_p: float, shift: float = 0.0, real_hit: float | None = None) -> Assessment:
    """Build ``p_cal`` and the footnote.

    ``shift`` is clamped at ``MAX_SHIFT``. A real-hit value is stored beside
    ``p_cal`` and is not written into it.
    """
    applied = shift if shift > MAX_SHIFT else MAX_SHIFT
    p_cal = _clip(float(pacer_p) + applied)
    if real_hit is None:
        return Assessment(
            p_cal=p_cal,
            real_hit=None,
            real_bit="honest real-hit: none",
            mapped=False,
        )
    hit = _clip(float(real_hit))
    return Assessment(
        p_cal=p_cal,
        real_hit=hit,
        real_bit=f"honest real-hit {hit:.0%}",
        mapped=True,
    )
