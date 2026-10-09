"""Telegram /parlay: the best 2-leg and 3-leg parlays from Marv's current board (NFL, CFB, NBA, WNBA, NCAAB, NCAAW,
EuroLeague).

Only three kinds of leg are allowed (nothing new is modelled; each reuses an existing Marv read):
  * UPSET  - the underdog ML where marv/upset.py's upset score fires (heavy favourite whose metrics fall short of
             the price, with at least one non-market metric against it)
  * H2H    - the game's H2H pick ML when Marv's every-metric read (marv/insight.side_insight) is 👍 good and the game
             is not a close (~50/50) one; favourites shorter than PARLAY_MAX_FAV (default -500) are skipped (they add
             almost nothing to the payout)
  * O/U    - a total only when its rule passed the proven bar (marv/proven.py: today CFB OVER-FADE -> Under and the
             CFB Bovada-vs-Pinnacle total GAP); sources: ou_tags_log, sharp_gap_log and the engine's own notes
One leg per game (no correlated same-game legs). Legs are ranked by metric strength (upset score, H2H weighted net,
proven backtest hit rate) -- a ranking, NOT a probability: no parlay % prints, since no parlay rule is proven.
Prices: real Bovada prices from state/bovada_prices.json (saved by every board refresh at no extra Odds API cost;
re-fetched with bookmakers=bovada when older than PARLAY_BOVADA_MAX_AGE_MIN, default 90). A leg Bovada hasn't posted
falls back to the median ML across books ("cons.") or the logged total at -110 ("est."), and the parlay says so.
Read-only: nothing is sent unprompted, nothing is logged as a bet. PAPER_MODE labels every card as paper.
"""

import html
import itertools
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from .data.teams import similarity

log = logging.getLogger(__name__)
ET = ZoneInfo("America/New_York")
SPORTS = ("nfl", "cfb", "nba", "wnba", "ncaab", "ncaaw", "euroleague")
PRICES = "bovada_prices.json"
ODDS_KEYS = {"nfl": "americanfootball_nfl", "cfb": "americanfootball_ncaaf", "nba": "basketball_nba",
             "wnba": "basketball_wnba", "ncaab": "basketball_ncaab", "ncaaw": "basketball_wncaab",
             "euroleague": "basketball_euroleague"}
MAX_FAV = float(os.environ.get("PARLAY_MAX_FAV", "-500"))
MAX_AGE_MIN = float(os.environ.get("PARLAY_BOVADA_MAX_AGE_MIN", "90"))
DAYS = int(os.environ.get("PARLAY_DAYS", "7"))
TOP_LEGS = 14  # combos are built from the strongest legs only


# ---------------------------------------------------------------- odds math
def decimal(price: float) -> float:
    price = float(price)
    return 1 + (price / 100 if price > 0 else 100 / -price)


def combined_american(prices) -> int:
    d = 1.0
    for p in prices:
        d *= decimal(p)
    return round((d - 1) * 100) if d >= 2 else round(-100 / (d - 1))


# ---------------------------------------------------------------- Bovada prices
def event_prices(event: dict, book: str = "bovada") -> dict | None:
    """{'home','away','start','ml':{'home','away'},'total':(pt, over, under),'spread':(home pt, home px, away px)}
    from one Odds API event, using only `book`'s quotes; None when the book hasn't posted the game."""
    home, away = event["home_team"], event["away_team"]
    b = next((x for x in event.get("bookmakers", []) if x.get("key") == book), None)
    if b is None:
        return None
    out = {"home": home, "away": away, "start": event.get("commence_time")}
    for m in b.get("markets", []):
        outs = {o["name"]: o for o in m.get("outcomes", [])}
        if m["key"] == "h2h" and home in outs and away in outs:
            out["ml"] = {"home": outs[home]["price"], "away": outs[away]["price"]}
        elif m["key"] == "totals" and "Over" in outs and "Under" in outs:
            out["total"] = [outs["Over"].get("point"), outs["Over"]["price"], outs["Under"]["price"]]
        elif m["key"] == "spreads" and home in outs and away in outs:
            out["spread"] = [outs[home].get("point"), outs[home]["price"], outs[away]["price"]]
    return out


