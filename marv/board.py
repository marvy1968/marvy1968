"""The edge board: Marv's probabilities against every current price, game markets and player props.

For each upcoming game Marv has projected (state/predictions.json), current odds from every US book
(The Odds API) are compared with Marv's probability for the moneyline, spread and total:

  fair market probability   = no-vig consensus across books (power method)
  Marv probability          = pregame simulation (normal approximation of its margin/total)
  blended probability       = logit blend, weight from the edge backtest (market-anchored)
  edge                      = expected profit per unit at the BEST available price among your books

An entry is only labelled RECOMMENDED when that sport/market has a positive, statistically
significant held-out result in marv/backtest_edges.json; everything else is a LEAN shown for
information. Player props from the props runners are added with their own status. Stakes are
quarter-Kelly, capped at 2% of bankroll.
"""

import json
import logging
import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

from . import edges as E
from . import proven
from .bridge import GAME, find_game  # noqa: F401  (find_game re-exported for the query bot)
from .data.teams import similarity

log = logging.getLogger(__name__)
BACKTEST = Path(__file__).with_name("backtest_edges.json")
DEFAULT_BOOKS = "bovada,draftkings,fanduel,betmgm,caesars,betrivers,espnbet,fanatics"


def _phi(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def backtest_table() -> dict:
    try:
        return json.loads(BACKTEST.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def market_status(sport: str, market: str) -> tuple[str, float, float, str]:
    """(status, model weight, edge threshold, note) for a sport/market from the edge backtest."""
    row = backtest_table().get(sport, {}).get(market)
    if not row:
        return "lean", 0.25, 0.04, "not backtested on closing prices"
    ok = row.get("roi_test", -1) > 0 and row.get("p_value", 1) < 0.10 and row.get("bets_test", 0) >= 100
    note = (f"held-out {row.get('bets_test')} bets, ROI {row.get('roi_test', 0):+.1%}, p={row.get('p_value', 1):.2f}")
    if row.get("roi_test", -1) < 0 and os.environ.get("BOARD_SHOW_LOSING", "false").lower() not in ("1", "true", "yes"):
        return "skip", 0.0, 1.0, note  # this market lost money on held-out seasons: don't suggest it at all
    return ("recommended" if ok else "lean"), row.get("weight", 0.25), max(row.get("edge", 0.04), 0.04), note


def offers(event: dict, books: list[str]) -> dict:
    """Best price per side across your books, plus the no-vig consensus for each market."""
    home, away = event["home_team"], event["away_team"]
    best, all_prices = {}, {}
    for b in event.get("bookmakers", []):
        for m in b.get("markets", []):
            outs = {o["name"]: o for o in m.get("outcomes", [])}
            if m["key"] == "h2h" and home in outs and away in outs:
                pairs = {("ml", "home", None): outs[home]["price"], ("ml", "away", None): outs[away]["price"]}
                all_prices.setdefault(("ml", None), []).append((outs[home]["price"], outs[away]["price"]))
            elif m["key"] == "spreads" and home in outs and away in outs:
                pt = outs[home].get("point")
                pairs = {("spread", "home", pt): outs[home]["price"], ("spread", "away", pt): outs[away]["price"]}
                all_prices.setdefault(("spread", pt), []).append((outs[home]["price"], outs[away]["price"]))
            elif m["key"] == "totals" and "Over" in outs and "Under" in outs:
                pt = outs["Over"].get("point")
                pairs = {("total", "over", pt): outs["Over"]["price"], ("total", "under", pt): outs["Under"]["price"]}
                all_prices.setdefault(("total", pt), []).append((outs["Over"]["price"], outs["Under"]["price"]))
            else:
                continue
            if b["key"] not in books:
                continue
            for k, price in pairs.items():
                if k not in best or E.payout(price) > E.payout(best[k][0]):
                    best[k] = (price, b.get("title", b["key"]))
    fair = {}
    for (market, pt), quotes in all_prices.items():
        a = sorted(q[0] for q in quotes)[len(quotes) // 2]
        c = sorted(q[1] for q in quotes)[len(quotes) // 2]
        fair[(market, pt)] = E.devig([a, c])[0]
    return {"best": best, "fair": fair}


def ml_consensus(event: dict) -> dict:
    """{'home': median ML, 'away': median ML} across every book quoting the game, or {}."""
    home, away = event["home_team"], event["away_team"]
    hs, as_ = [], []
    for b in event.get("bookmakers", []):
        for m in b.get("markets", []):
            outs = {o["name"]: o for o in m.get("outcomes", [])}
            if m["key"] == "h2h" and home in outs and away in outs:
                hs.append(outs[home]["price"])
                as_.append(outs[away]["price"])
    if not hs:
        return {}
    return {"home": sorted(hs)[len(hs) // 2], "away": sorted(as_)[len(as_) // 2]}


def model_prob(rec: dict, sport: str, market: str, side: str, point) -> float:
    cfg = GAME.get(sport, GAME["nfl"])
    if market == "ml":
        return rec["home_win"] if side == "home" else 1 - rec["home_win"]
    if market == "spread":  # home line `point`: home covers when margin + point > 0
        p_home = 1 - _phi((-point - rec["model_margin"]) / cfg["margin_sd"])
        return p_home if side == "home" else 1 - p_home
    p_over = 1 - _phi((point - rec["model_total"]) / cfg["total_sd"])
    return p_over if side == "over" else 1 - p_over


def game_entries(sport: str, rec: dict, event: dict, books: list[str]) -> list[dict]:
    o = offers(event, books)
    out = []
    for (market, side, point), (price, book) in o["best"].items():
        fair_home = o["fair"].get((market, point))
        if fair_home is None:
            continue
        p_fair = fair_home if side in ("home", "over") else 1 - fair_home
        status, w, threshold, note = market_status(sport, market)
        if status == "skip":
            continue
        p_marv = model_prob(rec, sport, market, side, point)
        p = E.blend(p_marv, p_fair, w)
        ev = E.edge(p, price)
        if ev < threshold:
            continue
        team = rec["home"] if side == "home" else rec["away"] if side == "away" else side.title()
        if market == "ml":
            label = f"{team} ML"
        elif market == "spread":
            label = f"{team} {(point if side == 'home' else -point):+g}"
        else:
            label = f"{side.title()} {point:g}"
        out.append({"sport": sport, "game": f"{rec['away']} @ {rec['home']}", "start": rec["start"], "market": market,
                    "pick": label, "price": price, "book": book, "p_marv": round(p_marv, 4), "p_market": round(p_fair, 4),
                    "p": round(p, 4), "edge": round(ev, 4), "stake": E.kelly(p, price), "status": status, "note": note,
                    "key": f"{sport}:{rec['game_id']}:{market}:{side}:{point}"})
    return out


def build(settings, sports: list[str], hours: int = 36) -> list[dict]:
    from .data import oddsapi
    from .sports import SPORTS
    state = Path(settings.state_dir)
    path = state / "predictions.json"
    preds = json.loads(path.read_text()) if path.exists() else {}
    now = datetime.now(timezone.utc)
    books = [b.strip().replace("bovado", "bovada") for b in os.environ.get("ODDS_BOOKS", DEFAULT_BOOKS).split(",") if b.strip()]
    entries = []
    ml_prices = {}
    for sport in sports:
        recs = [r for r in preds.values() if r["sport"] == sport
                and now <= datetime.fromisoformat(r["start"]) <= now + timedelta(hours=hours)]
        if not recs or not settings.odds_api_key:
            continue
        events = []
        for key in SPORTS[sport].odds_api_keys:
            try:
                events += oddsapi.fetch(settings.odds_api_key, key)
            except Exception as exc:
                log.warning("board odds %s: %s", key, exc)
        for rec in recs:
            ev = max(events, key=lambda e: min(similarity(rec["home"], e["home_team"]), similarity(rec["away"], e["away_team"])),
                     default=None)
            if ev and min(similarity(rec["home"], ev["home_team"]), similarity(rec["away"], ev["away_team"])) >= 0.75:
                entries += game_entries(sport, rec, ev, books)
                ml = ml_consensus(ev)
                if ml:
                    ml_prices[f"{sport}:{rec['game_id']}"] = {**ml, "at": now.isoformat()}
    if ml_prices:  # median moneyline per game (UPSET ALERT uses it to name the favourite's price)
        try:
            (state / "ml_prices.json").write_text(json.dumps(ml_prices, indent=1))
        except OSError:
            pass
    entries += _props_entries(state)
    entries.sort(key=lambda e: (e["status"] != "recommended", -e["edge"]))
    (state / "board.json").write_text(json.dumps({"built": now.isoformat(), "entries": entries}, indent=1))
    return entries


def _props_entries(state: Path, min_edge: float = 0.04) -> list[dict]:
    """Props from the latest priced runs (state/props_priced_<sport>.json): best side vs its price."""
    out = []
    for f in state.glob("props_priced_*.json"):
        sport = f.stem.split("_")[-1]
        try:
            rows = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        for r in rows:
            for side, p, price in (("Over", r["p_over"], r.get("over_price")), ("Under", 1 - r["p_over"], r.get("under_price"))):
                if price is None or (isinstance(price, float) and math.isnan(price)):
                    continue
                ev = E.edge(p, price)
                if ev < min_edge or p < 0.55:
                    continue
                out.append({"sport": sport, "game": r.get("game_id", ""), "start": r.get("updated", ""), "market": "prop",
                            "pick": f"{r['player']} {r.get('label', r['market'])} {side} {r['line']:g}", "price": price,
                            "book": r.get("book_title", ""), "p_marv": round(p, 4), "p_market": None, "p": round(p, 4),
                            "edge": round(ev, 4), "stake": E.kelly(p, price), "status": "paper",
                            "note": f"proj {r['proj']:.1f}; props real-line backtest pending",
                            "key": f"prop:{sport}:{r['player']}:{r['market']}:{r['line']}:{side}"})
    return out


def text(entries: list[dict], limit: int = 12, h2h=None) -> str:
    """h2h: optional callable(entry) -> Marv H2H line; printed once per game under its first entry."""
    if not entries:
        return "📋 MARV EDGE BOARD\nNo edges right now (or no projections / odds yet)."
    rec = [e for e in entries if e["status"] == "recommended"]
    lines = ["📋 MARV EDGE BOARD", f"{len(rec)} recommended · {len(entries) - len(rec)} leans/paper"]
    seen = set()
    for e in entries[:limit]:
        tag = {"recommended": "✅", "lean": "·", "paper": "📝"}[e["status"]]
        qg = proven.query_gated(e["sport"]) if h2h is not None else proven.gated(e["sport"])
        if qg:
            lines.append(f"{tag} {e['sport'].upper()} {e['pick']} {int(e['price']):+d} ({e['book']}) · Marv {proven.UNPROVEN}")
        else:
            lines.append(f"{tag} {e['sport'].upper()} {e['pick']} {int(e['price']):+d} ({e['book']}) · Marv {e['p_marv']:.0%}"
                         + (f" / market {e['p_market']:.0%}" if e.get("p_market") else "")
                         + f" · edge {e['edge']:+.1%} · stake {e['stake']:.1%}")
        gk = (e["sport"], e.get("game"))
        if h2h is not None and gk not in seen:
            seen.add(gk)
            hl = h2h(e)
            if hl:
                lines.append("   " + hl)
    lines.append("✅ = backtested profitable market · · = lean, no proven edge · 📝 = paper (props)")
    return "\n".join(lines)
