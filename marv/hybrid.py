"""Hybrid engine for Marv Predict (NFL + college football): trend catcher -> full-spectrum H2H matrix -> MC totals.

Built from a basketball design and mapped onto football data Marv already caches (no network, overlay-safe):
  CFB: state/cache/cfb_teamgames_<season>.parquet (CFBD/ESPN box + play-by-play: drives, plays, EPA, success,
       explosive rate, turnovers) joined to cfb_games_<season>.parquet (points, date, neutral, spread, total)
  NFL: state/cache/nfl_stats_team_week_<season>.csv (nflverse team box: EPA, yards, plays, turnovers, 20+ yd plays,
       first downs) joined to nflverse_games.csv (points, date, spread_line, total_line)

Basketball -> football mapping (one row per category, winner-take-all):
  off rating     -> points per drive (CFB) / points per play (NFL)              higher wins
  def rating     -> points per drive / per play ALLOWED                          lower wins
  net EPA        -> EPA/play minus EPA/play allowed                              higher wins
  eFG%           -> yards per play                                               higher wins
  TS%            -> success rate (CFB) / first-down rate per play (NFL)          higher wins
  TOV rate       -> turnovers per drive (CFB) / per 100 plays (NFL)              lower wins
  OREB%          -> explosive-play rate (CFB) / 20+ yd plays per play (NFL)      higher wins (extra-value possessions)
  reb margin     -> yards-per-play margin (ypp gained minus ypp allowed)         higher wins (possession battle)
  pace           -> plays per game; direction learned from earlier seasons (0 = no point) -- totals use it either way

1) trend_catcher_modifier: last 3 games vs season-to-date. A turnover spike (TOV rate up) and a "rebound-margin"
   drop (ypp margin down) each shave the team's ratings; modifier = 1 - TOV_K*spike - REB_K*drop, floor 0.80.
   Scaled: off rating x mod, def rating (allowed) / mod, and every category the team wins is worth mod points.
2) full_spectrum_h2h_matrix: category points per team (sum of mods over categories won). ML side = more points
   (tie -> net EPA). ATS: margin = HFA + K * point diff (K/HFA fit on earlier seasons only) vs the spread.
3) restricted_monte_carlo_totals: Monte Carlo ONLY on pace (possessions/plays) and scoring efficiency (points per
   possession) -> median / p25 / p75 game total. The side never uses Monte Carlo.

Backtest (Oct 9 2026, walk-forward, closing lines @-110, state/reports/hybrid_backtest.json): NOTHING passes the
proven bar. CFB 2019-26: ML pick 67.3% vs market favourite 73.9%; ATS 50.2% of 5,694 (ROI -4.2%), edge 3+ 49.8%;
MC O/U 51.0% of 5,565 (-2.7%); MC p25-p75 band holds 49% of totals (well calibrated). Upset rule (heavy fav spread
-6.5+ that loses the matrix or has trend mod <= 0.90): outright upsets 20.1% of 567 vs 16.9% for all heavy favs,
dog ATS 51.2% (-2.3%). NFL 2021-26: ML 62.1% vs market 67.4%; ATS 48.3%; edge 7+ 53.3% of 122 (+1.7%) but only
2 of 5 seasons above break-even; O/U 49.1%. So it prints no %, ever, until a rule clears the bar.
Descriptive unless the walk-forward backtest (tools/hybrid_bt.py) clears marv/proven.py's bar; the overlay /
Upset Alert only add FOR/AGAINST factors. Never raises into callers.
"""

import math
import time
from pathlib import Path

import numpy as np