def save_prices(state_dir: Path, sport: str, events: list[dict], now: datetime | None = None) -> None:
    """Board hook: keep Bovada's posted prices for every event the board already fetched (no extra credits)."""
    path = Path(state_dir) / PRICES
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        data = {}
    rows = [r for r in (event_prices(e) for e in events) if r]
    data[sport] = {"at": (now or datetime.now(timezone.utc)).isoformat(), "events": rows}
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    tmp.replace(path)


def load_prices(settings, state_dir: Path, sports, now: datetime | None = None, fetch=None) -> dict:
    """{sport: [event prices]}; re-fetches a sport (Bovada only, 3 credits) when its saved prices are stale."""
    now = now or datetime.now(timezone.utc)
    path = Path(state_dir) / PRICES
    try:
        data = json.loads(path.read_text()) if path.exists() else {}
    except (OSError, ValueError):
        data = {}
    out = {}
    for sport in sports:
        row = data.get(sport) or {}
        try:
            age = (now - datetime.fromisoformat(row["at"])).total_seconds() / 60
        except (KeyError, ValueError):
            age = None
        key = getattr(settings, "odds_api_key", None) if settings is not None else None
        if (age is None or age > MAX_AGE_MIN) and (key or fetch):
            try:
                events = (fetch or _fetch_bovada)(key, ODDS_KEYS[sport])
                save_prices(state_dir, sport, events, now)
                row = {"events": [r for r in (event_prices(e) for e in events) if r]}
            except Exception as exc:  # noqa: BLE001 - stale prices beat no answer
                log.warning("parlay bovada %s: %s", sport, exc)
        out[sport] = row.get("events", [])
    return out


def _fetch_bovada(api_key: str, sport_key: str) -> list[dict]:
    import requests
    from .data.oddsapi import URL
    resp = requests.get(URL.format(sport=sport_key), timeout=30, params={
        "apiKey": api_key, "bookmakers": "bovada", "markets": "h2h,spreads,totals", "oddsFormat": "american"})
    resp.raise_for_status()
    log.info("Odds API %s (bovada, parlay): %s credits left", sport_key, resp.headers.get("x-requests-remaining"))
    return resp.json()


def match(prices: list[dict], home: str, away: str) -> dict | None:
    best, score = None, 0.0
    for p in prices:
        s = min(similarity(home, p["home"]), similarity(away, p["away"]))
        if s > score:
            best, score = p, s
    return best if score >= 0.75 else None


# ---------------------------------------------------------------- legs
def _short(sport: str, name: str) -> str:
    return name.split()[-1] if sport == "nfl" and len(name.split()) > 1 else name


def _gkey(sport: str, home: str, away: str) -> str:
    return f"{sport}:" + "|".join(sorted(x.lower() for x in (home, away)))


def _start(v) -> datetime | None:
    try:
        d = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)
    except (TypeError, ValueError):
        return None


def _ml_price(state_dir: Path, sport: str, rec: dict, t: str, bov: dict | None) -> tuple[float | None, str]:
    if bov and bov.get("ml"):
        # bov home/away may be listed the other way round from Marv's record
        same = similarity(rec["home"], bov["home"]) >= similarity(rec["home"], bov["away"])
        return bov["ml"][t if same else ("away" if t == "home" else "home")], "Bovada"
    from . import upset
    ml = upset._ml(state_dir, sport, rec.get("game_id"))
    if ml.get(t) is not None:
        return ml[t], "cons."
    return None, ""


def _games(state_dir: Path, sports, prices: dict, now: datetime, days: int):
    """(sport, rec, card, start, bovada) for every upcoming game in Marv's slate or on a fresh H2H card."""
    from . import bridge
    from . import insight as I
    try:
        preds = json.loads((Path(state_dir) / "predictions.json").read_text())
    except (OSError, ValueError):
        preds = {}
    seen, out = set(), []
    for rec in preds.values():
        sport = rec.get("sport")
        if sport not in sports:
            continue
        rec = bridge._engine(rec)
        card = bridge._card_for(state_dir, sport, rec)
        out.append((sport, rec, card))
        seen.add(_gkey(sport, rec["home"], rec["away"]))
    for sport in sports:
        for c in bridge._h2h_cards(state_dir, sport):
            if _gkey(sport, c["home"], c["away"]) in seen or any(
                    o[0] == sport and I._match(o[1]["home"], o[1]["away"], c["home"], c["away"]) for o in out):
                continue
            out.append((sport, bridge._from_card(c) | {"start": c.get("start")}, c))
    rows = []
    for sport, rec, card in out:
        bov = match(prices.get(sport, []), rec["home"], rec["away"])
        start = _start(rec.get("start")) or _start((card or {}).get("start")) or _start((bov or {}).get("start"))
        if start is None or start <= now or start > now + timedelta(days=days):
            continue
        rows.append((sport, rec, card, start, bov))
    return rows


