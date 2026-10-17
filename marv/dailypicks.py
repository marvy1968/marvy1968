"""Daily Marv Predict card (Hybrid Engine v2.0 style): ranked 2-leg parlays for EVERY analyzed sport + the best
pregame straight moneylines and over/unders. Paper-tracked and graded; nothing here is a proven edge.

Run: `python -m marv.dailypicks [--dry-run] [--test] [--no-refresh] [--sports nfl,cfb,...]` (marv-dailypicks.timer,
08:00 America/Chicago). Telegram `/parlay [sport]` answers from the stored card (state/marv_predict/daily_card.json).

Sports: NFL, CFB, WNBA, NBA, EuroLeague, NCAAB, NCAAW, MLB, NHL (each runs only when it has games today) +
soccer from the Marv Soccer node (state/marv_predict/soccer/paper.json, refreshed every 3 h by marv-soccer.timer).

Legs (nothing new is modelled; each reuses a Marv read):
  Tier A  🔥 HIGH    Marv pick that passed the full veto stack (backtested confidence floor, injuries, eliminations)
  Tier B  📈 SIGNAL  a backtested paper signal (college O/U trend fades, NFL inflated total, MARV4); the only legs
                     that may print a %, and only the BACKTEST hit rate via marv/proven.py
  Tier C  🧮 LEAN    Marv model edge (2%+) that failed only a soft rule; soccer node picks (paper, unproven)
UF filter (upset/unsafe filter) removes a leg when: the game has a hard veto (backup QB, key injury, stale stats,
starters out, wind...); the upset score fires against the ML favourite we'd back (marv/upset.py); the ML is
shorter than UF_MAX_FAV (default -500: adds almost nothing to a parlay and carries the upset risk); soccer picks
with a hard veto or node confidence < 50.
Parlays: every 2-leg combo of distinct games inside one sport, ranked by summed leg strength (a ranking, not a
probability). No model %, fair price or edge prints on Telegram (proven-logic rule). Everything is logged to
state/marv_predict/daily_picks_log.json and graded from final scores on the next run.
"""

import argparse
import html
import itertools
import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

log = logging.getLogger(__name__)
CT = ZoneInfo("America/Chicago")
SPORTS = ("nfl", "cfb", "wnba", "nba", "euroleague", "ncaab", "ncaaw", "mlb", "nhl")
EMOJI = {"nfl": "🏈", "cfb": "🏈", "wnba": "🏀", "nba": "🏀", "euroleague": "🏀", "ncaab": "🏀", "ncaaw": "🏀",
         "mlb": "⚾", "nhl": "🏒", "soccer": "⚽"}
NAME = {"nfl": "NFL", "cfb": "CFB", "wnba": "WNBA", "nba": "NBA", "euroleague": "EuroLeague", "ncaab": "NCAAB",
        "ncaaw": "NCAAW", "mlb": "MLB", "nhl": "NHL", "soccer": "Soccer"}
TIER_BASE = {"HIGH": 3.0, "SIGNAL": 2.0, "LEAN": 1.0}
TIER_ICON = {"HIGH": "🔥", "SIGNAL": "📈", "LEAN": "🧮"}
TIER_NAME = {"HIGH": "A·HIGH", "SIGNAL": "B·SIGNAL", "LEAN": "C·LEAN"}
UF_MAX_FAV = float(os.environ.get("UF_MAX_FAV", "-500"))
MIN_LEAN_EDGE = float(os.environ.get("DAILY_MIN_EDGE", "0.02"))
MAX_LEG_REUSE = int(os.environ.get("DAILY_MAX_LEG_REUSE", "2"))  # 2026-10-17: a leg appears in <= 2 ranked parlays
PARLAYS_PER_SPORT = int(os.environ.get("DAILY_PARLAYS_PER_SPORT", "5"))
STRAIGHT_MAX = int(os.environ.get("DAILY_STRAIGHT_MAX", "10"))
WINDOW_H = int(os.environ.get("DAILY_WINDOW_HOURS", "22"))
CARD = "daily_card.json"
LOG = "daily_picks_log.json"


# ---------------------------------------------------------------- odds math
def dec_to_american(d: float) -> float:
    d = float(d)
    return round((d - 1) * 100) if d >= 2 else round(-100 / (d - 1))


def american_to_dec(p: float) -> float:
    p = float(p)
    return 1 + (p / 100 if p > 0 else 100 / -p)


def combined(prices) -> int:
    d = 1.0
    for p in prices:
        d *= american_to_dec(p)
    return int(dec_to_american(d))


def _dir(state: Path) -> Path:
    d = Path(state) / "marv_predict"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _jload(path: Path, default):
    try:
        return json.loads(Path(path).read_text())
    except (OSError, ValueError):
        return default


def _jsave(path: Path, data) -> None:
    tmp = Path(path).with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=1, default=str))
    tmp.replace(path)


# ---------------------------------------------------------------- legs from Marv's engine (US sports)
def _leg(sport, g, market, side, line, price, tier, strength, why, **kw) -> dict:
    return {"sport": sport, "game": f"{g.away} @ {g.home}", "home": g.home, "away": g.away,
            "start": g.start.isoformat(), "market": market, "side": side, "line": line, "price": float(price),
            "tier": tier, "strength": round(strength, 3), "why": why, **kw}


