"""Marv Predict soccer node: daily refresh + paper tracking + the March_edge H2H overlay.

  run()      refresh football-data, fit every league's ratings (power + H2H table + trend), project upcoming
             fixtures, compare with the pre-match market, log qualified paper picks, grade finished ones, write the
             output card (state/marv_predict/soccer/). Never sends Telegram.
  overlay()  one line for a March_edge soccer alert (league key eng.1 / soccer_epl ...): lean FOR / AGAINST /
             neutral + why (projected goals, power, H2H categories, trend; live totals from the score and minute).
             Verdict 'paper' (never 'not'), so it cannot veto an alert; never a probability, fair price or edge.
             Every read is logged and graded against the final score (paper record).
  game_read() the same game-level read for /game queries.

Promotion out of paper: the walk-forward (validate.py) must pass marv/proven.py's bar AND the logged paper reads must
hold up on 100+ graded matches; then the rule is added to marv/proven.py by hand. Until then: no %.
"""

import json
import logging
import os
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

from . import data as D
from . import engine as E
from . import names as N

log = logging.getLogger(__name__)
SUBDIR = Path("marv_predict") / "soccer"
NO_PCT = "paper-tracking — no %"
_cache: dict = {}
_lock = threading.Lock()


def mode() -> str:
    """paper (default). 'live' is reserved for after promotion and still never prints an unproven %."""
    return os.environ.get("SOCCER_PREDICT_MODE", "paper").strip().lower()


def out_dir(state_dir: Path) -> Path:
    d = Path(state_dir) / SUBDIR
    d.mkdir(parents=True, exist_ok=True)
    return d


def _jdump(path: Path, obj) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=1, default=lambda o: o.item() if hasattr(o, "item") else str(o)))
    tmp.replace(path)


def _jload(path: Path, default):
    try:
        return json.loads(path.read_text()) if path.exists() else default
    except (OSError, ValueError):
        return default


# ------------------------------------------------------------------ ratings
def validation(state_dir: Path) -> dict:
    return _jload(out_dir(state_dir) / "soccer_validation.json", {})


def live_params(state_dir: Path) -> E.Params:
    v = validation(state_dir)
    return E.Params.from_dict(v.get("params_live"))


def calib_penalty(state_dir: Path, key: str) -> bool:
    r = validation(state_dir).get("leagues", {}).get(key, {})
    return bool(r.get("market_brier") is not None and r.get("model_brier_same_games", 0) > r["market_brier"] + 0.01)


def refresh(state_dir: Path, now: datetime | None = None, leagues: list[str] | None = None) -> dict:
    """Fit and save every league's ratings as of now."""
    now = now or datetime.now(timezone.utc)
    cache = Path(state_dir) / "cache" / "soccer_fd"
    p = live_params(state_dir)
    asof = pd.Timestamp(now.date()) + pd.Timedelta(days=1)  # every match played up to today
    status = {}
    for key in leagues or list(D.LEAGUES):
        try:
            df = D.load_league(key, cache, now)
            if df.empty:
                status[key] = "no data"
                continue
            cur = D.current_season(now)
            proxy = E.fit_proxy(df[df["season"] < cur]) if df["hst"].notna().any() else None
            r = E.fit_ratings(df, asof, p, proxy)
            if not r:
                status[key] = "too few matches"
                continue
            src = D.LEAGUES[key][2]
            r.update({"league": key, "label": D.LEAGUES[key][1], "fitted": now.isoformat(),
                      "last_match": str(df.loc[df["played"], "date"].max().date()),
                      "source": "espn" if src == "espn" else "football-data",
                      "low_confidence": src == "espn"})
            _jdump(out_dir(state_dir) / f"ratings_{key}.json", r)
            status[key] = f"ok ({len(r['teams'])} teams, last match {r['last_match']})"
        except Exception as exc:  # noqa: BLE001 - one league never stops the others
            log.exception("soccer refresh %s failed", key)
            status[key] = f"error: {exc}"
    return status


def ratings(state_dir: Path, key: str) -> dict | None:
    path = out_dir(state_dir) / f"ratings_{key}.json"
    try:
        mt = path.stat().st_mtime
    except OSError:
        return None
    with _lock:
        hit = _cache.get(key)
        if hit and hit[0] == mt:
            return hit[1]
        r = _jload(path, None)
        _cache[key] = (mt, r)
        return r


