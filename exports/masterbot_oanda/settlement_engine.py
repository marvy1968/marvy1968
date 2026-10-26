#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from live_scoreboard_bridge import fetch_all_scoreboard_games

PERF_DB = Path(os.getenv("PERF_DB", "performance_tracking.db"))
HISTORICAL_FILE = Path(os.getenv("HISTORICAL_FILE", "logs/historical_results.json"))
SETTLED_JSON = Path(os.getenv("SETTLED_RESULTS_JSON", "logs/settled_results.json"))
SETTLED_JSONL = Path(os.getenv("SETTLED_RESULTS_JSONL", "logs/settled_results.jsonl"))
MIN_SAMPLES_TO_ADJUST = int(os.getenv("LEARN_MIN_SAMPLES_TO_ADJUST", "100"))


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def save_json(path: Path, payload: Any) -> None:
    ensure_parent(path)
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    ensure_parent(path)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row) + "\n")


def norm(v: Any) -> str:
    return " ".join(str(v or "").strip().lower().split())


def to_float(v: Any, default: float = 0.0) -> float:
    try:
        if v is None:
            return default
        return float(v)
    except Exception:
        return default


def to_int(v: Any, default: int = 0) -> int:
    try:
        if v is None:
            return default
        return int(float(v))
    except Exception:
        return default


def parse_game(game: str) -> tuple[str, str]:
    parts = str(game).split("@")
    if len(parts) != 2:
        return "", ""
    away = parts[0].strip()
    home = parts[1].strip()
    return away, home


def game_key(game: str) -> str:
    away, home = parse_game(game)
    return f"{norm(away)} @ {norm(home)}"


def scoreboard_key(row: dict[str, Any]) -> str:
    return f"{norm(row.get('away_team'))} @ {norm(row.get('home_team'))}"