def engine_legs(sport: str, preds, projections, slate, uf_favs: set, uf: list) -> list[dict]:
    from . import betcard, proven
    legs = []
    by_id = {}
    for pred in preds:
        g = pred.game
        by_id[g.id] = g
        blocked = betcard._game_blocked(pred)
        for pk in pred.picks:
            if pk.market not in ("ml", "total") or pk.price is None:
                continue
            hard = betcard._hard(pk.vetoes)
            if blocked or hard:
                if pk.active or pk.edge >= MIN_LEAN_EDGE:
                    uf.append(f"{sport.upper()} {g.away} @ {g.home} {pk.side}: hard veto")
                continue
            if pk.active:
                tier = "HIGH"
            elif pk.edge >= MIN_LEAN_EDGE:
                tier = "LEAN"
            else:
                continue
            if pk.market == "ml":
                if pk.price < UF_MAX_FAV:
                    uf.append(f"{sport.upper()} {pk.side} ML {pk.price:+.0f}: shorter than {UF_MAX_FAV:+.0f}")
                    continue
                if (sport, pk.side.lower()) in uf_favs:
                    uf.append(f"{sport.upper()} {pk.side} ML: upset score fires against it")
                    continue
                side, line = pk.side, None
            else:
                side, line = pk.side, pk.line
            # Master architecture (MARV_MASTER=1): drop UF=0, mark half stake, tag Master-active sides
            mp = next((m for m in (getattr(pred, "master_picks", None) or []) if m.get("market") == pk.market
                       and m.get("side") == pk.side), None)
            stake = (getattr(pred, "master_uf_stake_total", None) if pk.market == "total" else
                     (mp.get("uf_stake") if mp and mp.get("active_master") else getattr(pred, "master_uf_stake_side", None)))
            if stake is not None and float(stake) <= 0:
                uf.append(f"{sport.upper()} {g.away} @ {g.home} {pk.side}: Master UF=0 ({getattr(pred, 'master_uf_reason', '')})")
                continue
            mtag = {"master": bool(mp and mp.get("active_master")),
                    "uf_stake": None if stake is None else float(stake)}
            strength = TIER_BASE[tier] + min(max(pk.edge, 0), 0.15) * 10 + (max(pk.prob - 0.5, 0) * 2 if pk.market == "ml" else 0)
            why = ("passed every check (backtested floor, injuries, eliminations)" if tier == "HIGH"
                   else "model edge, held back by: " + "; ".join(pk.vetoes)[:70])
            legs.append(_leg(sport, g, pk.market, side, line, pk.price, tier, strength + (0.5 if mtag["master"] else 0),
                             why + f" · {proven.UNPROVEN}", **mtag))
    # backtested O/U signals (trend fades, inflated totals) on projected and unprojected slate games
    seen = set()
    cands = betcard.candidates(sport, preds, projections) + betcard.tag_candidates(
        sport, slate, getattr(projections, "tags", {}) or {})
    for b in cands:
        if b.tier != "SIGNAL" or b.market != "total" or b.line is None:
            continue
        key = (b.game, b.side)
        if key in seen:
            continue
        seen.add(key)
        hit = betcard.SIGNAL_RANK.get(b.why.split(":")[0], 52.5) / 100
        g = type("G", (), {"home": b.home, "away": b.away, "start": datetime.fromisoformat(b.start.replace("Z", "+00:00"))})
        legs.append(_leg(sport, g, "total", b.side, b.line, b.price, "SIGNAL", TIER_BASE["SIGNAL"] + (hit - 0.524) * 20,
                         b.why))
    return legs


def _upset_favs(state: Path, sports, now) -> set:
    """(sport, team) favourites the upset score fires against today."""
    try:
        from . import upset
        return {(u["sport"], str(u["fav"]).lower()) for u in upset.candidates(state, list(sports), now) if u.get("fires")}
    except Exception:  # noqa: BLE001
        log.exception("upset filter failed (no UF upset check this run)")
        return set()


def run_engine(s, sport_key: str, now: datetime, grade_only: bool = False):
    """Fresh Marv projections for one sport (no Telegram, no ledgers except predictions.json); grades our log."""
    from . import bridge
    from .engine import predict
    from .config import Settings
    from .sources import load_for_run
    from .sports import SPORTS as REG
    from .stats import registry as stats_registry
    sport = REG[sport_key]
    history, slate, ctx = load_for_run(sport, s, now, WINDOW_H)
    grade(Path(s.state_dir), sport_key, history)
    try:  # UFP/UVF tracking ledger (MARV_UPSET=1; no betting effect)
        from . import ufp_uvf
        if ufp_uvf.ON:
            ufp_uvf.grade(Path(s.state_dir), sport_key, history)
    except Exception:  # noqa: BLE001
        log.exception("ufp_uvf grade")
    if grade_only or not slate:
        return [], None, slate or []
    projections = None
    if s.stats_model and sport.key in stats_registry.MODULES:
        projections = stats_registry.project(sport.key, slate, s, now, history=history)
    preds = predict(sport, history, slate, now, s.simulations, ctx, veto=Settings.veto_for(sport.key),
                    model_weight=Settings.model_weight(sport.key), projections=projections) or []
    if preds:
        bridge.export(Path(s.state_dir), sport.key, preds)
    return preds, projections, slate


# ---------------------------------------------------------------- soccer node
def soccer_legs(state: Path, days: set, uf: list) -> list[dict]:
    od = Path(state) / "marv_predict" / "soccer"
    paper = _jload(od / "paper.json", {})
    card = {(m["home"], m["away"]): m for m in _jload(od / "card.json", {}).get("matches", [])}
    legs = []
    for key, e in paper.items():
        if e.get("date") not in days or "result" in e:
            continue
        m = card.get((e["home"], e["away"]), {})
        conf = e.get("confidence", m.get("confidence", 0)) or 0
        hard = [v for v in e.get("vetoes", []) if not str(v).startswith("soft")]
        best = None
        for mk in e.get("market", []):
            if mk.get("selection") == "draw" or mk.get("edge") is None or mk["edge"] < MIN_LEAN_EDGE:
                continue
            if best is None or mk["edge"] > best["edge"]:
                best = mk
        if not best:
            continue
        game = f"{e['away']} @ {e['home']}"
        if hard or conf < 50:
            uf.append(f"SOCCER {game}: {'hard veto' if hard else f'node confidence {conf}'}")
            continue
        price = dec_to_american(best["price"])
        if best["market"] == "1x2":
            side, market, line = (e["home"] if best["selection"] == "home" else e["away"]), "ml", None
            if price < UF_MAX_FAV:
                uf.append(f"SOCCER {side} ML {price:+.0f}: shorter than {UF_MAX_FAV:+.0f}")
                continue
        else:
            side, market, line = best["selection"].split()[0].title(), "total", float(best["selection"].split()[1])
        ko = str(m.get("kickoff") or e["date"])
        try:
            start = datetime.strptime(ko, "%Y-%m-%d %H:%M").replace(tzinfo=ZoneInfo("Europe/London"))
        except ValueError:
            start = datetime.fromisoformat(e["date"]).replace(hour=12, tzinfo=timezone.utc)
        legs.append({"sport": "soccer", "game": game, "home": e["home"], "away": e["away"], "league": e["league"],
                     "skey": key, "start": start.isoformat(), "market": market, "side": side, "line": line,
                     "price": float(price), "tier": "LEAN",
                     "strength": round(TIER_BASE["LEAN"] + min(best["edge"], 0.15) * 10 + (conf - 50) / 100, 3),
                     "why": f"{m.get('competition', e['league'])} · soccer node (Poisson+H2H+trend), conf {conf} · unproven, paper"})
    return legs


# ---------------------------------------------------------------- card
def _gkey(leg: dict) -> tuple:
    """Normalized game key: book feeds disagree on names ("Delaware" vs "Delaware Blue Hens")."""
    try:
        st = datetime.fromisoformat(str(leg.get("start", "")).replace("Z", "+00:00")).astimezone(timezone.utc).strftime("%Y-%m-%dT%H")
    except Exception:  # noqa: BLE001
        st = str(leg.get("start", ""))[:13]
    w = lambda t: (str(t or "").lower().split() or [""])[0]
    return (leg["sport"], st, w(leg.get("away")), w(leg.get("home")))


def _backtested(leg: dict) -> bool:
    why = str(leg.get("why", "")).lower()
    return "backtest" in why and "unproven" not in why


