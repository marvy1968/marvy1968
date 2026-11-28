"""Conference power ranks with champion references, bottom-3 lists, head-coach and injury adjustments.

Standalone: reads CSVs (or ESPN on the VM via --fetch), changes nothing in marv/. College football by default, but any
league with teams, conferences and games works (college basketball, NFL divisions as "conference").

Inputs
  games.csv     season,date,home,away,hg,ag[,neutral]
  teams.csv     season,team,conference          (conference as of that season)
  champions.csv season,kind,team[,conference]   kind = national | conference  (conference rows optional, see --derive)
  coaches.csv   team,coach,career_w,career_l    (optional)
  injuries.csv  team,player,position,status     (optional; status Out/Doubtful/Questionable)

Rating = opponent-adjusted margin (Massey/ridge, capped margins, within-season time decay, home field).
  power = rating + coach_pts + injury_pts   (coach weight is FITTED on history and applied only if significant)
Reference rows: average rating of the last N national champions (in their title seasons), the same per conference
(last N conference champions) and the average of each conference's bottom 3 teams this season.
"""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
import pandas as pd

MARGIN_CAP = 28.0
HALF_LIFE_DAYS = 70.0
RIDGE = 1.5
COACH_PRIOR_GAMES = 25.0     # pseudo-games at a .500 record pulling a coach's win% to average
POS_PTS = {"QB": 6.0, "RB": 1.0, "WR": 0.8, "TE": 0.5, "OL": 0.7, "DL": 0.8, "EDGE": 0.8, "LB": 0.6, "DB": 0.6,
           "CB": 0.6, "S": 0.5, "K": 0.3, "P": 0.2}  # heuristic points per starter lost (NOT validated)
STATUS_F = {"out": 1.0, "doubtful": 0.75, "questionable": 0.25}


# ------------------------------------------------------------------------------------------------ ratings
def massey(games: pd.DataFrame, asof: pd.Timestamp | None = None, return_hfa: bool = False):
    """Ridge least-squares margin rating (points above an average team), home-field fitted jointly.
    return_hfa=True -> (ratings, home_field_points)."""
    g = games.dropna(subset=["hg", "ag"]).copy()
    if g.empty:
        return (pd.Series(dtype=float), 0.0) if return_hfa else pd.Series(dtype=float)
    teams = sorted(set(g["home"]) | set(g["away"]))
    ix = {t: i for i, t in enumerate(teams)}
    n = len(teams)
    asof = asof or g["date"].max()
    X = np.zeros((len(g), n + 1))
    for k, (h, a, neu) in enumerate(zip(g["home"], g["away"], g.get("neutral", pd.Series(0, index=g.index)).fillna(0))):
        X[k, ix[h]], X[k, ix[a]] = 1.0, -1.0
        X[k, n] = 0.0 if neu else 1.0
    y = np.clip(g["hg"].to_numpy(float) - g["ag"].to_numpy(float), -MARGIN_CAP, MARGIN_CAP)
    age = (asof - g["date"]).dt.days.to_numpy(float).clip(min=0)
    w = np.sqrt(0.5 ** (age / HALF_LIFE_DAYS))
    A = np.vstack([X * w[:, None], np.hstack([np.eye(n) * math.sqrt(RIDGE), np.zeros((n, 1))])])
    b = np.concatenate([y * w, np.zeros(n)])
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    r = pd.Series(sol[:n], index=teams)
    r = r - r.mean()
    return (r, float(sol[n])) if return_hfa else r


def season_ratings(games: pd.DataFrame) -> dict[int, pd.Series]:
    return {int(s): massey(g) for s, g in games.groupby("season")}


# ------------------------------------------------------------------------------------------------ adjustments
def coach_shrunk(coaches: pd.DataFrame) -> pd.Series:
    """Career win% shrunk toward .500, indexed by team (value 0 = average coach, +0.10 = 10 points of win%)."""
    c = coaches.copy()
    gp = c["career_w"] + c["career_l"]
    sh = (c["career_w"] + 0.5 * COACH_PRIOR_GAMES) / (gp + COACH_PRIOR_GAMES) - 0.5
    return pd.Series(sh.to_numpy(float), index=c["team"].to_numpy())


