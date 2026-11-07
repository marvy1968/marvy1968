"""Exchange paper trader: in-game basketball on Kalshi and Polymarket, PAPER ONLY (no orders, no keys, no money).

At every quarter / half break of an NBA, NCAA men's or NCAA women's game (`marv-live` calls `on_break`):

  1. Fair prices from Marv, in-game:
       game winner, spread, total   bridge.live_state (pregame projection + score + time left + this game's pace),
                                    replaced by the quarter-by-quarter model (marv/ingame) when it's trained
       player points/reb/ast/threes Claude Prop 1 (marv/live_prop_engine: per-possession production x pace x script)
  2. Exchange prices (public, no account needed):
       Kalshi      api.elections.kalshi.com   KX<league>GAME / SPREAD / TOTAL, NBA player PTS / REB / AST / 3PT
       Polymarket  gamma-api.polymarket.com   NBA + men's college basketball events (moneyline, spreads, totals, props)
     Cost of a contract = the ask + the taker fee (Kalshi 0.07*p*(1-p); Polymarket from each market's feeSchedule).
  3. Bovada cross-check: when the live monitor has Bovada's live ML / total (Odds API), its no-vig probability is
     recorded next to every trade, and a trade is skipped if Bovada says the contract is overpriced.
  4. A paper trade is logged only when
        fair - (ask + fee) >= EX_MIN_EDGE (props: EX_MIN_EDGE_PROPS), the ask is 0.08-0.92, the bid/ask gap is <= 0.08
        (wider = no real liquidity), enough game is left, at most EX_MAX_PER_GAME per break, each contract once per game.
     Flat paper stake EX_STAKE dollars (contracts = stake // ask). Every price seen is logged (snapshots.jsonl).
  5. At the final: graded from the final score / box score -> P&L after fees, closing value (last price seen
     vs entry), record by venue, sport and market. `python -m marv exchange report`.

Honesty: nothing here is proven. Basketball in-game prices have never been backtested against exchange prices.
Real orders need a separate, explicit step (demo account first) after 100+ graded paper trades show positive ROI after
fees AND positive closing value. Polymarket's international site is closed to US users: use Polymarket US only.
"""

from __future__ import annotations

import json
import logging
import math
import os
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path

import requests

from .data.teams import similarity

log = logging.getLogger(__name__)
NAME = "Exchange paper"
KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"
SPORTS = ("nba", "ncaab", "ncaaw")
KALSHI_LEAGUE = {"nba": "NBA", "ncaab": "NCAAMB", "ncaaw": "NCAAWB"}
KALSHI_PROPS = {"PTS": "player_points", "REB": "player_rebounds", "AST": "player_assists", "3PT": "player_threes"}
PM_SERIES = {"nba": "10345", "ncaab": "10470"}  # no Polymarket women's college basketball series
PM_PROPS = {"points": "player_points", "rebounds": "player_rebounds", "assists": "player_assists",
            "threes": "player_threes"}
MONTHS = {m: i for i, m in enumerate(["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV",
                                      "DEC"], 1)}


def _env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


def _f(x) -> float | None:
    try:
        return None if x is None or x == "" else float(x)
    except (TypeError, ValueError):
        return None


def _ncdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


# ---------------------------------------------------------------- fees


def kalshi_fee(p: float) -> float:
    """Kalshi taker fee per contract (dollars): 0.07 x p x (1 - p) (Kalshi rounds the order total up to the cent)."""
    return 0.07 * p * (1 - p)


def pm_fee(p: float, schedule: dict | None) -> float:
    """Polymarket taker fee per share: rate x (p x (1 - p)) ^ exponent from the market's feeSchedule (0 if none)."""
    if not schedule:
        return 0.0
    rate, exp = float(schedule.get("rate") or 0), float(schedule.get("exponent") or 1)
    return rate * (p * (1 - p)) ** exp


# ---------------------------------------------------------------- contracts
# A contract (one buyable side) is a dict:
#   venue, id, sport, kind (ml|spread|total|prop), team (for ml/spread), line, player, market (props),
#   direction ("over" = the stat / margin / total must go over the line; ml: team wins), ask, bid, fee, title


