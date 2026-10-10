"""WNBA institutional simulation, PAPER: net rating + rolling TS / defensive TS + rotation depth + steam + skew-normal MC.

wnba_institutional_simulation() sits on the WNBA pick-engine blend (marv/pick_engine.py: power rating + trend analyzer),
the same way marv/institutional.py (CFB) and marv/nfl_institutional.py (NFL) do:

  1. Net rating      exponentially weighted (half-life HL games, this season, before the game) points per 100 possessions
                     scored minus allowed. net_d = net_home - net_away.
  2. TS / def TS     same weighting of true shooting (pts / 2(FGA + 0.44 FTA)) made and allowed. ts_d = (TS - TS allowed)
                     home - away, in TS points; ts_s = expected TS of both offences vs league average (for the total).
  3. Rotation depth  player box minutes over the team's last ROT_N games: depth = players averaging >= DEPTH_MIN minutes;
                     missing = usual minutes (/40) of rotation players (>= MISS_MIN) absent from the team's last game
                     (no historical injury reports, so absence is the proxy). depth_d = home - away; miss_d = away - home.
  4. Steam           open -> current HOME spread / total, velocity-decayed (marv/institutional.py steam()).
  5. Skew-normal MC  10,000 draws, margin residual skew shape fitted on EARLIER seasons, oriented to the projected
                     favourite, rescaled to mean 0 and std SD_MARGIN = 7.5 (owner setting); total residual skewnorm fitted
                     freely -> P(win), P(cover), P(over). Internal only (no % prints; marv/proven.py decides).

Margin += N*net_d + T*ts_d + D*depth_d + M*miss_d - S*steam_spread; total += TT*ts_s + MT*miss_s + ST*steam_total.
Coefficients come from tools/wnba_institutional_bt.py (walk-forward, earlier seasons only) and are overwritten by
state/reports/wnba_institutional_backtest.json "latest_fit". env INSTITUTIONAL_WNBA=0 -> plain pick-engine MC.
Live helpers never raise.
"""

import json
import math
import time
from pathlib import Path

import numpy as np

from .institutional import steam

SIMS = 10000
SD_MARGIN = 7.5
HL = 6.0          # games, EWM half-life for net rating / TS
MIN_GP = 3
ROT_N = 5
DEPTH_MIN = 12.0
MISS_MIN = 15.0
REPORT = "reports/wnba_institutional_backtest.json"
PARAMS = {"N": 0.0, "T": 0.0, "D": 0.0, "M": 0.0, "S": 0.0, "TT": 0.0, "MT": 0.0, "ST": 0.0,
          "skew_a": 0.0, "sd_m": SD_MARGIN, "skew_t": [0.0, 0.0, 15.0]}
FEATS = ("net", "ts_o", "ts_d", "depth", "miss")
_LOADED = {"t": 0.0}


# ------------------------------------------------------------------ point-in-time team features
def team_games(cache: Path, seasons):
    """Team-game rows with net rating / TS made / TS allowed (hybrid.bb_team_games + opponent TS)."""
    from . import hybrid as HY
    tg = HY.bb_team_games(Path(cache), "wnba", seasons)
    if tg.empty:
        return tg
    o = tg[["game_id", "team", "succ"]].rename(columns={"team": "opp", "succ": "ts_allowed"})
    tg = tg.merge(o, on=["game_id", "opp"], how="left")
    tg["net100"] = (tg["off"] - tg["def"]) * 100
    return tg.sort_values(["date", "game_id"]).reset_index(drop=True)


