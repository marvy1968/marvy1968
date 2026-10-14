"""UFP (Upset Filter Protocol) / UVF (Upset Value Finder) tags — TRACKING ONLY (2026-10-13).

Backtest (experiments/upset/, NFL 2020-26 ML, CFB 2018-26 ATS, WNBA 2018-26 ML): no signal held in both
Aug 1-Oct 12 and Sep 21-Oct 12 2026 windows AND prior seasons -> nothing adopted; tags have NO betting effect
(no UF v2 stake change, no Master/demo-feed picks). They are logged so the live record can be measured.
Flag: MARV_UPSET=1 (default off). Rollback: MARV_UPSET=0 (or restore *.bak-20261013-upset).
Labels on the card: 'UFP Trap Warning' (heavy fav ML <= -200 with >=1 UFP signal), 'UVF Value Dog' (dog ML >= +150 with >=1 UVF signal). No %.
Sports: NFL, CFB (cached team-game stats). WNBA = off-season (schedule/fatigue tested, not live). MLB = no cached odds -> not available.
"""
from __future__ import annotations
import json, logging, os
from datetime import datetime, timezone
from pathlib import Path
import numpy as np, pandas as pd

log = logging.getLogger(__name__)
ON = os.environ.get("MARV_UPSET", "0") == "1"
FAV_MAX, DOG_MIN = -200, 150
UFP_LABEL, UVF_LABEL = "UFP Trap Warning", "UVF Value Dog"


def _pct(s): return s.rank(pct=True)


def _nfl_table(C: Path, now) -> tuple[pd.DataFrame, pd.DataFrame]:
    y = now.year
    st = pd.concat([pd.read_csv(C / f"nfl_stats_team_week_{v}.csv") for v in (y - 1, y) if (C / f"nfl_stats_team_week_{v}.csv").exists()])
    st = st[st.season == st.season.max()]
    st["plays"] = st.attempts + st.carries + st.sacks_suffered
    st["yds"] = st.passing_yards + st.rushing_yards
    st["epa"] = (st.passing_epa.fillna(0) + st.rushing_epa.fillna(0)) / st.plays
    td = st.passing_tds + st.rushing_tds; st["rz"] = td / (td + st.fg_att.fillna(0)).replace(0, np.nan)
    st["give"] = st.passing_interceptions + st.sack_fumbles_lost + st.rushing_fumbles_lost + st.receiving_fumbles_lost
    st["fum"] = st.fumbles_total.fillna(0); st["fumrec"] = st.fumble_recovery_own.fillna(0)
    st["press"] = st.def_sacks.fillna(0) + st.def_qb_hits.fillna(0)
    o = st[["game_id", "team", "epa", "give", "fum", "fumrec", "sacks_suffered", "attempts"]].rename(columns=lambda c: c if c == "game_id" else "o_" + c)
    st = st.merge(o, on="game_id"); st = st[st.team != st.o_team]
    st["netepa"] = st.epa - st.o_epa; st["ypp"] = st.yds / st.plays; st["tom"] = st.o_give - st.give
    st["dsack"] = st.def_sacks.fillna(0) / (st.o_attempts + st.o_sacks_suffered)
    st["allfum"] = st.fum + st.o_fum; st["recov"] = st.fumrec + (st.o_fum - st.o_fumrec)
    g = pd.read_csv(C / "nflverse_games.csv"); g["date"] = pd.to_datetime(g.gameday)
    st = st.merge(g[["game_id", "date", "home_team", "home_score", "away_score"]], on="game_id")
    st["marg"] = np.where(st.team == st.home_team, st.home_score - st.away_score, st.away_score - st.home_score)
    st = st[st.date < pd.Timestamp(now).tz_localize(None)].sort_values("date")
    rows = []
    for t, d in st.groupby("team"):
        rows.append(dict(team=t, n=len(d), epa_s=d.netepa.mean(), ypp_s=d.ypp.mean(), rz_s=d.rz.mean(), tom_s=d.tom.mean(),
                         press_s=d.press.mean(), dsack_s=d.dsack.mean(), pace3=d.plays.tail(3).mean(), var3=d.netepa.tail(3).std(),
                         rz3=d.rz.tail(3).mean(), fumluck=(d.recov.sum() + 1) / (d.allfum.sum() + 2),
                         lastmarg=d.marg.iloc[-1], wp=(d.marg > 0).mean()))
    T = pd.DataFrame(rows).set_index("team")
    for c in ("epa_s", "pace3", "var3", "ypp_s", "press_s", "dsack_s"):
        T[c + "_p"] = _pct(T[c])
    sched = g[g.season == g.season.max()][["date", "home_team", "away_team", "div_game"]]
    return T, sched


