"""Marv Soccer Predict Max engines: strength (power), H2H category matrix, trend, expected goals, Monte Carlo scoreline
simulation, market derivation, market comparison, veto stack and confidence. Pure functions over pandas / numpy;
no network, so the March_edge overlay (1.5 s timeout) can call project_match directly on saved ratings.

Projection contract (blueprint section 1): the projection is built from match data only and frozen before any odds are
read; evaluate_market() is the only place prices enter.
"""

import zlib
from dataclasses import asdict, dataclass, field
from math import lgamma

import numpy as np
import pandas as pd

MODEL_VERSION = "soccer-max-1.0"
MAX_GOALS = 10
CATS_FULL = ["gf", "ga", "xgf", "xga", "sf", "sa", "sotf", "sota", "cf", "ca"]
CATS_GOALS = ["gf", "ga"]
LOWER_BETTER = {"ga", "xga", "sa", "sota", "ca"}
CAT_LABEL = {"gf": "goals", "ga": "goals allowed", "xgf": "xG", "xga": "xG allowed", "sf": "shots", "sa": "shots allowed",
             "sotf": "on target", "sota": "on target allowed", "cf": "corners", "ca": "corners allowed"}


@dataclass
class Params:
    half_life_days: float = 240.0  # time decay of the power ratings
    goal_weight: float = 0.6  # strength measure = w * goals + (1 - w) * xG (real xG when published, else shots proxy)
    shrink_games: float = 4.0  # pseudo-games pulling each rating to its prior (1.0, or promoted-team prior)
    window_days: int = 730
    k_h2h: float = 0.06  # log-xG shift per unit of H2H category net (-1..1)
    k_trend: float = 0.03  # log-xG shift per goal of last-5 xG-margin trend (half to each side)
    rho: float = -0.06  # Dixon-Coles low-score dependence
    trend_games: int = 5
    prior_games: float = 5.0  # last season's per-game stats count as this many games in the H2H matrix
    promoted_att: float = 0.85
    promoted_def: float = 1.15
    n_sims: int = 20000

    @classmethod
    def from_dict(cls, d: dict | None) -> "Params":
        p = cls()
        for k, v in (d or {}).items():
            if hasattr(p, k):
                setattr(p, k, type(getattr(p, k))(v))
        return p


# ------------------------------------------------------------------ data layer helpers
def fit_proxy(df: pd.DataFrame) -> tuple[float, float] | None:
    """xG proxy from shots: goals ~ a * shots_on_target + b * off_target (least squares, no intercept)."""
    d = df[df["played"] & df["hst"].notna() & df["hs"].notna()]
    if len(d) < 200:
        return None
    sot = np.concatenate([d["hst"], d["ast"]])
    off = np.clip(np.concatenate([d["hs"] - d["hst"], d["as_"] - d["ast"]]), 0, None)
    g = np.concatenate([d["hg"], d["ag"]])
    X = np.column_stack([sot, off])
    coef, *_ = np.linalg.lstsq(X, g, rcond=None)
    a, b = float(max(coef[0], 0.05)), float(max(coef[1], 0.0))
    return a, b


def team_rows(df: pd.DataFrame, proxy: tuple[float, float] | None) -> pd.DataFrame:
    """One row per team per played match (for / against), with an xG measure (real xG, else shots proxy, else goals)."""
    d = df[df["played"]].copy()

    def xg(g, s, sot, real):
        est = pd.Series(np.nan, index=d.index)
        if proxy is not None:
            est = proxy[0] * sot + proxy[1] * np.clip(s - sot, 0, None)
        return real.fillna(est)

    d["hxg_m"] = xg(d["hg"], d["hs"], d["hst"], d["hxg"])
    d["axg_m"] = xg(d["ag"], d["as_"], d["ast"], d["axg"])
    cols = dict(date=d["date"], season=d["season"])
    h = pd.DataFrame({**cols, "team": d["home"], "opp": d["away"], "home": 1, "gf": d["hg"], "ga": d["ag"],
                      "xgf": d["hxg_m"], "xga": d["axg_m"], "sf": d["hs"], "sa": d["as_"], "sotf": d["hst"],
                      "sota": d["ast"], "cf": d["hc"], "ca": d["ac"]})
    a = pd.DataFrame({**cols, "team": d["away"], "opp": d["home"], "home": 0, "gf": d["ag"], "ga": d["hg"],
                      "xgf": d["axg_m"], "xga": d["hxg_m"], "sf": d["as_"], "sa": d["hs"], "sotf": d["ast"],
                      "sota": d["hst"], "cf": d["ac"], "ca": d["hc"]})
    return pd.concat([h, a]).sort_values("date", kind="stable").reset_index(drop=True)