def upset_legs(state_dir: Path, sports, prices: dict, now: datetime, days: int) -> list[dict]:
    from . import upset
    legs = []
    for u in upset.best(state_dir, sports, now, days=days):
        if not u.get("fires"):
            continue
        rec = {"home": u["home"], "away": u["away"], "game_id": u.get("game_id")}
        t = "home" if u["dog"] == u["home"] else "away"
        bov = match(prices.get(u["sport"], []), u["home"], u["away"])
        price, book = _ml_price(state_dir, u["sport"], rec, t, bov)
        if price is None or price < 0:
            continue  # the "dog" isn't plus money at the book: not an upset ticket
        legs.append({"kind": "upset", "sport": u["sport"], "gkey": _gkey(u["sport"], u["home"], u["away"]),
                     "game": f"{u['away']} @ {u['home']}", "start": u.get("start"), "market": "ml",
                     "pick": f"{_short(u['sport'], u['dog'])} ML", "price": price, "book": book,
                     "strength": round(2 + min(u["score"], 4.0), 2),
                     "why": f"upset score {u['score']:+.1f} vs {_short(u['sport'], u['fav'])} "
                            f"({(u.get('reasons') or ['metrics short of price'])[0]})"})
    return legs


def h2h_legs(state_dir: Path, games, max_fav: float = MAX_FAV) -> list[dict]:
    from . import bridge
    from . import insight as I
    legs = []
    for sport, rec, card, start, bov in games:
        margin = card["margin"] if card and card.get("margin") is not None else rec.get("model_margin") or 0.0
        if abs(margin) < bridge.CLOSE_MARGIN:
            continue  # coin-flip game: H2H pick, never a leg
        pick, method = bridge._h2h_pick(card, rec, margin)
        t = "home" if similarity(pick, rec["home"]) >= similarity(pick, rec["away"]) else "away"
        price, book = _ml_price(state_dir, sport, rec, t, bov)
        if price is None or price < max_fav:
            continue
        ins = I.side_insight(state_dir, sport, rec, card, t, None, price, False, spread=False)
        if ins.verdict != "good":
            continue
        name = rec[t]
        pro = [f.label for f in sorted((f for f in ins.factors if f.side > 0), key=lambda f: -f.w)
               if not f.label.startswith("Bovada favours")][:2]
        a, b = ins.tally
        legs.append({"kind": "h2h", "sport": sport, "gkey": _gkey(sport, rec["home"], rec["away"]),
                     "game": f"{rec['away']} @ {rec['home']}", "start": start.isoformat(), "market": "ml",
                     "pick": f"{_short(sport, name)} ML", "price": price, "book": book,
                     "strength": round(min(float(ins.net), 6.0), 2),
                     "why": f"H2H 👍 {method} ({a}-{b}): " + ", ".join(pro)})
    return legs


def _total_price(bov: dict | None, side: str, line: float | None) -> tuple[float | None, float | None, str]:
    if bov and bov.get("total") and bov["total"][0] is not None:
        pt, o, u = bov["total"]
        return float(pt), (o if side == "Over" else u), "Bovada"
    if line is not None:
        return float(line), -110.0, "est."
    return None, None, ""