def _ewm_pre(tg):
    """Pre-game (shifted) EWM features per team-season + post-last-game state (unshifted) for live use."""
    g = tg.groupby(["team", "season"], sort=False)
    out = tg[["game_id", "team", "season", "date"]].copy()
    for src, dst in (("net100", "net"), ("succ", "ts_o"), ("ts_allowed", "ts_d")):
        e = g[src].transform(lambda s: s.ewm(halflife=HL, min_periods=1).mean())
        n = g.cumcount()
        out[dst] = g[src].transform(lambda s: s.ewm(halflife=HL, min_periods=1).mean().shift(1))
        out[dst] = out[dst].where(n >= MIN_GP)
        out[dst + "_post"] = e.where(n + 1 >= MIN_GP)
    return out


def player_minutes(cache: Path, seasons):
    import pandas as pd
    out = []
    for s in seasons:
        f = Path(cache) / f"wnba_player_box_{s}.parquet"
        if f.exists():
            p = pd.read_parquet(f, columns=["game_id", "season", "season_type", "game_date", "athlete_id",
                                            "team_display_name", "minutes", "did_not_play"])
            out.append(p[p.season_type.isin([2, 3])])
    if not out:
        return pd.DataFrame()
    p = pd.concat(out, ignore_index=True)
    p["game_id"] = p.game_id.astype(str).str.replace(r"\.0$", "", regex=True)
    p["minutes"] = pd.to_numeric(p.minutes, errors="coerce").fillna(0.0)
    d = pd.to_datetime(p.game_date)
    p["date"] = (d.dt.tz_convert(None) if d.dt.tz is not None else d).dt.normalize()
    return p.rename(columns={"team_display_name": "team"})


def rotation(pm):
    """Per (game_id, team): pre-game depth / missing from the team's previous ROT_N games (same season) + post state."""
    import pandas as pd
    if pm.empty:
        return pd.DataFrame(columns=["game_id", "team", "depth", "miss", "depth_post", "miss_post"])
    rows = []
    for (team, season), d in pm.groupby(["team", "season"], sort=False):
        games = d.groupby("game_id").date.first().sort_values()
        ids = list(games.index)
        mins = d.pivot_table(index="game_id", columns="athlete_id", values="minutes", aggfunc="sum").reindex(ids).fillna(0)
        M = mins.to_numpy()
        for i, gid in enumerate(ids + [None]):
            lo = max(0, i - ROT_N)
            if i - lo < MIN_GP:
                rec = (np.nan, np.nan)
            else:
                usual = M[lo:i].mean(axis=0)
                depth = float((usual >= DEPTH_MIN).sum())
                rot = usual >= MISS_MIN
                miss = float(usual[rot & (M[i - 1] <= 0)].sum() / 40.0)
                rec = (depth, miss)
            if gid is None:
                rows.append({"game_id": None, "team": team, "season": season, "depth_post": rec[0], "miss_post": rec[1]})
            else:
                rows.append({"game_id": gid, "team": team, "season": season, "depth": rec[0], "miss": rec[1]})
    return pd.DataFrame(rows)


def game_features(cache: Path, seasons):
    """One row per home game: net_d, ts_d, ts_s, depth_d, miss_d, miss_s (pre-game, NaN-safe -> 0 later)."""
    tg = team_games(cache, seasons)
    if tg.empty:
        import pandas as pd
        return pd.DataFrame()
    ew = _ewm_pre(tg)
    tg = tg.merge(ew[["game_id", "team", "net", "ts_o", "ts_d"]], on=["game_id", "team"], how="left")
    rot = rotation(player_minutes(cache, seasons))
    rot = rot[rot.game_id.notna()][["game_id", "team", "depth", "miss"]]
    tg = tg.merge(rot, on=["game_id", "team"], how="left")
    lg = tg.groupby("season").succ.transform("mean")
    tg["lg_ts"] = lg
    h = tg[tg.is_home == 1].set_index(["game_id"])
    a = tg[tg.is_home == 0].set_index(["game_id"])
    a = a.reindex(h.index)
    import pandas as pd
    f = pd.DataFrame(index=h.index)
    f["net_d"] = h.net - a.net
    f["ts_d"] = ((h.ts_o - h.ts_d) - (a.ts_o - a.ts_d)) * 100
    f["ts_s"] = (h.ts_o + a.ts_d + a.ts_o + h.ts_d - 4 * h.lg_ts) * 100
    f["depth_d"] = h.depth - a.depth
    f["miss_d"] = a.miss - h.miss
    f["miss_s"] = a.miss + h.miss
    f["has_rot"] = h.depth.notna() & a.depth.notna()
    return f.reset_index()