def _measure(gf, xgf, w):
    return np.where(np.isnan(xgf), gf, w * gf + (1 - w) * xgf)


# ------------------------------------------------------------------ strength engine (power)
def fit_ratings(df: pd.DataFrame, asof: pd.Timestamp, p: Params, proxy=None, rows: pd.DataFrame | None = None) -> dict:
    """Opponent-adjusted attack / defence ratings as of `asof` (matches strictly before it), plus the H2H category
    table and last-5 trend for every team. Multiplicative: E[goals] = mu_venue * att[team] * def[opp]."""
    d = df[df["played"] & (df["date"] < asof) & (df["date"] >= asof - pd.Timedelta(days=p.window_days))]
    if len(d) < 30:
        return {}
    season = int(df.loc[df["date"] < asof, "season"].max()) if (df["date"] < asof).any() else None
    cur_season = int(df.loc[df["date"] >= asof, "season"].min()) if (df["date"] >= asof).any() else season
    rows = team_rows(df, proxy) if rows is None else rows
    r = rows[(rows["date"] < asof)]

    # measure per match (home / away)
    hx = d["hxg"].fillna(proxy[0] * d["hst"] + proxy[1] * np.clip(d["hs"] - d["hst"], 0, None)) if proxy else d["hxg"]
    ax = d["axg"].fillna(proxy[0] * d["ast"] + proxy[1] * np.clip(d["as_"] - d["ast"], 0, None)) if proxy else d["axg"]
    mh = _measure(d["hg"].to_numpy(float), hx.to_numpy(float), p.goal_weight)
    ma = _measure(d["ag"].to_numpy(float), ax.to_numpy(float), p.goal_weight)
    age = (asof - d["date"]).dt.days.to_numpy(float)
    wt = 0.5 ** (age / p.half_life_days)
    teams = sorted(set(d["home"]) | set(d["away"]) | set(df.loc[df["season"] == cur_season, "home"]))
    ix = {t: i for i, t in enumerate(teams)}
    hi = d["home"].map(ix).to_numpy()
    ai = d["away"].map(ix).to_numpy()
    n = len(teams)
    mu_h = float(np.sum(wt * mh) / np.sum(wt))
    mu_a = float(np.sum(wt * ma) / np.sum(wt))
    mbar = (mu_h + mu_a) / 2
    before = set(df.loc[(df["season"] < (cur_season or 0)) & df["played"], "home"])
    new = np.array([t not in before for t in teams])
    pa = np.where(new, p.promoted_att, 1.0)
    pdf = np.where(new, p.promoted_def, 1.0)
    att, de = pa.copy(), pdf.copy()
    k = p.shrink_games * mbar
    for _ in range(40):
        num = np.bincount(hi, wt * mh, n) + np.bincount(ai, wt * ma, n) + k * pa
        den = np.bincount(hi, wt * mu_h * de[ai], n) + np.bincount(ai, wt * mu_a * de[hi], n) + k
        att = num / den
        num = np.bincount(ai, wt * mh, n) + np.bincount(hi, wt * ma, n) + k * pdf
        den = np.bincount(ai, wt * mu_h * att[hi], n) + np.bincount(hi, wt * mu_a * att[ai], n) + k
        de = num / den
    ga, gd = np.exp(np.mean(np.log(att))), np.exp(np.mean(np.log(de)))
    att, de = att / ga, de / gd
    mu_h, mu_a = mu_h * ga * gd, mu_a * ga * gd
    n_win = np.bincount(hi, None, n) + np.bincount(ai, None, n)

    # H2H category table: season-to-date per-game averages, last season's averages as prior_games pseudo games
    cats = CATS_FULL if proxy is not None else CATS_GOALS
    cur = r[r["season"] == cur_season]
    prev = r[r["season"] == (cur_season or 0) - 1]
    cs, cn = cur.groupby("team")[cats].sum(min_count=1), cur.groupby("team")[cats].count()
    pm = prev.groupby("team")[cats].mean().reindex(teams)
    cs, cn = cs.reindex(teams).fillna(0.0), cn.reindex(teams).fillna(0.0)
    num = cs + pm.fillna(0.0) * p.prior_games
    den = cn + pm.notna() * p.prior_games
    avg = (num / den.where(den > 0)).round(3)
    tab = {t: {c: (None if pd.isna(v) else float(v)) for c, v in row.items()} for t, row in avg.iterrows()}
    # trend: last-N xG-measure margin vs the team's season(+prior) baseline
    r2 = r.assign(m=_measure(r["gf"].to_numpy(float), r["xgf"].to_numpy(float), p.goal_weight)
                  - _measure(r["ga"].to_numpy(float), r["xga"].to_numpy(float), p.goal_weight))
    last = r2.groupby("team").tail(p.trend_games).groupby("team")["m"].agg(["mean", "count"])
    base = r2[r2["season"] >= (cur_season or 0) - 1].groupby("team")["m"].mean()
    n_season = cur.groupby("team").size()
    last_date = r.groupby("team")["date"].max()
    out = {"asof": str(pd.Timestamp(asof).date()), "season": cur_season, "mu_h": mu_h, "mu_a": mu_a, "cats": cats,
           "params": asdict(p), "proxy": list(proxy) if proxy else None, "model_version": MODEL_VERSION, "teams": {}}
    for i, t in enumerate(teams):
        tr = None
        if t in last.index and last.at[t, "count"] >= 3 and t in base.index:
            tr = round(float(last.at[t, "mean"] - base.at[t]), 3)
        out["teams"][t] = {"att": round(float(att[i]), 4), "def": round(float(de[i]), 4), "n_window": int(n_win[i]),
                           "n_season": int(n_season.get(t, 0)), "new": bool(new[i]), "h2h": tab[t], "trend": tr,
                           "last": str(last_date[t].date()) if t in last_date.index else None}
    return out