def dedupe(legs: list[dict]) -> list[dict]:
    """Best leg per (sport, game, market); ML and total of the same game may both stand in the straight cards.
    Conflict resolution (2026-10-13): never list opposite sides of one market/game; backtested basis beats
    unproven, then strength. Dropped side is logged 'conflict resolved'."""
    best = {}
    for leg in legs:
        k = _gkey(leg) + (leg["market"],)
        cur = best.get(k)
        if cur is None:
            best[k] = leg
            continue
        rank = lambda x: (_backtested(x), x["strength"])
        win, lose = (leg, cur) if rank(leg) > rank(cur) else (cur, leg)
        if win["side"] != lose["side"]:
            log.warning("daily picks: conflict resolved %s %s %s: kept %s %s (%s), dropped %s %s (%s)",
                        win["sport"], win["game"], win["market"], win["side"], win.get("line"),
                        "backtested" if _backtested(win) else "unproven", lose["side"], lose.get("line"),
                        "backtested" if _backtested(lose) else "unproven")
        best[k] = win
    return sorted(best.values(), key=lambda x: -x["strength"])


def parlays(legs: list[dict], n: int = PARLAYS_PER_SPORT) -> list[dict]:
    """Ranked 2-leg parlays inside one sport, distinct games only (no correlated same-game legs)."""
    out = []
    for a, b in itertools.combinations(legs, 2):
        if a["game"] == b["game"] or _gkey(a) == _gkey(b):
            continue
        out.append({"legs": sorted([a, b], key=lambda x: x["start"]), "price": combined([a["price"], b["price"]]),
                    "score": round(a["strength"] + b["strength"], 3),
                    "tiers": "+".join(sorted({a["tier"], b["tier"]}, key=lambda t: -TIER_BASE[t]))})
    out.sort(key=lambda p: (-p["score"], -p["price"]))
    return _cap_reuse(out, n)


def _lid(leg: dict) -> tuple:
    return _gkey(leg) + (leg.get("market"), leg.get("side"))


def _cap_reuse(ranked: list[dict], n: int, cap: int = MAX_LEG_REUSE) -> list[dict]:
    """2026-10-17 (Marvin): the same leg may appear in at most `cap` of the ranked parlays."""
    out, used = [], {}
    for p in ranked:
        ids = [_lid(x) for x in p["legs"]]
        if any(used.get(i, 0) >= cap for i in ids):
            continue
        for i in ids:
            used[i] = used.get(i, 0) + 1
        out.append(p)
        if len(out) >= n:
            break
    return out


def _marv_total(leg: dict):
    """Marv's own projected total for this leg's game (state/predictions.json), or None."""
    from .data.teams import similarity
    P = _jload(Path(os.environ.get("STATE_DIR", "/opt/marv-bot/state")) / "predictions.json", {})
    best, sc = None, 0.0
    for r in P.values():
        if r.get("sport") != leg.get("sport"):
            continue
        v = min(similarity(str(r.get("home")), str(leg.get("home"))), similarity(str(r.get("away")), str(leg.get("away"))))
        if v > sc:
            best, sc = r, v
    return float(best["model_total"]) if best is not None and sc >= 0.75 and best.get("model_total") is not None else None


def refine(legs: list[dict], uf: list) -> list[dict]:
    """2026-10-17 (Marvin): (3) generic CFB OVER-FADE under counts as a B signal only when Marv's projected total is
    also below the line; (2) a leg opposite a Master/Script extreme on the same game total is dropped (the extreme is
    the stronger side), so /parlay and the /card plan never disagree."""
    out = []
    try:
        mx = {(_gkey(x), "total"): x for x in master_extremes()}
    except Exception:  # noqa: BLE001
        mx = {}
    for x in legs:
        why = str(x.get("why", ""))
        if (x.get("sport") == "cfb" and x.get("market") == "total" and x.get("side") == "Under" and x.get("tier") == "SIGNAL"
                and why.split(":")[0].strip() == "OVER-FADE" and not x.get("master_extreme")):
            mt = _marv_total(x)
            if mt is None or x.get("line") is None or mt >= float(x["line"]):
                uf.append(f"CFB {x.get('game')} Under {x.get('line')}: OVER-FADE without Marv under "
                          f"(Marv {'n/a' if mt is None else f'{mt:.1f}'})")
                continue
        m = mx.get((_gkey(x), x.get("market")))
        if m is not None and not x.get("master_extreme") and m["side"] != x.get("side") and m["strength"] >= x["strength"]:
            uf.append(f"{x['sport'].upper()} {x.get('game')} {x.get('side')} {x.get('line')}: conflicts with "
                      f"Master extreme {m['side']} {m.get('line')} (kept the stronger side)")
            continue
        out.append(x)
    return out


def refine_card(card: dict, now=None) -> dict:
    """Re-apply refine + leg cap to a stored card (no projections, no credits)."""
    legs, seen = [], set()
    for x in card["ml"] + card["ou"] + [l for ps in card["parlays"].values() for p in ps for l in p["legs"]]:
        k = _lid(x)
        if k not in seen:
            seen.add(k); legs.append(x)
    uf = list(card.get("uf_removed", []))
    new = build(legs, uf, card["sports_checked"], now or datetime.fromisoformat(card["built"]))
    for k in ("date", "built", "upset_tags"):
        if k in card:
            new[k] = card[k]
    new["refined"] = datetime.now(timezone.utc).isoformat()
    return new


def build(legs: list[dict], uf: list, sports_checked: list, now: datetime) -> dict:
    legs = dedupe(refine(legs, uf))
    by_sport = {}
    for leg in legs:
        by_sport.setdefault(leg["sport"], []).append(leg)
    order = list(SPORTS) + ["soccer"]
    return {"date": now.astimezone(CT).date().isoformat(), "built": now.isoformat(), "sports_checked": sports_checked,
            "sports_with_legs": [s for s in order if s in by_sport],
            "parlays": {s: p for s in order if s in by_sport and (p := parlays(by_sport[s]))},
            "ml": [x for x in legs if x["market"] == "ml"][:STRAIGHT_MAX],
            "ou": [x for x in legs if x["market"] == "total"][:STRAIGHT_MAX],
            "uf_removed": uf, "n_legs": len(legs)}


def _when(start: str) -> str:
    return datetime.fromisoformat(start).astimezone(CT).strftime("%-I:%M %p CT")


def _pick(leg: dict) -> str:
    t = leg["side"].split()[-1] if leg["sport"] == "nfl" else leg["side"]
    if leg["market"] == "ml":
        return f"{t} ML {leg['price']:+.0f}"
    return f"{leg['side']} {leg['line']:g} ({leg['price']:+.0f})"


def _mpick(leg: dict) -> str:
    """Pick with its matchup in front, e.g. 'Arizona State @ Texas Tech Over 48.5 (-110)' (2026-10-16)."""
    return f"{leg['game']} {_pick(leg)}"


def _why(leg: dict) -> str:
    from . import proven
    if leg["tier"] == "SIGNAL":  # % only when the rule is proven (backtest number), else no %
        sig = leg["why"].split(":")[0]
        ev = proven.evidence(leg["sport"], "total", sig)
        return f"{sig}: " + (ev.label() if ev else "backtested paper signal, not yet 100+ live graded")
    return leg["why"]


