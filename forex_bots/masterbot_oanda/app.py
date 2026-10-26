from __future__ import annotations

import os
from datetime import datetime, timezone

import requests
from dotenv import load_dotenv

from alerts.dispatcher import AlertDispatcher
from edge_engine.ev import calculate_ev_and_kelly
from edge_engine.model_edge import evaluate_total_line_edge
from live_engine.foul_extension import FoulExtensionInput, evaluate_foul_extension
from market_engine.consensus import ConsensusQuote, build_consensus
from market_engine.consensus_pairs import build_consensus_pairs
from market_engine.no_vig import find_fair_prob_for_side, remove_vig_two_way
from monte_carlo.live_sim import LiveGameState, run_live_simulation


def now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_env_list(name: str, default: str = "") -> list[str]:
    raw = os.getenv(name, default)
    return [x.strip().lower() for x in raw.split(",") if x.strip()]


def fetch_odds_api_events(api_key: str, bookmakers: list[str]) -> list[dict]:
    sport_key = "basketball_ncaab"
    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"

    params = {
        "apiKey": api_key,
        "regions": "us",
        "markets": "totals,h2h,spreads",
        "oddsFormat": "american",
        "dateFormat": "iso",
        "bookmakers": ",".join(bookmakers),
    }

    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def event_to_quotes(event: dict, trusted_books: list[str]) -> list[ConsensusQuote]:
    quotes: list[ConsensusQuote] = []

    for bookmaker in event.get("bookmakers", []):
        book_key = str(bookmaker.get("key", "")).lower().strip()
        if book_key not in trusted_books:
            continue

        updated_at = bookmaker.get("last_update")

        for market in bookmaker.get("markets", []):
            market_key = str(market.get("key", "")).lower().strip()

            for outcome in market.get("outcomes", []):
                side = str(outcome.get("name", "")).strip()
                line = outcome.get("point")
                price = outcome.get("price")

                quotes.append(
                    ConsensusQuote(
                        sportsbook_key=book_key,
                        market_type=market_key,
                        side=side,
                        line=float(line) if line is not None else None,
                        price_american=int(price) if price is not None else None,
                        source_updated_at_utc=updated_at,
                    )
                )

    return quotes


def should_flag_stale_line(
    quote: ConsensusQuote,
    consensus_line: float | None,
    min_delta: float = 1.0,
) -> bool:
    if quote.line is None or consensus_line is None:
        return False
    return abs(float(quote.line) - float(consensus_line)) >= min_delta


def build_simulated_fair_line(
    market_type: str,
    quote_line: float | None,
) -> float | None:
    if market_type.lower() != "totals" or quote_line is None:
        return None

    foul_result = evaluate_foul_extension(
        FoulExtensionInput(
            universe="mens",
            seconds_remaining=178,
            score_diff=5,
            trailing_team_fouls=8,
            leading_team_fouls=6,
            trailing_team_timeouts=2,
            leading_team_timeouts=1,
            possession_team="leading",
            trailing_team_ft_pct=0.71,
            leading_team_ft_pct=0.79,
            recent_possessions_per_min=2.6,
            baseline_possessions_per_min=3.0,
            recent_points_per_possession=0.88,
            baseline_points_per_possession=1.02,
        )
    )

    sim_result = run_live_simulation(
        LiveGameState(
            seconds_remaining=178,
            home_score=66,
            away_score=61,
            possessions_per_min=3.0,
            points_per_possession=1.02,
        ),
        foul_extension=foul_result,
        simulations=1000,
    )

    return sim_result.projected_total


def model_edge_to_pct(line_gap: float) -> float:
    """
    Convert model line gap into a simple edge proxy.
    Example:
    2.0 points => 2.0%
    6.5 points => 6.5%
    """
    return max(0.0, abs(line_gap) / 100.0)