# ------------------------------------------------------------------ helpers
def american_to_prob(a: float | None) -> float | None:
    if a is None:
        return None
    a = float(a)
    if -100 < a < 100:  # decimal price
        return 1 / a if a > 1 else None
    return 100 / (a + 100) if a > 0 else -a / (-a + 100)


LIVE_RE = re.compile(r"(\d+)\s*[–\-]\s*(\d+)[^\n·\d]{0,24}·\s*(HT|FT|\d+)(?:\+\d+)?'?")


def parse_live(text: str | None) -> tuple[int, float] | None:
    """(goals so far, minute) from a March_edge live card line like '⚽ LA 1–6 STL · 64''."""
    if not text:
        return None
    m = LIVE_RE.search(text)
    if not m:
        return None
    goals = int(m.group(1)) + int(m.group(2))
    t = m.group(3)
    minute = 45.0 if t == "HT" else 90.0 if t == "FT" else float(t)
    return goals, minute


def _poisson_cdf(k: float, lam: float) -> float:
    if k < 0:
        return 0.0
    k = int(np.floor(k))
    i = np.arange(k + 1)
    from math import lgamma
    return float(np.sum(np.exp(-lam + i * np.log(max(lam, 1e-12)) - np.array([lgamma(x + 1) for x in i]))))


def total_settle(lam: float, need_line: float) -> tuple[float, float, float]:
    """(over, push, under) for remaining goals ~ Poisson(lam) against a line already net of goals scored."""
    if abs(need_line * 4 - round(need_line * 4)) < 1e-9 and abs(need_line * 2 - round(need_line * 2)) > 1e-9:
        a, b = total_settle(lam, need_line - 0.25), total_settle(lam, need_line + 0.25)
        return tuple((x + y) / 2 for x, y in zip(a, b))
    if need_line < 0:
        return 1.0, 0.0, 0.0
    if float(need_line).is_integer():
        under = _poisson_cdf(need_line - 1, lam)
        push = _poisson_cdf(need_line, lam) - under
        return 1 - under - push, push, under
    under = _poisson_cdf(np.floor(need_line), lam)
    return 1 - under, 0.0, under


def _lean(p: float | None, implied: float | None) -> str:
    if p is None:
        return "neutral"
    if implied is None:
        return "for" if p > 0.5 else "against"
    d = p - implied
    return "for" if d >= 0.02 else "against" if d <= -0.04 else "neutral"


LEAN_TXT = {"for": "leans FOR", "against": "leans AGAINST", "neutral": "no clear lean on"}


def _display(r: dict, home: str, away: str, team: str | None, other: str | None) -> tuple[str, str]:
    """Show the alert's own team names when they are what resolved (e.g. 'Atletico Madrid', not 'Ath Madrid')."""
    hd = team if team and N.resolve(team, r["teams"]) == home else home
    ad = other if other and N.resolve(other, r["teams"]) == away else away
    return hd, ad


def _drivers(proj: dict, home: str, away: str) -> str:
    c = proj["components"]
    pw = home if c["power_home_xg"] >= c["power_away_xg"] else away
    hp, ap = c["h2h_points"]
    h2h = f"H2H cats {hp}-{ap} {home if hp > ap else away}" if hp != ap else f"H2H cats {hp}-{ap} even"
    td = c["trend_diff"]
    tr = "trend even" if abs(td) < 0.15 else f"trend → {home if td > 0 else away}"
    return f"power {pw}, {h2h}, {tr}"


def _conf_word(c: int) -> str:
    return "high" if c >= 75 else "medium" if c >= 55 else "low"


def _find(state_dir: Path, key: str, team: str | None, other: str | None, text: str | None):
    r = ratings(state_dir, key)
    if not r:
        return None, None, None
    teams = list(r["teams"])
    a, b = N.resolve(team, teams), N.resolve(other, teams)
    if (not a or not b) and text:
        hits = N.in_text(text, teams)
        if len(hits) >= 2:
            a, b = a or hits[0], b or next(h for h in hits if h != (a or hits[0]))
    if not a or not b or a == b:
        return r, None, None
    return r, a, b  # March_edge passes team = home, other = away


