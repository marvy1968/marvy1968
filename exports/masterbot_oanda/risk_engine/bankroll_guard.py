from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import List, Optional


@dataclass
class GuardConfig:
    max_active_bets_per_game: int = 2
    max_bets_per_hour: int = 6
    max_total_exposure_fraction: float = 0.12
    max_game_exposure_fraction: float = 0.03
    max_daily_risk_fraction: float = 0.08
    cooldown_minutes_per_game: int = 10
    min_confidence_score: float = 70.0
    min_edge_pct: float = 0.02


@dataclass
class ProposedBet:
    game_id: int
    market_key: str
    stake_fraction: float
    edge_pct: float
    confidence_score: float
    event_time_utc: str
    is_live: bool = False


@dataclass
class ExistingBet:
    game_id: int
    market_key: str
    stake_fraction: float
    placed_at_utc: str
    status: str = "open"


@dataclass
class GuardDecision:
    approved: bool
    reason: str
    total_open_exposure: float
    game_open_exposure: float
    recent_bets_last_hour: int
    active_bets_this_game: int


def _parse_utc(ts: str) -> datetime:
    return datetime.fromisoformat(ts.replace("Z", "+00:00")).astimezone(timezone.utc)


def _now_utc() -> datetime:
    return datetime.now(timezone.utc)


def _sum_open_exposure(bets: List[ExistingBet]) -> float:
    return sum(b.stake_fraction for b in bets if b.status == "open")


def _sum_game_exposure(bets: List[ExistingBet], game_id: int) -> float:
    return sum(b.stake_fraction for b in bets if b.status == "open" and b.game_id == game_id)


def _count_recent_bets_last_hour(bets: List[ExistingBet], now: datetime) -> int:
    cutoff = now - timedelta(hours=1)
    count = 0
    for bet in bets:
        if bet.status != "open":
            continue
        placed = _parse_utc(bet.placed_at_utc)
        if placed >= cutoff:
            count += 1
    return count


def _count_active_bets_this_game(bets: List[ExistingBet], game_id: int) -> int:
    return sum(1 for b in bets if b.status == "open" and b.game_id == game_id)


def _find_recent_game_bet(
    bets: List[ExistingBet],
    game_id: int,
    now: datetime,
    cooldown_minutes: int,
) -> Optional[ExistingBet]:
    cutoff = now - timedelta(minutes=cooldown_minutes)
    candidates = []
    for bet in bets:
        if bet.status != "open":
            continue
        if bet.game_id != game_id:
            continue
        placed = _parse_utc(bet.placed_at_utc)
        if placed >= cutoff:
            candidates.append((placed, bet))
    if not candidates:
        return None
    candidates.sort(key=lambda x: x[0], reverse=True)
    return candidates[0][1]


