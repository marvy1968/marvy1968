"""Normalize play-by-play (historical files and ESPN's live feed) into one schema per sport family.

Football plays:   game_id, period, side ("home"/"away" = offense), down, distance, yards, kind
                  ("pass", "rush", "sack", "other"), turnover, home_score, away_score
Basketball plays: game_id, period, side, kind ("fg2", "fg3", "ft", "oreb", "dreb", "tov", "foul", "other"),
                  made, home_score, away_score
The live feed and the training data go through the same rules, so the model sees identical stats.
"""

import numpy as np
import pandas as pd

TURNOVER_WORDS = ("interception", "fumble recovery (opponent)", "fumble lost")


def _kind_football(text: str) -> str:
    t = str(text).lower()
    if "sack" in t:
        return "sack"
    if "pass" in t or "interception" in t:
        return "pass"
    if "rush" in t or "run" in t:
        return "rush"
    return "other"


def nflverse(pbp: pd.DataFrame) -> pd.DataFrame:
    p = pbp[pbp["play_type"].isin(["pass", "run"]) & pbp["posteam"].notna() & pbp["qtr"].notna()]
    return pd.DataFrame({
        "game_id": p["game_id"].astype(str), "period": p["qtr"].astype(int),
        "side": np.where(p["posteam"] == p["home_team"], "home", "away"),
        "down": p["down"], "distance": p["ydstogo"], "yards": p["yards_gained"].fillna(0),
        "kind": np.where(p["sack"] == 1, "sack", np.where(p["play_type"] == "pass", "pass", "rush")),
        "turnover": ((p["interception"] == 1) | (p["fumble_lost"] == 1)).astype(int),
        "home_score": p["total_home_score"], "away_score": p["total_away_score"],
    })


def cfbfastr(pbp: pd.DataFrame) -> pd.DataFrame:
    p = pbp[((pbp["rush"] == 1) | (pbp["pass"] == 1)) & pbp["pos_team"].notna()]
    home_off = p["pos_team"] == p["home"]
    return pd.DataFrame({
        "game_id": p["game_id"].astype(str), "period": p["period"].astype(int),
        "side": np.where(home_off, "home", "away"), "down": p["down"], "distance": p["distance"],
        "yards": p["yards_gained"].fillna(0),
        "kind": np.where(p["sack"] == 1, "sack", np.where(p["pass"] == 1, "pass", "rush")),
        "turnover": ((p["int"] == 1) | p["play_type"].astype(str).str.startswith("Fumble Recovery (Opponent)")).astype(int),
        "home_score": np.where(home_off, p["pos_team_score"], p["def_pos_team_score"]),
        "away_score": np.where(home_off, p["def_pos_team_score"], p["pos_team_score"]),
    })


def espn_football(summary: dict, game_id: str) -> pd.DataFrame:
    """ESPN game summary (live): drives -> plays."""
    comp = (summary.get("header", {}).get("competitions") or [{}])[0]
    ids = {c.get("homeAway"): str(c.get("team", {}).get("id") or c.get("id")) for c in comp.get("competitors", [])}
    rows = []
    for drive in (summary.get("drives", {}).get("previous") or []) + \
            ([summary["drives"]["current"]] if summary.get("drives", {}).get("current") else []):
        off = str(drive.get("team", {}).get("id"))
        side = "home" if off == ids.get("home") else "away"
        for pl in drive.get("plays", []):
            text = (pl.get("type") or {}).get("text", "")
            kind = _kind_football(text)
            if kind == "other":
                continue
            start = pl.get("start") or {}
            low = (text + " " + str(pl.get("text", ""))).lower()
            rows.append({"game_id": game_id, "period": int((pl.get("period") or {}).get("number") or 0), "side": side,
                         "down": start.get("down"), "distance": start.get("distance"),
                         "yards": float(pl.get("statYardage") or 0), "kind": kind,
                         "turnover": int(any(w in low for w in TURNOVER_WORDS)),
                         "home_score": pl.get("homeScore"), "away_score": pl.get("awayScore")})
    return pd.DataFrame(rows)