def _side(venue, cid, sport, kind, ask, bid, fee, title, **kw) -> dict:
    return {"venue": venue, "id": cid, "sport": sport, "kind": kind, "ask": ask, "bid": bid, "fee": fee,
            "title": title, "team": kw.get("team"), "line": kw.get("line"), "player": kw.get("player"),
            "market": kw.get("market"), "direction": kw.get("direction", "over")}


def kalshi_contracts(sport: str, markets: list[dict]) -> list[dict]:
    """Both sides (buy YES, buy NO) of every active Kalshi market for one game."""
    out = []
    for m in markets:
        if m.get("status") not in ("active", "open"):
            continue
        series = m["ticker"].split("-")[0]
        kind_key = series[len("KX" + KALSHI_LEAGUE[sport]):]
        ya, yb = _f(m.get("yes_ask_dollars")), _f(m.get("yes_bid_dollars"))
        na, nb = _f(m.get("no_ask_dollars")), _f(m.get("no_bid_dollars"))
        if na is None and yb is not None:
            na = round(1 - yb, 4)
        if nb is None and ya is not None:
            nb = round(1 - ya, 4)
        title, line = m.get("title", ""), _f(m.get("floor_strike"))
        if kind_key == "GAME":
            kw = {"team": m.get("yes_sub_title")}
            kind = "ml"
        elif kind_key == "SPREAD":
            t = re.match(r"^(.*?) wins by over", title)
            if not t or line is None:
                continue
            kw, kind = {"team": t.group(1).strip(), "line": line}, "spread"
        elif kind_key == "TOTAL":
            if line is None:
                continue
            kw, kind = {"line": line}, "total"
        elif kind_key in KALSHI_PROPS:
            t = re.match(r"^(.*?):", title)
            if not t or line is None:
                continue
            kw, kind = {"player": t.group(1).strip(), "line": line, "market": KALSHI_PROPS[kind_key]}, "prop"
        else:
            continue
        if ya is not None:
            out.append(_side("kalshi", m["ticker"] + ":yes", sport, kind, ya, yb, kalshi_fee(ya), title, **kw))
        if na is not None:
            nkw = dict(kw)
            if kind == "ml":
                nkw["direction"] = "not"
            else:
                nkw["direction"] = "under"
            out.append(_side("kalshi", m["ticker"] + ":no", sport, kind, na, nb, kalshi_fee(na), "NOT " + title, **nkw))
    return out


def pm_contracts(sport: str, event: dict) -> list[dict]:
    """Both outcomes of every open Polymarket market in one game event."""
    out = []
    for m in event.get("markets", []):
        if m.get("closed") or not m.get("acceptingOrders", True):
            continue
        try:
            outcomes = json.loads(m.get("outcomes") or "[]")
        except (TypeError, json.JSONDecodeError):
            continue
        if len(outcomes) != 2:
            continue
        ask0, bid0 = _f(m.get("bestAsk")), _f(m.get("bestBid"))
        ask1 = round(1 - bid0, 4) if bid0 is not None else None
        bid1 = round(1 - ask0, 4) if ask0 is not None else None
        smt, line, q = m.get("sportsMarketType"), _f(m.get("line")), m.get("question", "")
        sched = m.get("feeSchedule") if m.get("feesEnabled") else None
        base = m.get("conditionId") or m.get("id")
        sides = []
        if smt == "moneyline":
            sides = [("ml", {"team": outcomes[0]}), ("ml", {"team": outcomes[1]})]
        elif smt == "spreads":
            # 'Spread: Nuggets (-6.5)': Nuggets must win by more than 6.5; the other outcome covers +6.5
            t = re.match(r"^Spread:\s*(.*?)\s*\(([-+]?\d+(?:\.\d+)?)\)", q)
            if not t:
                continue
            named, hcap = t.group(1), float(t.group(2))
            i_named = 0 if similarity(named, outcomes[0]) >= similarity(named, outcomes[1]) else 1
            lines_by = {i_named: -hcap, 1 - i_named: hcap}  # each outcome: its team's margin must beat this
            sides = [("spread", {"team": outcomes[0], "line": lines_by[0]}),
                     ("spread", {"team": outcomes[1], "line": lines_by[1]})]
        elif smt == "totals" and line is not None:
            sides = [("total", {"line": line, "direction": "over"}), ("total", {"line": line, "direction": "under"})]
        elif smt in PM_PROPS and line is not None:
            t = re.match(r"^(.*?):", q)
            if not t:
                continue
            kw = {"player": t.group(1).strip(), "line": line, "market": PM_PROPS[smt]}
            sides = [("prop", {**kw, "direction": "over"}), ("prop", {**kw, "direction": "under"})]
        for i, (kind, kw) in enumerate(sides):
            ask, bid = (ask0, bid0) if i == 0 else (ask1, bid1)
            if ask is None:
                continue
            title = f"{q} · {outcomes[i]}"
            out.append(_side("polymarket", f"{base}:{i}", sport, kind, ask, bid, pm_fee(ask, sched), title, **kw))
    return out