TOV_K = {"cfb": 0.5, "nfl": 0.02}   # per +1 turnover/drive (CFB) or per +1 TO/100 plays (NFL)
REB_K = 0.05                         # per -1.0 yard-per-play margin
MOD_FLOOR = 0.80
MIN_GAMES = 3
# category points -> margin and pace direction for live reads: the latest walk-forward fit in tools/hybrid_bt.py
# (CFB fit on 2018-25: K 1.77, HFA 3.5, faster pace wins 5.2% more often; NFL fit on 2020-25: K 0.92, HFA 2.0)
DEFAULT_K = {"cfb": 1.77, "nfl": 0.92}
DEFAULT_HFA = {"cfb": 3.5, "nfl": 2.0}
PACE_DIR = {"cfb": 1, "nfl": 1}      # +1 = more plays per game wins the pace category; 0 = no side point
WEAK_MOD = 0.90                      # trend modifier at/below this = "trending down" factor
CATS = ("off", "def", "net_epa", "ypp", "succ", "tov", "expl", "yppm", "pace")
LOWER_BETTER = {"def", "tov"}
LABEL = {"off": "off rating", "def": "def rating", "net_epa": "net EPA", "ypp": "yds/play", "succ": "success rate",
         "tov": "TO rate", "expl": "explosive rate", "yppm": "ypp margin", "pace": "pace"}
_CACHE: dict = {}
_TTL = 1800


# ------------------------------------------------------------------ team-game tables
def cfb_team_games(cache: Path, seasons) -> "pd.DataFrame":
    import pandas as pd
    out = []
    for s in seasons:
        gf, tf = cache / f"cfb_games_{s}.parquet", cache / f"cfb_teamgames_{s}.parquet"
        if not gf.exists() or not tf.exists():
            continue
        g = pd.read_parquet(gf, columns=["game_id", "date", "home", "away", "neutral", "home_points", "away_points"])
        g = g[g.home_points.notna()]
        t = pd.read_parquet(tf, columns=["game_id", "team", "plays", "drives", "ypp", "epa_play", "success_rate",
                                         "explosive_rate", "turnovers"])
        t["game_id"] = t.game_id.astype(str)
        g["game_id"] = g.game_id.astype(str)
        rows = []
        for r in g.itertuples():
            rows.append((r.game_id, r.home, r.away, r.home_points, r.away_points, r.date, 1))
            rows.append((r.game_id, r.away, r.home, r.away_points, r.home_points, r.date, 0))
        gg = pd.DataFrame(rows, columns=["game_id", "team", "opp", "pts", "pa", "date", "is_home"])
        x = gg.merge(t, on=["game_id", "team"]).merge(
            t.rename(columns={"team": "opp", **{c: "o_" + c for c in t.columns if c not in ("game_id", "team")}}),
            on=["game_id", "opp"])
        x["season"] = s
        out.append(x)
    if not out:
        return pd.DataFrame()
    x = pd.concat(out, ignore_index=True)
    dr, odr = x.drives.clip(lower=1), x.o_drives.clip(lower=1)
    x["off"] = x.pts / dr
    x["def"] = x.pa / odr
    x["net_epa"] = x.epa_play - x.o_epa_play
    x["succ"] = x.success_rate
    x["tov"] = x.turnovers / dr
    x["expl"] = x.explosive_rate
    x["yppm"] = x.ypp - x.o_ypp
    x["pace"] = x.plays
    x["poss"] = x.drives + x.o_drives          # game possessions (MC pace)
    x["date"] = pd.to_datetime(x.date).dt.tz_localize(None)
    return x


