"""Live (in-game) NFL player props: read the box score and the live prop lines during a game.

Stage 1 (this module): record what's observable. Every poll saves the player's box-score line and the live
prop lines (book, line, over/under prices, last update) to state/live_props_log.json, so live props can be
backtested honestly later. Nothing here sends alerts: pregame props lost money against real lines (48.7-49.1%,
ROI -5%), and a live version has no history to test yet.
"""

import json
from datetime import datetime, timezone
from pathlib import Path

# Odds API market -> (ESPN boxscore group, stat label)
MARKET_STAT = {
    "player_pass_yds": ("passing", "YDS"),
    "player_rush_yds": ("rushing", "YDS"),
    "player_reception_yds": ("receiving", "YDS"),
    "player_receptions": ("receiving", "REC"),
}


def box_stats(summary: dict) -> dict[str, dict[str, dict[str, float]]]:
    """{player display name: {group: {label: value}}} from an ESPN game summary's box score."""
    out: dict[str, dict[str, dict[str, float]]] = {}
    for team in (summary.get("boxscore") or {}).get("players", []):
        for grp in team.get("statistics", []):
            name = str(grp.get("name", "")).lower()
            labels = [str(x).upper() for x in grp.get("labels", [])]
            for a in grp.get("athletes", []):
                who = (a.get("athlete") or {}).get("displayName")
                if not who:
                    continue
                vals = {}
                for label, raw in zip(labels, a.get("stats", [])):
                    try:
                        vals[label] = float(str(raw).split("/")[0].split("-")[0])
                    except ValueError:
                        continue
                out.setdefault(who, {})[name] = vals
    return out


def current_value(stats: dict, market: str) -> float | None:
    group, label = MARKET_STAT[market]
    return stats.get(group, {}).get(label)


def live_prop_snapshot(event_odds: dict, box: dict) -> list[dict]:
    """One row per book/player/market with the player's current box-score value next to the live line."""
    rows = []
    for b in event_odds.get("bookmakers", []):
        for m in b.get("markets", []):
            if m["key"] not in MARKET_STAT:
                continue
            by_player: dict[str, dict] = {}
            for o in m.get("outcomes", []):
                if o.get("name") in ("Over", "Under") and o.get("description") and o.get("point") is not None:
                    r = by_player.setdefault(o["description"], {"line": float(o["point"])})
                    r["over" if o["name"] == "Over" else "under"] = o.get("price")
            for player, r in by_player.items():
                rows.append({"book": b["key"], "market": m["key"], "player": player, "line": r["line"],
                             "over": r.get("over"), "under": r.get("under"), "updated": m.get("last_update"),
                             "so_far": current_value(box.get(player, {}), m["key"])})
    return rows


def log_snapshot(state: Path, event: dict, quarter: str, score: str, rows: list[dict]) -> None:
    path = state / "live_props_log.json"
    book = json.loads(path.read_text()) if path.exists() else []
    book.append({"at": datetime.now(timezone.utc).isoformat(), "event": event.get("id"),
                 "game": f"{event.get('away_team')} @ {event.get('home_team')}", "period": quarter,
                 "score": score, "props": rows})
    path.write_text(json.dumps(book[-400:]))  # keep the most recent snapshots only