def _kind_basketball(type_text: str, text: str, shooting: bool, points_attempted=None) -> str:
    t, x = str(type_text), str(text).lower()
    if "Free Throw" in t:
        return "ft"
    if shooting:
        return "fg3" if (points_attempted == 3 or "three point" in x) else "fg2"
    if "Offensive Rebound" in t:
        return "oreb"
    if "Defensive Rebound" in t:
        return "dreb"
    if "turnover" in t.lower() or t == "Traveling":
        return "tov"
    if "Foul" in t:
        return "foul"
    return "other"


def espn_basketball_file(pbp: pd.DataFrame) -> pd.DataFrame:
    p = pbp[pbp["team_id"].notna()]
    kind = [_kind_basketball(a, b, bool(c), d) for a, b, c, d in
            zip(p["type_text"], p["text"], p["shooting_play"], p.get("points_attempted", pd.Series(None, index=p.index)))]
    return pd.DataFrame({
        "game_id": p["game_id"].astype(str), "period": p["period_number"].astype(int),
        "side": np.where(p["team_id"].astype(float) == p["home_team_id"].astype(float), "home", "away"),
        "kind": kind, "made": p["scoring_play"].fillna(False).astype(bool).astype(int),
        "home_score": p["home_score"], "away_score": p["away_score"]})


def espn_basketball(summary: dict, game_id: str) -> pd.DataFrame:
    """ESPN game summary (live): plays."""
    comp = (summary.get("header", {}).get("competitions") or [{}])[0]
    home_id = next((str(c.get("team", {}).get("id") or c.get("id")) for c in comp.get("competitors", [])
                    if c.get("homeAway") == "home"), None)
    rows = []
    for pl in summary.get("plays", []):
        team = (pl.get("team") or {}).get("id")
        if team is None:
            continue
        rows.append({"game_id": game_id, "period": int((pl.get("period") or {}).get("number") or 0),
                     "side": "home" if str(team) == home_id else "away",
                     "kind": _kind_basketball((pl.get("type") or {}).get("text", ""), pl.get("text", ""),
                                              bool(pl.get("shootingPlay")), pl.get("pointsAttempted")),
                     "made": int(bool(pl.get("scoringPlay"))), "home_score": pl.get("homeScore"),
                     "away_score": pl.get("awayScore")})
    return pd.DataFrame(rows)


def _cum(df: pd.DataFrame, cols: list[str], checkpoints: list[int]) -> pd.DataFrame:
    """Per game, side and checkpoint: sums over all periods up to the checkpoint."""
    per = df.groupby(["game_id", "side", "period"])[cols].sum().reset_index()
    out = []
    for k in checkpoints:
        c = per[per["period"] <= k].groupby(["game_id", "side"])[cols].sum().reset_index()
        out.append(c.assign(checkpoint=k))
    long = pd.concat(out, ignore_index=True)
    wide = long.pivot_table(index=["game_id", "checkpoint"], columns="side", values=cols, aggfunc="sum")
    wide.columns = [f"{'h' if side == 'home' else 'a'}_{c}" for c, side in wide.columns]
    return wide.reset_index()


def _scores(plays: pd.DataFrame, checkpoints: list[int]) -> pd.DataFrame:
    out = []
    for k in checkpoints:
        s = plays[plays["period"] <= k].groupby("game_id")[["home_score", "away_score"]].max()
        out.append(s.assign(checkpoint=k).reset_index())
    s = pd.concat(out, ignore_index=True).rename(columns={"home_score": "h_score", "away_score": "a_score"})
    s[["h_score", "a_score"]] = s[["h_score", "a_score"]].astype(float).fillna(0)
    return s


