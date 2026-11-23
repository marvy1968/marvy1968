"""Prop and game-line alert decision.

Stand-in for ``value_scan.evaluate_live_props`` and ``value_scan.emit``
(VM emit around lines 1443-1465 and 1515, send gate around 1446-1462, live
EV gate around 1486-1500).

Today, for a pacer prop:

1. ``calib.adjust_finding`` calibrates ``fair_p`` and caps it at 65%.
2. ``pacer_hook.apply`` overwrites ``fair_p`` with the raw pacer probability.
3. ``me_shrink.apply_pacer`` is a no-op for NBA (``pacer:nba:flat_skip``).
4. ``live_wincap`` skips props, so the raw probability stays.
5. Emit sets ``pc = extra['pacer_p']`` and labels any non-None ``pc``
   ``calibrated``.
6. The card Win chance is ``paper.p_cal`` (pacer plus a paper shift clamped
   at -5 points).
7. The send gate fires when that probability is at least 62% or the Bovada
   edge is at least 4%. The live EV gate uses the same raw pacer probability.
8. The stake uses that same raw probability. The honest footnote is not read.

``AJ_HONEST_PCT`` off is that path. ``AJ_HONEST_PCT`` on caps ``fair_p``
again after the pacer overwrite, then sets ``p_final`` from the honest
real-hit map when one exists. The header, the Win chance, both gates, and
the stake all use ``p_final``. The label is ``calibrated`` only when that
map produced ``p_final``; otherwise it is ``model estimate`` and ``p_final``
stays at the 65% cap. The footnote is still attached, as a cross-check.

Game lines and props with no pacer probability never enter that branch.
They keep the consensus calibration they already have.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

from . import friendly, paper_conf
from .calib import FAIR_CAP, adjust_finding
from .live_wincap import cap as wincap
from .me_shrink import apply_pacer
from .pacer_hook import apply as apply_pacer_hook
from .stake import kelly_fraction

SEND_P = 0.62
SEND_EDGE = 0.04
# Decimal percents such as 0.58 - 0.54 are not exact in binary. A difference
# that is 4% on the page can land one ulp under SEND_EDGE. This tolerance is
# far smaller than a tenth of a percentage point.
_GATE_TOL = 1e-9
_ON = frozenset({"1", "true", "yes", "on"})


def honest_pct_enabled() -> bool:
    """True only when ``AJ_HONEST_PCT`` is explicitly on."""
    return os.environ.get("AJ_HONEST_PCT", "").strip().lower() in _ON


@dataclass(frozen=True)
class AlertDecision:
    kind: str
    flag: bool
    header_p: float
    card_p: float
    gate_p: float
    ev_p: float
    stake_p: float
    edge: float
    send: bool
    ev: float
    ev_ok: bool
    stake: float
    label: str
    header: str
    win_line: str
    footnote: str
    tags: tuple[str, ...]
    fair_p_after_calib: float
    fair_p_after_pacer: float | None
    capped_fair_p: float | None
    p_final: float | None
    p_cal: float
    real_hit: float | None


def _clip(probability: float) -> float:
    if probability < 0.0:
        return 0.0
    if probability > 1.0:
        return 1.0
    return probability


def bovada_edge(probability: float, bovada_implied: float) -> float:
    """Model probability minus the no-vig Bovada implied probability."""
    return probability - bovada_implied


def send_gate(probability: float, bovada_implied: float) -> tuple[bool, float]:
    """Send when ``probability`` is at least 62% or the Bovada edge is at least 4%."""
    edge = bovada_edge(probability, bovada_implied)
    return (probability >= SEND_P - _GATE_TOL) or (edge >= SEND_EDGE - _GATE_TOL), edge


def live_ev(probability: float, decimal_odds: float) -> float:
    """Expected value per 1 unit staked at a decimal price."""
    return probability * decimal_odds - 1.0


def live_ev_gate(probability: float, decimal_odds: float, min_ev: float = 0.0) -> tuple[bool, float]:
    """The live EV check. The probability is the only input the flag changes."""
    ev = live_ev(probability, decimal_odds)
    return ev >= min_ev, ev


def evaluate_live_props(
    consensus_fair_p: float,
    pacer_p: float | None,
    sport: str,
) -> dict:
    """Run calibration, then the pacer overwrite, then NBA shrink.

    The post-pacer 65% cap is applied by ``emit_prop`` when the flag is on,
    not here, so this function stays the same shape as today's VM order.
    """
    finding: dict = {
        "fair_p": float(consensus_fair_p),
        "tags": [],
        "extra": {},
    }
    adjust_finding(finding)
    finding["fair_p_after_calib"] = finding["fair_p"]
    if pacer_p is None:
        finding["fair_p_after_pacer"] = None
        return finding
    apply_pacer_hook(finding, pacer_p)
    apply_pacer(finding, sport)
    finding["fair_p_after_pacer"] = finding["fair_p"]
    # Props skip live_wincap. The returned value is the raw pacer probability.
    finding["fair_p"] = wincap(finding["fair_p"], "prop")
    return finding


def _decision(
    *,
    kind: str,
    flag: bool,
    header_p: float,
    card_p: float,
    gate_p: float,
    ev_p: float,
    stake_p: float,
    label: str,
    footnote: str,
    tags: list[str],
    fair_p_after_calib: float,
    fair_p_after_pacer: float | None,
    capped_fair_p: float | None,
    p_final: float | None,
    p_cal: float,
    real_hit: float | None,
    bovada_implied: float,
    decimal_odds: float,
    min_ev: float,
) -> AlertDecision:
    send, edge = send_gate(gate_p, bovada_implied)
    ev_ok, ev = live_ev_gate(ev_p, decimal_odds, min_ev)
    text = friendly.card(header_p, card_p, label, footnote)
    return AlertDecision(
        kind=kind,
        flag=flag,
        header_p=header_p,
        card_p=card_p,
        gate_p=gate_p,
        ev_p=ev_p,
        stake_p=stake_p,
        edge=edge,
        send=send,
        ev=ev,
        ev_ok=ev_ok,
        stake=kelly_fraction(stake_p, decimal_odds),
        label=label,
        header=text["header"],
        win_line=text["win_line"],
        footnote=text["footnote"],
        tags=tuple(tags),
        fair_p_after_calib=fair_p_after_calib,
        fair_p_after_pacer=fair_p_after_pacer,
        capped_fair_p=capped_fair_p,
        p_final=p_final,
        p_cal=p_cal,
        real_hit=real_hit,
    )


def emit_prop(
    consensus_fair_p: float,
    pacer_p: float | None,
    *,
    sport: str = "nba",
    shift: float = 0.0,
    real_hit: float | None = None,
    bovada_implied: float,
    decimal_odds: float,
    min_ev: float = 0.0,
) -> AlertDecision:
    """Decide one prop alert.

    ``real_hit`` is the honest map's probability. ``None`` means the map did
    not produce one. Bovada's implied probability and the decimal price are
    separate inputs: the 4% edge uses the implied probability, and the EV
    gate and the Kelly stake use the decimal price.
    """
    flag = honest_pct_enabled()
    finding = evaluate_live_props(consensus_fair_p, pacer_p, sport)
    stored = finding["extra"].get("pacer_p")
    anchor = stored if stored is not None else finding["fair_p"]
    paper = paper_conf.assess(anchor, shift=shift, real_hit=real_hit)

    if stored is None:
        # No pacer overwrite. The consensus calibration already capped this.
        probability = finding["fair_p"]
        return _decision(
            kind="prop",
            flag=flag,
            header_p=probability,
            card_p=probability,
            gate_p=probability,
            ev_p=probability,
            stake_p=probability,
            label="calibrated",
            footnote=paper.real_bit,
            tags=list(finding["tags"]),
            fair_p_after_calib=finding["fair_p_after_calib"],
            fair_p_after_pacer=None,
            capped_fair_p=None,
            p_final=None,
            p_cal=paper.p_cal,
            real_hit=paper.real_hit,
            bovada_implied=bovada_implied,
            decimal_odds=decimal_odds,
            min_ev=min_ev,
        )

    if flag:
        capped = min(FAIR_CAP, finding["fair_p"])
        finding["fair_p"] = capped
        if paper.mapped and paper.real_hit is not None:
            p_final = _clip(paper.real_hit)
            label = "calibrated"
        else:
            p_final = capped
            label = "model estimate"
        return _decision(
            kind="prop",
            flag=True,
            header_p=p_final,
            card_p=p_final,
            gate_p=p_final,
            ev_p=p_final,
            stake_p=p_final,
            label=label,
            footnote=paper.real_bit,
            tags=list(finding["tags"]),
            fair_p_after_calib=finding["fair_p_after_calib"],
            fair_p_after_pacer=finding["fair_p_after_pacer"],
            capped_fair_p=capped,
            p_final=p_final,
            p_cal=paper.p_cal,
            real_hit=paper.real_hit,
            bovada_implied=bovada_implied,
            decimal_odds=decimal_odds,
            min_ev=min_ev,
        )

    # Flag off. Identical to today: the cap was overwritten, and any
    # non-None pacer probability is labeled calibrated.
    raw = float(stored)
    return _decision(
        kind="prop",
        flag=False,
        header_p=finding["fair_p"],
        card_p=paper.p_cal,
        gate_p=raw,
        ev_p=raw,
        stake_p=raw,
        label="calibrated",
        footnote=paper.real_bit,
        tags=list(finding["tags"]),
        fair_p_after_calib=finding["fair_p_after_calib"],
        fair_p_after_pacer=finding["fair_p_after_pacer"],
        capped_fair_p=None,
        p_final=None,
        p_cal=paper.p_cal,
        real_hit=paper.real_hit,
        bovada_implied=bovada_implied,
        decimal_odds=decimal_odds,
        min_ev=min_ev,
    )


def emit_game_line(
    consensus_fair_p: float,
    *,
    bovada_implied: float,
    decimal_odds: float,
    real_hit: float | None = None,
    shift: float = 0.0,
    min_ev: float = 0.0,
) -> AlertDecision:
    """Game line. ``real_hit`` and ``shift`` are ignored.

    The decision records whether ``AJ_HONEST_PCT`` is set. The probability,
    the label, both gates, and the stake stay on the calibrated game-line
    cap either way. There is no pacer overwrite on this path.
    """
    del real_hit, shift
    finding: dict = {"fair_p": float(consensus_fair_p), "tags": [], "extra": {}}
    adjust_finding(finding)
    after_calib = finding["fair_p"]
    finding["fair_p"] = wincap(finding["fair_p"], "game")
    probability = finding["fair_p"]
    paper = paper_conf.assess(probability, shift=0.0, real_hit=None)
    return _decision(
        kind="game",
        flag=honest_pct_enabled(),
        header_p=probability,
        card_p=probability,
        gate_p=probability,
        ev_p=probability,
        stake_p=probability,
        label="calibrated",
        footnote=paper.real_bit,
        tags=(),
        fair_p_after_calib=after_calib,
        fair_p_after_pacer=None,
        capped_fair_p=None,
        p_final=None,
        p_cal=paper.p_cal,
        real_hit=None,
        bovada_implied=bovada_implied,
        decimal_odds=decimal_odds,
        min_ev=min_ev,
    )
