from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any


@dataclass
class ConsensusResult:
    market_type: str
    consensus_line: float | None
    consensus_over_prob: float | None = None
    consensus_under_prob: float | None = None
    consensus_home_prob: float | None = None
    consensus_away_prob: float | None = None
    num_books: int = 0


@dataclass
class OutlierResult:
    is_outlier: bool
    delta: float
    reason: str


def american_to_prob(odds: int) -> float:
    if odds > 0:
        return 100.0 / (odds + 100.0)
    return abs(odds) / (abs(odds) + 100.0)


def american_to_decimal(odds: int) -> float:
    if odds > 0:
        return 1.0 + (odds / 100.0)
    return 1.0 + (100.0 / abs(odds))


def calculate_fair_probs(side_a_odds: int, side_b_odds: int) -> tuple[float, float]:
    a_prob = american_to_prob(side_a_odds)
    b_prob = american_to_prob(side_b_odds)
    total = a_prob + b_prob
    if total <= 0:
        return 0.5, 0.5
    return a_prob / total, b_prob / total


def calculate_ev(model_prob: float, odds: int) -> float:
    decimal_odds = american_to_decimal(odds)
    return (model_prob * (decimal_odds - 1.0)) - (1.0 - model_prob)


def _paired_outcomes(outcomes: list[dict[str, Any]], side_a_name: str, side_b_name: str) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    side_a = next((o for o in outcomes if str(o.get("name", "")).lower() == side_a_name.lower()), None)
    side_b = next((o for o in outcomes if str(o.get("name", "")).lower() == side_b_name.lower()), None)
    return side_a, side_b


def build_totals_consensus(matched_event: dict[str, Any]) -> ConsensusResult:
    lines: list[float] = []
    over_probs: list[float] = []
    under_probs: list[float] = []

    for bookmaker in matched_event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market.get("key") != "totals":
                continue

            outcomes = market.get("outcomes", [])
            over, under = _paired_outcomes(outcomes, "Over", "Under")
            if not over or not under:
                continue

            line = over.get("point", under.get("point"))
            if line is None:
                continue

            try:
                over_odds = int(over["price"])
                under_odds = int(under["price"])
                fair_over, fair_under = calculate_fair_probs(over_odds, under_odds)
                lines.append(float(line))
                over_probs.append(fair_over)
                under_probs.append(fair_under)
            except Exception:
                continue

    if not lines:
        return ConsensusResult(
            market_type="totals",
            consensus_line=None,
            consensus_over_prob=None,
            consensus_under_prob=None,
            num_books=0,
        )

    return ConsensusResult(
        market_type="totals",
        consensus_line=float(median(lines)),
        consensus_over_prob=sum(over_probs) / len(over_probs),
        consensus_under_prob=sum(under_probs) / len(under_probs),
        num_books=len(lines),
    )


def build_h2h_consensus(matched_event: dict[str, Any]) -> ConsensusResult:
    home_team = str(matched_event.get("home_team", ""))
    away_team = str(matched_event.get("away_team", ""))

    home_probs: list[float] = []
    away_probs: list[float] = []

    for bookmaker in matched_event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market.get("key") != "h2h":
                continue

            outcomes = market.get("outcomes", [])
            home = next((o for o in outcomes if str(o.get("name", "")) == home_team), None)
            away = next((o for o in outcomes if str(o.get("name", "")) == away_team), None)
            if not home or not away:
                continue

            try:
                home_odds = int(home["price"])
                away_odds = int(away["price"])
                fair_home, fair_away = calculate_fair_probs(home_odds, away_odds)
                home_probs.append(fair_home)
                away_probs.append(fair_away)
            except Exception:
                continue

    if not home_probs:
        return ConsensusResult(
            market_type="h2h",
            consensus_line=None,
            consensus_home_prob=None,
            consensus_away_prob=None,
            num_books=0,
        )

    return ConsensusResult(
        market_type="h2h",
        consensus_line=None,
        consensus_home_prob=sum(home_probs) / len(home_probs),
        consensus_away_prob=sum(away_probs) / len(away_probs),
        num_books=len(home_probs),
    )


