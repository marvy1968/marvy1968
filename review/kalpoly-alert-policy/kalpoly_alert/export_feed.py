"""Review export of the alerts feed.

The production query keeps sent=1 and the existing NFL team-trend exception.
This adds gate-passed eligibility without treating an undelivered row as sent.
"""
from __future__ import annotations

import json
import sqlite3

# Provenance export_props.py line 35, plus feed_eligibility.
ALERT_SQL = """
SELECT a.id, a.ts, a.event_id, a.league, a.strategy, a.market, a.selection, a.line, a.price, a.book,
       a.fair_p, a.edge, a.trigger, a.message, a.details_json,
       (SELECT player FROM paper_bets p WHERE p.alert_id=a.id LIMIT 1),
       (SELECT commence FROM paper_bets p WHERE p.alert_id=a.id LIMIT 1),
       a.sent
FROM alerts a
WHERE (
    a.sent=1
    OR (a.strategy='team_trend' AND a.league='nfl')
    OR a.id IN (SELECT alert_id FROM feed_eligibility)
) AND a.ts >= ?
ORDER BY a.id
"""


def export_alerts(con: sqlite3.Connection, since_iso: str) -> list[dict]:
    from .delivery import ensure
    ensure(con)
    out = []
    for row in con.execute(ALERT_SQL, (since_iso,)):
        try:
            details = json.loads(row[14] or "{}")
        except json.JSONDecodeError:
            details = {}
        if row[4] == "team_trend" and row[3] == "nfl":
            details = dict(details, paper_feed=True)
        out.append({
            "id": row[0], "ts": row[1], "event_id": row[2], "league": row[3], "strategy": row[4],
            "market": row[5], "selection": row[6], "line": row[7], "price": row[8], "book": row[9],
            "fair_p": row[10], "edge": row[11], "trigger": row[12],
            "headline": (row[13] or "").split("\n")[0][:160],
            "details": {k: details.get(k) for k in (
                "matchup", "in_game", "live", "p", "win_p", "trend", "label", "move", "live_total",
                "pregame_total", "my_book", "my_price", "situation", "paper_feed",
            ) if k in details},
            "player": row[15], "commence": row[16], "sent": int(row[17] or 0),
        })
    return out