def text(card: dict, test: bool = False, paper: bool = True, sport: str | None = None, full: bool = True) -> str:
    d = datetime.fromisoformat(card["date"])
    head = ("🧪 TEST · " if test else "") + f"👽 MARV PREDICT · DAILY CARD · Hybrid v2.0 style · {d:%a %b %-d}"
    lines = [head + (" · 📝 PAPER" if paper else ""),
             f"Sports checked: {', '.join(NAME[s] for s in card['sports_checked'] if s not in TG_EXCLUDE)}" + (' (NBA excluded)' if 'nba' in TG_EXCLUDE else ''),
             f"UF filter removed {len(card['uf_removed'])} leg(s) (hard veto / upset score fires / shorter than "
             f"{UF_MAX_FAV:+.0f} / soccer conf<50)"]
    sports = [sport] if sport else card["sports_with_legs"]
    lines += ["", "━━ 🎰 BEST 2-LEG PARLAYS (ranked best → least) ━━"]
    any_p = False
    for s in sports:
        ps = card["parlays"].get(s) or []
        if not ps:
            continue
        any_p = True
        lines.append(f"{EMOJI[s]} {NAME[s]}")
        for i, p in enumerate(ps, 1):
            a, b = p["legs"]
            lines.append(f" {i}) {p['price']:+d} · {TIER_ICON[a['tier']]} {_mpick(a)} [{NAME[a['sport']]}] + {TIER_ICON[b['tier']]} {_mpick(b)} [{NAME[b['sport']]}]")
            lines.append(f"    {a['game']} {_when(a['start'])} | {b['game']} {_when(b['start'])}")
    thin = [f"{NAME[s]} {len({l['game'] for l in card['ml'] + card['ou'] if l['sport'] == s})}"
            for s in sports if not card["parlays"].get(s)]
    if not any_p:
        lines.append(" none today (need 2+ qualifying games in one sport)")
    if thin:
        lines.append(" only 1 qualifying game (no 2-leg combo): " + ", ".join(thin))
    if full:
        for title, key in (("💰 BEST PREGAME MONEYLINES", "ml"), ("📐 BEST PREGAME OVER/UNDERS", "ou")):
            lines += ["", f"━━ {title} ━━"]
            rows = [x for x in card[key] if not sport or x["sport"] == sport]
            if not rows:
                lines.append(" none qualified today")
            for i, x in enumerate(rows, 1):
                lines.append(f" {i}) {TIER_ICON[x['tier']]} {TIER_NAME[x['tier']]} · {EMOJI[x['sport']]} {_mpick(x)} [{NAME[x['sport']]}] · "
                             f"{_when(x['start'])}")
                lines.append(f"    {_why(x)}")
    try:
        from . import ufp_uvf
        lines += ufp_uvf.card_lines([t for t in card.get("upset_tags", []) if not sport or t["sport"] == sport])
    except Exception:  # noqa: BLE001
        pass
    try:  # 2026-10-17 (Marvin): one line, best Bovada CFB pregame prop 👍 (tracking; no % until proven)
        from . import cfb_props_shadow as _cps
        _pl = _cps.card_line() if sport in (None, "cfb") else ""
        if _pl:
            lines.extend(["", _pl])
    except Exception:  # noqa: BLE001
        pass
    lines += ["", "Tiers: 🔥A passed every veto · 📈B backtested paper signal · 🧮C model lean. Ranked by leg "
              "strength, not a probability; no model % (only proven backtests print a %). 0.5u paper leans, "
              "graded daily — not real-money picks until 100+ graded above 52.4%."]
    return html.escape("\n".join(lines))


# ---------------------------------------------------------------- paper log + grading
def log_card(state: Path, card: dict) -> int:
    path = _dir(state) / LOG
    book = _jload(path, [])
    have = {(r["date"], r["kind"], r.get("sport"), r["id"]) for r in book}
    n = 0

    def add(kind, sport, legs, price):
        nonlocal n
        rid = "|".join(f"{l['game']}:{l['market']}:{l['side']}:{l['line']}" for l in legs)
        if (card["date"], kind, sport, rid) in have:
            return
        book.append({"date": card["date"], "kind": kind, "sport": sport, "id": rid, "price": price, "units": 0.5,
                     "legs": [{k: l.get(k) for k in ("sport", "game", "home", "away", "start", "market", "side", "line",
                                                    "price", "tier", "league", "skey")} | {"result": None} for l in legs],
                     "result": None, "profit": None})
        have.add((card["date"], kind, sport, rid))
        n += 1
    for s, ps in card["parlays"].items():
        for p in ps:
            add("parlay2", s, p["legs"], p["price"])
    for x in card["ml"] + card["ou"]:
        add("straight", x["sport"], [x], x["price"])
    _jsave(path, book)
    return n


def _settle(book: list) -> None:
    from . import edges as E
    for r in book:
        if r["result"] is not None:
            continue
        res = [l["result"] for l in r["legs"]]
        if any(x is None for x in res):
            if "loss" in res:  # a parlay dies with its first losing leg
                r["result"], r["profit"] = "loss", -r["units"]
            continue
        if "loss" in res:
            r["result"], r["profit"] = "loss", -r["units"]
        elif all(x == "push" for x in res):
            r["result"], r["profit"] = "push", 0.0
        else:
            price = combined([l["price"] for l in r["legs"] if l["result"] == "win"])
            r["result"], r["profit"] = "win", round(r["units"] * E.payout(price), 3)


def _grade_leg(l: dict, hs: float, as_: float) -> str:
    if l["market"] == "total":
        v = (hs + as_ - l["line"]) * (1 if l["side"] == "Over" else -1)
    else:
        from .data.teams import similarity
        v = (hs - as_) if similarity(l["side"], l["home"]) >= similarity(l["side"], l["away"]) else (as_ - hs)
        if l["sport"] == "soccer" and v == 0:
            return "loss"  # 1X2 moneyline: a draw loses
    return "push" if v == 0 else ("win" if v > 0 else "loss")


def grade(state: Path, sport: str, finished) -> None:
    from .data.teams import similarity
    path = _dir(state) / LOG
    book = _jload(path, [])
    if not book:
        return
    done = [g for g in finished if g.completed and g.home_score is not None and g.away_score is not None]
    for r in book:
        for l in r["legs"]:
            if l["sport"] != sport or l["result"] is not None:
                continue
            st = datetime.fromisoformat(l["start"])
            g = next((x for x in done if abs(x.start - st) < timedelta(hours=12)
                      and min(similarity(x.home, l["home"]), similarity(x.away, l["away"])) >= 0.75), None)
            if g:
                l["result"] = _grade_leg(l, g.home_score, g.away_score)
    _settle(book)
    _jsave(path, book)