def is_soccer(league: str | None) -> bool:
    return bool(D.league_key(league)) or (league or "").strip().lower() in ("soccer", "football_soccer")


def locate(state_dir: Path, league: str, team, other, text):
    """(league key, ratings, home, away); a bare 'soccer' league searches every supported league."""
    key = D.league_key(league)
    keys = [key] if key else (list(D.LEAGUES) if is_soccer(league) else [])
    for k in keys:
        r, home, away = _find(state_dir, k, team, other, text)
        if home:
            return k, r, home, away
    return key, None, None, None


def _side(side: str, home: str, away: str) -> str | None:
    s = (side or "").strip().lower()
    if s in ("draw", "tie", "x"):
        return "draw"
    if s in ("home", "1"):
        return "home"
    if s in ("away", "2"):
        return "away"
    hs, as_ = N.score(side, home), N.score(side, away)
    if max(hs, as_) < 0.6:
        return None
    return "home" if hs >= as_ else "away"


def _kind(market: str) -> str:
    m = (market or "").lower()
    if m in ("h2h", "ml", "moneyline", "1x2", "h2h_3_way"):
        return "ml"
    if m in ("spreads", "spread", "alternate_spreads", "asian_handicap"):
        return "spread"
    if m in ("totals", "total", "alternate_totals"):
        return "total"
    return "other"


# ------------------------------------------------------------------ overlay (March_edge alerts)
def overlay(state_dir: Path, league: str, market: str, side: str, line: float | None = None, team: str | None = None,
            other: str | None = None, text: str | None = None, price: float | None = None, live: bool = False) -> dict | None:
    """Paper read for one March_edge soccer alert, or None (no read). Never a %."""
    key, r, home, away = locate(state_dir, league, team, other, text)
    if not home:
        return None
    m = E.MarvSoccerPredictMax(r, calib_penalty(state_dir, key))
    proj = m.project_match(home, away, key=f"{key}|{home}|{away}|{r['asof']}")
    kind = _kind(market)
    implied = american_to_prob(price) if price not in (None, "") else None
    lh, la = proj["home_xg"], proj["away_xg"]
    hd, ad = _display(r, home, away, team, other)
    game = f"{ad} @ {hd}"
    head = f"proj {hd} {lh:.1f}–{la:.1f} {ad} ({_drivers(proj, hd, ad)})"
    p, what, extra, st = None, "", "", None
    if kind == "total" and line is not None:
        over = (side or "").lower().startswith("o")
        st = parse_live(text) if live else None
        if st:
            goals, minute = st
            frac = max(0.0, 93.0 - minute) / 93.0
            lam = (lh + la) * frac
            po, pu, pun = total_settle(lam, float(line) - goals)
            p = (po if over else pun) / max(1 - pu, 1e-9)
            extra = f" · live: {goals} scored by {minute:.0f}', ~{lam:.1f} more expected"
        elif live:
            extra = " · live: no score/minute in the alert, pregame read only"
        else:
            po, pu, pun = total_settle(lh + la, float(line))
            p = (po if over else pun) / max(1 - pu, 1e-9)
        what = f"{'Over' if over else 'Under'} {float(line):g}"
    elif kind in ("ml", "spread"):
        s = _side(side, home, away)
        if s is None:
            return None
        if kind == "ml":
            p = {"home": proj["p_home"], "draw": proj["p_draw"], "away": proj["p_away"]}[s]
            what = "the draw" if s == "draw" else f"{hd if s == 'home' else ad} ML"
        else:
            ln = float(line or 0.0)
            key_ln = f"{(ln if s == 'home' else -ln):+g}"
            ah = proj["asian_handicap"].get(key_ln)
            if ah:
                w, pu = (ah["home"], ah["push"]) if s == "home" else (ah["away"], ah["push"])
                p = w / max(1 - pu, 1e-9)
            what = f"{hd if s == 'home' else ad} {ln:+g}"
        if live:  # a pregame side probability says nothing once the score has moved
            extra, p = " · live: pregame matchup read only", None
    else:
        why = f"⚽ paper read — no soccer prop logic; game read: {head}"
        _log(state_dir, key, home, away, market, side, line, price, live, None, None, "n/a", None)
        return {"verdict": "paper", "why": why, "prob": NO_PCT, "game": game}
    lean = _lean(p, implied)
    why = f"⚽ paper read — {LEAN_TXT[lean]} {what}: {head}{extra} · data conf {_conf_word(proj['confidence'])}"
    _log(state_dir, key, home, away, market, side, line, price, live, st, p, lean, implied)
    return {"verdict": "paper", "why": _no_pct(why), "prob": NO_PCT, "game": game}