# ------------------------------------------------------------------ H2H + trend + xG engine
def h2h_net(r: dict, home: str, away: str) -> tuple[float, int, int, list]:
    """(net -1..1, home points, away points, [(category, winner)]) from the category matrix."""
    h, a = r["teams"][home]["h2h"], r["teams"][away]["h2h"]
    hp = ap = 0
    won = []
    for c in r["cats"]:
        x, y = h.get(c), a.get(c)
        if x is None or y is None or x == y:
            continue
        better_home = (x < y) if c in LOWER_BETTER else (x > y)
        hp += better_home
        ap += not better_home
        won.append((c, "home" if better_home else "away"))
    tot = hp + ap
    return ((hp - ap) / tot if tot else 0.0), hp, ap, won


def trend_diff(r: dict, home: str, away: str) -> float:
    th, ta = r["teams"][home].get("trend"), r["teams"][away].get("trend")
    return float(np.clip((th or 0.0) - (ta or 0.0), -2.0, 2.0))


def expected_goals(r: dict, home: str, away: str, p: Params | None = None, neutral: bool = False) -> dict:
    p = p or Params.from_dict(r.get("params"))
    th, ta = r["teams"][home], r["teams"][away]
    mu_h, mu_a = (r["mu_h"], r["mu_a"]) if not neutral else ((r["mu_h"] + r["mu_a"]) / 2,) * 2
    base_h = mu_h * th["att"] * ta["def"]
    base_a = mu_a * ta["att"] * th["def"]
    net, hp, ap, won = h2h_net(r, home, away)
    td = trend_diff(r, home, away)
    adj = p.k_h2h * net + p.k_trend * td / 2
    lh = float(np.clip(base_h * np.exp(adj), 0.15, 4.5))
    la = float(np.clip(base_a * np.exp(-adj), 0.15, 4.5))
    return {"home_xg": lh, "away_xg": la, "power_home_xg": base_h, "power_away_xg": base_a, "h2h_net": net,
            "h2h_points": [hp, ap], "h2h_won": won, "trend_diff": td}