def football_states(plays: pd.DataFrame, checkpoints=(1, 2, 3)) -> pd.DataFrame:
    """Cumulative team stats and score at the end of each checkpoint period, one row per game/checkpoint."""
    x = plays.copy()
    need = np.where(x["down"] == 1, 0.4, np.where(x["down"] == 2, 0.6, 1.0)) * x["distance"].fillna(10).astype(float)
    x["plays"] = 1
    x["success"] = (x["yards"] >= need).astype(int)
    x["explosive"] = (x["yards"] >= 20).astype(int)
    x["sacks"] = (x["kind"] == "sack").astype(int)
    x["passes"] = (x["kind"] != "rush").astype(int)
    x["third"] = (x["down"] == 3).astype(int)
    x["third_conv"] = ((x["down"] == 3) & (x["yards"] >= x["distance"].fillna(10))).astype(int)
    cols = ["plays", "yards", "success", "explosive", "turnover", "sacks", "passes", "third", "third_conv"]
    w = _cum(x, cols, list(checkpoints)).merge(_scores(x, list(checkpoints)), on=["game_id", "checkpoint"], how="left")
    for pre in ("h", "a"):
        n = w[f"{pre}_plays"].replace(0, np.nan)
        w[f"{pre}_ypp"] = w[f"{pre}_yards"] / n
        w[f"{pre}_success_rate"] = w[f"{pre}_success"] / n
        w[f"{pre}_pass_share"] = w[f"{pre}_passes"] / n
        w[f"{pre}_third_rate"] = w[f"{pre}_third_conv"] / w[f"{pre}_third"].replace(0, np.nan)
    for k in ("yards", "ypp", "success_rate", "explosive", "turnover", "sacks", "third_rate", "plays"):
        w[f"d_{k}"] = w[f"h_{k}"] - w[f"a_{k}"]
    return w


def basketball_states(plays: pd.DataFrame, checkpoints=(1, 2, 3)) -> pd.DataFrame:
    x = plays.copy()
    k, m = x["kind"], x["made"]
    x["fga"] = k.isin(["fg2", "fg3"]).astype(int)
    x["fgm"] = (k.isin(["fg2", "fg3"]) & (m == 1)).astype(int)
    x["fg3a"] = (k == "fg3").astype(int)
    x["fg3m"] = ((k == "fg3") & (m == 1)).astype(int)
    x["fta"] = (k == "ft").astype(int)
    x["ftm"] = ((k == "ft") & (m == 1)).astype(int)
    for c in ("oreb", "dreb", "tov", "foul"):
        x[c] = (k == c).astype(int)
    cols = ["fga", "fgm", "fg3a", "fg3m", "fta", "ftm", "oreb", "dreb", "tov", "foul"]
    w = _cum(x, cols, list(checkpoints)).merge(_scores(x, list(checkpoints)), on=["game_id", "checkpoint"], how="left")
    for pre in ("h", "a"):
        fga = w[f"{pre}_fga"].replace(0, np.nan)
        w[f"{pre}_efg"] = (w[f"{pre}_fgm"] + 0.5 * w[f"{pre}_fg3m"]) / fga
        w[f"{pre}_fg3_pct"] = w[f"{pre}_fg3m"] / w[f"{pre}_fg3a"].replace(0, np.nan)
        w[f"{pre}_ft_pct"] = w[f"{pre}_ftm"] / w[f"{pre}_fta"].replace(0, np.nan)
        w[f"{pre}_poss"] = w[f"{pre}_fga"] + 0.44 * w[f"{pre}_fta"] - w[f"{pre}_oreb"] + w[f"{pre}_tov"]
    for c in ("efg", "fg3_pct", "ft_pct", "fta", "oreb", "dreb", "tov", "foul", "poss", "fga"):
        w[f"d_{c}"] = w[f"h_{c}"] - w[f"a_{c}"]
    w["pace"] = (w["h_poss"] + w["a_poss"]) / 2
    return w