def approve_bet(
    proposed: ProposedBet,
    existing_bets: List[ExistingBet],
    config: GuardConfig,
    now_utc: Optional[str] = None,
) -> GuardDecision:
    now = _parse_utc(now_utc) if now_utc else _now_utc()

    total_open_exposure = _sum_open_exposure(existing_bets)
    game_open_exposure = _sum_game_exposure(existing_bets, proposed.game_id)
    recent_bets_last_hour = _count_recent_bets_last_hour(existing_bets, now)
    active_bets_this_game = _count_active_bets_this_game(existing_bets, proposed.game_id)

    if proposed.edge_pct < config.min_edge_pct:
        return GuardDecision(
            approved=False,
            reason=f"Rejected: edge {proposed.edge_pct:.2%} is below minimum {config.min_edge_pct:.2%}.",
            total_open_exposure=total_open_exposure,
            game_open_exposure=game_open_exposure,
            recent_bets_last_hour=recent_bets_last_hour,
            active_bets_this_game=active_bets_this_game,
        )

    if proposed.confidence_score < config.min_confidence_score:
        return GuardDecision(
            approved=False,
            reason=(
                f"Rejected: confidence {proposed.confidence_score:.1f} "
                f"is below minimum {config.min_confidence_score:.1f}."
            ),
            total_open_exposure=total_open_exposure,
            game_open_exposure=game_open_exposure,
            recent_bets_last_hour=recent_bets_last_hour,
            active_bets_this_game=active_bets_this_game,
        )

    if recent_bets_last_hour >= config.max_bets_per_hour:
        return GuardDecision(
            approved=False,
            reason=(
                f"Rejected: hourly bet cap reached "
                f"({recent_bets_last_hour}/{config.max_bets_per_hour})."
            ),
            total_open_exposure=total_open_exposure,
            game_open_exposure=game_open_exposure,
            recent_bets_last_hour=recent_bets_last_hour,
            active_bets_this_game=active_bets_this_game,
        )

    if active_bets_this_game >= config.max_active_bets_per_game:
        return GuardDecision(
            approved=False,
            reason=(
                f"Rejected: active bet cap for game reached "
                f"({active_bets_this_game}/{config.max_active_bets_per_game})."
            ),
            total_open_exposure=total_open_exposure,
            game_open_exposure=game_open_exposure,
            recent_bets_last_hour=recent_bets_last_hour,
            active_bets_this_game=active_bets_this_game,
        )

    recent_game_bet = _find_recent_game_bet(
        existing_bets,
        proposed.game_id,
        now,
        config.cooldown_minutes_per_game,
    )
    if recent_game_bet is not None:
        return GuardDecision(
            approved=False,
            reason=(
                f"Rejected: cooldown active for game_id={proposed.game_id} "
                f"within last {config.cooldown_minutes_per_game} minutes."
            ),
            total_open_exposure=total_open_exposure,
            game_open_exposure=game_open_exposure,
            recent_bets_last_hour=recent_bets_last_hour,
            active_bets_this_game=active_bets_this_game,
        )

    if (total_open_exposure + proposed.stake_fraction) > config.max_total_exposure_fraction:
        return GuardDecision(
            approved=False,
            reason=(
                f"Rejected: total exposure would become "
                f"{(total_open_exposure + proposed.stake_fraction):.2%}, "
                f"above cap {config.max_total_exposure_fraction:.2%}."
            ),
            total_open_exposure=total_open_exposure,
            game_open_exposure=game_open_exposure,
            recent_bets_last_hour=recent_bets_last_hour,
            active_bets_this_game=active_bets_this_game,
        )

    if (game_open_exposure + proposed.stake_fraction) > config.max_game_exposure_fraction:
        return GuardDecision(
            approved=False,
            reason=(
                f"Rejected: game exposure would become "
                f"{(game_open_exposure + proposed.stake_fraction):.2%}, "
                f"above cap {config.max_game_exposure_fraction:.2%}."
            ),
            total_open_exposure=total_open_exposure,
            game_open_exposure=game_open_exposure,
            recent_bets_last_hour=recent_bets_last_hour,
            active_bets_this_game=active_bets_this_game,
        )

    if proposed.stake_fraction > config.max_daily_risk_fraction:
        return GuardDecision(
            approved=False,
            reason=(
                f"Rejected: single proposed stake {proposed.stake_fraction:.2%} "
                f"exceeds daily risk guardrail {config.max_daily_risk_fraction:.2%}."
            ),
            total_open_exposure=total_open_exposure,
            game_open_exposure=game_open_exposure,
            recent_bets_last_hour=recent_bets_last_hour,
            active_bets_this_game=active_bets_this_game,
        )

    return GuardDecision(
        approved=True,
        reason=(
            f"Approved: edge={proposed.edge_pct:.2%}, "
            f"confidence={proposed.confidence_score:.1f}, "
            f"stake={proposed.stake_fraction:.2%}."
        ),
        total_open_exposure=total_open_exposure,
        game_open_exposure=game_open_exposure,
        recent_bets_last_hour=recent_bets_last_hour,
        active_bets_this_game=active_bets_this_game,
    )