# ------------------------------------------------------------------ score simulation
def _pmf(lam: np.ndarray, G: int = MAX_GOALS) -> np.ndarray:
    k = np.arange(G + 1)
    lf = np.array([lgamma(i + 1) for i in k])
    lam = np.asarray(lam, float)[..., None]
    return np.exp(-lam + k * np.log(lam) - lf)


def score_matrices(lh, la, rho: float, G: int = MAX_GOALS) -> np.ndarray:
    """Dixon-Coles corrected scoreline matrices, vectorised: shape (n, G+1, G+1) [home goals, away goals]."""
    lh, la = np.atleast_1d(np.asarray(lh, float)), np.atleast_1d(np.asarray(la, float))
    m = _pmf(lh, G)[:, :, None] * _pmf(la, G)[:, None, :]
    m[:, 0, 0] *= np.clip(1 - lh * la * rho, 1e-6, None)
    m[:, 0, 1] *= np.clip(1 + lh * rho, 1e-6, None)
    m[:, 1, 0] *= np.clip(1 + la * rho, 1e-6, None)
    m[:, 1, 1] *= max(1 - rho, 1e-6)
    return m / m.sum(axis=(1, 2))[:, None, None]


def probs_1x2(m: np.ndarray) -> np.ndarray:
    """(n, 3) home / draw / away from score matrices."""
    G = m.shape[1]
    i, j = np.indices((G, G))
    return np.stack([m[:, i > j].sum(1), m[:, i == j].sum(1), m[:, i < j].sum(1)], axis=1)


def p_over(m: np.ndarray, line: float) -> np.ndarray:
    G = m.shape[1]
    i, j = np.indices((G, G))
    return m[:, (i + j) > line].sum(1)


def seed_for(key: str) -> int:
    return zlib.crc32(key.encode())


def _settle(diff: np.ndarray, line: float) -> tuple[float, float, float]:
    """win / push / loss probabilities of a bet that wins when diff + line > 0 (quarter lines split in two)."""
    if abs(line * 4 - round(line * 4)) < 1e-9 and abs(line * 2 - round(line * 2)) > 1e-9:  # quarter line
        a, b = _settle(diff, line - 0.25), _settle(diff, line + 0.25)
        return tuple((x + y) / 2 for x, y in zip(a, b))
    v = diff + line
    return float(np.mean(v > 0)), float(np.mean(v == 0)), float(np.mean(v < 0))


