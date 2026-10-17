"""Assemble the prop-grade backtest table from real data only (no API calls)."""
import json, sqlite3, numpy as np, pandas as pd
R = "/opt/marv-bot"; OUT = "/tmp/pg"
A = pd.read_csv(f"{R}/research/propgrade_v1/out/A_features.csv")
real = pd.read_csv(f"{R}/state/reports/props_backtest_real.csv", low_memory=False,
                   usecols=["player", "market", "line", "gameday", "game_id", "team", "opponent_team", "is_home", "spread_line",
                            "total_line", "player_id", "book"])
real = real.drop_duplicates(["player", "market", "line", "gameday"])
A = A.merge(real.rename(columns={"gameday": "date", "total_line": "total_close"}), on=["player", "market", "line", "date"], how="left")
print("A rows", len(A), "joined game_id", A.game_id.notna().mean())
# Marv walk-forward game projections (held-out seasons): projected total + margin
hy = pd.read_csv(f"{R}/state/reports/hybrid_refine_nfl_games.csv")[["game_id", "tot_hat", "proj", "total", "spread"]]
pe = pd.read_csv(f"{R}/state/reports/pick_engine_nfl_games.csv")[["game_id", "pt", "ph", "pm"]]
g = hy.merge(pe, on="game_id", how="outer")
A = A.merge(g, on="game_id", how="left")
print("A with Marv game proj", A.tot_hat.notna().mean().round(3), "with ph", A.ph.notna().mean().round(3))
A.to_csv(f"{OUT}/A.csv", index=False)

B = pd.read_csv(f"{R}/research/propgrade_v1/out/B_features.csv")
allb = pd.read_csv(f"{R}/research/propgrade_v1/out/B_all_picks.csv")
B = B.merge(allb[["id", "league", "status", "commence"]].drop_duplicates("id"), on="id", how="left")
B = B.merge(g, on="game_id", how="left")
import sys; sys.path.insert(0, R)
from marv.props.odds import norm_name
p26 = pd.read_csv(f"{R}/state/cache/nfl_players_week_2026.csv", low_memory=False, usecols=["player_display_name", "team", "week", "season_type"])
p26 = p26.sort_values("week").groupby("player_display_name").tail(1); tm = dict(zip(p26.player_display_name.map(norm_name), p26.team))
B["team"] = B.player.map(lambda n: tm.get(norm_name(n)))
B["home_team"] = B.game_id.str.split("_").str[3]; B["away_team"] = B.game_id.str.split("_").str[2]
B["is_home"] = (B.team == B.home_team).astype(float); B.loc[~B.team.isin(B.home_team.tolist() + B.away_team.tolist()), "is_home"] = np.nan
B.to_csv(f"{OUT}/B.csv", index=False)
print("B team mapped", B.is_home.notna().sum(), "of", len(B))
print("B rows", len(B), "with Marv game proj", B.tot_hat.notna().sum(), "ph", B.ph.notna().sum())
print(B.game_id.str[:7].value_counts())
# week-5 2026 Marv game reads from the pregame predictions snapshot (updated 2026-10-08, before those games)
pb = json.load(open(f"{R}/state/predictions.json.bak-master-20261013"))
w5 = {v["game_id"]: v for v in pb.values() if isinstance(v, dict) and v.get("sport") == "nfl" and str(v.get("game_id", "")).startswith("2026_")}
for i, r in B.iterrows():
    v = w5.get(r.game_id)
    if v is not None and pd.isna(r.ph):
        B.at[i, "ph"] = v["home_win"]; B.at[i, "pt"] = v["model_total"]; B.at[i, "tot_hat"] = v["model_total"]
        B.at[i, "src_read"] = "predictions snapshot 2026-10-08"
B.to_csv(f"{OUT}/B.csv", index=False)
print("B with Marv read after snapshot", B.ph.notna().sum(), "of", len(B))
