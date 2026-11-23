"""Alert text.

Stand-in for ``friendly.card`` (VM lines ~216-244). The Win chance line is
whatever probability the caller passes. Today that is ``paper.p_cal``. With
``AJ_HONEST_PCT`` the caller passes ``p_final`` instead.
"""

from __future__ import annotations


def _pct(probability: float) -> str:
    return f"{probability:.0%}"


def card(header_p: float, win_p: float, label: str, footnote: str) -> dict:
    """Header, Win chance, and the footnote cross-check."""
    header = f"🛡️ {_pct(header_p)} · win {_pct(win_p)} ({label})"
    win_line = f"Win chance: {_pct(win_p)}"
    return {"header": header, "win_line": win_line, "footnote": footnote}
