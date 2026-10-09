"""Bovado vs Pinnacle: flag games where Bovado's number is off the sharpest book's.

Backtest (college football 2014-19, ANALYSIS.md "Line movement, Bovado vs sharp books"): when Bovado's
closing total was 0.5+ points off Pinnacle/BetCRIS, the better side at Bovado went 54.4% over 2,340 games
(+4.6% ROI at Bovado's real prices). Spreads went 53.2% but only +0.6% after Bovado's juice, so spread gaps
are shown for information. NFL has no free history to test, so NFL gaps are untested leans.

One Odds API call per sport asks only for Bovado and Pinnacle spreads and totals (2 credits). Every gap
is logged to state/sharp_gap_log.json and graded against final scores (paper tracking).
"""

import json
import logging
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from . import alertday
from . import edges as E
from .data.oddsapi import URL
from .data.teams import similarity

log = logging.getLogger(__name__)
SPORT_KEYS = {"cfb": "americanfootball_ncaaf", "nfl": "americanfootball_nfl"}
LABEL = {("cfb", "total"): "LEAN · backtest 54.4% (2,340), +4.6% ROI",
         ("cfb", "spread"): "info · backtest 53.2%, ~break-even after Bovado juice",
         ("nfl", "total"): "untested in NFL (paper)", ("nfl", "spread"): "untested in NFL (paper)"}


def fetch(api_key: str, sport: str) -> list[dict]:
    resp = requests.get(URL.format(sport=SPORT_KEYS[sport]), timeout=30, params={
        "apiKey": api_key, "bookmakers": "bovada,pinnacle", "markets": "spreads,totals", "oddsFormat": "american"})
    resp.raise_for_status()
    log.info("Odds API %s (bovada+pinnacle): %s credits left", sport, resp.headers.get("x-requests-remaining"))
    return resp.json()


def _markets(event: dict, book: str) -> dict:
    out = {}
    for b in event.get("bookmakers", []):
        if b.get("key") != book:
            continue
        for m in b.get("markets", []):
            outs = {o["name"]: o for o in m.get("outcomes", [])}
            if m["key"] == "totals" and "Over" in outs and "Under" in outs:
                out["total"] = (outs["Over"]["point"], outs["Over"]["price"], outs["Under"]["price"])
            elif m["key"] == "spreads" and event["home_team"] in outs and event["away_team"] in outs:
                h, a = outs[event["home_team"]], outs[event["away_team"]]
                out["spread"] = (h["point"], h["price"], a["price"])
    return out


def gaps(sport: str, events: list[dict], min_gap: float = 0.5) -> list[dict]:
    """One entry per game and market where Bovado's line differs from Pinnacle's by min_gap or more."""
    out = []
    for ev in events:
        bov, pin = _markets(ev, "bovada"), _markets(ev, "pinnacle")
        home, away = ev["home_team"], ev["away_team"]
        for market in ("total", "spread"):
            if market not in bov or market not in pin:
                continue
            (bl, bp1, bp2), (pl, pp1, pp2) = bov[market], pin[market]
            gap = bl - pl
            if abs(gap) < min_gap:
                continue
            if market == "total":  # Bovado lower than Pinnacle -> over is cheap at Bovado, and vice versa
                side, line, price = ("Over", bl, bp1) if gap < 0 else ("Under", bl, bp2)
                fair = E.devig([pp1, pp2])[0 if side == "Over" else 1]
                pick = f"{side} {line:g}"
            else:  # home line: Bovado gives the home team more points than Pinnacle -> home at Bovado
                home_side = gap > 0
                side, line, price = (home, bl, bp1) if home_side else (away, -bl, bp2)
                fair = E.devig([pp1, pp2])[0 if home_side else 1]
                pick = f"{side} {line:+g}"
            out.append({"key": f"{sport}:{ev['id']}:{market}:{side}", "sport": sport, "event": ev["id"],
                        "start": ev["commence_time"], "home": home, "away": away, "market": market, "side": side,
                        "line": line, "price": price, "pinnacle_line": pl if market == "total" or side == home else -pl,
                        "pinnacle_fair": round(fair, 4), "gap": abs(gap), "pick": pick, "label": LABEL[(sport, market)]})
    out.sort(key=lambda e: (e["market"] != "total", -e["gap"]))
    return out


COVERAGE: dict = {}  # sport -> (games, with Bovado, with Pinnacle, with both) from the last scan


def scan(settings, sports=("cfb", "nfl"), min_gap: float = 0.5) -> list[dict]:
    found = []
    for sport in sports:
        if sport not in SPORT_KEYS:
            continue
        try:
            events = fetch(settings.odds_api_key, sport)
            books = [{b.get("key") for b in ev.get("bookmakers", [])} for ev in events]
            COVERAGE[sport] = (len(events), sum("bovada" in b for b in books), sum("pinnacle" in b for b in books),
                               sum({"bovada", "pinnacle"} <= b for b in books))
            found += gaps(sport, events, min_gap)
        except Exception as exc:
            log.warning("sharp gap %s: %s", sport, exc)
    return found