def nfl_team_games(cache: Path, seasons) -> "pd.DataFrame":
    import pandas as pd
    from .data.nflverse import NFL_TEAMS
    g = pd.read_csv(cache / "nflverse_games.csv", usecols=["game_id", "season", "gameday", "home_team", "away_team",
                                                           "home_score", "away_score"])
    g = g[g.home_score.notna() & g.season.isin(list(seasons))]
    out = []
    for s in seasons:
        f = cache / f"nfl_stats_team_week_{s}.csv"
        if not f.exists():
            continue
        t = pd.read_csv(f)
        t = pd.DataFrame({
            "game_id": t.game_id, "team": t.team,
            "plays": t.attempts + t.carries + t.sacks_suffered,
            "yards": t.passing_yards + t.rushing_yards,
            "epa": t.passing_epa.fillna(0) + t.rushing_epa.fillna(0),
            "fd": t.passing_first_downs + t.rushing_first_downs,
            "big": t.passing_20 + t.rushing_20,
            "turnovers": t.passing_interceptions + t.sack_fumbles_lost + t.rushing_fumbles_lost + t.receiving_fumbles_lost,
        })
        out.append(t)
    if not out:
        return pd.DataFrame()
    t = pd.concat(out, ignore_index=True)
    rows = []
    for r in g.itertuples():
        rows.append((r.game_id, r.home_team, r.away_team, r.home_score, r.away_score, r.gameday, r.season, 1))
        rows.append((r.game_id, r.away_team, r.home_team, r.away_score, r.home_score, r.gameday, r.season, 0))
    gg = pd.DataFrame(rows, columns=["game_id", "team", "opp", "pts", "pa", "date", "season", "is_home"])
    x = gg.merge(t, on=["game_id", "team"]).merge(
        t.rename(columns={"team": "opp", **{c: "o_" + c for c in t.columns if c not in ("game_id", "team")}}),
        on=["game_id", "opp"])
    pl, opl = x.plays.clip(lower=1), x.o_plays.clip(lower=1)
    x["off"] = x.pts / pl
    x["def"] = x.pa / opl
    x["net_epa"] = x.epa / pl - x.o_epa / opl
    x["ypp"] = x.yards / pl
    x["succ"] = x.fd / pl
    x["tov"] = 100 * x.turnovers / pl
    x["expl"] = x.big / pl
    x["yppm"] = x.ypp - x.o_yards / opl
    x["pace"] = x.plays
    x["poss"] = x.plays + x.o_plays             # game plays (MC pace; scoring = points per play)
    x["team"] = x.team.map(lambda a: NFL_TEAMS.get(a, a))
    x["opp"] = x.opp.map(lambda a: NFL_TEAMS.get(a, a))
    x["date"] = pd.to_datetime(x.date)
    return x


# ------------------------------------------------------------------ point-in-time team profiles
def profile_from_rows(rows) -> dict | None:
    """Season-to-date + last-3 profile from one team's completed games (a DataFrame sorted by date)."""
    if len(rows) < MIN_GAMES:
        return None
    s, l3 = rows, rows.tail(3)
    p = {"n": len(rows)}
    for c in CATS:
        p[c] = float(s[c].mean())
        p[c + "3"] = float(l3[c].mean())
    p["poss"] = float(s.poss.mean())
    p["poss_sd"] = float(s.poss.std(ddof=1)) if len(s) > 1 else 0.0
    p["off_sd"] = float(s["off"].std(ddof=1)) if len(s) > 1 else 0.0
    p["def_sd"] = float(s["def"].std(ddof=1)) if len(s) > 1 else 0.0
    return p


def trend_catcher_modifier(p: dict, sport: str) -> tuple[float, list[str]]:
    """(modifier in [0.80, 1.0], reasons). Last-3 TOV spike + ypp-margin ('rebound margin') drop vs season."""
    spike = max(0.0, p["tov3"] - p["tov"])
    drop = max(0.0, p["yppm"] - p["yppm3"])
    mod = max(MOD_FLOOR, 1.0 - TOV_K.get(sport, 0.5) * spike - REB_K * drop)
    why = []
    unit = "/drive" if sport == "cfb" else "/100 plays"
    if TOV_K.get(sport, 0.5) * spike >= 0.03:
        why.append(f"TO spike {p['tov3']:.2f} vs {p['tov']:.2f}{unit} last 3")
    if REB_K * drop >= 0.03:
        why.append(f"ypp margin {p['yppm3']:+.1f} last 3 vs {p['yppm']:+.1f}")
    return round(mod, 3), why