def simulate(home_xg: float, away_xg: float, rho: float, n: int, key: str) -> dict:
    """Monte Carlo over the Dixon-Coles matrix (deterministic seed per match) -> full market set + score distribution."""
    m = score_matrices(home_xg, away_xg, rho)[0]
    rng = np.random.default_rng(seed_for(key))
    idx = rng.choice(m.size, size=n, p=m.ravel())
    hg, ag = np.divmod(idx, m.shape[1])
    tot, diff = hg + ag, hg - ag
    exact = probs_1x2(m[None])[0]
    ph, pdr, pa = float(np.mean(diff > 0)), float(np.mean(diff == 0)), float(np.mean(diff < 0))
    flat = sorted(((float(m[i, j]), f"{i}-{j}") for i in range(m.shape[0]) for j in range(m.shape[1])), reverse=True)
    totals = {}
    for ln in (0.5, 1.5, 2.0, 2.25, 2.5, 2.75, 3.0, 3.5, 4.5):
        w, pu, l_ = _settle(tot.astype(float), -ln)  # over wins when tot - line > 0
        totals[f"{ln:g}"] = {"over": round(w, 4), "push": round(pu, 4), "under": round(l_, 4)}
    ah = {}
    for ln in np.arange(-2.0, 2.01, 0.25):
        w, pu, l_ = _settle(diff.astype(float), float(ln))
        ah[f"{ln:+g}"] = {"home": round(w, 4), "push": round(pu, 4), "away": round(l_, 4)}
    team_totals = {side: {f"{ln:g}": round(float(np.mean(g > ln)), 4) for ln in (0.5, 1.5, 2.5)}
                   for side, g in (("home", hg), ("away", ag))}
    se = float(np.sqrt(max(ph * (1 - ph), 1e-9) / n))
    return {
        "p_home": round(ph, 4), "p_draw": round(pdr, 4), "p_away": round(pa, 4),
        "exact_1x2": [round(float(x), 4) for x in exact],
        "dnb_home": round(ph / max(ph + pa, 1e-9), 4),
        "p_btts": round(float(np.mean((hg > 0) & (ag > 0))), 4),
        "total_mean": round(float(np.mean(tot)), 3), "totals": totals, "asian_handicap": ah, "team_totals": team_totals,
        "score_distribution": [{"score": s, "p": round(pr, 4)} for pr, s in flat[:12]],
        "first_half_note": "1st-half markets need their own scoring-rate model (not built; never halve full-match goals)",
        "sim_n": n, "sim_se": round(se, 5), "sim_gap": round(float(np.max(np.abs(exact - [ph, pdr, pa]))), 4),
    }


# ------------------------------------------------------------------ market, vetoes, confidence
def novig(prices: list[float | None]) -> list[float] | None:
    """Decimal prices -> no-vig probabilities (multiplicative margin removal)."""
    if any(x is None or not np.isfinite(x) or x <= 1.0 for x in prices):
        return None
    inv = np.array([1 / x for x in prices])
    return list(inv / inv.sum())


def american_to_decimal(a: float) -> float:
    return 1 + (a / 100 if a > 0 else 100 / -a)


@dataclass
class MarketEdge:
    market: str
    selection: str
    price: float
    p_model: float
    p_market: float
    edge: float
    ev: float


def evaluate_market(proj: dict, snap: dict, min_edge: float = 0.04, min_conf: int = 60, max_price: float = 6.0) -> dict:
    """snap: {'h','d','a','over','under' (decimal, total 2.5), 'book', 'ts'}. Returns edges, vetoes, best qualified."""
    edges, vetoes = [], list(proj.get("vetoes", []))
    nv = novig([snap.get("h"), snap.get("d"), snap.get("a")])
    if nv:
        for sel, pm, pk, pr in zip(("home", "draw", "away"), (proj["p_home"], proj["p_draw"], proj["p_away"]), nv,
                                   (snap["h"], snap["d"], snap["a"])):
            edges.append(MarketEdge("1x2", sel, float(pr), float(pm), round(float(pk), 4), round(float(pm - pk), 4),
                                    round(float(pm * pr - 1), 4)))
        if max(abs(a - b) for a, b in zip((proj["p_home"], proj["p_draw"], proj["p_away"]), nv)) > 0.20:
            vetoes.append("extreme model disagreement with the market (>20 pts)")
    nt = novig([snap.get("over"), snap.get("under")])
    if nt:
        t = proj["totals"]["2.5"]
        for sel, pm, pk, pr in (("over 2.5", t["over"], nt[0], snap["over"]), ("under 2.5", t["under"], nt[1], snap["under"])):
            edges.append(MarketEdge("total", sel, float(pr), float(pm), round(float(pk), 4), round(float(pm - pk), 4),
                                    round(float(pm * pr - 1), 4)))
    if not edges:
        vetoes.append("no market snapshot")
    if any(e.edge > 0.15 for e in edges):
        vetoes.append("suspiciously large edge (>15 pts): probably bad data")
    cands = [e for e in edges if e.edge >= min_edge and e.ev > 0 and e.price <= max_price]
    best = max(cands, key=lambda e: e.ev, default=None)
    hard = [v for v in vetoes if not v.startswith("soft:")]
    qualified = bool(best) and not hard and proj.get("confidence", 0) >= min_conf
    return {"edges": [asdict(e) for e in edges], "best": asdict(best) if best else None, "vetoes": vetoes,
            "qualified": qualified, "book": snap.get("book"), "market_ts": snap.get("ts")}