def coverage_text() -> str:
    return "\n".join(f"{s.upper()}: {n} games, Bovado on {b}, Pinnacle on {p}, both on {both}"
                     for s, (n, b, p, both) in COVERAGE.items())


def log_gaps(state: Path, entries: list[dict]) -> list[dict]:
    """Log entries (keeping the latest line before kickoff); return the ones not seen before."""
    path = state / "sharp_gap_log.json"
    book = json.loads(path.read_text()) if path.exists() else {}
    now = datetime.now(timezone.utc)
    if alertday.day_only():  # advance entries logged before the day-of rule: forget them so they alert on game day
        book = {k: v for k, v in book.items() if v["result"] is not None
                or datetime.fromisoformat(v["start"].replace("Z", "+00:00")) <= now or alertday.is_today(v["start"], now)}
    new = []
    for e in entries:
        if datetime.fromisoformat(e["start"].replace("Z", "+00:00")) <= now or not alertday.is_today(e["start"], now):
            continue  # only today's games are logged and alerted; /gaps still lists every game
        old = book.get(e["key"])
        if old is None:
            new.append(e)
            book[e["key"]] = {**e, "first_seen": now.isoformat(), "result": None}
        elif old["result"] is None:
            old.update({k: e[k] for k in ("line", "price", "pinnacle_line", "pinnacle_fair", "gap", "pick")})
    path.write_text(json.dumps(book, indent=1))
    return new


def grade(state: Path, sport: str, finished) -> None:
    """Grade logged gaps with final scores (Game objects matched by team names and start time)."""
    path = state / "sharp_gap_log.json"
    if not path.exists():
        return
    book = json.loads(path.read_text())
    done = [g for g in finished if g.completed and g.home_score is not None and g.away_score is not None]
    for b in book.values():
        if b["sport"] != sport or b["result"] is not None:
            continue
        start = datetime.fromisoformat(b["start"].replace("Z", "+00:00"))
        g = max(done, key=lambda x: min(similarity(x.home, b["home"]), similarity(x.away, b["away"]))
                if abs(x.start - start) < timedelta(hours=12) else 0, default=None)
        if not g or abs(g.start - start) >= timedelta(hours=12) or \
                min(similarity(g.home, b["home"]), similarity(g.away, b["away"])) < 0.75:
            continue
        if b["market"] == "total":
            v = g.home_score + g.away_score - b["line"]
            v = v if b["side"] == "Over" else -v
        else:
            margin = g.home_score - g.away_score
            v = margin + b["line"] if b["side"] == b["home"] else -margin + b["line"]
        b["result"] = "push" if v == 0 else ("win" if v > 0 else "loss")
        b["profit"] = 0.0 if v == 0 else (E.payout(b["price"]) if v > 0 else -1.0)
    path.write_text(json.dumps(book, indent=1))


def record(state: Path) -> dict:
    path = state / "sharp_gap_log.json"
    book = json.loads(path.read_text()) if path.exists() else {}
    rec = {}
    for b in book.values():
        if b["result"] in ("win", "loss"):
            r = rec.setdefault(f"{b['sport']} {b['market']}", [0, 0, 0.0])
            r[0 if b["result"] == "win" else 1] += 1
            r[2] += b.get("profit", 0.0)
    return rec


def report(rec: dict) -> str:
    if not rec:
        return "Bovado vs Pinnacle gaps: nothing graded yet."
    lines = ["📐 Bovado vs Pinnacle gaps (paper, 1 unit each at Bovado's price)"]
    for k, (w, l, units) in sorted(rec.items()):
        lines.append(f"{k}: {w}-{l} ({w / (w + l):.1%}), {units:+.1f}u")
    return "\n".join(lines)


def text(entries: list[dict], limit: int = 15) -> str:
    if not entries:
        return "No Bovado vs Pinnacle gaps right now."
    lines = ["📐 Bovado off Pinnacle (take the better number at Bovado)"]
    for e in entries[:limit]:
        start = datetime.fromisoformat(e["start"].replace("Z", "+00:00")).strftime("%a %H:%M UTC")
        lines.append(f"{e['sport'].upper()} {e['away']} @ {e['home']} ({start}): {e['pick']} {int(e['price']):+d} at Bovado · "
                     f"Pinnacle {e['pinnacle_line']:g} (fair {e['pinnacle_fair']:.0%}) · gap {e['gap']:g} · {e['label']}")
    return "\n".join(lines)