def full_spectrum_h2h_matrix(h: dict, a: dict, sport: str, mh: float = 1.0, ma: float = 1.0,
                             pace_dir: int | None = None) -> dict:
    """Winner-take-all category points with trend mods. Ratings (off/def) are scaled by the modifier first."""
    pace_dir = PACE_DIR.get(sport, 0) if pace_dir is None else pace_dir
    hv = {c: h[c] for c in CATS}
    av = {c: a[c] for c in CATS}
    hv["off"], av["off"] = h["off"] * mh, a["off"] * ma
    hv["def"], av["def"] = h["def"] / mh, a["def"] / ma
    won = {"home": [], "away": []}
    hp = ap = 0.0
    for c in CATS:
        d = hv[c] - av[c]
        if c in LOWER_BETTER:
            d = -d
        if c == "pace":
            d *= pace_dir
        if not d or d != d:
            continue
        if d > 0:
            hp += mh
            won["home"].append(c)
        else:
            ap += ma
            won["away"].append(c)
    diff = hp - ap
    ml = "home" if diff > 0 else "away" if diff < 0 else ("home" if hv["net_epa"] >= av["net_epa"] else "away")
    return {"home_pts": round(hp, 2), "away_pts": round(ap, 2), "diff": round(diff, 2), "ml": ml, "won": won}


def restricted_monte_carlo_totals(h: dict, a: dict, sport: str, mh: float = 1.0, ma: float = 1.0, n: int = 4000,
                                  seed: int = 0) -> dict:
    """Monte Carlo ONLY over pace (possessions / plays) and points-per-possession variance -> total quantiles."""
    rng = np.random.default_rng(seed)
    pace = (h["poss"] + a["poss"]) / 2
    pace_sd = max(math.sqrt((h["poss_sd"] ** 2 + a["poss_sd"] ** 2) / 2), 0.08 * pace)
    mu_h = (h["off"] * mh + a["def"] / ma) / 2
    mu_a = (a["off"] * ma + h["def"] / mh) / 2
    sd_h = max(math.sqrt((h["off_sd"] ** 2 + a["def_sd"] ** 2) / 2), 0.15 * mu_h) / math.sqrt(2)
    sd_a = max(math.sqrt((a["off_sd"] ** 2 + h["def_sd"] ** 2) / 2), 0.15 * mu_a) / math.sqrt(2)
    P = np.clip(rng.normal(pace, pace_sd, n), 0.5 * pace, None)
    eh = np.clip(rng.normal(mu_h, sd_h, n), 0, None)
    ea = np.clip(rng.normal(mu_a, sd_a, n), 0, None)
    tot = P / 2 * (eh + ea)
    q25, q50, q75 = np.percentile(tot, [25, 50, 75])
    return {"median": round(float(q50), 1), "p25": round(float(q25), 1), "p75": round(float(q75), 1),
            "pace": round(pace, 1)}


def ats_margin(diff: float, sport: str, neutral: bool = False, k: float | None = None, hfa: float | None = None) -> float:
    k = DEFAULT_K.get(sport, 2.0) if k is None else k
    hfa = DEFAULT_HFA.get(sport, 2.0) if hfa is None else hfa
    return (0.0 if neutral else hfa) + k * diff


def analyze(h: dict, a: dict, sport: str, spread: float | None = None, total: float | None = None,
            neutral: bool = False, k: float | None = None, hfa: float | None = None, pace_dir: int | None = None,
            mc_n: int = 4000, seed: int = 0) -> dict:
    """Full hybrid read for one matchup (h/a = profiles). spread = HOME spread (negative = home favoured)."""
    mh, wh = trend_catcher_modifier(h, sport)
    ma, wa = trend_catcher_modifier(a, sport)
    mx = full_spectrum_h2h_matrix(h, a, sport, mh, ma, pace_dir)
    proj = ats_margin(mx["diff"], sport, neutral, k, hfa)
    ats = None
    if spread is not None and spread == spread and proj + spread != 0:
        ats = "home" if proj + spread > 0 else "away"
    mc = restricted_monte_carlo_totals(h, a, sport, mh, ma, n=mc_n, seed=seed)
    ou = None
    if total is not None and total == total and mc["median"] != total:
        ou = "over" if mc["median"] > total else "under"
    return {"mod_home": mh, "mod_away": ma, "why_home": wh, "why_away": wa, **mx, "proj_margin": round(proj, 1),
            "ats": ats, "spread": spread, "mc": mc, "total": total, "ou": ou}