def vetoes_and_confidence(r: dict, home: str, away: str, xg: dict, sim: dict, calib_penalty: bool = False) -> tuple[list, int, list]:
    """Hard vetoes (no play) and soft notes ('soft:' prefix), and a 0-100 data/model confidence score
    (not a win probability: blueprint section 12)."""
    th, ta = r["teams"][home], r["teams"][away]
    v, notes, conf = [], [], 100
    nmin = min(th["n_window"], ta["n_window"])
    if nmin < 5:
        v.append(f"insufficient history ({nmin} matches)")
        conf -= 40
    elif nmin < 12:
        conf -= 15
    smin = min(th["n_season"], ta["n_season"])
    if smin < 6:
        notes.append(f"soft: early season ({smin} league matches)")
        conf -= 10
    if th["new"] or ta["new"]:
        notes.append("soft: promoted/new team (league-strength translation uncertain)")
        conf -= 10
    notes.append("soft: lineups unconfirmed (no XI feed; recheck near kickoff)")
    conf -= 10
    p_dir = np.sign(xg["power_home_xg"] - xg["power_away_xg"])
    h_dir = np.sign(xg["h2h_net"])
    t_dir = np.sign(xg["trend_diff"]) if abs(xg["trend_diff"]) > 0.15 else 0
    dis = int(h_dir != 0 and h_dir != p_dir) + int(t_dir != 0 and t_dir != p_dir)
    if dis:
        notes.append(f"soft: {dis} component(s) disagree with power")
        conf -= 8 * dis
    if sim["sim_gap"] > 0.01:
        v.append("simulation unstable (MC vs exact gap > 1 pt)")
    if calib_penalty:
        notes.append("soft: league calibration worse than the closing market in validation")
        conf -= 10
    # Oct 16 2026 (Marvin): ESPN-only leagues (no football-data shots/xG) — track with low confidence
    if (r.get("source") or "") == "espn" or r.get("low_confidence"):
        notes.append("soft: ESPN goals-only history (no shots/xG feed) — low-confidence paper track")
        conf -= 15
    return v + notes, int(np.clip(conf, 0, 100)), notes


class MarvSoccerPredictMax:
    """Blueprint section 18 object contract over one league's saved ratings."""

    def __init__(self, ratings: dict, calib_penalty: bool = False):
        self.r = ratings
        self.p = Params.from_dict(ratings.get("params"))
        self.calib_penalty = calib_penalty

    def project_match(self, home: str, away: str, key: str = "", neutral: bool = False) -> dict:
        xg = expected_goals(self.r, home, away, self.p, neutral)
        sim = simulate(xg["home_xg"], xg["away_xg"], self.p.rho, self.p.n_sims, key or f"{home}|{away}|{self.r['asof']}")
        vetoes, conf, _ = vetoes_and_confidence(self.r, home, away, xg, sim, self.calib_penalty)
        return {"home": home, "away": away, "asof": self.r["asof"], "model_version": MODEL_VERSION,
                "home_xg": round(xg["home_xg"], 3), "away_xg": round(xg["away_xg"], 3), **sim,
                "components": {"power_home_xg": round(float(xg["power_home_xg"]), 3),
                               "power_away_xg": round(float(xg["power_away_xg"]), 3),
                               "h2h_points": xg["h2h_points"], "h2h_net": round(xg["h2h_net"], 3),
                               "h2h_won": xg["h2h_won"], "trend_diff": round(xg["trend_diff"], 3)},
                "vetoes": vetoes, "confidence": conf}

    def evaluate_market(self, projection: dict, market_snapshot: dict, **kw) -> dict:
        return evaluate_market(projection, market_snapshot, **kw)