def fit_coach_weight(r_prev: pd.Series, r_next: pd.Series, coach: pd.Series) -> tuple[float, float]:
    """Slope (points per 1.0 of shrunk win%) of next-season rating on [prior rating, coach], and its t-stat."""
    idx = r_prev.index.intersection(r_next.index).intersection(coach.index)
    if len(idx) < 25:
        return 0.0, 0.0
    X = np.column_stack([np.ones(len(idx)), r_prev[idx], coach[idx]])
    y = r_next[idx].to_numpy()
    beta, res, *_ = np.linalg.lstsq(X, y, rcond=None)
    resid = y - X @ beta
    dof = max(len(idx) - 3, 1)
    cov = (resid @ resid / dof) * np.linalg.inv(X.T @ X)
    se = math.sqrt(max(cov[2, 2], 1e-12))
    return float(beta[2]), float(beta[2] / se)


def injury_points(inj: pd.DataFrame) -> pd.Series:
    if inj is None or inj.empty:
        return pd.Series(dtype=float)
    pts = [-POS_PTS.get(str(p).upper(), 0.4) * STATUS_F.get(str(s).lower(), 0.0)
           for p, s in zip(inj["position"], inj["status"])]
    return pd.Series(pts, index=inj["team"].to_numpy()).groupby(level=0).sum()


# ------------------------------------------------------------------------------------------------ champions
def derive_conference_champions(games: pd.DataFrame, teams: pd.DataFrame, ratings: dict[int, pd.Series]) -> pd.DataFrame:
    """Approximation: best conference win% (ties broken by rating). Replace with real champions.csv rows where known."""
    rows = []
    for s, g in games.groupby("season"):
        conf = teams[teams["season"] == s].set_index("team")["conference"]
        rec: dict = {}
        for h, a, hg, ag in zip(g["home"], g["away"], g["hg"], g["ag"]):
            if h in conf.index and a in conf.index and conf[h] == conf[a] and pd.notna(hg) and pd.notna(ag):
                for t, win in ((h, hg > ag), (a, ag > hg)):
                    w, n = rec.get(t, (0, 0)); rec[t] = (w + int(win), n + 1)
        df = pd.DataFrame([{"team": t, "conference": conf[t], "pct": w / n, "r": ratings[s].get(t, -99)}
                           for t, (w, n) in rec.items() if n >= 4])
        if df.empty:
            continue
        top = df.sort_values(["conference", "pct", "r"], ascending=[True, False, False]).groupby("conference").head(1)
        rows += [{"season": s, "kind": "conference", "team": r.team, "conference": r.conference} for r in top.itertuples()]
    return pd.DataFrame(rows)


# ------------------------------------------------------------------------------------------------ main build
def build(games, teams, champions, coaches=None, injuries=None, n_champs=3, bottom_n=3, derive=False):
    games = games.copy(); games["date"] = pd.to_datetime(games["date"])
    R = season_ratings(games)
    cur = max(R)
    champs = champions.copy()
    if derive or not (champs["kind"] == "conference").any():
        champs = pd.concat([champs, derive_conference_champions(games, teams, R)], ignore_index=True)
    # --- coach weight, fitted on history (prior season coach table is approximated by the current one)
    cw, ct = 0.0, 0.0
    cs = coach_shrunk(coaches) if coaches is not None and len(coaches) else pd.Series(dtype=float)
    seasons = sorted(R)
    if len(cs) and len(seasons) >= 2:
        cw, ct = fit_coach_weight(R[seasons[-2]], R[seasons[-1]], cs)
    coach_w = cw if (ct >= 2.5 and cw > 0) else 0.0  # only a significant POSITIVE effect is ever applied
    cur_t = teams[teams["season"] == cur].set_index("team")["conference"]
    base = pd.DataFrame({"team": R[cur].index, "rating": R[cur].to_numpy()})
    base["conference"] = base["team"].map(cur_t)
    base["coach_pct"] = base["team"].map(cs) if len(cs) else np.nan
    base["coach_pts"] = (base["coach_pct"].fillna(0.0) * coach_w)
    ip = injury_points(injuries)
    base["injury_pts"] = base["team"].map(ip).fillna(0.0) if len(ip) else 0.0
    base["power"] = base["rating"] + base["coach_pts"] + base["injury_pts"]
    base = base.dropna(subset=["conference"])
    # --- references
    def champ_ref(kind, conf=None):
        c = champs[champs["kind"] == kind]
        if conf is not None:
            c = c[c["conference"] == conf]
        c = c[c["season"] < cur].sort_values("season").tail(n_champs)  # completed seasons only
        vals = [R[int(s)].get(t, np.nan) for s, t in zip(c["season"], c["team"]) if int(s) in R]
        vals = [v for v in vals if pd.notna(v)]
        return (float(np.mean(vals)) if vals else np.nan), list(zip(c["season"], c["team"]))
    nat_ref, nat_list = champ_ref("national")
    conf_ref = {c: champ_ref("conference", c) for c in sorted(base["conference"].unique())}
    base["nat_rank"] = base["power"].rank(ascending=False, method="min").astype(int)
    base["conf_rank"] = base.groupby("conference")["power"].rank(ascending=False, method="min").astype(int)
    base["gap_vs_national_ref"] = base["power"] - nat_ref
    base["gap_vs_conf_ref"] = base.apply(lambda r: r["power"] - conf_ref[r["conference"]][0], axis=1)
    out = {"season": cur, "table": base.sort_values(["conference", "conf_rank"]).reset_index(drop=True),
           "national_ref": nat_ref, "national_list": nat_list, "conf_ref": conf_ref,
           "coach_weight": cw, "coach_t": ct, "coach_applied": coach_w != 0.0}
    bot = {}
    for c, g in base.groupby("conference"):
        b = g.nsmallest(bottom_n, "power")
        bot[c] = (float(b["power"].mean()), list(b["team"]))
    out["bottom"] = bot
    out["worst_overall"] = list(base.nsmallest(10, "power")[["team", "conference", "power"]].itertuples(index=False, name=None))
    return out