def grade_soccer(state: Path) -> None:
    path = _dir(state) / LOG
    book = _jload(path, [])
    paper = _jload(Path(state) / "marv_predict" / "soccer" / "paper.json", {})
    for r in book:
        for l in r["legs"]:
            if l["sport"] == "soccer" and l["result"] is None and "result" in paper.get(l.get("skey") or "", {}):
                hg, ag = paper[l["skey"]]["result"]
                l["result"] = _grade_leg(l, hg, ag)
    _settle(book)
    _jsave(path, book)


def record(state: Path) -> str:
    book = _jload(_dir(state) / LOG, [])
    out = []
    for kind in ("parlay2", "straight"):
        rows = [r for r in book if r["kind"] == kind and r["result"] in ("win", "loss", "push")]
        w = sum(r["result"] == "win" for r in rows)
        lo = sum(r["result"] == "loss" for r in rows)
        u = sum(r["profit"] or 0 for r in rows)
        out.append(f"{'2-leg parlays' if kind == 'parlay2' else 'straight ML/O-U'} {w}-{lo} ({u:+.1f}u, n={len(rows)})")
    line = "Paper record: " + " · ".join(out)
    try:  # 2026-10-17 (Marvin): one compact line per sport (ML / O/U / Props W-L)
        bd = sport_breakdown(state)
        if bd:
            line += "\n" + bd
    except Exception:  # noqa: BLE001
        log.exception("daily picks: sport breakdown")
    return line


def _wl(rows, price_key="price"):
    from . import edges as E
    d = [r for r in rows if r.get("result") in ("win", "loss")]
    w = sum(r["result"] == "win" for r in d)
    u = sum((r.get("profit") if r.get("profit") is not None else
             (0.5 * E.payout(r.get(price_key) or -110) if r["result"] == "win" else -0.5)) for r in d)
    return w, len(d) - w, u


def _fmt(name, w, l, u):
    n = w + l
    if n >= 100 and u > 0:  # proven: 100+ graded and profitable -> % allowed
        return f"{name} {w}-{l} ({w / n:.0%})"
    return f"{name} {w}-{l}"


def sport_breakdown(state: Path) -> str:
    """Per sport: straight ML, O/U (daily card paper log) and Props (Marv prop shadows). Empty categories omitted;
    sports in TG_EXCLUDE (NBA until the Oct 27 review) hidden. No % unless 100+ graded and profitable."""
    book = [r for r in _jload(_dir(state) / LOG, []) if r.get("kind") == "straight"]
    cats: dict = {}
    for r in book:
        leg = (r.get("legs") or [{}])[0]
        sp = r.get("sport") or leg.get("sport")
        cats.setdefault(sp, {}).setdefault("O/U" if leg.get("market") == "total" else "ML", []).append(r)
    props: dict = {}
    for b in _jload(Path(state) / "props_cfb_shadow.json", []):  # CFB Bovada props 👍 (marv/cfb_props_shadow)
        if b.get("grade") == "👍":
            props.setdefault("cfb", []).append({"result": b.get("result"), "price": b.get("price")})
    pg = Path(state) / "propgrade" / "shadow.jsonl"  # NFL prop grade v2 👍 (if graded)
    if pg.exists():
        for ln in pg.read_text().splitlines():
            try:
                b = json.loads(ln)
            except ValueError:
                continue
            if str(b.get("grade", "")).startswith("👍") and b.get("result") in ("win", "loss"):
                props.setdefault("nfl", []).append({"result": b["result"], "price": b.get("price")})
    for sp, rows in props.items():
        cats.setdefault(sp, {})["Props"] = rows
    out = []
    for sp in [x for x in SPORTS if x in cats] + sorted(x for x in cats if x not in SPORTS and x):
        if sp in TG_EXCLUDE:
            continue
        parts = []
        for name in ("ML", "O/U", "Props"):
            w, l, u = _wl(cats[sp].get(name, []))
            if w + l:
                parts.append(_fmt(name, w, l, u))
        if parts:
            out.append(f"{EMOJI.get(sp, '•')} {NAME.get(sp, sp.upper())}: " + " · ".join(parts))
    return "\n".join(out)


# ---------------------------------------------------------------- entry points
def collect(s, now: datetime, sports=SPORTS, refresh: bool = True) -> dict:
    state = Path(s.state_dir)
    legs, uf, checked = [], [], []
    results = {}
    for key in sports:
        try:
            results[key] = run_engine(s, key, now, grade_only=not refresh)
            checked.append(key)
        except Exception:  # noqa: BLE001
            log.exception("daily picks: %s failed", key)
    favs = _upset_favs(state, sports, now)
    end = now + timedelta(hours=WINDOW_H)
    for key, (preds, proj, slate) in results.items():
        preds = [p for p in preds if now < p.game.start <= end]
        slate = [g for g in slate if now < g.start <= end]
        try:
            legs += engine_legs(key, preds, proj, slate, favs, uf)
        except Exception:  # noqa: BLE001
            log.exception("daily picks legs: %s", key)
    try:
        grade_soccer(state)
        days = {(now + timedelta(days=i)).astimezone(CT).date().isoformat() for i in (0, 1)}
        sl = soccer_legs(state, days, uf)
        legs += [x for x in sl if now < datetime.fromisoformat(x["start"]) <= end]
        checked.append("soccer")
    except Exception:  # noqa: BLE001
        log.exception("daily picks: soccer failed")
    card = build(legs, uf, checked, now)
    try:  # UFP Trap Warning / UVF Value Dog tags (tracking only, MARV_UPSET=1)
        from . import ufp_uvf
        if ufp_uvf.ON:
            card["upset_tags"] = [t for k, (pr, _p, _s) in results.items()
                                  for t in ufp_uvf.tag_games(k, [p for p in pr if now < p.game.start <= end], state, now)]
    except Exception:  # noqa: BLE001
        log.exception("daily picks: ufp_uvf tags")
    _jsave(_dir(state) / CARD, card)
    return card


def answer(settings, parts=None, full: bool = True) -> str:
    """Telegram /reportcard [sport] (full card) and /parlay [sport] (full=False: parlays only): today's stored card (no Odds API credits). Falls back to the old /parlay."""
    state = Path(settings.state_dir)
    card = _jload(_dir(state) / CARD, None)
    today = datetime.now(CT).date().isoformat()
    sport = (parts[1].lower() if parts and len(parts) > 1 else None)
    if sport in ("ncaam", "cbb"):
        sport = "ncaab"
    if not card or card.get("date") != today:
        from . import parlay
        return ("(today's daily card isn't built yet — it runs 07:40 CT; showing the live /parlay board)\n"
                + parlay.answer(settings))
    if sport and sport not in card["parlays"] and not any(x["sport"] == sport for x in card["ml"] + card["ou"]):
        return f"No qualifying {NAME.get(sport, sport)} parlays on today's card. Sports with legs: " + \
            ", ".join(NAME[s] for s in card["sports_with_legs"])
    extra = ""
    try:
        from . import ufp_uvf
        if ufp_uvf.ON:
            extra = "\n" + html.escape(ufp_uvf.record_line(state))
    except Exception:  # noqa: BLE001
        pass
    try:  # Oct 16 2026 (Marvin): CFB +200..+299 dog shadow (tracking-only)
        from . import cfb_dog_shadow
        _dog = cfb_dog_shadow.record_line(state)
        if _dog:
            extra += "\n" + html.escape(_dog)
    except Exception:  # noqa: BLE001
        pass
    return text(card, paper=getattr(settings, "paper_mode", True), sport=sport, full=full) + "\n" + \
        html.escape(record(state)) + extra


