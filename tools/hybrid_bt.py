"""Walk-forward backtest of the hybrid engine (marv/hybrid.py): trend catcher + full-spectrum H2H matrix + restricted
Monte Carlo totals, NFL and college football, at closing lines (-110; no historical ML prices in the cache).

Point-in-time: every game sees only its teams' earlier games THAT season (>= 3). Pace direction and the
category-points -> margin mapping (K, HFA) are fit on earlier seasons only. Nothing is tuned on the test season.
Proven bar (marv/proven.py): n >= 100, ROI > 0 after -110 vig, and above 52.4% in most test seasons.
Usage: nice /opt/marv-bot/.venv/bin/python tools/hybrid_bt.py [cfb|nfl|all] [OUTDIR]
"""
import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from marv import hybrid as H  # noqa: E402

CACHE = Path("/opt/marv-bot/state/cache")
BE = 0.5238
HEAVY = {"cfb": -6.5, "nfl": -4.5}


def features(tg: pd.DataFrame) -> pd.DataFrame:
    tg = tg.sort_values(["team", "season", "date"]).copy()
    g = tg.groupby(["team", "season"], sort=False)
    f = tg[["game_id", "team", "season", "date"]].copy()
    f["n"] = g.cumcount()
    for c in list(H.CATS) + ["poss"]:
        sh = g[c].shift(1)
        f[c] = sh.groupby([tg.team, tg.season]).expanding().mean().reset_index(level=[0, 1], drop=True)
        if c in H.CATS:
            f[c + "3"] = sh.groupby([tg.team, tg.season]).rolling(3, min_periods=1).mean().reset_index(level=[0, 1], drop=True)
    for c, out in (("poss", "poss_sd"), ("off", "off_sd"), ("def", "def_sd")):
        sh = g[c].shift(1)
        f[out] = sh.groupby([tg.team, tg.season]).expanding().std().reset_index(level=[0, 1], drop=True).fillna(0)
    return f


def games(sport: str) -> pd.DataFrame:
    if sport == "cfb":
        seasons = list(range(2018, 2027))
        tg = H.cfb_team_games(CACHE, seasons)
        gm = pd.concat([pd.read_parquet(CACHE / f"cfb_games_{s}.parquet") for s in seasons])
        gm = gm[gm.home_points.notna()]
        gm = gm.assign(game_id=gm.game_id.astype(str), neutral=gm.neutral.fillna(False).astype(bool))
        gm = gm[["game_id", "season", "home", "away", "neutral", "home_points", "away_points", "spread", "total"]]
    else:
        from marv.data.nflverse import NFL_TEAMS
        seasons = list(range(2020, 2027))
        tg = H.nfl_team_games(CACHE, seasons)
        g = pd.read_csv(CACHE / "nflverse_games.csv")
        g = g[g.home_score.notna() & g.season.isin(seasons)]
        gm = pd.DataFrame({"game_id": g.game_id, "season": g.season, "home": g.home_team.map(lambda a: NFL_TEAMS.get(a, a)),
                           "away": g.away_team.map(lambda a: NFL_TEAMS.get(a, a)), "neutral": g.location.eq("Neutral"),
                           "home_points": g.home_score, "away_points": g.away_score,
                           "spread": -g.spread_line, "total": g.total_line})
    f = features(tg)
    f = f[f.n >= H.MIN_GAMES]
    h = f.add_prefix("h_").rename(columns={"h_game_id": "game_id", "h_team": "home"})
    a = f.add_prefix("a_").rename(columns={"a_game_id": "game_id", "a_team": "away"})
    m = gm.merge(h.drop(columns=["h_season", "h_date"]), on=["game_id", "home"]).merge(
        a.drop(columns=["a_season", "a_date"]), on=["game_id", "away"])
    return m


def prof(r, p):
    return {c: r[f"{p}{c}"] for c in [*H.CATS, *[x + "3" for x in H.CATS], "poss", "poss_sd", "off_sd", "def_sd", "n"]}


def run(sport: str, out: Path) -> dict:
    m = games(sport)
    m["margin"] = m.home_points - m.away_points
    m["tot"] = m.home_points + m.away_points
    seasons = sorted(m.season.unique())
    res = []
    fits = {}
    for s in seasons[1:]:
        train, test = m[m.season < s], m[m.season == s]
        if train.empty or test.empty:
            continue
        pdiff = np.sign(train.h_pace - train.a_pace)
        agree = float(np.mean(pdiff * np.sign(train.margin)))
        pace_dir = int(np.sign(agree)) if abs(agree) > 0.02 else 0
        diffs = []
        for _, r in train.iterrows():
            h, a = prof(r, "h_"), prof(r, "a_")
            mh, _ = H.trend_catcher_modifier(h, sport)
            ma, _ = H.trend_catcher_modifier(a, sport)
            diffs.append(H.full_spectrum_h2h_matrix(h, a, sport, mh, ma, pace_dir)["diff"])
        X = np.column_stack([(~train.neutral.astype(bool)).astype(float), np.array(diffs)])
        hfa, k = np.linalg.lstsq(X, train.margin.to_numpy(), rcond=None)[0]
        fits[int(s)] = {"pace_dir": pace_dir, "pace_agree": round(agree, 3), "K": round(float(k), 3), "HFA": round(float(hfa), 2)}
        for _, r in test.iterrows():
            h, a = prof(r, "h_"), prof(r, "a_")
            sp = r.spread if pd.notna(r.spread) else None
            tl = r.total if pd.notna(r.total) else None
            x = H.analyze(h, a, sport, sp, tl, bool(r.neutral), k, hfa, pace_dir, mc_n=1500, seed=int(s))
            res.append({"game_id": r.game_id, "season": int(s), "home": r.home, "away": r.away, "neutral": bool(r.neutral),
                        "margin": r.margin, "tot": r.tot, "spread": sp, "total": tl, "diff": x["diff"], "ml": x["ml"],
                        "proj": x["proj_margin"], "ats": x["ats"], "mod_h": x["mod_home"], "mod_a": x["mod_away"],
                        "mc_med": x["mc"]["median"], "mc_p25": x["mc"]["p25"], "mc_p75": x["mc"]["p75"], "ou": x["ou"]})
    df = pd.DataFrame(res)
    df.to_csv(out / f"hybrid_{sport}_games.csv", index=False)
    return {"fits": fits, **grade(df, sport)}


