"""Proven-logic gate: Marv prints a probability / confidence only for logic with walk-forward proof.

The bar (design note: proven-logic.md in the marv-predict workspace, summarized here):
  1. Walk-forward / held-out test: nothing tuned on the graded games.
  2. n >= 100 graded bets.
  3. ROI > 0 after the vig at real book prices (Bovada; -110 where historical total juice is missing).
  4. Stable: above break-even in most test seasons/eras, and no larger independent test of the same rule
     below break-even.
  OR (calibration route) the model's own probability has a Brier score <= the market's no-vig Brier on the
  same games (n >= 300), with the model carrying >= 50% of the number (a market-anchored blend is the
  market's probability, not Marv's proven logic, so it doesn't count).

A win rate alone never qualifies: CFB ratings ML hits ~75% (92% at 80%+ confidence) and still loses money
at Bovada, and the market's no-vig Brier beats the model's (0.168 vs 0.184, 1,469 games, 2021-26).

When a signal qualifies, the number printed is the signal's BACKTESTED hit rate with its sample and ROI
(e.g. "53.5% hit (backtest n=1,829, ROI +2.1% @-110)"), never the model's raw output. Everything else in
a gated sport prints UNPROVEN instead of a % (and no fair price / edge / Kelly stake, which are the same
number in disguise).

Gated sports: PROVEN_GATE_SPORTS (comma list, "all" or "none"; default "cfb" -- college football first).
This module never touches the March_edge bridge (marv/bridge.py) or its alert types.
"""

import os
from dataclasses import dataclass

UNPROVEN = "unproven — no %"
MIN_N = 100


@dataclass(frozen=True)
class Evidence:
    hit: float  # backtested hit rate of the rule
    n: int  # graded bets
    roi: float  # ROI per unit after vig
    price: str  # price basis of the ROI
    source: str  # where the number comes from

    def label(self) -> str:
        return f"{self.hit:.1%} hit (backtest n={self.n:,}, ROI {self.roi:+.1%} {self.price})"

    def meets_bar(self) -> bool:
        return self.n >= MIN_N and self.roi > 0


# (sport, market, signal) -> evidence. market: ml | spread | total | prop | live_ml | live_total | live_prop.
# Only rules that meet the bar are listed; anything not here prints UNPROVEN in a gated sport.
PROVEN: dict[tuple[str, str, str], Evidence] = {
    # Over trend (both teams over the closing total in 2+ of their last 3) -> UNDER. Most conservative of three
    # samples: all FBS 2014-25 consensus closes 53.5% of 1,829 (+2.1%); top-30 walk-forward 2021-26 55.3% of 365
    # (+5.7%, 4 of 6 seasons above 52.4%); ou_tags 2017-26 54.9% of 994.
    ("cfb", "total", "OVER-FADE"): Evidence(0.535, 1829, 0.021, "@-110",
                                            "ANALYSIS.md line-movement table; cfb_power_bt 2021-26"),
    # Bovado total 0.5+ pts off Pinnacle/BetCRIS -> the better side at Bovado (Bovado's real juice), 2014-19.
    ("cfb", "total", "GAP"): Evidence(0.544, 2340, 0.046, "at Bovado juice", "ANALYSIS.md sharp-book table, 2014-19"),
}
# Refinements nested inside a proven rule print the parent's (lower) number, not their own smaller-sample one.
PARENT = {"OVER-FADE+MOVE": "OVER-FADE", "OVER-FADE+INFLATED": "OVER-FADE"}


def gated_sports() -> set[str] | str:
    raw = os.environ.get("PROVEN_GATE_SPORTS", "cfb").strip().lower()
    if raw in ("all", "*"):
        return "all"
    if raw in ("", "none", "off"):
        return set()
    return {s.strip() for s in raw.split(",") if s.strip()}


def gated(sport: str | None) -> bool:
    g = gated_sports()
    return g == "all" or (sport or "").lower() in g


def evidence(sport: str | None, market: str, signal: str | None = None) -> Evidence | None:
    if not signal:
        return None
    sig = PARENT.get(signal, signal)
    ev = PROVEN.get(((sport or "").lower(), market, sig))
    return ev if ev and ev.meets_bar() else None


def show_prob(sport: str | None, market: str, signal: str | None = None) -> bool:
    """True when a raw model % may be printed (sport not gated). Proven signals print their backtest instead."""
    return not gated(sport)


def pct(sport: str | None, market: str, p: float | None = None, signal: str | None = None, fmt: str = "{:.0%}") -> str:
    """Text for the spot where a probability would go."""
    if not gated(sport):
        return fmt.format(p) if p is not None else ""
    ev = evidence(sport, market, signal)
    return ev.label() if ev else UNPROVEN
