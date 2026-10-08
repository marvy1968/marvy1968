"""College football opening and closing lines from ~25 sportsbooks (sportsdataverse cfbfastR-data).

One row per game (ESPN game id), home perspective:
  spread_open / total_open   opening number (5Dimes through 2019, then Bovado, DraftKings, ESPN Bet)
  spread_close / total_close median closing number across books
  spread_sharp / total_sharp Pinnacle / BetCRIS closing number (2006-2019)
  spread_bovada / total_bovada and Bovado's prices for each side
Opening lines exist for ~800-950 games a season from 2012 (none in 2020). No betting-split (ticket or
money %) history is available from any free source; line movement is the measurable trace of it.
"""

from collections import Counter
from pathlib import Path

import pandas as pd

from ..stats.base import fetch

URL = "https://raw.githubusercontent.com/sportsdataverse/cfbfastR-data/main/betting/parquet/cfb_line_odds.parquet"
SHARP = {"PINNACLE", "BetCRIS & BOOKMAKER", "CIRCA"}
BOVADA = {"BOVADA & bodog", "Bovada"}
SKIP = {"consensus", "teamrankings", "numberfire", "SBR", "5Dimes & sportbet"}  # 5Dimes closes look unreliable
OPEN_PREF = ["5Dimes & sportbet", "Bovada", "BOVADA & bodog", "DraftKings", "ESPN Bet"]


def raw(cache: Path, max_age_hours: float | None = 24 * 7) -> pd.DataFrame:
    path = fetch(URL, cache / "cfb_line_odds.parquet", max_age_hours)
    return pd.read_parquet(path) if path else pd.DataFrame()


def team_ids(spreads: pd.DataFrame) -> dict:
    """Book abbreviation -> ESPN team id: the id present in all of that abbreviation's games."""
    out = {}
    for abbr, part in spreads.drop_duplicates(["abbr", "game_id"]).groupby("abbr"):
        out[abbr] = Counter(list(part["home_team_id"]) + list(part["away_team_id"])).most_common(1)[0][0]
    return out


def _summary(x: pd.DataFrame, name: str) -> pd.DataFrame:
    x = x[x["lines"].notna()]
    books = x[~x["book"].isin(SKIP)].groupby("game_id")["lines"]
    o = x[x["opening_lines"].notna() & x["book"].isin(OPEN_PREF)]
    if name == "total":
        o = o[o["opening_lines"] > 0]
    o = o.assign(pref=o["book"].map({b: i for i, b in enumerate(OPEN_PREF)})).sort_values("pref")
    return pd.concat([
        books.median().rename(f"{name}_close"), books.std().rename(f"{name}_disp"),
        x[x["book"].isin(SHARP)].groupby("game_id")["lines"].median().rename(f"{name}_sharp"),
        x[x["book"].isin(BOVADA)].groupby("game_id")["lines"].median().rename(f"{name}_bovada"),
        o.groupby("game_id")["opening_lines"].first().rename(f"{name}_open")], axis=1)


def games_table(d: pd.DataFrame) -> pd.DataFrame:
    d = d[d["game_id"].notna() & d["market_type"].isin(["spread", "total"])].copy()
    d = d.drop_duplicates(["game_id", "market_type", "abbr", "book", "lines", "opening_lines"])
    d["game_id"] = d["game_id"].astype("int64").astype(str)
    sp = d[d["market_type"] == "spread"]
    ids = team_ids(sp)
    sp = sp.assign(is_home=sp["abbr"].map(ids) == sp["home_team_id"])
    tot = d[d["market_type"] == "total"]
    over = tot[tot["abbr"].str.lower() == "over"]
    out = _summary(sp[sp["is_home"]], "spread").join(_summary(over, "total"), how="outer")
    bov = sp[sp["book"].isin(BOVADA)]
    tb = tot[tot["book"].isin(BOVADA)]
    prices = pd.concat([
        bov[bov["is_home"]].groupby("game_id")["odds"].first().rename("bov_home_odds"),
        bov[~bov["is_home"]].groupby("game_id")["odds"].first().rename("bov_away_odds"),
        tb[tb["abbr"].str.lower() == "over"].groupby("game_id")["odds"].first().rename("bov_over_odds"),
        tb[tb["abbr"].str.lower() == "under"].groupby("game_id")["odds"].first().rename("bov_under_odds")], axis=1)
    meta = d.groupby("game_id").agg(season=("season", "first"), week=("week", "first"), desc=("game_desc", "first"))
    out = meta.join(out).join(prices)
    out["spread_move"] = out["spread_close"] - out["spread_open"]  # < 0: moved toward the home team
    out["total_move"] = out["total_close"] - out["total_open"]
    return out


def load(cache: Path) -> pd.DataFrame:
    d = raw(cache)
    return games_table(d) if not d.empty else pd.DataFrame()