# ---------------------------------------------------------------- fetching (public endpoints)


def _get(url: str, params: dict) -> dict | list:
    r = requests.get(url, params=params, timeout=20)
    r.raise_for_status()
    return r.json()


def _date_ok(game_start: datetime, d: datetime | None) -> bool:
    if d is None:
        return True
    local = (game_start - timedelta(hours=5)).date()  # Eastern-ish: the date the exchanges put on the game
    return abs((local - d.date()).days) <= 1


def _kalshi_date(event_ticker: str) -> datetime | None:
    m = re.search(r"-(\d\d)([A-Z]{3})(\d\d)", event_ticker)
    if not m or m.group(2) not in MONTHS:
        return None
    return datetime(2000 + int(m.group(1)), MONTHS[m.group(2)], int(m.group(3)))


def match_event(title_teams: tuple[str, str], home: str, away: str) -> float:
    """How well an exchange's two team names match this game (either order). Both must match."""
    a, b = title_teams
    return max(min(similarity(a, away), similarity(b, home)), min(similarity(a, home), similarity(b, away)))


def kalshi_game(sport: str, home: str, away: str, start: datetime) -> list[dict]:
    """All active Kalshi contracts for one game (winner, spread, total, NBA props)."""
    lg = KALSHI_LEAGUE[sport]
    events = _get(f"{KALSHI}/events", {"series_ticker": f"KX{lg}GAME", "status": "open", "limit": 200}).get("events", [])
    best, score = None, 0.0
    for e in events:
        teams = tuple(x.strip() for x in str(e.get("title", "")).split(" vs ", 1))
        if len(teams) != 2 or not _date_ok(start, _kalshi_date(e["event_ticker"])):
            continue
        s = match_event(teams, home, away)
        if s > score:
            best, score = e, s
    if not best or score < 0.75:
        return []
    suffix = best["event_ticker"].split("-", 1)[1]
    kinds = ["GAME", "SPREAD", "TOTAL"] + (list(KALSHI_PROPS) if sport == "nba" else [])
    markets = []
    for k in kinds:
        try:
            markets += _get(f"{KALSHI}/markets", {"event_ticker": f"KX{lg}{k}-{suffix}", "limit": 1000}).get("markets", [])
        except Exception as exc:
            log.debug("kalshi %s %s: %s", k, suffix, exc)
    return kalshi_contracts(sport, markets)