def _nfl_abbr(team: str, T) -> str | None:
    if team in T.index:
        return team
    try:
        from .data.teams import NFL_TEAMS
        ab = {v: k for k, v in NFL_TEAMS.items()}.get(team)
        if ab in T.index:
            return ab
    except Exception:  # noqa: BLE001
        pass
    for ab in T.index:
        if team.upper().startswith(ab) or team.split()[-1].upper() == ab:
            return ab
    return None


def _cfb_table(C: Path, now):
    y = now.year
    tg = pd.read_parquet(C / f"cfb_teamgames_{y}.parquet").drop_duplicates(["game_id", "team"]); tg["game_id"] = tg.game_id.astype(str)
    o = tg[["game_id", "team", "turnovers", "sack_rate", "stuff_rate", "success_rate"]].rename(columns=lambda c: c if c == "game_id" else "o_" + c)
    tg = tg.merge(o, on="game_id"); tg = tg[tg.team != tg.o_team]
    g = pd.read_parquet(C / f"cfb_games_{y}.parquet"); g["game_id"] = g.game_id.astype(str); g["date"] = pd.to_datetime(g.date).dt.tz_localize(None)
    tg = tg.merge(g[["game_id", "date", "home", "home_points", "away_points"]], on="game_id")
    tg["marg"] = np.where(tg.team == tg.home, tg.home_points - tg.away_points, tg.away_points - tg.home_points)
    tg["tom"] = tg.o_turnovers - tg.turnovers; tg["net_sr"] = tg.success_rate - tg.o_success_rate; tg["dpress"] = tg.o_sack_rate + tg.o_stuff_rate
    tg = tg[tg.date < pd.Timestamp(now).tz_localize(None)].sort_values("date")
    rows = []
    for t, d in tg.groupby("team"):
        rows.append(dict(team=t, n=len(d), sr_s=d.net_sr.mean(), ypp_s=d.ypp.mean(), rz_s=d.redzone_td_rate.mean(), tom_s=d.tom.mean(),
                         dpress_s=d.dpress.mean(), pace3=d.plays.tail(3).mean(), var3=d.net_sr.tail(3).std(),
                         rz3=d.redzone_td_rate.tail(3).mean(), lastmarg=d.marg.iloc[-1], wp=(d.marg > 0).mean()))
    T = pd.DataFrame(rows).set_index("team")
    for c in ("sr_s", "pace3", "var3", "ypp_s", "dpress_s"):
        T[c + "_p"] = _pct(T[c])
    sched = g[["date", "home", "away"]].rename(columns={"home": "home_team", "away": "away_team"}); sched["div_game"] = np.nan
    return T, sched


def _next(sched, team, after):
    s = sched[((sched.home_team == team) | (sched.away_team == team)) & (sched.date > pd.Timestamp(after).tz_localize(None) + pd.Timedelta(days=1))].sort_values("date")
    if s.empty:
        return None, False
    r = s.iloc[0]
    return (r.away_team if r.home_team == team else r.home_team), bool(r.div_game == 1)