def build_spreads_consensus(matched_event: dict[str, Any]) -> ConsensusResult:
    home_team = str(matched_event.get("home_team", ""))
    away_team = str(matched_event.get("away_team", ""))

    lines: list[float] = []
    home_probs: list[float] = []
    away_probs: list[float] = []

    for bookmaker in matched_event.get("bookmakers", []):
        for market in bookmaker.get("markets", []):
            if market.get("key") != "spreads":
                continue

            outcomes = market.get("outcomes", [])
            home = next((o for o in outcomes if str(o.get("name", "")) == home_team), None)
            away = next((o for o in outcomes if str(o.get("name", "")) == away_team), None)
            if not home or not away:
                continue

            home_line = home.get("point")
            away_line = away.get("point")
            if home_line is None or away_line is None:
                continue

            try:
                home_odds = int(home["price"])
                away_odds = int(away["price"])
                fair_home, fair_away = calculate_fair_probs(home_odds, away_odds)
                lines.append(float(home_line))
                home_probs.append(fair_home)
                away_probs.append(fair_away)
            except Exception:
                continue

    if not lines:
        return ConsensusResult(
            market_type="spreads",
            consensus_line=None,
            consensus_home_prob=None,
            consensus_away_prob=None,
            num_books=0,
        )

    return ConsensusResult(
        market_type="spreads",
        consensus_line=float(median(lines)),
        consensus_home_prob=sum(home_probs) / len(home_probs),
        consensus_away_prob=sum(away_probs) / len(away_probs),
        num_books=len(lines),
    )


def detect_line_outlier(
    offer_line: float,
    consensus_line: float | None,
    threshold: float,
    label: str,
) -> OutlierResult:
    if consensus_line is None:
        return OutlierResult(False, 0.0, f"No consensus available for {label}.")

    delta = offer_line - consensus_line
    is_outlier = abs(delta) >= threshold
    reason = (
        f"{label} outlier detected: offer_line={offer_line:.2f}, "
        f"consensus_line={consensus_line:.2f}, delta={delta:+.2f}"
        if is_outlier
        else f"{label} not an outlier: delta={delta:+.2f}"
    )
    return OutlierResult(is_outlier, delta, reason)


