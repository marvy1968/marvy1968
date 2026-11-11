#!/usr/bin/env python3
"""
kalpoly - Kalshi + Polymarket prop & price scanner

Commands
  gaps     Find price gaps / possible arbitrage between Kalshi and Polymarket sports markets
  books    Pull sportsbook player props (The Odds API), de-vig them, flag +EV book bets,
           and compare the fair price to Kalshi / Polymarket prop markets
  model    Your own prop projection model from a game-log CSV
  all      Run gaps + books and save everything to ./kalpoly_out/
  sample   Write a sample game-log CSV so you can try `model` right away

Setup
  pip install requests
  export ODDS_API_KEY=your_key      # only needed for `books` (https://the-odds-api.com)

Examples
  python kalpoly.py gaps
  python kalpoly.py books --sport basketball_nba --markets player_points,player_rebounds
  python kalpoly.py sample && python kalpoly.py model --gamelog sample_gamelog.csv \
        --player "Sample Guard" --stat pts --line 24.5 --price 0.52
  python kalpoly.py all --sport basketball_nba

Reading Kalshi & Polymarket prices needs no API key. Nothing here places trades.
For personal research only: Kalshi's developer agreement restricts redistributing its data.
"""
import argparse
import csv
import json
import math
import os
import re
import statistics
import sys
import time
from datetime import datetime, timezone
from difflib import SequenceMatcher

try:
    import requests
except ImportError:  # pragma: no cover
    requests = None

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA = "https://gamma-api.polymarket.com"
ODDS = "https://api.the-odds-api.com/v4"
OUT_DIR = "kalpoly_out"
UA = {"User-Agent": "kalpoly/1.0"}

STOP = {"will", "the", "a", "an", "of", "vs", "v", "at", "to", "win", "game", "match", "on",
        "in", "be", "by", "and", "or", "yes", "no", "who", "winner", "o", "u", "over", "under"}