def _no_pct(s: str) -> str:
    """Belt and braces: the overlay text never carries a percentage."""
    return re.sub(r"\d+(?:\.\d+)?\s*%", "", s).replace("%", "")


def game_read(state_dir: Path, league: str, team: str | None = None, other: str | None = None,
              text: str | None = None) -> dict | None:
    key, r, home, away = locate(state_dir, league, team, other, text)
    if not home:
        return None
    proj = E.MarvSoccerPredictMax(r, calib_penalty(state_dir, key)).project_match(home, away, key=f"{key}|{home}|{away}|{r['asof']}")
    ps = {"home": proj["p_home"], "draw": proj["p_draw"], "away": proj["p_away"]}
    pick = max(ps, key=ps.get)
    home, away = _display(r, home, away, team, other)
    pick_name = {"home": home, "away": away, "draw": "Draw"}[pick]
    tot = proj["total_mean"]
    ou = "Over 2.5" if proj["totals"]["2.5"]["over"] > 0.5 else "Under 2.5"
    btts = "yes" if proj["p_btts"] > 0.5 else "no"
    why = (f"⚽ paper read — proj {home} {proj['home_xg']:.1f}–{proj['away_xg']:.1f} {away} ({_drivers(proj, home, away)}) · "
           f"likely {proj['score_distribution'][0]['score']} · O/U lean {ou} (proj {tot:.1f} goals) · BTTS lean {btts} · "
           f"data conf {_conf_word(proj['confidence'])}")
    return {"verdict": "pick", "why": _no_pct(why), "prob": NO_PCT, "game": f"{away} @ {home}", "pick": pick_name}


def _log(state_dir, key, home, away, market, side, line, price, live, st, p, lean, implied) -> None:
    try:
        rec = {"ts": datetime.now(timezone.utc).isoformat(), "league": key, "home": home, "away": away,
               "market": market, "side": side, "line": line, "price": price, "live": bool(live),
               "goals_at": st[0] if st else None, "minute": st[1] if st else None,
               "p_model": None if p is None else round(float(p), 4),
               "implied": None if implied is None else round(float(implied), 4), "lean": lean,
               "model_version": E.MODEL_VERSION}
        with _lock, open(out_dir(state_dir) / "overlay_log.jsonl", "a") as fh:
            fh.write(json.dumps(rec) + "\n")
    except Exception:  # noqa: BLE001 - logging never breaks the overlay
        log.debug("overlay log failed", exc_info=True)


# ------------------------------------------------------------------ paper run + grading
def _snapshot(row) -> dict:
    g = lambda c: None if pd.isna(row.get(c)) else float(row.get(c))
    return {"h": g("o_h"), "d": g("o_d"), "a": g("o_a"), "over": g("o_over"), "under": g("o_under"),
            "book": "Bovada (else market avg)"}