# ---------------------------------------------------------------- Telegram action plan (2026-10-13)
TG_EXCLUDE = {x for x in os.environ.get("DAILY_TG_EXCLUDE", "nba").lower().split(",") if x}  # Marvin: no NBA on Telegram
TG_TOP = int(os.environ.get("DAILY_TG_TOP", "5"))


def _tag(leg):
    return f"[{NAME[leg['sport']]}]"


def _just(leg):
    if leg.get("master_extreme"):
        st = leg.get("uf_stake")
        return leg["why"] + " · " + ("UF clean" if st is None or st >= 1 else "UF ½ STAKE")
    bits = [TIER_NAME[leg["tier"]]]
    if leg.get("master"):
        bits.append("Master ✓ (TCI/PPE edge)")
    st = leg.get("uf_stake")
    if st is not None:
        bits.append("UF clean" if st >= 1 else "UF ½ STAKE")
    if leg["tier"] == "SIGNAL":
        bits.append(_why(leg))
    elif leg["tier"] == "HIGH":
        bits.append("passed every veto")
    else:
        bits.append("model lean, unproven")
    return " · ".join(bits)


def _combos(legs, k, n):
    out = []
    pool = legs[:12]
    for c in itertools.combinations(pool, k):  # ranked; reuse capped by _cap_reuse
        if len({_gkey(x) for x in c}) < k:
            continue  # distinct games only
        tiers = sorted({x["tier"] for x in c if not x.get("master_extreme")}, key=lambda t: -TIER_BASE[t])
        tn = (["🎯MASTER EXTREME"] if any(x.get("master_extreme") for x in c) else []) + [TIER_NAME[t] for t in tiers]
        out.append({"legs": sorted(c, key=lambda x: x["start"]), "price": combined([x["price"] for x in c]),
                    "score": sum(x["strength"] for x in c) / k, "tiers": "+".join(tn),
                    "half": any((x.get("uf_stake") or 1) < 1 for x in c)})
    out.sort(key=lambda p: (-p["score"], -p["price"]))
    return _cap_reuse(out, n)


MASTER_DIR = Path(os.environ.get("MARV_MASTER_STATE", "/opt/marv-bot/state/master"))
MASTER_DAYS = int(os.environ.get("DAILY_MASTER_DAYS", "7"))


def master_extremes(now=None) -> list[dict]:
    """Pending upcoming Master extremes-rule totals (ledger.json): High Shootout = Over only, Def Grind = Under only.
    UF stake from the latest decisions.jsonl row per game: 0 -> excluded, <1 -> half stake."""
    now = now or datetime.now(timezone.utc)
    stake = {}
    try:
        for ln in open(MASTER_DIR / "decisions.jsonl"):
            try:
                r = json.loads(ln)
                stake[r["key"]] = float(r.get("uf_stake_total", 1.0))
            except Exception:  # noqa: BLE001
                continue
    except Exception:  # noqa: BLE001
        pass
    out = []
    for r in _jload(MASTER_DIR / "ledger.json", []):
        try:
            if r.get("kind") != "total" or r.get("status") != "pending":
                continue
            tier, sd = r.get("tier"), r.get("side")
            if not ((tier == "high_shootout" and sd == 1) or (tier == "defensive_grind" and sd == -1)):
                continue
            st = datetime.fromisoformat(r["start"])
            if not (now < st <= now + timedelta(days=MASTER_DAYS)):
                continue
            k = stake.get(r.get("pred_key"), 1.0)
            if k <= 0:
                continue
            label = "High Shootout" if tier == "high_shootout" else "Def Grind"
            g = type("G", (), {"home": r["home"], "away": r["away"], "start": st})
            out.append(_leg(r["sport"], g, "total", "Over" if sd == 1 else "Under", float(r["line"]),
                            float(r.get("price") or -110), "SIGNAL", 10.0 + abs(float(r.get("d", 0))) / 10,
                            f"🎯 MASTER EXTREME · {label} (tracking, unproven)", master_extreme=label, uf_stake=k))
        except Exception:  # noqa: BLE001
            log.exception("daily picks: master extreme row")
    # 2026-10-14: also surface Script O/U extremes (state/script_ou/ledger.json), same High Shootout/Def Grind rule
    w = lambda t: (str(t or "").lower().split() or [""])[0]
    seen = {(x["sport"], w(x["away"]), w(x["home"])) for x in out}
    for r in _jload(Path(os.environ.get("SCRIPT_OU_LEDGER", "/opt/marv-bot/state/script_ou/ledger.json")), []):
        try:
            if r.get("status") != "pending" or r.get("uf", 1) != 1 or r.get("sport") == "soccer":
                continue
            st = datetime.fromisoformat(str(r["start"]).replace("Z", "+00:00"))
            if not (now < st <= now + timedelta(days=MASTER_DAYS)) or (r["sport"], w(r["away"]), w(r["home"])) in seen:
                continue
            sd = 1 if r.get("side", 1) > 0 else -1
            label = "High Shootout" if sd == 1 else "Def Grind"
            g = type("G", (), {"home": r["home"], "away": r["away"], "start": st})
            out.append(_leg(r["sport"], g, "total", "Over" if sd == 1 else "Under", float(r["line"]),
                            float(r.get("price") or -110), "SIGNAL", 10.0 + abs(float(r.get("d", 0))) / 10,
                            f"🎯 MASTER EXTREME · {label} (Script O/U, tracking, unproven)", master_extreme=label, uf_stake=1.0))
            seen.add((r["sport"], w(r["away"]), w(r["home"])))
        except Exception:  # noqa: BLE001
            log.exception("daily picks: script_ou extreme row")
    return sorted(out, key=lambda x: -x["strength"])