# ----------------------------------------------------------------------------- helpers
def get(url, params=None, tries=3):
    if requests is None:
        sys.exit("Install requests first: pip install requests")
    for i in range(tries):
        try:
            r = requests.get(url, params=params, headers=UA, timeout=20)
            if r.status_code == 429:
                time.sleep(2 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            if i == tries - 1:
                print(f"  ! request failed: {url} ({e})", file=sys.stderr)
                return None
            time.sleep(1 + i)
    return None


def jload(v, default=None):
    """Polymarket returns some list fields as JSON strings."""
    if isinstance(v, list):
        return v
    try:
        return json.loads(v) if v else default
    except (TypeError, ValueError):
        return default


def fnum(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def tokens(text):
    text = re.sub(r"[^a-z0-9.+ ]", " ", (text or "").lower())
    return {t for t in text.split() if t not in STOP and len(t) > 1}


def sim(a, b):
    ta, tb = tokens(a), tokens(b)
    if not ta or not tb:
        return 0.0
    jac = len(ta & tb) / len(ta | tb)
    seq = SequenceMatcher(None, " ".join(sorted(ta)), " ".join(sorted(tb))).ratio()
    return 0.6 * jac + 0.4 * seq


def parse_time(s):
    if not s:
        return None
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        return None


def american_to_prob(odds):
    odds = float(odds)
    return 100 / (odds + 100) if odds > 0 else -odds / (-odds + 100)


def american_to_decimal(odds):
    odds = float(odds)
    return 1 + odds / 100 if odds > 0 else 1 + 100 / -odds


def prob_to_american(p):
    if p <= 0 or p >= 1:
        return None
    return round(-100 * p / (1 - p)) if p >= 0.5 else round(100 * (1 - p) / p)


KALSHI_CONTRACTS = 10   # order size the fee is computed for (Kalshi rounds the fee UP to the cent per order)


def kalshi_fee(price, rate=0.07, contracts=None):
    """Kalshi taker fee per $1 contract: ceil(rate * C * P * (1-P)) to the cent, divided by C.
    Check Kalshi's current fee schedule; some markets use a different rate."""
    c = contracts or KALSHI_CONTRACTS
    return math.ceil(round(rate * c * price * (1 - price) * 100, 6)) / 100 / c


def save_csv(name, rows):
    if not rows:
        return None
    os.makedirs(OUT_DIR, exist_ok=True)
    path = os.path.join(OUT_DIR, name)
    keys = list(dict.fromkeys(k for r in rows for k in r))
    with open(path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(rows)
    return path


def show(rows, cols, limit=25):
    if not rows:
        print("  (none)")
        return
    widths = {c: min(max(len(c), *(len(str(r.get(c, ""))) for r in rows[:limit])), 48) for c in cols}
    print("  " + "  ".join(c.ljust(widths[c]) for c in cols))
    for r in rows[:limit]:
        print("  " + "  ".join(str(r.get(c, ""))[:48].ljust(widths[c]) for c in cols))
    if len(rows) > limit:
        print(f"  ... {len(rows) - limit} more in the CSV")


# ----------------------------------------------------------------------------- Kalshi
def kalshi_price(m, key):
    """Kalshi has moved between cent ints (yes_ask) and dollar strings (yes_ask_dollars)."""
    d = fnum(m.get(f"{key}_dollars"))
    if d is not None:
        return d
    c = fnum(m.get(key))
    return c / 100 if c is not None else None


def fetch_kalshi(series=None, max_pages=15):
    """Return a flat list of open Kalshi sports contracts with yes/no ask prices."""
    out, cursor = [], None
    print("Fetching Kalshi sports events...")
    for _ in range(max_pages):
        params = {"status": "open", "with_nested_markets": "true", "limit": 200}
        if series:
            params["series_ticker"] = series
        if cursor:
            params["cursor"] = cursor
        data = get(f"{KALSHI}/events", params)
        if not data:
            break
        for ev in data.get("events", []):
            if not series and (ev.get("category") or "").lower() != "sports":
                continue
            for m in ev.get("markets") or []:
                ya, yb = kalshi_price(m, "yes_ask"), kalshi_price(m, "yes_bid")
                if ya is None or yb is None or ya <= 0:
                    continue
                out.append({
                    "src": "kalshi",
                    "event": ev.get("title", ""),
                    "question": m.get("title", ""),
                    "outcome": m.get("yes_sub_title") or m.get("subtitle") or "",
                    "yes_ask": ya,
                    "no_ask": kalshi_price(m, "no_ask") or round(1 - yb, 4),
                    "time": m.get("expected_expiration_time") or m.get("close_time"),
                    "id": m.get("ticker"),
                    "volume": m.get("volume"),
                })
        cursor = data.get("cursor")
        if not cursor:
            break
    print(f"  {len(out)} Kalshi contracts")
    return out


# ----------------------------------------------------------------------------- Polymarket
def fetch_poly(tag="sports", max_pages=10):
    """Return a flat list of open Polymarket outcomes with yes/no ask prices."""
    out = []
    print("Fetching Polymarket sports events...")
    for page in range(max_pages):
        data = get(f"{GAMMA}/events", {"closed": "false", "active": "true", "tag_slug": tag,
                                       "limit": 100, "offset": page * 100})
        if not data:
            break
        for ev in data:
            for m in ev.get("markets") or []:
                if m.get("closed") or m.get("enableOrderBook") is False:
                    continue
                outcomes = jload(m.get("outcomes"), [])
                prices = [fnum(p) for p in jload(m.get("outcomePrices"), [])]
                if len(outcomes) != 2 or len(prices) != 2:
                    continue
                bid, ask = fnum(m.get("bestBid")), fnum(m.get("bestAsk"))
                # bestBid/bestAsk are for outcome[0]; outcome[1] ask = 1 - bid of outcome[0].
                # No live book -> skip: outcomePrices are last/mid prices you cannot buy at (fake arbs).
                if not bid or not ask or not 0 < bid < ask < 1:
                    continue
                a0, a1 = ask, 1 - bid
                base = {"src": "poly", "event": ev.get("title", ""), "question": m.get("question", ""),
                        "time": m.get("gameStartTime") or m.get("endDate") or ev.get("endDate"),
                        "id": m.get("slug") or m.get("conditionId"), "volume": m.get("volumeNum")}
                if [o.lower() for o in outcomes] == ["yes", "no"]:
                    out.append({**base, "outcome": "", "yes_ask": a0, "no_ask": a1})
                else:  # e.g. ["Lakers", "Celtics"] -> one row per side
                    out.append({**base, "outcome": outcomes[0], "yes_ask": a0, "no_ask": a1})
                    out.append({**base, "outcome": outcomes[1], "yes_ask": a1, "no_ask": a0})
        if len(data) < 100:
            break
    print(f"  {len(out)} Polymarket outcomes")
    return out


# ----------------------------------------------------------------------------- 1. gaps
def side_ok(k, p, poly_by_id):
    """For a two-sided Polymarket market (e.g. Lakers / Celtics), the Kalshi contract must name THIS side
    and not the other one. A Kalshi contract with no outcome label whose question names both sides is
    ambiguous and is skipped (pairing the wrong side shows a fake arbitrage)."""
    if not p["outcome"]:
        return True
    other = [q for q in poly_by_id.get(p["id"], []) if q["outcome"] and q["outcome"] != p["outcome"]]
    mine, theirs = tokens(p["outcome"]), set().union(*(tokens(q["outcome"]) for q in other)) if other else set()
    ktext = tokens(k["outcome"]) if k["outcome"] else tokens(k["question"])
    return bool(mine & ktext) and not (theirs & ktext)


def match_markets(kal, poly, min_event=0.45, min_outcome=0.6, max_hours=36):
    poly_by_id = {}
    for p in poly:
        poly_by_id.setdefault(p["id"], []).append(p)
    index = {}
    for i, p in enumerate(poly):
        for t in tokens(p["event"] + " " + p["question"]):
            index.setdefault(t, set()).add(i)
    pairs = []
    for k in kal:
        cand = {}
        for t in tokens(k["event"] + " " + k["question"]):
            for i in index.get(t, ()):
                cand[i] = cand.get(i, 0) + 1
        best = None
        for i, shared in cand.items():
            if shared < 2:
                continue
            p = poly[i]
            kt, pt = parse_time(k["time"]), parse_time(p["time"])
            if kt and pt and abs((kt - pt).total_seconds()) > max_hours * 3600:
                continue
            ev = sim(k["event"] + " " + k["question"], p["event"] + " " + p["question"])
            oc = sim(k["outcome"] or k["question"], p["outcome"] or p["question"])
            if ev < min_event or oc < min_outcome or not side_ok(k, p, poly_by_id):
                continue
            score = 0.5 * ev + 0.5 * oc
            if not best or score > best[0]:
                best = (score, p)
        if best:
            pairs.append((best[0], k, best[1]))
    return pairs


def cmd_gaps(args, save=True):
    kal = fetch_kalshi(args.kalshi_series)
    poly = fetch_poly(args.poly_tag)
    pairs = match_markets(kal, poly)
    print(f"Matched {len(pairs)} contracts (fuzzy - verify before trading)\n")
    rows = []
    for score, k, p in pairs:
        # Two ways to lock $1: YES on one venue + NO on the other
        legs = [("K yes + P no", k["yes_ask"], p["no_ask"], kalshi_fee(k["yes_ask"])),
                ("P yes + K no", p["yes_ask"], k["no_ask"], kalshi_fee(k["no_ask"]))]
        for name, a, b, fee in legs:
            cost = a + b + fee + args.poly_fee
            rows.append({
                "match": round(score, 2), "kalshi": f"{k['question']} [{k['outcome']}]",
                "poly": f"{p['question']} [{p['outcome']}]", "trade": name,
                "leg1": round(a, 3), "leg2": round(b, 3), "cost_w_fees": round(cost, 4),
                "profit_per_$1": round(1 - cost, 4),
                "price_gap": round(abs(k["yes_ask"] - p["yes_ask"]), 3),
                "kalshi_id": k["id"], "poly_id": p["id"],
            })
    rows.sort(key=lambda r: -r["profit_per_$1"])
    arbs = [r for r in rows if r["profit_per_$1"] > args.min_edge]
    print(f"Arbitrage candidates (profit > {args.min_edge:.3f} per $1 after est. fees):")
    show(arbs, ["match", "kalshi", "trade", "leg1", "leg2", "profit_per_$1"])
    print("\nBiggest price gaps (not arbs, but where the venues disagree):")
    gaps = sorted({r["kalshi_id"]: r for r in rows}.values(), key=lambda r: -r["price_gap"])
    show(gaps, ["match", "kalshi", "poly", "price_gap"], 15)
    if save:
        print("\nSaved:", save_csv("gaps.csv", rows))
    return rows, kal, poly


# ----------------------------------------------------------------------------- 2. books
def fetch_book_props(sport, markets, regions, max_events):
    key = os.environ.get("ODDS_API_KEY")
    if not key:
        sys.exit("Set ODDS_API_KEY (free tier at https://the-odds-api.com) to use `books`.")
    events = get(f"{ODDS}/sports/{sport}/events", {"apiKey": key}) or []
    print(f"Fetching sportsbook props for {min(len(events), max_events)} {sport} games "
          f"(each game x market x region uses API credits)...")
    lines = []
    for ev in events[:max_events]:
        data = get(f"{ODDS}/sports/{sport}/events/{ev['id']}/odds",
                   {"apiKey": key, "regions": regions, "markets": markets, "oddsFormat": "american"})
        if not data:
            continue
        game = f"{data.get('away_team')} @ {data.get('home_team')}"
        for bk in data.get("bookmakers", []):
            for mk in bk.get("markets", []):
                for o in mk.get("outcomes", []):
                    lines.append({"game": game, "time": data.get("commence_time"), "book": bk["key"],
                                  "market": mk["key"], "player": o.get("description", ""),
                                  "side": o.get("name"), "point": o.get("point"), "price": o.get("price")})
    print(f"  {len(lines)} prop prices")
    return lines


def devig_props(lines, sharp_books=("pinnacle", "circasports", "betonlineag")):
    """Group by player/market/line, de-vig each book, consensus fair prob = median (sharp books x2)."""
    groups = {}
    for l in lines:
        if l["point"] is None or l["side"] not in ("Over", "Under"):
            continue
        g = groups.setdefault((l["game"], l["market"], l["player"], l["point"]), {})
        g.setdefault(l["book"], {})[l["side"]] = l["price"]
    props = []
    for (game, market, player, point), books in groups.items():
        fairs, best = [], {"Over": None, "Under": None}
        for book, sides in books.items():
            for s in ("Over", "Under"):
                if s in sides and (best[s] is None or american_to_decimal(sides[s]) > american_to_decimal(best[s][1])):
                    best[s] = (book, sides[s])
            if "Over" in sides and "Under" in sides:
                po, pu = american_to_prob(sides["Over"]), american_to_prob(sides["Under"])
                fair = po / (po + pu)
                fairs += [fair] * (2 if book in sharp_books else 1)
        if len(fairs) < 2:
            continue
        p = statistics.median(fairs)
        props.append({"game": game, "market": market, "player": player, "line": point,
                      "fair_over": round(p, 4), "fair_over_odds": prob_to_american(p),
                      "n_books": len(books), "best_over": best["Over"], "best_under": best["Under"]})
    return props


def find_exchange_prop(prop, contracts):
    """Match a book prop (player + line) to an exchange contract like 'LeBron James: 25+ points'."""
    last = prop["player"].split()[-1].lower() if prop["player"] else ""
    stat = prop["market"].replace("player_", "").replace("batter_", "").replace("pitcher_", "").split("_")[0]
    hits = []
    for c in contracts:
        text = f"{c['event']} {c['question']} {c['outcome']}".lower()
        if not last or last not in text or prop["player"].split()[0].lower()[:3] not in text:
            continue
        if stat[:3] not in text:
            continue
        for num in re.findall(r"(\d+(?:\.\d+)?)\s*(\+)?", text):
            n, plus = float(num[0]), num[1] == "+"
            line = n - 0.5 if plus else n  # "25+" == over 24.5
            if abs(line - prop["line"]) < 0.01:
                is_over = plus or "over" in text or c["outcome"].lower() == "over"
                if c["outcome"].lower() == "under":
                    is_over = False
                hits.append((c, is_over))
                break
    return hits


def cmd_books(args, kal=None, poly=None, save=True):
    lines = fetch_book_props(args.sport, args.markets, args.regions, args.max_events)
    props = devig_props(lines)
    ev_rows = []
    for p in props:
        for side in ("Over", "Under"):
            b = p[f"best_{side.lower()}"]
            if not b:
                continue
            fair = p["fair_over"] if side == "Over" else 1 - p["fair_over"]
            ev = fair * american_to_decimal(b[1]) - 1
            ev_rows.append({"player": p["player"], "market": p["market"], "line": p["line"], "side": side,
                            "book": b[0], "odds": b[1], "fair_prob": round(fair, 3),
                            "fair_odds": prob_to_american(fair), "ev_%": round(100 * ev, 2),
                            "n_books": p["n_books"], "game": p["game"]})
    ev_rows.sort(key=lambda r: -r["ev_%"])
    print(f"\nSportsbook +EV props (EV > {100 * args.min_edge:.1f}%):")
    show([r for r in ev_rows if r["ev_%"] > 100 * args.min_edge],
         ["player", "market", "line", "side", "book", "odds", "fair_odds", "ev_%"])

    if kal is None:
        kal = fetch_kalshi(args.kalshi_series)
    if poly is None:
        poly = fetch_poly(args.poly_tag)
    ex_rows = []
    for p in props:
        for c, is_over in find_exchange_prop(p, kal + poly):
            fair_yes = p["fair_over"] if is_over else 1 - p["fair_over"]
            fee = kalshi_fee(c["yes_ask"]) if c["src"] == "kalshi" else args.poly_fee
            for side, price, fair in (("yes", c["yes_ask"], fair_yes), ("no", c["no_ask"], 1 - fair_yes)):
                edge = fair - price - fee
                ex_rows.append({"venue": c["src"], "contract": f"{c['question']} [{c['outcome']}]",
                                "buy": side, "ask": round(price, 3), "book_fair": round(fair, 3),
                                "edge_per_$1": round(edge, 4), "player": p["player"],
                                "line": p["line"], "id": c["id"]})
    ex_rows.sort(key=lambda r: -r["edge_per_$1"])
    print(f"\nKalshi/Polymarket props vs sportsbook fair price ({len(ex_rows) // 2} matched):")
    show([r for r in ex_rows if r["edge_per_$1"] > args.min_edge],
         ["venue", "contract", "buy", "ask", "book_fair", "edge_per_$1"])
    if save:
        print("\nSaved:", save_csv("book_ev.csv", ev_rows), save_csv("exchange_vs_books.csv", ex_rows))
    return ev_rows, ex_rows


# ----------------------------------------------------------------------------- 3. model
def nb_or_poisson_over(mean, var, line):
    """P(X > line) using negative binomial if overdispersed, else Poisson."""
    k_max = int(math.floor(line))
    if mean <= 0:
        return 0.0
    if var <= mean * 1.05:  # Poisson
        cdf = sum(math.exp(k * math.log(mean) - mean - math.lgamma(k + 1)) for k in range(k_max + 1))
    else:  # negative binomial, r = mean^2/(var-mean), p = r/(r+mean)
        r = mean * mean / (var - mean)
        p = r / (r + mean)
        cdf = sum(math.exp(math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1)
                           + r * math.log(p) + k * math.log(1 - p)) for k in range(k_max + 1))
    return max(0.0, min(1.0, 1 - cdf))


def project(games, stat, minutes=None, half_life=10):
    """games: newest first, dicts with 'minutes' and stat. Exponentially weighted per-minute rate."""
    games = [g for g in games if fnum(g.get("minutes")) and fnum(g.get(stat)) is not None]
    if len(games) < 5:
        raise ValueError(f"need at least 5 games with minutes and {stat}")
    w = [0.5 ** (i / half_life) for i in range(len(games))]
    mins = [fnum(g["minutes"]) for g in games]
    vals = [fnum(g[stat]) for g in games]
    rate = sum(wi * v for wi, v in zip(w, vals)) / sum(wi * m for wi, m in zip(w, mins))
    proj_min = minutes or sum(wi * m for wi, m in zip(w, mins[:10])) / sum(w[:10])
    scaled = [v / m * proj_min for v, m in zip(vals, mins)]
    mean = rate * proj_min
    var = sum(wi * (s - mean) ** 2 for wi, s in zip(w, scaled)) / sum(w)
    return mean, max(var, 1e-6), proj_min


def cmd_model(args):
    with open(args.gamelog) as f:
        rows = list(csv.DictReader(f))
    games = [r for r in rows if r.get("player", "").lower() == args.player.lower()]
    games.sort(key=lambda r: r.get("date", ""), reverse=True)
    if not games:
        sys.exit(f"No games for {args.player} in {args.gamelog}")
    mean, var, pm = project(games, args.stat, args.minutes)
    p_over = nb_or_poisson_over(mean, var, args.line)
    dist = "Poisson" if var <= mean * 1.05 else "Negative binomial"
    print(f"\n{args.player} - {args.stat} over {args.line}")
    print(f"  games used        {len(games)}")
    print(f"  projected minutes {pm:.1f}")
    print(f"  projected {args.stat:<8} {mean:.2f}  (sd {math.sqrt(var):.2f}, {dist})")
    print(f"  P(over)           {p_over:.3f}  fair odds {prob_to_american(p_over)}")
    print(f"  P(under)          {1 - p_over:.3f}  fair odds {prob_to_american(1 - p_over)}")
    if args.price is not None:
        price = american_to_prob(args.price) if abs(args.price) >= 100 else args.price
        print(f"  market price      {price:.3f} for over -> model edge {p_over - price:+.3f} "
              f"| under edge {(1 - p_over) - (1 - price):+.3f} (before fees/vig)")
    print("  Note: a simple model. Check it against de-vigged book prices before trusting it.")


def cmd_sample(args):
    import random
    random.seed(7)
    rows, d0 = [], datetime(2026, 10, 21)
    for i in range(30):
        m = max(18, random.gauss(34, 3))
        rows.append({"player": "Sample Guard",
                     "date": datetime.fromordinal(d0.toordinal() + i * 2).strftime("%Y-%m-%d"),
                     "minutes": round(m, 1), "pts": max(0, round(random.gauss(0.72 * m, 6))),
                     "reb": max(0, round(random.gauss(0.15 * m, 2))), "ast": max(0, round(random.gauss(0.2 * m, 2.5)))})
    with open("sample_gamelog.csv", "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print("Wrote sample_gamelog.csv (columns: player,date,minutes,pts,reb,ast)")


def cmd_all(args):
    _, kal, poly = cmd_gaps(args)
    if os.environ.get("ODDS_API_KEY"):
        cmd_books(args, kal, poly)
    else:
        print("\nSkipping `books`: set ODDS_API_KEY to add sportsbook comparisons.")
    print(f"\nDone {datetime.now(timezone.utc):%Y-%m-%d %H:%M} UTC. Outputs in ./{OUT_DIR}/")


# ----------------------------------------------------------------------------- CLI
def main():
    ap = argparse.ArgumentParser(prog="kalpoly", description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def common(p):
        p.add_argument("--kalshi-series", help="limit Kalshi to one series ticker, e.g. KXNBAGAME")
        p.add_argument("--poly-tag", default="sports", help="Polymarket tag slug (sports, nba, nfl...)")
        p.add_argument("--poly-fee", type=float, default=0.0, help="Polymarket fee per $1 (default 0)")
        p.add_argument("--min-edge", type=float, default=0.01, help="min edge per $1 to show (0.01 = 1%%)")
        p.add_argument("--contracts", type=int, default=10, help="Kalshi order size used for the fee (fee rounds up per order)")

    def books(p):
        p.add_argument("--sport", default="basketball_nba",
                       help="Odds API sport key: basketball_nba, americanfootball_nfl, baseball_mlb, icehockey_nhl")
        p.add_argument("--markets", default="player_points,player_rebounds,player_assists",
                       help="comma list, e.g. player_pass_yds,player_reception_yds,player_anytime_td")
        p.add_argument("--regions", default="us,eu", help="'eu' includes Pinnacle")
        p.add_argument("--max-events", type=int, default=5, help="games to pull (saves API credits)")

    p = sub.add_parser("gaps"); common(p); p.set_defaults(fn=cmd_gaps)
    p = sub.add_parser("books"); common(p); books(p); p.set_defaults(fn=cmd_books)
    p = sub.add_parser("all"); common(p); books(p); p.set_defaults(fn=cmd_all)
    p = sub.add_parser("model")
    p.add_argument("--gamelog", required=True, help="CSV: player,date,minutes,<stat columns>")
    p.add_argument("--player", required=True)
    p.add_argument("--stat", required=True, help="column name, e.g. pts")
    p.add_argument("--line", type=float, required=True)
    p.add_argument("--minutes", type=float, help="override projected minutes")
    p.add_argument("--price", type=float, help="market price for the over: 0.52 or American odds like -115")
    p.set_defaults(fn=cmd_model)
    p = sub.add_parser("sample"); p.set_defaults(fn=cmd_sample)

    args = ap.parse_args()
    global KALSHI_CONTRACTS
    KALSHI_CONTRACTS = max(1, getattr(args, "contracts", None) or KALSHI_CONTRACTS)
    args.fn(args)


if __name__ == "__main__":
    main()