def ou_legs(state_dir: Path, sports, games, prices: dict, now: datetime, days: int) -> list[dict]:
    from . import insight as I
    from . import proven
    cand: dict[str, list[dict]] = {}

    def add(sport, home, away, start, side, signal, line, price=None, book=None, rec=None, card=None):
        ev = proven.evidence(sport, "total", signal)
        if ev is None:
            return
        st = _start(start)
        if st is None or st <= now or st > now + timedelta(days=days):
            return
        bov = match(prices.get(sport, []), home, away)
        if price is None:
            line, price, book = _total_price(bov, side, line)
        elif bov and bov.get("total") and bov["total"][0] is not None and float(bov["total"][0]) == float(line):
            price, book = (bov["total"][1] if side == "Over" else bov["total"][2]), "Bovada"
        if price is None or line is None:
            return
        if rec is not None:  # Marv's every-metric total read must not be against the proven side
            if I.total_insight(state_dir, sport, rec, card, side == "Over", float(line)).verdict == "not":
                return
        cand.setdefault(_gkey(sport, home, away), []).append(
            {"kind": "ou", "sport": sport, "gkey": _gkey(sport, home, away), "game": f"{away} @ {home}",
             "start": st.isoformat(), "market": "total", "pick": f"{side} {float(line):g}", "side": side,
             "price": price, "book": book, "signal": proven.PARENT.get(signal, signal), "ev": ev,
             "strength": round(3 + (ev.hit - 0.524) * 100, 2)})

    by_key = {_gkey(s, r["home"], r["away"]): (s, r, c) for s, r, c, _, _ in games}
    try:
        tags = json.loads((Path(state_dir) / "ou_tags_log.json").read_text())
    except (OSError, ValueError):
        tags = {}
    for r in (tags.values() if isinstance(tags, dict) else tags):
        if r.get("sport") in sports and r.get("result") is None:
            g = by_key.get(_gkey(r["sport"], r.get("home", ""), r.get("away", "")))
            add(r["sport"], r.get("home", ""), r.get("away", ""), r.get("start"), r.get("side"), r.get("tag"),
                r.get("line"), rec=g[1] if g else None, card=g[2] if g else None)
    try:
        gaps = json.loads((Path(state_dir) / "sharp_gap_log.json").read_text())
    except (OSError, ValueError):
        gaps = {}
    for r in (gaps.values() if isinstance(gaps, dict) else gaps):
        if r.get("sport") in sports and r.get("market") == "total" and r.get("result") is None:
            add(r["sport"], r["home"], r["away"], r.get("start"), r.get("side"), "GAP", r.get("line"),
                r.get("price"), "Bovada (gap scan)")
    for sport, rec, card, start, bov in games:  # engine notes / H2H card fade on Marv's own slate
        tl = I.best_total_line(state_dir, sport, rec, card)
        if tl is None:
            continue
        for over in (False, True):
            ins = I.total_insight(state_dir, sport, rec, card, over, tl)
            if ins.signal and ins.verdict != "not":
                add(sport, rec["home"], rec["away"], start.isoformat(), "Over" if over else "Under", ins.signal, tl,
                    rec=rec, card=card)
    legs = []
    for rows in cand.values():
        sides = {r["side"] for r in rows}
        if len(sides) > 1:
            continue  # proven rules disagree on this game: no leg
        sigs = sorted({r["signal"] for r in rows})
        best = max(rows, key=lambda r: (r["book"] == "Bovada", r["strength"]))
        ev = max((r["ev"] for r in rows), key=lambda e: e.hit)
        leg = {k: v for k, v in best.items() if k != "ev"}
        leg["strength"] = round(best["strength"] + (1.0 if len(sigs) > 1 else 0.0), 2)
        leg["why"] = f"proven O/U {'+'.join(sigs)} ({ev.hit:.1%} hit, backtest n={ev.n:,}, ROI {ev.roi:+.1%})"
        legs.append(leg)
    return legs


def all_legs(state_dir: Path, sports=SPORTS, prices: dict | None = None, now: datetime | None = None,
             days: int = DAYS) -> list[dict]:
    now = now or datetime.now(timezone.utc)
    state_dir = Path(state_dir)
    prices = prices or {}
    games = _games(state_dir, sports, prices, now, days)
    legs = upset_legs(state_dir, sports, prices, now, days) + h2h_legs(state_dir, games) + \
        ou_legs(state_dir, sports, games, prices, now, days)
    # one leg per game: keep the strongest (an upset dog and an H2H pick on the same game can't both be right)
    best: dict[str, dict] = {}
    for leg in legs:
        k = leg["gkey"]
        if k not in best or leg["strength"] > best[k]["strength"]:
            best[k] = leg
    return sorted(best.values(), key=lambda x: (-x["strength"], x.get("start") or ""))