def _rate(win, n):
    return {"n": int(n), "hit": round(win / n, 4) if n else None, "roi": round((win * 100 / 110 - (n - win)) / n, 4) if n else None}


def _by_season(df, ok_col, mask):
    rows = {}
    for s, d in df[mask].groupby("season"):
        rows[int(s)] = _rate(int(d[ok_col].sum()), len(d))
    return rows


def grade(df: pd.DataFrame, sport: str) -> dict:
    r = {}
    home_win = df.margin > 0
    dec = df.margin != 0
    ml_ok = (df.ml == "home") == home_win
    r["ml_all"] = {"n": int(dec.sum()), "hit": round(float(ml_ok[dec].mean()), 4)}
    has = df.spread.notna()
    fav_ok = ((df.spread < 0) == home_win)
    r["market_fav_ml"] = {"n": int((dec & has & (df.spread != 0)).sum()),
                          "hit": round(float(fav_ok[dec & has & (df.spread != 0)].mean()), 4)}
    r["ml_same_games"] = round(float(ml_ok[dec & has & (df.spread != 0)].mean()), 4)
    ats_res = df.margin + df.spread
    df = df.assign(ats_ok=((df.ats == "home") == (ats_res > 0)), ats_push=(ats_res == 0))
    edge = (df.proj + df.spread).abs()
    for lo in (0, 3, 7):
        mk = df.ats.notna() & ~df.ats_push & (edge >= lo)
        r[f"ats_edge{lo}"] = {**_rate(int(df.ats_ok[mk].sum()), int(mk.sum())), "seasons": _by_season(df, "ats_ok", mk)}
    ou_res = df.tot - df.total
    df = df.assign(ou_ok=((df.ou == "over") == (ou_res > 0)), ou_push=(ou_res == 0))
    gap = (df.mc_med - df.total).abs()
    for lo in (0, 3, 7):
        mk = df.ou.notna() & ~df.ou_push & (gap >= lo)
        r[f"ou_gap{lo}"] = {**_rate(int(df.ou_ok[mk].sum()), int(mk.sum())), "seasons": _by_season(df, "ou_ok", mk)}
    inside = df.total.notna() & (df.tot >= df.mc_p25) & (df.tot <= df.mc_p75)
    r["mc_iqr_coverage"] = round(float(inside[df.total.notna()].mean()), 4)
    r["mc_mae"] = round(float((df.mc_med - df.tot).abs()[df.total.notna()].mean()), 2)
    r["line_mae"] = round(float((df.total - df.tot).abs()[df.total.notna()].mean()), 2)
    # Upset Alert rule: heavy favourite (spread) whose category matrix does NOT back it or whose trend mod <= 0.90
    hv = HEAVY[sport]
    fav_home = df.spread <= hv
    fav_away = df.spread >= -hv
    heavy = has & (fav_home | fav_away)
    fav_diff = np.where(fav_home, df["diff"], -df["diff"])
    fav_mod = np.where(fav_home, df.mod_h, df.mod_a)
    weak = heavy & ((fav_diff <= 0) | (fav_mod <= H.WEAK_MOD))
    upset = np.where(fav_home, df.margin < 0, df.margin > 0)
    dog_cover = np.where(fav_home, ats_res > 0, ats_res < 0)  # True = favourite covered
    dog_cover = ~dog_cover & (ats_res != 0)
    nopush = ats_res != 0
    r["heavy_fav_base"] = {"n": int(heavy.sum()), "upset_rate": round(float(upset[heavy].mean()), 4),
                           "dog_ats": _rate(int(dog_cover[heavy & nopush].sum()), int((heavy & nopush).sum()))}
    r["upset_rule"] = {"n": int(weak.sum()), "upset_rate": round(float(upset[weak].mean()), 4) if weak.any() else None,
                       "dog_ats": _rate(int(dog_cover[weak & nopush].sum()), int((weak & nopush).sum())),
                       "seasons": {int(s): _rate(int(dog_cover[(df.season == s) & weak & nopush].sum()),
                                                 int(((df.season == s) & weak & nopush).sum())) for s in sorted(df.season.unique())}}
    return r


def passes(g: dict) -> bool:
    if not g or g.get("n", 0) < 100 or (g.get("roi") or -1) <= 0:
        return False
    ss = [v for v in g.get("seasons", {}).values() if v["n"] >= 10]
    return bool(ss) and sum(v["hit"] > BE for v in ss) > len(ss) / 2


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "all"
    out = Path(sys.argv[2] if len(sys.argv) > 2 else "/opt/marv-bot/state/reports")
    out.mkdir(parents=True, exist_ok=True)
    rep = {}
    for sp in (["cfb", "nfl"] if which == "all" else [which]):
        g = run(sp, out)
        g["proven"] = {k: passes(v) for k, v in g.items() if k.startswith(("ats_", "ou_")) or k == "upset_rule"
                       for v in [v if k != "upset_rule" else v["dog_ats"] | {"seasons": v["seasons"]}]}
        rep[sp] = g
    (out / "hybrid_backtest.json").write_text(json.dumps(rep, indent=1, default=str))
    print(json.dumps(rep, indent=1, default=str))