def format_report(o: dict) -> str:
    L = [f"POWER RANKS  season {o['season']}   (rating = points above an average team vs an average opponent)", ""]
    L.append(f"National champion reference (last {len(o['national_list'])}: "
             f"{', '.join(f'{s} {t}' for s, t in o['national_list'])}): {o['national_ref']:+.1f}")
    L.append(f"Coach factor: fitted {o['coach_weight']:+.1f} pts per 1.0 shrunk win%, t={o['coach_t']:.1f} -> "
             + ("APPLIED" if o["coach_applied"] else "not applied (needs t >= 2.5 and a positive slope, or no data)"))
    t = o["table"]
    for conf, g in t.groupby("conference"):
        cref, clist = o["conf_ref"][conf]
        bavg, bnames = o["bottom"][conf]
        L += ["", f"== {conf} ==  conf-champ ref (last {len(clist)}) {cref:+.1f}   bottom-3 avg {bavg:+.1f} ({', '.join(bnames)})"]
        L.append(f"{'#':>3} {'team':<24}{'power':>7}{'rating':>8}{'coach':>7}{'inj':>6}{'vs nat':>8}{'vs conf':>8}{'nat#':>6}")
        for r in g.itertuples():
            L.append(f"{r.conf_rank:>3} {r.team:<24}{r.power:>7.1f}{r.rating:>8.1f}{r.coach_pts:>7.1f}{r.injury_pts:>6.1f}"
                     f"{r.gap_vs_national_ref:>+8.1f}{r.gap_vs_conf_ref:>+8.1f}{r.nat_rank:>6}")
    L += ["", "Worst 10 overall: " + ", ".join(f"{a} ({b}, {c:+.1f})" for a, b, c in o["worst_overall"])]
    return "\n".join(L)


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dir", required=True, help="folder with games.csv teams.csv champions.csv [coaches.csv injuries.csv]")
    ap.add_argument("--n-champs", type=int, default=3)
    ap.add_argument("--derive-conf-champs", action="store_true", help="approximate conference champions by conf record")
    ap.add_argument("--out", default="power_ranks.csv")
    a = ap.parse_args()
    d = Path(a.dir)
    rd = lambda n: pd.read_csv(d / n) if (d / n).exists() else None
    games, teams, champs = rd("games.csv"), rd("teams.csv"), rd("champions.csv")
    if games is None or teams is None or champs is None:
        raise SystemExit("need games.csv, teams.csv and champions.csv in --dir")
    o = build(games, teams, champs, rd("coaches.csv"), rd("injuries.csv"), a.n_champs, derive=a.derive_conf_champs)
    o["table"].to_csv(a.out, index=False)
    print(format_report(o))


if __name__ == "__main__":
    main()