# ------------------------------------------------------------------ adjust + skew-normal MC
def _z(x) -> float:
    return 0.0 if x is None or x != x else float(x)


def adjust(bm: float, bt: float, f: dict, s_open, s_now, t_open, t_now, p: dict) -> tuple:
    es, et = steam(s_open, s_now), steam(t_open, t_now)
    parts = {"net_pts": p["N"] * _z(f.get("net_d")), "ts_pts": p["T"] * _z(f.get("ts_d")),
             "depth_pts": p["D"] * _z(f.get("depth_d")), "miss_pts": p["M"] * _z(f.get("miss_d")),
             "steam_pts": -p["S"] * es}
    tparts = {"ts_tot_pts": p["TT"] * _z(f.get("ts_s")), "miss_tot_pts": p["MT"] * _z(f.get("miss_s")),
              "steam_tot_pts": p["ST"] * et}
    m = bm + sum(parts.values())
    t = bt + sum(tparts.values())
    info = {k: round(v, 2) for k, v in {**parts, **tparts}.items()}
    info.update({"steam_spread": round(es, 2), "steam_total": round(et, 2),
                 "features": {k: (None if f.get(k) is None or f.get(k) != f.get(k) else round(float(f[k]), 3))
                              for k in ("net_d", "ts_d", "ts_s", "depth_d", "miss_d", "miss_s")}})
    return m, t, info


def skew_params(a: float, sd: float = SD_MARGIN) -> tuple[float, float, float]:
    """(a, loc, scale) of a skew-normal with shape a, mean 0 and standard deviation sd."""
    d = a / math.sqrt(1 + a * a)
    scale = sd / math.sqrt(1 - 2 * d * d / math.pi)
    return a, -scale * d * math.sqrt(2 / math.pi), scale


def simulate(m: float, t: float, p: dict, spread=None, line=None, n: int = SIMS, seed: int = 0) -> dict:
    from scipy.stats import skewnorm
    rng = np.random.default_rng(seed)
    a, loc, sc = skew_params(p.get("skew_a", 0.0), p.get("sd_m", SD_MARGIN))
    at, lt, st = p["skew_t"]
    sg = 1.0 if m >= 0 else -1.0
    M = m + sg * skewnorm.rvs(a, loc, sc, size=n, random_state=rng)
    T = t + skewnorm.rvs(at, lt, st, size=n, random_state=rng)
    out = {"p_home": float((M > 0).mean())}
    if spread is not None and spread == spread:
        c = M + spread
        c = c[np.abs(c) > 1e-9]
        out["p_home_cover"] = float((c > 0).mean()) if len(c) else 0.5
    if line is not None and line == line:
        o = T - line
        o = o[np.abs(o) > 1e-9]
        out["p_over"] = float((o > 0).mean()) if len(o) else 0.5
    return out


def wnba_institutional_simulation(bm: float, bt: float, features: dict | None = None, spread: float | None = None,
                                  line: float | None = None, spread_open: float | None = None,
                                  line_open: float | None = None, params: dict | None = None, n: int = SIMS,
                                  seed: int = 0) -> dict:
    """One WNBA game. bm / bt = pick-engine blend margin (HOME) / total; spread = current HOME spread. Pure."""
    p = params or PARAMS
    m, t, info = adjust(bm, bt, features or {}, spread_open, spread, line_open, line, p)
    sim = simulate(m, t, p, spread, line, n=n, seed=seed)
    return {"margin": round(m, 1), "total": round(t, 1), **info, "sim": sim}