def best_parlays(legs: list[dict], sizes=(2, 3)) -> dict[int, dict]:
    """Highest total-strength combo of distinct games per size; ties go to the bigger Bovada payout."""
    pool = legs[:TOP_LEGS]
    out = {}
    for n in sizes:
        best = None
        for combo in itertools.combinations(pool, n):
            if len({c["gkey"] for c in combo}) < n:
                continue
            key = (round(sum(c["strength"] for c in combo), 2), sum(c["book"].startswith("Bovada") for c in combo),
                   combined_american([c["price"] for c in combo]))
            if best is None or key > best[0]:
                best = (key, combo)
        if best:
            combo = sorted(best[1], key=lambda c: c.get("start") or "")
            out[n] = {"legs": list(combo), "price": combined_american([c["price"] for c in combo]),
                      "all_bovada": all(c["book"].startswith("Bovada") for c in combo)}
    return out


# ---------------------------------------------------------------- text
def _when(start) -> str:
    st = _start(start)
    return st.astimezone(ET).strftime("%a %-I:%M %p ET") if st else ""


def text(parlays: dict[int, dict], paper: bool = True, n_legs: int = 0) -> str:
    if not parlays:
        return ("🎰 MARV PARLAYS\nNot enough qualifying legs right now (need 2+ games with a firing upset score, "
                f"a 👍 H2H read or a proven O/U; found {n_legs}).")
    lines = ["🎰 MARV PARLAYS" + (" · 📝 PAPER" if paper else "")]
    icon = {"upset": "🚨", "h2h": "🧠", "ou": "📐"}
    for n in sorted(parlays):
        p = parlays[n]
        src = "Bovada" if p["all_bovada"] else "Bovada + est. legs"
        lines.append("")
        lines.append(f"{n}-LEG  {p['price']:+d} ({src})")
        for c in p["legs"]:
            book = "" if c["book"] == "Bovada" else f" {c['book'].replace('Bovada ', '')}"
            lines.append(f"{icon[c['kind']]} {c['sport'].upper()} {c['pick']} {int(c['price']):+d}{book} · "
                         f"{c['game']} · {_when(c['start'])}")
        lines.append("why: " + " + ".join(c["why"] for c in p["legs"]))
    lines.append("")
    lines.append("Legs: firing upset score 🚨, 👍 H2H read 🧠, proven O/U 📐 only · ranked by metric strength, not a "
                 "probability · no proven parlay % (only O/U legs carry a backtest)"
                 + (" · paper only (PAPER_MODE), not logged as bets" if paper else ""))
    return html.escape("\n".join(lines))


def active_sports(state_dir: Path, sports, now: datetime | None = None, days: int = DAYS) -> list[str]:
    """Sports with an upcoming game in Marv's slate (NFL/CFB also count when their H2H card exists)."""
    from . import bridge
    now = now or datetime.now(timezone.utc)
    try:
        preds = json.loads((Path(state_dir) / "predictions.json").read_text())
    except (OSError, ValueError):
        preds = {}
    live = set()
    for rec in preds.values():
        st = _start(rec.get("start"))
        if st and now < st <= now + timedelta(days=days):
            live.add(rec.get("sport"))
    return [s for s in sports if s in live or (s in ("nfl", "cfb") and bridge._h2h_cards(Path(state_dir), s))]


def answer(settings, sports=None, now: datetime | None = None, fetch=None) -> str:
    """Telegram /parlay (read-only)."""
    state = Path(settings.state_dir)
    sports = [s for s in (sports or getattr(settings, "sports", SPORTS)) if s in SPORTS] or list(SPORTS)
    sports = active_sports(state, sports, now)  # no Bovada refetch (Odds API credits) for a sport with no slate
    prices = load_prices(settings, state, sports, now, fetch)
    legs = all_legs(state, sports, prices, now)
    return text(best_parlays(legs), getattr(settings, "paper_mode", True), len(legs))