# ------------------------------------------------------------------ live (overlay / Upset Alert)
def league(state_dir: Path, sport: str, as_of=None) -> dict:
    """{team: profile} for the current season, from cached box scores (in-memory cache, 30 min)."""
    from datetime import datetime, timezone
    as_of = as_of or datetime.now(timezone.utc)
    key = (str(state_dir), sport, as_of.strftime("%Y-%m-%d"))
    hit = _CACHE.get(key)
    if hit and time.time() - hit[0] < _TTL:
        return hit[1]
    cache = Path(state_dir) / "cache"
    prof = {}
    try:
        if sport == "cfb":
            season = as_of.year if as_of.month >= 7 else as_of.year - 1
            tg = cfb_team_games(cache, [season])
        elif sport == "nfl":
            season = as_of.year if as_of.month >= 8 else as_of.year - 1
            tg = nfl_team_games(cache, [season])
        else:
            tg = None
        if tg is not None and not tg.empty:
            cut = as_of.replace(tzinfo=None) if as_of.tzinfo else as_of
            tg = tg[tg.date < cut.strftime("%Y-%m-%d")].sort_values("date")
            for t, rows in tg.groupby("team"):
                p = profile_from_rows(rows)
                if p:
                    prof[t] = p
    except Exception:  # noqa: BLE001 - never break the overlay
        prof = {}
    _CACHE[key] = (time.time(), prof)
    return prof


def team_profile(state_dir: Path, sport: str, team: str, as_of=None) -> dict | None:
    from .data.teams import similarity
    prof = league(state_dir, sport, as_of)
    if not prof:
        return None
    if team in prof:
        return prof[team]
    best = max(prof, key=lambda t: similarity(t, team))
    return prof[best] if similarity(best, team) >= 0.75 else None


def game(state_dir: Path, sport: str, home: str, away: str, spread: float | None = None, total: float | None = None,
         neutral: bool = False, as_of=None) -> dict | None:
    """Hybrid read for a live game from team names, or None (missing data). Never raises."""
    try:
        h, a = team_profile(state_dir, sport, home, as_of), team_profile(state_dir, sport, away, as_of)
        if not h or not a:
            return None
        return analyze(h, a, sport, spread, total, neutral)
    except Exception:  # noqa: BLE001
        return None


def text(r: dict, home: str, away: str, sport: str) -> str:
    """One line: 'Hybrid: FSU 6-3 categories (mods 1.00/0.86) · ATS FSU · MC total 57 (p25 51-p75 63)'."""
    sh = (lambda s: s.split()[-1] if sport == "nfl" and len(s.split()) > 1 else s)
    lead = home if r["ml"] == "home" else away
    hp, ap = (r["home_pts"], r["away_pts"]) if r["ml"] == "home" else (r["away_pts"], r["home_pts"])
    out = f"Hybrid: {sh(lead)} {hp:g}-{ap:g} categories"
    if r["mod_home"] < 1 or r["mod_away"] < 1:
        out += f" (trend mods {sh(home)} {r['mod_home']:.2f} / {sh(away)} {r['mod_away']:.2f})"
    if r.get("ats"):
        out += f" · ATS {sh(home if r['ats'] == 'home' else away)} (proj {sh(home)} {r['proj_margin']:+.1f} vs {r['spread']:+g})"
    mc = r["mc"]
    out += f" · MC total {mc['median']:.0f} (p25 {mc['p25']:.0f}–p75 {mc['p75']:.0f})"
    return out