# ------------------------------------------------------------------ live helpers
def load(state_dir: Path) -> dict:
    if time.time() - _LOADED["t"] < 1800:
        return PARAMS
    _LOADED["t"] = time.time()
    try:
        lf = json.loads((Path(state_dir) / REPORT).read_text()).get("latest_fit")
        if lf:
            PARAMS.update({k: lf[k] for k in PARAMS if k in lf})
    except (OSError, ValueError, KeyError):
        pass
    return PARAMS


def opener(state_dir: Path, home: str, away: str) -> tuple[float | None, float | None]:
    """First-seen HOME spread / total for a WNBA game from state/lines.json (marv/linemove.py)."""
    try:
        from datetime import datetime, timedelta, timezone
        from .data.teams import similarity
        d = json.loads((Path(state_dir) / "lines.json").read_text())
        recent = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
        best, sc = None, 0.0
        for k, v in d.items():
            if not k.startswith("wnba:") or str(v.get("start", "9999")) < recent:
                continue
            s = min(similarity(v.get("home", ""), home), similarity(v.get("away", ""), away))
            if s > sc:
                best, sc = v, s
        if best and sc >= 0.75:
            return (best.get("spread_open_src", best.get("spread")), best.get("total_open_src", best.get("total")))
    except Exception:  # noqa: BLE001
        pass
    return None, None


_LIVE: dict = {}


def live_features(state_dir: Path, home: str, away: str, as_of=None) -> dict:
    """Pre-game feature dict for HOME vs AWAY from cached box scores before as_of (cached 30 min). Never raises."""
    try:
        from datetime import datetime, timezone
        from . import hybrid as HY
        from .data.teams import similarity
        as_of = as_of or datetime.now(timezone.utc)
        season = HY.season_for("wnba", as_of)
        cache = Path(state_dir) / "cache"
        key = (str(state_dir), season, as_of.strftime("%Y-%m-%d"))
        hit = _LIVE.get(key)
        if not hit or time.time() - hit[0] > 1800:
            import pandas as pd
            day = pd.Timestamp(as_of.replace(tzinfo=None) if as_of.tzinfo else as_of).normalize()
            tg = team_games(cache, [season])
            tg = tg[tg.date < day]
            ew = _ewm_pre(tg).groupby("team").last()
            pm = player_minutes(cache, [season])
            pm = pm[pm.date < day] if not pm.empty else pm
            rot = rotation(pm)
            rot = rot[rot.game_id.isna()].set_index("team") if not rot.empty else rot
            lg = float(tg.succ.mean()) if len(tg) else float("nan")
            _LIVE[key] = hit = (time.time(), (ew, rot, lg))
        ew, rot, lg = hit[1]

        def nm(t):
            if t in ew.index:
                return t
            best = max(ew.index, key=lambda x: similarity(x, t)) if len(ew.index) else None
            return best if best is not None and similarity(best, t) >= 0.75 else None
        h, a = nm(home), nm(away)
        if not h or not a:
            return {}
        H, A = ew.loc[h], ew.loc[a]
        g = lambda df, t, c: (df.loc[t, c] if len(df) and t in df.index else float("nan"))  # noqa: E731
        f = {"net_d": H.net_post - A.net_post,
             "ts_d": ((H.ts_o_post - H.ts_d_post) - (A.ts_o_post - A.ts_d_post)) * 100,
             "ts_s": (H.ts_o_post + A.ts_d_post + A.ts_o_post + H.ts_d_post - 4 * lg) * 100,
             "depth_d": g(rot, h, "depth_post") - g(rot, a, "depth_post"),
             "miss_d": g(rot, a, "miss_post") - g(rot, h, "miss_post"),
             "miss_s": g(rot, a, "miss_post") + g(rot, h, "miss_post")}
        return {k: float(v) for k, v in f.items()}
    except Exception:  # noqa: BLE001
        return {}