def paper_run(state_dir: Path, now: datetime | None = None, days: int = 4) -> dict:
    now = now or datetime.now(timezone.utc)
    od = out_dir(state_dir)
    fx = D.load_fixtures(Path(state_dir) / "cache" / "soccer_fd")
    ledger = _jload(od / "paper.json", {})
    cards = []
    _res: dict = {}
    if not fx.empty:
        today = pd.Timestamp(now.date())
        fx = fx[(fx["date"] >= today) & (fx["date"] <= today + pd.Timedelta(days=days))]
        for _, row in fx.iterrows():
            key = row["league"]
            r = ratings(state_dir, key)
            if not r or row["home"] not in r["teams"] or row["away"] not in r["teams"]:
                continue
            mk = f"{key}|{row['date'].date()}|{row['home']}|{row['away']}"
            proj = E.MarvSoccerPredictMax(r, calib_penalty(state_dir, key)).project_match(row["home"], row["away"], key=mk)
            ev = E.evaluate_market(proj, _snapshot(row))
            old_best = ev["best"] if ev["qualified"] else None
            try:
                from .. import tci_ppe_uf as TPU
                if key not in _res:
                    _res[key] = _results(state_dir, key)
                uf, why = TPU.soccer_uf(_res[key], row["home"], row["away"], row["date"])
            except Exception:  # noqa: BLE001
                uf, why = 1, ""
            if ev["qualified"] and ev["best"] and ev["best"]["market"] == "1x2":
                alt = [e for e in ev["edges"] if e["market"] != "1x2" and e["edge"] >= 0.04 and e["ev"] > 0 and e["price"] <= 6.0]
                ev["best"] = max(alt, key=lambda e: e["ev"]) if alt else None
                if not alt:
                    ev["qualified"] = False
                    ev["vetoes"].append("soccer moneyline dropped from play list")
            if ev["qualified"] and uf == 0:
                ev["qualified"] = False
                ev["vetoes"].append(f"UF=0 ({why}): no bet")
            ent = ledger.setdefault(mk, {"league": key, "date": str(row["date"].date()), "home": row["home"],
                                         "away": row["away"], "first_eval": now.isoformat()})
            ent.update({"last_eval": now.isoformat(), "home_xg": proj["home_xg"], "away_xg": proj["away_xg"],
                        "p": [proj["p_home"], proj["p_draw"], proj["p_away"]], "p_over25": proj["totals"]["2.5"]["over"],
                        "market": ev["edges"], "confidence": proj["confidence"], "vetoes": ev["vetoes"]})
            ent.update({"uf": uf, "uf_reason": why})
            if ev["qualified"] and not ent.get("pick"):
                ent["pick"] = {**ev["best"], "at": now.isoformat()}
            if old_best and not ent.get("pick_old"):
                ent["pick_old"] = {**old_best, "at": now.isoformat()}  # pre-TCI/UF logic, paper side-by-side
            cards.append({"competition": D.LEAGUES[key][1], "kickoff": f"{row['date'].date()} {row.get('time', '')}",
                          "home": row["home"], "away": row["away"], "projected_goals": [proj["home_xg"], proj["away_xg"]],
                          "likely_scores": proj["score_distribution"][:3], "p_1x2": ent["p"],
                          "fair_1x2": [round(1 / max(x, 1e-6), 2) for x in ent["p"]], "total_mean": proj["total_mean"],
                          "p_btts": proj["p_btts"], "best_qualified": ev["best"] if ev["qualified"] else None,
                          "market": ev["book"], "confidence": proj["confidence"], "vetoes": ev["vetoes"],
                          "drivers": _drivers(proj, row["home"], row["away"]), "status": "PAPER"})
    _jdump(od / "paper.json", ledger)
    _jdump(od / "card.json", {"generated": now.isoformat(), "mode": mode(), "model_version": E.MODEL_VERSION,
                              "note": "paper only: internal file, never sent to Telegram", "matches": cards})
    return {"projected": len(cards), "picks_open": sum(1 for v in ledger.values() if v.get("pick") and "result" not in v)}


def _results(state_dir: Path, key: str) -> pd.DataFrame:
    df = D.load_league(key, Path(state_dir) / "cache" / "soccer_fd", first=D.current_season())
    return df[df["played"]] if not df.empty else df