def signals(sport, T, sched, fav, dog, when) -> tuple[list, list]:
    """(ufp signals on fav, uvf signals on dog) — same definitions as experiments/upset/bt_*.py."""
    f, d = T.loc[fav], T.loc[dog]
    if f.n < 3 or d.n < 3:
        return [], []
    nf = "epa_s" if sport == "nfl" else "sr_s"
    u, v = [], []
    if f[nf + "_p"] >= 0.6 and (f.pace3_p <= 0.25 or f.var3_p >= 0.75): u.append("pace/variance disconnect")
    nxt, div = _next(sched, fav, when)
    if d.wp <= 0.4 and nxt is not None and (div or (nxt in T.index and T.loc[nxt].wp >= 0.6)): u.append(f"lookahead (next {nxt})")
    if f.ypp_s_p >= 0.6 and f.rz3 <= f.rz_s - 0.10: u.append("red-zone TD% declining")
    if sport == "nfl" and f.tom_s > 0 and f.fumluck >= 0.6 and f.press_s_p <= 0.5: u.append("TO-margin luck (fumble recoveries)")
    if sport == "cfb" and f.tom_s >= 0.75 and f.dpress_s_p <= 0.5: u.append("TO-margin without pressure")
    if d[nf] >= f[nf] - (0.02 if sport == "nfl" else 0.01): v.append("per-play efficiency ~ favourite")
    if d.lastmarg <= (-17 if sport == "nfl" else -21) or f.lastmarg >= (21 if sport == "nfl" else 28): v.append("public overreaction (blowout)")
    if sport == "nfl" and d.dsack_s_p >= 0.66 and f.dsack_s_p <= 0.5: v.append("defensive trench edge")
    if sport == "cfb" and d.dpress_s_p >= 0.66 and f.dpress_s_p <= 0.5: v.append("defensive trench edge")
    return u, v


def tag_games(sport: str, preds, state_dir: Path, now=None) -> list[dict]:
    if not ON:
        return []
    now = now or datetime.now(timezone.utc)
    me = _model_edge_tags(sport, preds)
    if sport not in ("nfl", "cfb"):
        return me
    C = Path(state_dir) / "cache"
    try:
        T, sched = (_nfl_table if sport == "nfl" else _cfb_table)(C, now)
    except Exception:  # noqa: BLE001
        log.exception("ufp_uvf: %s table failed", sport); return []
    out = []
    for p in preds:
        g = p.game; o = g.odds
        if not o or o.home_ml is None or o.away_ml is None:
            continue
        hm, am = float(o.home_ml), float(o.away_ml)
        fav_n, dog_n, fml, dml = (g.home, g.away, hm, am) if hm < am else (g.away, g.home, am, hm)
        fa = _nfl_abbr(fav_n, T) if sport == "nfl" else (fav_n if fav_n in T.index else None)
        da = _nfl_abbr(dog_n, T) if sport == "nfl" else (dog_n if dog_n in T.index else None)
        if not fa or not da:
            continue
        try:
            u, v = signals(sport, T, sched, fa, da, g.start)
        except Exception:  # noqa: BLE001
            continue
        base = dict(sport=sport, gid=str(g.id), date=g.start.isoformat(), game=f"{g.away} @ {g.home}", fav=fav_n, dog=dog_n,
                    fav_ml=fml, dog_ml=dml, effect="none (tracking only)")
        if u and fml <= FAV_MAX:
            out.append({**base, "tag": UFP_LABEL, "team": fav_n, "price": fml, "signals": u})
        if v and dml >= DOG_MIN:
            out.append({**base, "tag": UVF_LABEL, "team": dog_n, "price": dml, "signals": v})
    return me + out