def pm_game(sport: str, home: str, away: str, start: datetime) -> list[dict]:
    if sport not in PM_SERIES:
        return []
    events = _get(f"{GAMMA}/events", {"series_id": PM_SERIES[sport], "active": "true", "closed": "false", "limit": 200,
                                      "end_date_min": (start - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ")})
    best, score = None, 0.0
    for e in events:
        teams = tuple(x.strip() for x in str(e.get("title", "")).split(" vs. ", 1))
        d = _f(None)
        try:
            d = datetime.fromisoformat(e["eventDate"]) if e.get("eventDate") else None
        except ValueError:
            d = None
        if len(teams) != 2 or not _date_ok(start, d):
            continue
        s = match_event(teams, home, away)
        if s > score:
            best, score = e, s
    if not best or score < 0.75:
        return []
    return pm_contracts(sport, best)


# ---------------------------------------------------------------- fair prices


def _is_home(team: str, home: str, away: str) -> bool:
    return similarity(team, home) >= similarity(team, away)


def fair(c: dict, st: dict, home: str, away: str, props: dict) -> float | None:
    """Marv's probability that this contract pays out.
    st: bridge.live_state output (p_home, exp_margin, sd_m, exp_total, sd_t). props: {(norm player, market, line): p_over}."""
    if c["kind"] == "ml":
        p = st["p_home"] if _is_home(c["team"], home, away) else 1 - st["p_home"]
        return p if c["direction"] != "not" else 1 - p
    if c["kind"] == "spread":
        m = st["exp_margin"] if _is_home(c["team"], home, away) else -st["exp_margin"]
        p = 1 - _ncdf((c["line"] - m) / st["sd_m"])  # team's margin > line
        return p if c["direction"] == "over" else 1 - p
    if c["kind"] == "total":
        p = 1 - _ncdf((c["line"] - st["exp_total"]) / st["sd_t"])
        return p if c["direction"] == "over" else 1 - p
    if c["kind"] == "prop":
        from .props.odds import norm_name
        po = props.get((norm_name(c["player"]), c["market"], c["line"]))
        if po is None:
            return None
        return po if c["direction"] == "over" else 1 - po
    return None


def prop_fairs(sport: str, summary: dict, anchor: dict, elapsed: float, home: str, away: str, hs: float, as_: float,
               wanted: list[dict]) -> dict:
    """Claude Prop 1's P(over) for each exchange prop (player, market, line) it can price."""
    from . import live_prop_engine as E
    box = E.parse_box(summary)
    by = {E._norm(n): p for n, p in box["players"].items()}
    out = {}
    for c in wanted:
        key = (E._norm(c["player"]), c["market"], c["line"])
        if key in out:
            continue
        pl, a = by.get(key[0]), anchor.get((key[0], c["market"]))
        if not pl or a is None:
            continue
        so_far, usage = E.stat_value(sport, E.specs(sport)[c["market"]], pl)
        if not usage:
            continue  # hasn't played: hurt, benched or not dressed
        tstats = next((v for k, v in box["teams"].items() if similarity(k, pl["team"]) >= 0.75), {})
        plays = E.team_plays(sport, tstats) or E.GAME[sport][1] * elapsed / E.GAME[sport][0]
        margin = (hs - as_) * (1 if _is_home(pl["team"], home, away) else -1)
        bst = next(iter(pl["stats"].values()), {})
        st = E.PropState(sport, c["market"], c["player"], float(so_far or 0), a, elapsed, plays, margin,
                         fouls=float(bst.get("PF") or 0), minutes=float(bst.get("MIN") or 0), usage=usage)
        pr = E.project(st)
        out[key] = E.p_over(sport, c["market"], st.so_far, pr["rem_mean"], c["line"])[0]
    return out


def bovada_check(c: dict, odds, home: str, away: str) -> float | None:
    """Bovada's no-vig probability for the same outcome (ML and the full-game total at the same line only)."""
    from .markets import no_vig
    if odds is None:
        return None
    if c["kind"] == "ml" and odds.home_ml is not None and odds.away_ml is not None:
        ph, pa = no_vig(odds.home_ml, odds.away_ml)
        p = ph if _is_home(c["team"], home, away) else pa
        return p if c["direction"] != "not" else 1 - p
    if c["kind"] == "total" and odds.total is not None and abs(odds.total - c["line"]) < 1e-9:
        po, pu = no_vig(odds.over_price, odds.under_price)
        return po if c["direction"] == "over" else pu
    return None


# ---------------------------------------------------------------- decision


def decide(contracts: list[dict], st: dict, home: str, away: str, props: dict, odds, minutes_left: float,
           sport: str) -> tuple[list[dict], list[dict]]:
    """(every priced contract, paper trades to take)."""
    total_min = {"nba": 48, "ncaab": 40, "ncaaw": 40}[sport]
    priced, take = [], []
    for c in contracts:
        p = fair(c, st, home, away, props)
        if p is None:
            continue
        cost = c["ask"] + c["fee"]
        edge = p - cost
        bov = bovada_check(c, odds, home, away)
        row = {**c, "fair": round(p, 4), "cost": round(cost, 4), "edge": round(edge, 4),
               "bovada_p": None if bov is None else round(bov, 4)}
        need = _env("EX_MIN_EDGE_PROPS", 0.07) if c["kind"] == "prop" else _env("EX_MIN_EDGE", 0.05)
        gap = (c["ask"] - c["bid"]) if c["bid"] is not None else 1.0
        why = ""
        if not 0.08 <= c["ask"] <= 0.92:
            why = "price range"
        elif gap > _env("EX_MAX_GAP", 0.08):
            why = "no liquidity"
        elif minutes_left < 3 or minutes_left > total_min - 6:
            why = "too early/late"
        elif edge < need:
            why = "edge"
        elif bov is not None and bov < c["ask"] - 0.03:
            why = "bovada disagrees"
        row["reason"] = why
        priced.append(row)
        if not why:
            take.append(row)
    take.sort(key=lambda r: -r["edge"])
    picked, seen = [], set()
    for r in take:
        k = (r["kind"], r.get("player"), r.get("market"))
        if k in seen:  # one contract per player-market / per market kind each break (no ladders of the same bet)
            continue
        seen.add(k)
        picked.append(r)
        if len(picked) >= int(_env("EX_MAX_PER_GAME", 3)):
            break
    return priced, picked


# ---------------------------------------------------------------- ledger


def _dir(state_dir: Path) -> Path:
    d = Path(state_dir) / "exchange_paper"
    d.mkdir(parents=True, exist_ok=True)
    return d


def load(state_dir: Path) -> list[dict]:
    p = _dir(state_dir) / "trades.json"
    return json.loads(p.read_text()) if p.exists() else []


def save(state_dir: Path, book: list[dict]) -> None:
    (_dir(state_dir) / "trades.json").write_text(json.dumps(book[-20000:], indent=1))


def log_snapshot(state_dir: Path, game_id: str, period: str, priced: list[dict]) -> None:
    at = datetime.now(timezone.utc).isoformat()
    with (_dir(state_dir) / "snapshots.jsonl").open("a") as fh:
        for r in priced:
            fh.write(json.dumps({"at": at, "game_id": game_id, "period": period, **r}) + "\n")


def record(state_dir: Path, sport: str, game_id: str, game: str, period: str, picks: list[dict],
           priced: list[dict]) -> list[dict]:
    """Logs new paper trades (each contract once per game) and marks open ones with the latest price (closing value)."""
    book = load(state_dir)
    now = {r["id"]: r for r in priced}
    for b in book:
        if b["game_id"] == str(game_id) and b.get("result") is None and b["id"] in now:
            b["last_bid"] = now[b["id"]]["bid"]
            b["last_ask"] = now[b["id"]]["ask"]
    have = {(b["game_id"], b["id"]) for b in book}
    stake = _env("EX_STAKE", 10)
    new = []
    for r in picks:
        if (str(game_id), r["id"]) in have:
            continue
        n = int(stake // r["ask"]) if r["ask"] > 0 else 0
        if n < 1:
            continue
        fee = math.ceil(r["fee"] * n * 100) / 100 if r["venue"] == "kalshi" else round(r["fee"] * n, 4)
        t = {"at": datetime.now(timezone.utc).isoformat(), "sport": sport, "game_id": str(game_id), "game": game,
             "period": period, **{k: r[k] for k in ("venue", "id", "kind", "team", "line", "player", "market",
                                                    "direction", "ask", "bid", "fair", "edge", "bovada_p", "title")},
             "contracts": n, "fee_total": fee, "result": None}
        book.append(t)
        new.append(t)
    save(state_dir, book)
    return new


def settle(t: dict, home: str, away: str, hs: float, as_: float, box_value) -> str | None:
    """'win' / 'loss' / 'void' for one trade from the final score (props: box_value(player, market))."""
    if t["kind"] == "ml":
        won = (hs > as_) == _is_home(t["team"], home, away)
        return "win" if won == (t["direction"] != "not") else "loss"
    if t["kind"] == "spread":
        m = (hs - as_) * (1 if _is_home(t["team"], home, away) else -1)
        hit = m > t["line"]
    elif t["kind"] == "total":
        hit = hs + as_ > t["line"]
    else:
        v = box_value(t["player"], t["market"])
        if v is None:
            return "void"
        hit = v > t["line"]
    return "win" if hit == (t["direction"] == "over") else "loss"


def grade(state_dir: Path, sport: str, game_id: str, home: str, away: str, hs: float, as_: float,
          summary: dict | None) -> list[dict]:
    from . import live_prop_engine as E
    box = E.parse_box(summary)["players"] if summary else {}
    by = {E._norm(n): p for n, p in box.items()}

    def box_value(player, market):
        pl = by.get(E._norm(player))
        return None if not pl else float(E.stat_value(sport, E.specs(sport)[market], pl)[0] or 0)

    book, done = load(state_dir), []
    for t in book:
        if t["game_id"] != str(game_id) or t.get("result") is not None:
            continue
        t["result"] = settle(t, home, away, hs, as_, box_value)
        cost = t["ask"] * t["contracts"]
        t["pnl"] = round((t["contracts"] - cost - t["fee_total"]) if t["result"] == "win" else
                         (0.0 if t["result"] == "void" else -cost - t["fee_total"]), 2)
        last = t.get("last_bid")
        t["clv"] = None if last is None else round(last - t["ask"], 4)  # sell-side value at the last break vs entry
        done.append(t)
    if done:
        save(state_dir, book)
    return done


def summary_stats(rows: list[dict]) -> dict:
    g = [t for t in rows if t.get("result") in ("win", "loss")]
    staked = sum(t["ask"] * t["contracts"] + t["fee_total"] for t in g)
    pnl = sum(t["pnl"] for t in g)
    clv = [t["clv"] for t in g if t.get("clv") is not None]
    w = sum(t["result"] == "win" for t in g)
    return {"n": len(g), "wins": w, "losses": len(g) - w, "staked": round(staked, 2), "pnl": round(pnl, 2),
            "roi": pnl / staked if staked else None, "clv": sum(clv) / len(clv) if clv else None,
            "proven": len(g) >= 100 and pnl > 0 and bool(clv) and sum(clv) / len(clv) > 0}


def report(state_dir: Path) -> str:
    book = load(state_dir)
    out = [f"{NAME} (Kalshi / Polymarket, in-game basketball, PAPER)"]
    groups = [("all", book)]
    for key in ("venue", "sport", "kind"):
        for v in sorted({str(t.get(key)) for t in book}):
            groups.append((f"{key}={v}", [t for t in book if str(t.get(key)) == v]))
    for name, rows in groups:
        s = summary_stats(rows)
        if not s["n"] and name != "all":
            continue
        roi = f"{s['roi']:+.1%}" if s["roi"] is not None else "-"
        clv = f"{s['clv'] * 100:+.1f}c" if s["clv"] is not None else "-"
        out.append(f"  {name:18s} {s['wins']}-{s['losses']}  staked ${s['staked']:.0f}  P&L ${s['pnl']:+.2f}  ROI {roi}"
                   f"  closing value {clv}" + ("  BAR MET" if s["proven"] else ""))
    open_n = sum(t.get("result") is None for t in book)
    out.append(f"  open trades: {open_n}")
    out.append("Bar before any real order: 100+ graded, positive ROI after fees AND positive closing value.")
    return "\n".join(out)


def trade_text(sport: str, game: str, period: str, score: str, new: list[dict]) -> str:
    lines = [f"📈 <b>EXCHANGE PAPER</b> · {sport.upper()} · {game} · {period} · {score}"]
    for t in new:
        bov = f" · Bovada {t['bovada_p']:.0%}" if t.get("bovada_p") is not None else ""
        lines.append(f"• {t['venue'].title()}: {t['title']} @ {t['ask'] * 100:.0f}¢ x{t['contracts']}"
                     f"\n   Marv {t['fair']:.0%} · edge {t['edge'] * 100:+.1f}¢ after fee{bov}")
    lines.append("<i>Paper only: no order placed.</i>")
    return "\n".join(lines)


# ---------------------------------------------------------------- live-monitor hook


def enabled(sport: str) -> bool:
    return sport in SPORTS and os.environ.get("EXCHANGE_PAPER", "on").lower() not in ("0", "off", "false", "no")


def on_break(settings, client, sport: str, game, ended: int, period_label: str, st: dict, send) -> int:
    """At a break: price the game's Kalshi + Polymarket contracts and log paper trades. Returns messages sent."""
    from . import live_prop_engine as E
    from .live_monitor import STRUCTURE, minutes_left
    state_dir = Path(settings.state_dir)
    hs, as_ = float(game.info.get("live_home") or 0), float(game.info.get("live_away") or 0)
    contracts = []
    for fetch in (kalshi_game, pm_game):
        try:
            contracts += fetch(sport, game.home, game.away, game.start)
        except Exception as exc:
            log.warning("exchange %s %s: %s", fetch.__name__, game.id, exc)
    if not contracts:
        return 0
    left = minutes_left(sport, ended, game.info.get("clock"), True)
    props = {}
    wanted = [c for c in contracts if c["kind"] == "prop"]
    if wanted:
        try:
            summary = client.summary(STRUCTURE[sport][3], str(game.id))
            props = prop_fairs(sport, summary, E.anchors(state_dir, sport), E.elapsed_minutes(sport, ended),
                               game.home, game.away, hs, as_, wanted)
        except Exception as exc:
            log.warning("exchange props %s: %s", game.id, exc)
    priced, picks = decide(contracts, st, game.home, game.away, props, game.odds, left, sport)
    log_snapshot(state_dir, str(game.id), period_label, priced)
    label = f"{game.away} @ {game.home}"
    new = record(state_dir, sport, str(game.id), label, period_label, picks, priced)
    if new and os.environ.get("EXCHANGE_PAPER_SEND", "on").lower() not in ("0", "off", "false", "no"):
        send(trade_text(sport, label, period_label, f"{game.away} {as_:.0f} – {hs:.0f} {game.home}", new))
        return 1
    return 0


def on_final(settings, client, sport: str, game, send) -> int:
    from .live_monitor import STRUCTURE
    state_dir = Path(settings.state_dir)
    if not any(t["game_id"] == str(game.id) and t.get("result") is None for t in load(state_dir)):
        return 0
    summary = None
    try:
        summary = client.summary(STRUCTURE[sport][3], str(game.id))
    except Exception as exc:
        log.warning("exchange final box %s: %s", game.id, exc)
    done = grade(state_dir, sport, str(game.id), game.home, game.away, float(game.home_score), float(game.away_score),
                 summary)
    if not done or os.environ.get("EXCHANGE_PAPER_SEND", "on").lower() in ("0", "off", "false", "no"):
        return 0
    icon = {"win": "✅", "loss": "❌", "void": "⚪"}
    s = summary_stats(load(state_dir))
    lines = [f"📈 <b>EXCHANGE PAPER · final</b> · {game.away} {float(game.away_score):.0f} – "
             f"{float(game.home_score):.0f} {game.home}"]
    lines += [f"{icon.get(t['result'], '?')} {t['venue'].title()}: {t['title']} @ {t['ask'] * 100:.0f}¢ · ${t['pnl']:+.2f}"
              for t in done]
    lines.append(f"Paper record: {s['wins']}-{s['losses']} · P&L ${s['pnl']:+.2f}"
                 + (f" · ROI {s['roi']:+.1%}" if s["roi"] is not None else ""))
    send("\n".join(lines))
    return 1