def scan_event(event: dict, dispatcher: AlertDispatcher) -> dict:
    trusted_books = get_env_list("TRUSTED_BOOKS", "draftkings,fanduel,betmgm")
    edge_floor = float(os.getenv("EDGE_FLOOR", "0.02"))
    max_kelly = float(os.getenv("MAX_KELLY", "0.08"))
    max_book_spread_points = float(os.getenv("MAX_BOOK_SPREAD_POINTS", "3.0"))
    stale_delta = float(os.getenv("STALE_LINE_DELTA", "1.0"))

    game_label = f'{event.get("home_team", "Home")} vs {event.get("away_team", "Away")}'
    commence_time = event.get("commence_time", now_utc_iso())

    quotes = event_to_quotes(event, trusted_books)
    if not quotes:
        return {
            "quotes": 0,
            "ev_checked": 0,
            "recommended": 0,
            "sent": 0,
        }

    consensus_results = build_consensus(
        quotes,
        min_books=2,
        max_book_spread_points=max_book_spread_points,
    )

    consensus_pairs = build_consensus_pairs(consensus_results)
    consensus_lookup = {
        (r.market_type.lower(), r.side.lower()): r for r in consensus_results
    }

    scanned_quotes = 0
    ev_checked = 0
    recommended_count = 0
    sent_count = 0

    for pair in consensus_pairs:
        if not pair.is_valid:
            continue

        no_vig_result = remove_vig_two_way(
            market_type=pair.market_type,
            side_a=pair.side_a,
            odds_a=pair.price_a,
            side_b=pair.side_b,
            odds_b=pair.price_b,
        )

        if not no_vig_result.is_valid:
            continue

        for q in quotes:
            if q.market_type.lower() != pair.market_type.lower():
                continue
            if q.price_american is None:
                continue

            scanned_quotes += 1
            ev_checked += 1

            ckey = (q.market_type.lower(), q.side.lower())
            consensus_row = consensus_lookup.get(ckey)
            if consensus_row is None or not consensus_row.is_stable:
                continue

            fair_prob = find_fair_prob_for_side(no_vig_result, q.side)
            if fair_prob is None:
                continue

            simulated_fair_line = build_simulated_fair_line(q.market_type, q.line)

            model_edge_result = None
            if simulated_fair_line is not None and q.line is not None and q.market_type.lower() == "totals":
                model_edge_result = evaluate_total_line_edge(
                    side=q.side,
                    market_line=float(q.line),
                    fair_line=float(simulated_fair_line),
                    min_gap=2.0,
                )

            edge_result = calculate_ev_and_kelly(
                fair_prob=fair_prob,
                offered_american=q.price_american,
                bankroll=1.0,
                edge_floor=edge_floor,
                kelly_multiplier=0.25,
                max_kelly=max_kelly,
            )

            market_recommended = edge_result.is_bet_recommended
            model_recommended = model_edge_result.is_bet_recommended if model_edge_result else False

            print(
                "DEBUG",
                game_label,
                q.market_type,
                q.side,
                q.sportsbook_key,
                "line=",
                q.line,
                "consensus=",
                consensus_row.consensus_line,
                "sim_fair_line=",
                simulated_fair_line,
                "price=",
                q.price_american,
                "fair_prob=",
                round(fair_prob, 4),
                "ev=",
                round(edge_result.expected_value, 4),
                "market_rec=",
                market_recommended,
                "model_gap=",
                round(model_edge_result.line_gap, 2) if model_edge_result else None,
                "model_rec=",
                model_recommended,
            )

            if not market_recommended and not model_recommended:
                continue

            recommended_count += 1

            stale_ok = should_flag_stale_line(
                q,
                consensus_row.consensus_line,
                min_delta=stale_delta,
            )

            if not stale_ok:
                print(
                    "SKIP_STALE",
                    game_label,
                    q.market_type,
                    q.side,
                    q.sportsbook_key,
                    "line=",
                    q.line,
                    "consensus=",
                    consensus_row.consensus_line,
                    "delta=",
                    round(abs((q.line or 0.0) - (consensus_row.consensus_line or 0.0)), 2),
                )
                continue

            effective_edge = edge_result.edge_pct
            effective_reason = edge_result.reason
            effective_stake_fraction = edge_result.recommended_stake_fraction

            if model_recommended and model_edge_result is not None:
                effective_edge = max(edge_floor, model_edge_to_pct(model_edge_result.line_gap))
                effective_reason = (
                    f"Model-driven edge | {model_edge_result.reason} | "
                    f"market_ev={edge_result.expected_value:.2%}"
                )
                if effective_stake_fraction <= 0:
                    effective_stake_fraction = min(max_kelly, 0.01)

            opportunity = {
                "game": game_label,
                "market": f"{q.market_type} {q.side}",
                "sportsbook": q.sportsbook_key,
                "market_line": q.line or 0.0,
                "consensus_line": consensus_row.consensus_line or 0.0,
                "fair_line": (
                    simulated_fair_line
                    if simulated_fair_line is not None
                    else (consensus_row.consensus_line or 0.0)
                ),
                "edge": effective_edge,
                "confidence": 75.0,
                "stake_fraction": effective_stake_fraction,
                "reason": effective_reason,
                "is_live": False,
                "game_id": 0,
            }

            existing_bets: list = []

            dispatch_result = dispatcher.dispatch(
                opportunity=opportunity,
                bankroll=1.0,
                existing_bets=existing_bets,
                event_time_utc=commence_time,
            )

            print(
                "DISPATCH",
                game_label,
                q.market_type,
                q.side,
                q.sportsbook_key,
                dispatch_result,
            )

            if dispatch_result.get("sent"):
                sent_count += 1

    return {
        "quotes": scanned_quotes,
        "ev_checked": ev_checked,
        "recommended": recommended_count,
        "sent": sent_count,
    }


def main() -> None:
    load_dotenv(".env")

    odds_api_key = os.getenv("ODDS_API_KEY", "").strip()
    if not odds_api_key:
        raise ValueError("Missing ODDS_API_KEY in .env")

    trusted_books = get_env_list("TRUSTED_BOOKS", "draftkings,fanduel,betmgm")
    dispatcher = AlertDispatcher()

    scanned_events = 0
    scanned_quotes = 0
    ev_checked = 0
    recommended_count = 0
    sent_count = 0

    events = fetch_odds_api_events(odds_api_key, trusted_books)
    scanned_events = len(events)

    print(f"Fetched {scanned_events} events")

    for event in events:
        result = scan_event(event, dispatcher)
        scanned_quotes += result["quotes"]
        ev_checked += result["ev_checked"]
        recommended_count += result["recommended"]
        sent_count += result["sent"]

    print(
        f"Done. Events={scanned_events}, Quotes={scanned_quotes}, "
        f"EV_Checked={ev_checked}, Recommended={recommended_count}, Sent={sent_count}"
    )


if __name__ == "__main__":
    main()