def telegram_text(card: dict, record_line: str, test: bool = False) -> str:
    """Daily Cross-Sport Ranked Action Plan (Telegram). Real Marv/Master legs only; NBA stripped; no unproven %."""
    mx = [x for x in master_extremes() if x["sport"] not in TG_EXCLUDE]
    mkeys = {(x["sport"], x["away"].split()[0].lower(), x["home"].split()[0].lower()): x for x in mx}
    legs = []
    for x in card["ml"] + card["ou"]:
        if x["sport"] in TG_EXCLUDE:
            continue
        m = mkeys.get((x["sport"], str(x["away"]).split()[0].lower(), str(x["home"]).split()[0].lower()))
        if m and x["market"] == "total":
            if x["side"] != m["side"]:
                log.warning("daily picks: conflict resolved %s %s total: kept Master extreme %s, dropped %s",
                            x["sport"], x["game"], m["side"], x["side"])
            continue  # Master extreme owns this game's total
        legs.append(x)
    legs = mx + sorted(legs, key=lambda x: -x["strength"])
    d = datetime.fromisoformat(card["date"])
    sports = [NAME[s] for s in card["sports_checked"] if s not in TG_EXCLUDE]
    L = [("🧪 TEST · " if test else "") + "👽 Daily Cross-Sport Ranked Action Plan · 📝 PAPER",
         f"Sports checked: {', '.join(sports)} (NBA excluded)",
         f"UF filter removed {len(card['uf_removed'])} leg(s) (UF=0 / hard veto / upset score / shorter than {UF_MAX_FAV:+.0f})"]
    def plist(title, ps):
        L.extend(["", f"━━ {title} ━━"])
        if not ps:
            L.append(" none today (not enough qualifying games)")
        for i, p in enumerate(ps, 1):
            L.append(f" {i}) {p['price']:+d} · " + " + ".join(f"{'🎯' if x.get('master_extreme') else TIER_ICON[x['tier']]} {_mpick(x)} {_tag(x)}" for x in p["legs"]))
            L.append(f"    {len(p['legs'])} legs · {p['tiers']}" + (" · ½ STAKE (UF)" if p["half"] else ""))
    plist("1️⃣ TOP RANKED 2-LEG PARLAYS", _combos(legs, 2, TG_TOP))
    big = []
    for k in (3, 4, 5):
        big += _combos(legs, k, 2)
    big.sort(key=lambda p: (-p["score"], -p["price"]))
    plist("2️⃣ TOP RANKED 3- TO 5-LEG PARLAYS", _cap_reuse(big, TG_TOP))
    for title, key in (("3️⃣ TOP RANKED STRAIGHT MONEYLINES / SIDES", "ml"), ("4️⃣ OVER/UNDERS", "total")):
        L.extend(["", f"━━ {title} ━━"])
        rows = [x for x in legs if x["market"] == key][:STRAIGHT_MAX]
        if not rows:
            L.append(" none qualified today")
        for i, x in enumerate(rows, 1):
            icon = "🎯" if x.get("master_extreme") else TIER_ICON[x["tier"]]
            when = _when(x["start"]) if datetime.fromisoformat(x["start"]).astimezone(CT).date() == d.date() else \
                datetime.fromisoformat(x["start"]).astimezone(CT).strftime("%a %b %-d %-I:%M %p CT")
            L.append(f" {i}) {icon} {_mpick(x)} {_tag(x)} · {when}")
            L.append(f"    {_just(x)}")
    try:
        from . import ufp_uvf
        L += ufp_uvf.card_lines([t for t in card.get("upset_tags", []) if t["sport"] not in TG_EXCLUDE])
        if ufp_uvf.ON:
            record_line = record_line + "\n" + ufp_uvf.record_line(Path(os.environ.get("STATE_DIR", "/opt/marv-bot/state")))
    except Exception:  # noqa: BLE001
        pass
    try:  # Oct 16 2026 (Marvin): CFB +200..+299 dog shadow tracking line
        from . import cfb_dog_shadow
        _dog = cfb_dog_shadow.record_line()
        if _dog:
            record_line = (record_line + "\n" if record_line else "") + _dog
    except Exception:  # noqa: BLE001
        pass
    try:  # 2026-10-15 Value Plays (tracking only unless state/value_play/validation.json passed)
        from . import value_play
        L += value_play.card_lines(TG_EXCLUDE)
    except Exception:  # noqa: BLE001
        log.exception("daily picks: value plays")
    try:  # 2026-10-17 (Marvin): one line, best Bovada CFB pregame prop 👍 (tracking; no % until proven)
        from . import cfb_props_shadow as _cps
        _pl = _cps.card_line()
        if _pl:
            L.extend(["", _pl])
    except Exception:  # noqa: BLE001
        pass
    L += ["", "Tiers: 🔥A passed every veto · 📈B backtested paper signal · 🧮C model lean. Ranked best → least by leg "
          "strength, not a probability; no model % (only proven backtests print one). Paper only.", record_line]
    try:
        ml = json.load(open("/opt/marv-bot/state/master/summary.json")).get("line", "")
        if ml:
            L.append(ml)
    except Exception:  # noqa: BLE001
        pass
    L.append(f"📅 {d:%a %b %-d, %Y} · built {datetime.fromisoformat(card['built']).astimezone(CT):%-I:%M %p} CT")
    return html.escape("\n".join(L))


def _march_edge_creds():
    """March_edge (@March_betbot) token/chat from the systemd credential (LoadCredential=march_edge:<aj-bet-bot env>)."""
    d = os.environ.get("CREDENTIALS_DIRECTORY", "")
    kv = {}
    try:
        for ln in open(os.path.join(d, "march_edge")):
            if "=" in ln and not ln.lstrip().startswith("#"):
                k, v = ln.split("=", 1)
                kv[k.strip()] = v.strip().strip("\"'")
    except Exception:  # noqa: BLE001
        return "", ""
    return kv.get("TELEGRAM_BOT_TOKEN", ""), kv.get("TELEGRAM_CHAT_ID", "")


def daily_card_telegram_enabled() -> bool:
    """DAILY_CARD_TELEGRAM=off skips the daily-card Telegram send (March_edge mirror already covers). Default on."""
    return os.environ.get("DAILY_CARD_TELEGRAM", "on").strip().lower() not in (
        "0", "false", "no", "off", "cancel", "cancelled", "canceled",
    )


def send(s, msg: str) -> str:
    """Marv bot first (mirrored as usual); if Marv's own bot fails (e.g. 403 blocked by the user) deliver through
    the March_edge mirror bot (MIRROR_BOT_TOKEN) instead. Returns where it landed; raises if both fail.

    DAILY_CARD_TELEGRAM=off: no Telegram at all (build + paper log still run)."""
    if os.environ.get("DAILY_CARD_TELEGRAM", "").strip().lower() == "march_edge":
        from . import telegram as T
        tok, chat = _march_edge_creds()
        if not tok or not chat:
            raise RuntimeError("DAILY_CARD_TELEGRAM=march_edge but march_edge credential missing")
        T._post(tok, chat, msg)
        return "March_edge @March_betbot"
    if not daily_card_telegram_enabled():
        log.info("daily picks: DAILY_CARD_TELEGRAM=off; Telegram send skipped (mirror already covers)")
        return "disabled (DAILY_CARD_TELEGRAM=off)"
    from . import telegram as T
    try:
        T.send_message(s.telegram_bot_token, s.telegram_chat_id, msg)
        return "Marv bot (+ mirror if configured)"
    except Exception as exc:  # noqa: BLE001
        m_token, m_chat = T.mirror_token(), os.environ.get("MIRROR_CHAT_ID", "") or s.telegram_chat_id
        if not m_token:
            raise
        log.warning("daily picks: Marv bot send failed (%s); using the March_edge mirror", str(exc)[:80])
        T._post(m_token, m_chat, f"{T.LABEL}\n{msg}")
        return f"March_edge mirror (Marv bot failed: {str(exc)[:60]})"