def grade(state_dir: Path, now: datetime | None = None) -> dict:
    """Grade paper picks / logged projections and the logged overlay reads against final scores."""
    now = now or datetime.now(timezone.utc)
    od = out_dir(state_dir)
    ledger = _jload(od / "paper.json", {})
    res_cache: dict = {}

    def result(key, home, away, day) -> tuple | None:
        if key not in res_cache:
            res_cache[key] = _results(state_dir, key)
        df = res_cache[key]
        if df.empty:
            return None
        d = df[(df["home"] == home) & (df["away"] == away) & ((df["date"] - pd.Timestamp(day)).abs() <= pd.Timedelta(days=3))]
        return (int(d["hg"].iat[0]), int(d["ag"].iat[0])) if len(d) else None

    for k, e in ledger.items():
        if "result" in e or pd.Timestamp(e["date"]) > pd.Timestamp(now.date()):
            continue
        rs = result(e["league"], e["home"], e["away"], e["date"])
        if not rs:
            continue
        e["result"] = list(rs)
        for pk in (e.get("pick"), e.get("pick_old")):
            if not pk:
                continue
            hg, ag = rs
            sel = pk["selection"]
            win = {"home": hg > ag, "draw": hg == ag, "away": hg < ag, "over 2.5": hg + ag > 2.5,
                   "under 2.5": hg + ag < 2.5}[sel]
            pk["win"] = bool(win)
            pk["profit"] = round(pk["price"] - 1, 4) if win else -1.0
    _jdump(od / "paper.json", ledger)

    # overlay reads
    graded = []
    path = od / "overlay_log.jsonl"
    seen = set()
    if path.exists():
        for ln in path.read_text().splitlines():
            try:
                o = json.loads(ln)
            except ValueError:
                continue
            dk = (o["league"], o["home"], o["away"], o["ts"][:10], o["market"], o["side"], o["line"], o.get("minute"))
            if dk in seen or o["lean"] in ("n/a",):
                continue
            seen.add(dk)
            if pd.Timestamp(o["ts"][:10]) >= pd.Timestamp(now.date()):
                continue
            rs = result(o["league"], o["home"], o["away"], o["ts"][:10])
            if not rs:
                continue
            hg, ag = rs
            kind, side = _kind(o["market"]), (o["side"] or "").lower()
            won = None
            if kind == "total" and o["line"] is not None:
                t, ln_ = hg + ag, float(o["line"])
                won = None if t == ln_ else (t > ln_) == side.startswith("o")
            elif kind == "ml":
                s = _side(o["side"], o["home"], o["away"])
                won = {"home": hg > ag, "draw": hg == ag, "away": hg < ag}.get(s)
            if won is None:
                continue
            graded.append({**o, "result": [hg, ag], "bet_won": bool(won)})
    summ = summary(ledger, graded)
    _jdump(od / "overlay_graded.json", graded[-5000:])
    _jdump(od / "paper_summary.json", summ)
    return summ


def summary(ledger: dict, graded: list) -> dict:
    picks = [e["pick"] for e in ledger.values() if e.get("pick") and "win" in e["pick"]]
    out = {"picks": {"n": len(picks)}}
    if picks:
        out["picks"].update({"hit": round(np.mean([p["win"] for p in picks]), 4),
                             "roi": round(float(np.mean([p["profit"] for p in picks])), 4)})
    proj = [e for e in ledger.values() if "result" in e and e.get("p")]
    if proj:
        y = np.array([[e["result"][0] > e["result"][1], e["result"][0] == e["result"][1], e["result"][0] < e["result"][1]]
                      for e in proj], float)
        p = np.array([e["p"] for e in proj])
        out["projections"] = {"n": len(proj), "brier_1x2": round(float(((p - y) ** 2).sum(1).mean()), 4)}
    for lean in ("for", "against", "neutral"):
        g = [x for x in graded if x["lean"] == lean]
        out[f"overlay_{lean}"] = {"n": len(g), "alert_bet_won": round(float(np.mean([x["bet_won"] for x in g])), 4) if g else None}
    out["note"] = "paper record; promotion needs 100+ graded reads AND the walk-forward bar in marv/proven.py"
    return out


def run(state_dir: Path, now: datetime | None = None, validate_if_older_days: float | None = 7) -> dict:
    now = now or datetime.now(timezone.utc)
    od = out_dir(state_dir)
    vpath = od / "soccer_validation.json"
    status = {"started": now.isoformat(), "mode": mode()}
    if validate_if_older_days is not None and (not vpath.exists() or (now.timestamp() - vpath.stat().st_mtime) > validate_if_older_days * 86400):
        from . import validate as V
        rep = V.run(Path(state_dir) / "cache" / "soccer_fd", od)
        (od / "soccer_validation.md").write_text(V.markdown(rep))
        status["validated"] = True
    status["refresh"] = refresh(state_dir, now)
    status["paper"] = paper_run(state_dir, now)
    status["grade"] = grade(state_dir, now)
    status["finished"] = datetime.now(timezone.utc).isoformat()
    _jdump(od / "status.json", status)
    return status