def build_scoreboard_index(games: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for g in games:
        if isinstance(g, dict):
            out[scoreboard_key(g)] = g
    return out


def build_historical_index(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {}
    for r in rows:
        if not isinstance(r, dict):
            continue
        game = str(r.get("game", "")).strip()
        if not game:
            away = str(r.get("away_team", "")).strip()
            home = str(r.get("home_team", "")).strip()
            if away and home:
                game = f"{away} @ {home}"
        if game:
            out[game_key(game)] = r
    return out


def merge_result_sources(
    scoreboard_index: dict[str, dict[str, Any]],
    historical_index: dict[str, dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    merged = dict(historical_index)
    merged.update(scoreboard_index)
    return merged


def grade_total(selection: str, line: float, home_score: int, away_score: int) -> str:
    total = home_score + away_score
    sel = norm(selection)
    if total > line:
        return "win" if sel == "over" else "loss"
    if total < line:
        return "win" if sel == "under" else "loss"
    return "push"


def grade_moneyline(selection: str, game: str, home_score: int, away_score: int) -> str:
    away, home = parse_game(game)
    if away_score > home_score:
        winner = away
    elif home_score > away_score:
        winner = home
    else:
        return "push"
    return "win" if norm(selection) == norm(winner) else "loss"


def grade_spread(selection: str, game: str, line: float, home_score: int, away_score: int) -> str:
    away, home = parse_game(game)
    sel = norm(selection)

    if sel == norm(home):
        adjusted_home = home_score + line
        if adjusted_home > away_score:
            return "win"
        if adjusted_home < away_score:
            return "loss"
        return "push"

    if sel == norm(away):
        adjusted_away = away_score - line
        if adjusted_away > home_score:
            return "win"
        if adjusted_away < home_score:
            return "loss"
        return "push"

    return "loss"


def grade_bet(market: str, selection: str, game: str, line: float, home_score: int, away_score: int) -> str:
    m = norm(market)
    if m in {"total", "totals"}:
        return grade_total(selection, line, home_score, away_score)
    if m in {"moneyline", "ml", "h2h"}:
        return grade_moneyline(selection, game, home_score, away_score)
    if m in {"spread", "spreads"}:
        return grade_spread(selection, game, line, home_score, away_score)
    return "loss"


def roi_units(result: str, bankroll_pct: float) -> float:
    if result == "win":
        return bankroll_pct
    if result == "loss":
        return -bankroll_pct
    return 0.0


def bucket_key(sport_key: str, market: str) -> str:
    return f"{norm(sport_key)}|{norm(market)}"


def ensure_tracking_columns(conn: sqlite3.Connection) -> None:
    cols = {r[1] for r in conn.execute("PRAGMA table_info(tracked_bets)").fetchall()}
    wanted = {
        "result": "TEXT",
        "profit_units": "REAL",
        "final_home_score": "REAL",
        "final_away_score": "REAL",
        "settled_at_utc": "TEXT",
    }
    for col, typ in wanted.items():
        if col not in cols:
            conn.execute(f"ALTER TABLE tracked_bets ADD COLUMN {col} {typ}")
    conn.commit()


def fetch_unsettled(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT
            id,
            timestamp_utc,
            game,
            market,
            selection,
            strength,
            bot_win_pct,
            book_win_pct,
            display_edge,
            ev_percent,
            confidence,
            kelly_quarter,
            recommended_bankroll_pct,
            leader,
            leader_win_prob,
            minutes_left,
            seconds_left,
            score_diff,
            pace,
            pressure,
            suspicious,
            tag,
            result,
            profit_units,
            final_home_score,
            final_away_score,
            settled_at_utc
        FROM tracked_bets
        WHERE result IS NULL
           OR TRIM(result) = ''
           OR LOWER(TRIM(result)) = 'pending'
        ORDER BY id ASC
        """
    ).fetchall()


def fetch_settled_count(conn: sqlite3.Connection, sport_key: str, market: str) -> int:
    row = conn.execute(
        """
        SELECT COUNT(*)
        FROM tracked_bets
        WHERE result IS NOT NULL
          AND TRIM(result) <> ''
          AND LOWER(TRIM(result)) <> 'pending'
          AND LOWER(COALESCE(tag, '')) = LOWER(COALESCE(?, tag))
          AND LOWER(COALESCE(market, '')) = LOWER(COALESCE(?, market))
        """,
        (sport_key, market),
    ).fetchone()
    return int(row[0] or 0) if row else 0


async def get_scoreboard_index() -> dict[str, dict[str, Any]]:
    games = await fetch_all_scoreboard_games()
    return build_scoreboard_index(games)


def settle_one(row: dict[str, Any], final_game: dict[str, Any]) -> dict[str, Any]:
    home_score = to_int(final_game.get("home_score", 0))
    away_score = to_int(final_game.get("away_score", 0))
    market = str(row.get("market", ""))
    selection = str(row.get("selection", ""))
    game = str(row.get("game", ""))
    line = to_float(row.get("line", 0.0))
    bankroll_pct = to_float(row.get("recommended_bankroll_pct", 1.0))

    result = grade_bet(
        market=market,
        selection=selection,
        game=game,
        line=line,
        home_score=home_score,
        away_score=away_score,
    )

    settled_at = utc_now_iso()
    profit = roi_units(result, bankroll_pct)

    return {
        "id": row.get("id"),
        "timestamp_utc": row.get("timestamp_utc", ""),
        "game": game,
        "sport_key": str(final_game.get("sport_key", "")),
        "market": market,
        "selection": selection,
        "strength": row.get("strength", ""),
        "bot_win_pct": to_float(row.get("bot_win_pct", 0.0)),
        "book_win_pct": to_float(row.get("book_win_pct", 0.0)),
        "display_edge": to_float(row.get("display_edge", 0.0)),
        "ev_percent": to_float(row.get("ev_percent", 0.0)),
        "confidence": to_float(row.get("confidence", 0.0)),
        "kelly_quarter": to_float(row.get("kelly_quarter", 0.0)),
        "recommended_bankroll_pct": bankroll_pct,
        "leader": row.get("leader", ""),
        "leader_win_prob": to_float(row.get("leader_win_prob", 0.0)),
        "minutes_left": to_float(row.get("minutes_left", 0.0)),
        "seconds_left": to_float(row.get("seconds_left", 0.0)),
        "score_diff": to_float(row.get("score_diff", 0.0)),
        "pace": row.get("pace", ""),
        "pressure": row.get("pressure", ""),
        "suspicious": int(bool(row.get("suspicious", 0))),
        "tag": row.get("tag", ""),
        "result": result,
        "profit_units": profit,
        "final_home_score": home_score,
        "final_away_score": away_score,
        "settled_at_utc": settled_at,
        "bucket_key": bucket_key(str(final_game.get("sport_key", "")), market),
    }


def update_tracking_row(conn: sqlite3.Connection, settled: dict[str, Any]) -> None:
    conn.execute(
        """
        UPDATE tracked_bets
        SET
            result = ?,
            profit_units = ?,
            final_home_score = ?,
            final_away_score = ?,
            settled_at_utc = ?
        WHERE id = ?
        """,
        (
            settled["result"],
            settled["profit_units"],
            settled["final_home_score"],
            settled["final_away_score"],
            settled["settled_at_utc"],
            settled["id"],
        ),
    )
    conn.commit()


def main() -> None:
    if not PERF_DB.exists():
        print(f"performance db not found: {PERF_DB}")
        return

    conn = sqlite3.connect(PERF_DB)
    conn.row_factory = sqlite3.Row
    ensure_tracking_columns(conn)

    unsettled = fetch_unsettled(conn)
    print(f"unsettled tracked_bets: {len(unsettled)}")

    scoreboard_index = asyncio.run(get_scoreboard_index())
    print(f"scoreboard games fetched: {len(scoreboard_index)}")

    historical_rows = load_json(HISTORICAL_FILE, [])
    if not isinstance(historical_rows, list):
        historical_rows = []
    historical_index = build_historical_index(historical_rows)
    print(f"historical results loaded: {len(historical_index)}")

    result_index = merge_result_sources(scoreboard_index, historical_index)

    settled_rows = load_json(SETTLED_JSON, [])
    if not isinstance(settled_rows, list):
        settled_rows = []

    settled_count = 0
    skipped_count = 0

    for r in unsettled:
        row = dict(r)
        gkey = game_key(row.get("game", ""))
        final_game = result_index.get(gkey)

        if not final_game:
            skipped_count += 1
            continue

        if not bool(final_game.get("completed", False)):
            skipped_count += 1
            continue

        settled = settle_one(row, final_game)
        update_tracking_row(conn, settled)

        settled_rows.append(settled)
        append_jsonl(SETTLED_JSONL, settled)

        bucket_samples = fetch_settled_count(conn, settled["tag"], settled["market"])
        adjust_ready = bucket_samples >= MIN_SAMPLES_TO_ADJUST

        print(
            f"settled: {settled['game']} | {settled['market']} | {settled['selection']} | "
            f"result={settled['result']} | profit_units={settled['profit_units']} | "
            f"samples={bucket_samples} | adjust_ready={adjust_ready}"
        )

        settled_count += 1

    save_json(SETTLED_JSON, settled_rows)
    conn.close()

    print(f"done | settled={settled_count} | skipped={skipped_count}")


if __name__ == "__main__":
    main()