def _model_edge_tags(sport, preds) -> list[dict]:
    """Model-edge dogs (+120 or longer, uvf_edge >= 3%) from Master picks. Adopted (uvf_dog) = WNBA half-stake paper bet;
    everything else is tracking only. No % is printed (the edge stays in the ledger only)."""
    out = []
    for p in preds:
        g = p.game
        for pk in getattr(p, "master_picks", None) or []:
            e = pk.get("uvf_edge")
            if pk.get("market") != "ml" or e is None or e < 0.03 or (pk.get("price") or 0) < 120:
                continue
            bet = bool(pk.get("uvf_dog"))
            out.append(dict(sport=sport, gid=str(g.id), date=g.start.isoformat(), game=f"{g.away} @ {g.home}", team=pk["side"],
                            price=float(pk["price"]), tag=UVF_LABEL, kind="model_edge", edge=e, bet=bet,
                            stake=float(pk.get("uf_stake", 0.5)) if bet else 0.0,
                            effect="Master + demo feeds, half stake (paper)" if bet else "none (tracking only)",
                            signals=["model-edge dog · ½ stake paper bet" if bet else "model-edge dog (tracking)"]))
    return out


# ------------------------------------------------------------- ledger / A-B / record
def _ledger(state_dir) -> Path:
    d = Path(state_dir) / "upset_tags"; d.mkdir(exist_ok=True); return d / "tags.json"


def log_tags(state_dir, tags: list[dict], legs: list[dict]) -> int:
    """Append new tags (one per game+tag). A/B: on_card = a Marv leg backs the tagged team (B = what UFP-skip would remove)."""
    p = _ledger(state_dir)
    book = json.loads(p.read_text()) if p.exists() else []
    have = {(r["gid"], r["tag"], r.get("kind", "rule")) for r in book}
    n = 0
    for t in tags:
        if (t["gid"], t["tag"], t.get("kind", "rule")) in have:
            continue
        t = dict(t, logged=datetime.now(timezone.utc).isoformat(), result=None,
                 on_card=any(l.get("market") == "ml" and str(l.get("side")) == t["team"] for l in legs))
        book.append(t); n += 1
    p.write_text(json.dumps(book, indent=1))
    return n


def grade(state_dir, sport, finished) -> None:
    p = _ledger(state_dir)
    if not p.exists():
        return
    book = json.loads(p.read_text()); ch = False
    fin = {str(g.id): g for g in finished or [] if getattr(g, "completed", False) and g.home_score is not None}
    for r in book:
        g = fin.get(r["gid"])
        if r["sport"] != sport or r["result"] or not g:
            continue
        won = (g.home_score > g.away_score) if r["team"] == g.home else (g.away_score > g.home_score)
        if g.home_score == g.away_score:
            r["result"], r["profit"] = "push", 0.0
        else:
            pr = r["price"]; r["result"] = "win" if won else "loss"
            r["profit"] = (pr / 100 if pr > 0 else 100 / -pr) if won else -1.0
        ch = True
    if ch:
        p.write_text(json.dumps(book, indent=1))


def record_line(state_dir) -> str:
    p = _ledger(state_dir)
    book = json.loads(p.read_text()) if p.exists() else []
    out = []
    for tag, kind, nm in ((UVF_LABEL, "model_edge", "UVF model-edge dogs"), (UVF_LABEL, "rule", "UVF rule dogs"),
                          (UFP_LABEL, "rule", "UFP-flagged favs")):
        r = [x for x in book if x["tag"] == tag and x.get("kind", "rule") == kind and x.get("result") in ("win", "loss")]
        w = sum(x["result"] == "win" for x in r); u = sum(x.get("profit") or 0 for x in r)
        out.append(f"{nm} {w}-{len(r) - w} ({u:+.1f}u at 1u ML, n={len(r)})")
    return "Upset tags: " + " · ".join(out)


def card_lines(tags: list[dict]) -> list[str]:
    if not tags:
        return []
    L = ["", "━━ 🚨 UPSET TAGS (tracking unless marked ½ stake paper) ━━"]
    for t in tags:
        icon = "⚠️" if t["tag"] == UFP_LABEL else "🐶"
        L.append(f" {icon} {t['tag']} · {t['sport'].upper()} {t['team']} ML {t['price']:+.0f} · {t['game']}")
        L.append(f"    {', '.join(t['signals'])}")
    return L