def main(argv=None) -> int:
    from .config import Settings
    ap = argparse.ArgumentParser(prog="marv.dailypicks")
    ap.add_argument("--dry-run", action="store_true", help="print, don't send or log")
    ap.add_argument("--test", action="store_true", help="send labelled TEST, don't log as paper picks")
    ap.add_argument("--no-refresh", action="store_true", help="grade only; no new projections")
    ap.add_argument("--sports", default=",".join(SPORTS))
    ap.add_argument("--cached", action="store_true", help="re-send today's stored card (no projections)")
    ap.add_argument("--tg-preview", action="store_true", help="print the Telegram action plan only; no send/log")
    a = ap.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = Settings.from_env()
    now = datetime.now(timezone.utc)
    cached = _jload(_dir(Path(s.state_dir)) / CARD, None) if a.cached else None
    card = cached or collect(s, now, [x for x in a.sports.split(",") if x in SPORTS], refresh=not a.no_refresh)
    msg = text(card, test=a.test, paper=s.paper_mode) + "\n" + html.escape(record(Path(s.state_dir)))
    print(msg)
    if os.environ.get("DAILY_CARD_TELEGRAM", "").strip().lower() == "march_edge" or a.tg_preview:
        msg = telegram_text(card, record(Path(s.state_dir)), test=a.test)
        print("----- TELEGRAM (March_edge) -----\n" + html.unescape(msg))
    if a.tg_preview:
        return 0
    if a.dry_run:
        return 0
    if not a.test:
        n = log_card(Path(s.state_dir), card)
        log.info("daily picks: logged %d paper entries", n)
        try:
            from . import ufp_uvf
            if ufp_uvf.ON:
                log.info("daily picks: logged %d UFP/UVF tracking tags",
                         ufp_uvf.log_tags(Path(s.state_dir), card.get("upset_tags", []), card["ml"] + card["ou"]))
        except Exception:  # noqa: BLE001
            log.exception("ufp_uvf log")
    where = send(s, msg)
    log.info("daily picks: sent %s via %s", "TEST" if a.test else "daily card", where)
    print(f"SENT via {where}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


# 2026-10-17 (Marvin): /parlay <league>
LEAGUE_ALIAS = {"ncaaf": "cfb", "cfb": "cfb", "cfootball": "cfb", "college": "cfb", "nfl": "nfl", "wnba": "wnba",
                "nba": "nba", "soccer": "soccer", "futbol": "soccer", "epl": "soccer", "mls": "soccer", "mlb": "mlb",
                "nhl": "nhl", "ncaab": "ncaab", "cbb": "ncaab", "ncaam": "ncaab", "ncaaw": "ncaaw",
                "euroleague": "euroleague", "el": "euroleague"}
LEAGUE_HELP = "ncaaf (cfb), nfl, wnba, nba, soccer, mlb, nhl, ncaab, ncaaw, euroleague"


def league_card(settings, arg: str = "", full: bool = False) -> str:
    """/parlay [league]: plain = the all-sport parlay card (unchanged); a league = that league's 2-leg parlays +
    ML + O/U picks (+ its best Bovada prop line where Marv prices one: CFB)."""
    a = (arg or "").strip().lower().lstrip("/")
    if not a:
        return answer(settings, None, full=full)
    key = LEAGUE_ALIAS.get(a)
    if key is None:
        return html.escape(f"Unknown league '{a}'. Try /parlay with one of: {LEAGUE_HELP}. Plain /parlay shows every sport.")
    if key == "nba" and "nba" in TG_EXCLUDE:
        return "🏀 NBA picks are off Telegram for now; they resume after the Oct 27 review."
    try:  # 2026-10-17: same legs as the image card (/card plan legs: Master extremes own their totals)
        return league_text(settings, key)
    except Exception:  # noqa: BLE001
        log.exception("league_text")
    out = answer(settings, ["/parlay", key], full=True)
    if key == "cfb" and out.startswith("No qualifying"):
        try:
            from . import cfb_props_shadow as _cps
            _pl = _cps.card_line()
            if _pl:
                out += "\n\n" + html.escape(_pl)
        except Exception:  # noqa: BLE001
            pass
    return out



def league_text(settings, key: str) -> str:
    """/parlay <league> text built from exactly the legs the PNG card uses (marv.cardimg._plan_legs + _combos)."""
    from . import cardimg as CI
    state = Path(settings.state_dir)
    card = _jload(_dir(state) / CARD, None)
    if not card or card.get("date") != datetime.now(CT).date().isoformat():
        raise RuntimeError("no card today")
    legs = CI._plan_legs(card, key)
    ps = _combos(legs, 2, TG_TOP)
    d = datetime.fromisoformat(card["date"])
    L = [f"👽 MARV PREDICT · {NAME.get(key, key)} CARD · {d:%a %b %-d}" + (" · 📝 PAPER" if getattr(settings, "paper_mode", True) else ""),
         "", "━━ 🎰 BEST 2-LEG PARLAYS (ranked best → least) ━━"]
    if not ps:
        L.append(" none today (need 2+ qualifying games)")
    for i, p in enumerate(ps, 1):
        L.append(f" {i}) {p['price']:+d} · " + " + ".join(f"{'🎯' if x.get('master_extreme') else TIER_ICON[x['tier']]} {_mpick(x)}" for x in p["legs"]))
        L.append("    " + " | ".join(f"{x['game']} {_when(x['start'])}" for x in p["legs"]))
    for title, mk in (("💰 MONEYLINES / SIDES", "ml"), ("📐 OVER/UNDERS", "total")):
        rows = [x for x in legs if x["market"] == mk][:STRAIGHT_MAX]
        L += ["", f"━━ {title} ━━"] + ([" none qualified today"] if not rows else [])
        for i, x in enumerate(rows, 1):
            L.append(f" {i}) {'🎯' if x.get('master_extreme') else TIER_ICON[x['tier']]} {_mpick(x)} · {_when(x['start'])}")
            L.append(f"    {_just(x)}")
    if key == "cfb":
        try:
            from . import cfb_props_shadow as _cps
            pl = _cps.card_line()
            if pl:
                L += ["", pl]
        except Exception:  # noqa: BLE001
            pass
    bd = [ln for ln in sport_breakdown(state).splitlines() if f" {NAME.get(key, key)}:" in ln]
    L += ["", "Tiers: 🔥A passed every veto · 📈B backtested paper signal · 🧮C model lean · 🎯 Master Extreme (tracking). "
          "Ranked by leg strength, not a probability; no win % unless proven. Paper only."] + bd
    return html.escape("\n".join(L))