def scan_books_for_best_edge(
    matched_event: dict[str, Any],
    model_probs: dict[str, float],
    min_edge: float = 0.03,
) -> dict[str, Any] | None:
    best_candidate: dict[str, Any] | None = None
    highest_ev = 0.0

    totals_consensus = build_totals_consensus(matched_event)
    spreads_consensus = build_spreads_consensus(matched_event)
    h2h_consensus = build_h2h_consensus(matched_event)

    home_team = str(matched_event.get("home_team", ""))
    away_team = str(matched_event.get("away_team", ""))

    for bookmaker in matched_event.get("bookmakers", []):
        book_name = bookmaker.get("key", "unknown_book")

        for market in bookmaker.get("markets", []):
            market_key = market.get("key")
            outcomes = market.get("outcomes", [])

            if len(outcomes) != 2:
                continue

            # TOTALS
            if market_key == "totals":
                over = next((o for o in outcomes if str(o.get("name", "")).lower() == "over"), None)
                under = next((o for o in outcomes if str(o.get("name", "")).lower() == "under"), None)
                if not over or not under:
                    continue

                try:
                    over_odds = int(over["price"])
                    under_odds = int(under["price"])
                    line = float(over.get("point", under.get("point")))
                except Exception:
                    continue

                over_model_prob = float(model_probs.get("over", 0.0))
                under_model_prob = float(model_probs.get("under", 0.0))

                over_ev = calculate_ev(over_model_prob, over_odds)
                under_ev = calculate_ev(under_model_prob, under_odds)

                fair_over, fair_under = calculate_fair_probs(over_odds, under_odds)

                over_edge = over_model_prob - float(totals_consensus.consensus_over_prob or fair_over)
                under_edge = under_model_prob - float(totals_consensus.consensus_under_prob or fair_under)

                outlier = detect_line_outlier(
                    offer_line=line,
                    consensus_line=totals_consensus.consensus_line,
                    threshold=1.0,
                    label="Totals",
                )

                if over_ev > min_edge and over_ev > highest_ev:
                    highest_ev = over_ev
                    best_candidate = {
                        "book": book_name,
                        "market_type": "totals",
                        "side": "over",
                        "line": line,
                        "odds": over_odds,
                        "ev": over_ev,
                        "model_prob": over_model_prob,
                        "consensus_prob": totals_consensus.consensus_over_prob,
                        "edge_vs_consensus": over_edge,
                        "consensus_line": totals_consensus.consensus_line,
                        "is_outlier": outlier.is_outlier,
                        "outlier_reason": outlier.reason,
                    }

                if under_ev > min_edge and under_ev > highest_ev:
                    highest_ev = under_ev
                    best_candidate = {
                        "book": book_name,
                        "market_type": "totals",
                        "side": "under",
                        "line": line,
                        "odds": under_odds,
                        "ev": under_ev,
                        "model_prob": under_model_prob,
                        "consensus_prob": totals_consensus.consensus_under_prob,
                        "edge_vs_consensus": under_edge,
                        "consensus_line": totals_consensus.consensus_line,
                        "is_outlier": outlier.is_outlier,
                        "outlier_reason": outlier.reason,
                    }

            # MONEYLINE
            elif market_key == "h2h":
                home = next((o for o in outcomes if str(o.get("name", "")) == home_team), None)
                away = next((o for o in outcomes if str(o.get("name", "")) == away_team), None)
                if not home or not away:
                    continue

                try:
                    home_odds = int(home["price"])
                    away_odds = int(away["price"])
                except Exception:
                    continue

                home_model_prob = float(model_probs.get("home_ml", 0.0))
                away_model_prob = float(model_probs.get("away_ml", 0.0))

                home_ev = calculate_ev(home_model_prob, home_odds)
                away_ev = calculate_ev(away_model_prob, away_odds)

                fair_home, fair_away = calculate_fair_probs(home_odds, away_odds)
                home_edge = home_model_prob - float(h2h_consensus.consensus_home_prob or fair_home)
                away_edge = away_model_prob - float(h2h_consensus.consensus_away_prob or fair_away)

                if home_ev > min_edge and home_ev > highest_ev:
                    highest_ev = home_ev
                    best_candidate = {
                        "book": book_name,
                        "market_type": "h2h",
                        "side": "home",
                        "team": home_team,
                        "odds": home_odds,
                        "ev": home_ev,
                        "model_prob": home_model_prob,
                        "consensus_prob": h2h_consensus.consensus_home_prob,
                        "edge_vs_consensus": home_edge,
                    }

                if away_ev > min_edge and away_ev > highest_ev:
                    highest_ev = away_ev
                    best_candidate = {
                        "book": book_name,
                        "market_type": "h2h",
                        "side": "away",
                        "team": away_team,
                        "odds": away_odds,
                        "ev": away_ev,
                        "model_prob": away_model_prob,
                        "consensus_prob": h2h_consensus.consensus_away_prob,
                        "edge_vs_consensus": away_edge,
                    }

            # SPREADS
            elif market_key == "spreads":
                home = next((o for o in outcomes if str(o.get("name", "")) == home_team), None)
                away = next((o for o in outcomes if str(o.get("name", "")) == away_team), None)
                if not home or not away:
                    continue

                try:
                    home_line = float(home["point"])
                    away_line = float(away["point"])
                    home_odds = int(home["price"])
                    away_odds = int(away["price"])
                except Exception:
                    continue

                home_model_prob = float(model_probs.get("home_spread", 0.0))
                away_model_prob = float(model_probs.get("away_spread", 0.0))

                home_ev = calculate_ev(home_model_prob, home_odds)
                away_ev = calculate_ev(away_model_prob, away_odds)

                fair_home, fair_away = calculate_fair_probs(home_odds, away_odds)
                home_edge = home_model_prob - float(spreads_consensus.consensus_home_prob or fair_home)
                away_edge = away_model_prob - float(spreads_consensus.consensus_away_prob or fair_away)

                home_outlier = detect_line_outlier(
                    offer_line=home_line,
                    consensus_line=spreads_consensus.consensus_line,
                    threshold=1.0,
                    label="Spread",
                )

                away_outlier = detect_line_outlier(
                    offer_line=away_line,
                    consensus_line=(-spreads_consensus.consensus_line if spreads_consensus.consensus_line is not None else None),
                    threshold=1.0,
                    label="Spread",
                )

                if home_ev > min_edge and home_ev > highest_ev:
                    highest_ev = home_ev
                    best_candidate = {
                        "book": book_name,
                        "market_type": "spreads",
                        "side": "home",
                        "team": home_team,
                        "line": home_line,
                        "odds": home_odds,
                        "ev": home_ev,
                        "model_prob": home_model_prob,
                        "consensus_prob": spreads_consensus.consensus_home_prob,
                        "edge_vs_consensus": home_edge,
                        "consensus_line": spreads_consensus.consensus_line,
                        "is_outlier": home_outlier.is_outlier,
                        "outlier_reason": home_outlier.reason,
                    }

                if away_ev > min_edge and away_ev > highest_ev:
                    highest_ev = away_ev
                    best_candidate = {
                        "book": book_name,
                        "market_type": "spreads",
                        "side": "away",
                        "team": away_team,
                        "line": away_line,
                        "odds": away_odds,
                        "ev": away_ev,
                        "model_prob": away_model_prob,
                        "consensus_prob": spreads_consensus.consensus_away_prob,
                        "edge_vs_consensus": away_edge,
                        "consensus_line": (-spreads_consensus.consensus_line if spreads_consensus.consensus_line is not None else None),
                        "is_outlier": away_outlier.is_outlier,
                        "outlier_reason": away_outlier.reason,
                    }

    return best_candidate
