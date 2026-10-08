"""College football stats module: CFBD per-game team box scores (every category CFBD reports),
predicting only games involving the top 30 teams by power rating."""

import json
import re
from pathlib import Path

import numpy as np
import pandas as pd

from ..data.cfbd import CFBDClient
from ..ratings import RatingParams
from ..sims import football
from .base import StatsModule
from .experts import ExpertConfig

TOP_N = 30
_SPLIT = re.compile(r"^\s*(-?\d+)\s*-\s*(-?\d+)\s*$")
_CLOCK = re.compile(r"^\s*(\d+):(\d{2})\s*$")


def parse_stat(category: str, value) -> dict[str, float]:
    """CFBD box values: plain numbers, "made-att" splits, or "mm:ss" possession time."""
    s = str(value)
    m = _SPLIT.match(s)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        out = {f"{category}_a": a, f"{category}_b": b}
        if category.endswith("Eff") or category == "completionAttempts":
            out[f"{category}_pct"] = a / b if b else np.nan
        return out
    m = _CLOCK.match(s)
    if m:
        return {category: int(m.group(1)) + int(m.group(2)) / 60}
    try:
        return {category: float(s)}
    except ValueError:
        return {}


def team_box_rows(payload: list[dict]) -> list[dict]:
    rows = []
    for game in payload:
        for t in game.get("teams", []):
            row = {"game_id": str(game.get("id")), "team": t.get("team", t.get("school")),
                   "points": t.get("points")}
            for st in t.get("stats", []):
                row.update(parse_stat(st.get("category", ""), st.get("stat")))
            rows.append(row)
    return rows


class NCAAFStats(StatsModule):
    """Needs CFBD_API_KEY; responses are cached per season/week under the cache directory."""

    api_key: str = ""

    def _cached(self, cache: Path, name: str, call, refresh: bool):
        path = cache / "cfbd" / name
        if path.exists() and not refresh:
            return json.loads(path.read_text())
        data = call()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data))
        return data

    def load(self, cache: Path, seasons: list[int], current: int | None = None):
        import os
        client = CFBDClient(self.api_key or os.environ.get("CFBD_API_KEY", ""))
        games_raw, box_rows, lines_raw, talent_rows = [], [], [], []
        for season in seasons:
            live = season == current
            for stype in ("regular", "postseason"):
                g = self._cached(cache, f"games_{season}_{stype}.json", lambda: client.games(season, season_type=stype), live)
                games_raw += [dict(x, _stype=stype) for x in g]
                lines_raw += self._cached(cache, f"lines_{season}_{stype}.json",
                                          lambda: client._get("/lines", year=season, seasonType=stype), live)
                weeks = sorted({x.get("week") for x in g if x.get("week") is not None})
                if stype == "regular":
                    talent = self._cached(cache, f"talent_{season}.json", lambda: client._get("/talent", year=season), False)
                    talent_rows += [{"season": season, "team": t.get("team", t.get("school")),
                                     "talent": pd.to_numeric(t.get("talent"), errors="coerce")} for t in talent]
                for week in weeks:
                    done = all(x.get("completed") for x in g if x.get("week") == week)
                    box = self._cached(cache, f"box_{season}_{stype}_{week}.json",
                                       lambda: client._get("/games/teams", year=season, week=week, seasonType=stype),
                                       live and not done)
                    box_rows += team_box_rows(box)

        def gv(x, camel, snake, default=None):
            return x.get(camel, x.get(snake, default))

        games = pd.DataFrame([{
            "game_id": str(x.get("id")), "date": pd.to_datetime(gv(x, "startDate", "start_date")).tz_localize(None)
            if pd.to_datetime(gv(x, "startDate", "start_date")).tzinfo else pd.to_datetime(gv(x, "startDate", "start_date")),
            "season": x.get("season"), "home": gv(x, "homeTeam", "home_team"), "away": gv(x, "awayTeam", "away_team"),
            "neutral": bool(gv(x, "neutralSite", "neutral_site", False)),
            "home_points": gv(x, "homePoints", "home_points"), "away_points": gv(x, "awayPoints", "away_points"),
            "home_fbs": str(gv(x, "homeClassification", "home_division", "fbs")).lower() == "fbs",
            "away_fbs": str(gv(x, "awayClassification", "away_division", "fbs")).lower() == "fbs",
        } for x in games_raw])
        games["date"] = games["date"].dt.normalize()
        lines = {}
        preferred = ("consensus", "draftkings", "espn bet", "bovada")
        for entry in lines_raw:
            ls = sorted(entry.get("lines", []), key=lambda l: preferred.index((l.get("provider") or "").lower())
                        if (l.get("provider") or "").lower() in preferred else 9)
            if ls:
                lines[str(entry.get("id"))] = ls[0]
        for col, keys in (("spread", ("spread",)), ("total", ("overUnder", "over_under")),
                          ("home_ml", ("homeMoneyline", "home_moneyline")), ("away_ml", ("awayMoneyline", "away_moneyline"))):
            games[col] = games["game_id"].map(lambda gid: next((pd.to_numeric(lines[gid].get(k), errors="coerce")
                                                                for k in keys if gid in lines and lines[gid].get(k) is not None), np.nan))

        box = pd.DataFrame(box_rows).drop(columns=["points"], errors="ignore")
        long = []
        for side, other in (("home", "away"), ("away", "home")):
            part = games[["game_id", "date", "season", side, other, f"{side}_points", "neutral"]].rename(
                columns={side: "team", other: "opp", f"{side}_points": "points"})
            part["home"] = np.where(part["neutral"], 0.5, 1.0 if side == "home" else 0.0)
            long.append(part.drop(columns="neutral"))
        tg = pd.concat(long, ignore_index=True).merge(box, on=["game_id", "team"], how="left")
        # Roster talent composite (247Sports, via CFBD): a stable anchor for lopsided matchups.
        if talent_rows:
            tal = pd.DataFrame(talent_rows).drop_duplicates(["season", "team"])
            tg = tg.merge(tal, on=["season", "team"], how="left")
        if "talent" not in tg:
            tg["talent"] = np.nan
        tg["talent"] = tg["talent"].fillna(tg["talent"].quantile(0.1) if tg["talent"].notna().any() else 0)
        if "totalYards" in tg and "rushingAttempts" in tg and "completionAttempts_b" in tg:
            tg["yards_per_play"] = tg["totalYards"] / (tg["rushingAttempts"] + tg["completionAttempts_b"])
        return games, tg

    extra_cols = ["talent"]

    def stat_columns(self, tg):
        return [c for c in super().stat_columns(tg) if c not in self.extra_cols]

    def season_of(self, date):
        return date.year if date.month >= 7 else date.year - 1

    def focus(self, rows, panel):
        top = self.top_teams(panel.ratings, list(panel.ratings.offense))
        return rows[rows["team"].isin(top) | rows["opp"].isin(top)]

    @staticmethod
    def top_teams(ratings, teams, n: int = TOP_N) -> set[str]:
        return set(sorted(teams, key=ratings.strength, reverse=True)[:n])


def _sim(h, a, n, rng):
    return football.simulate_game(h, a, football.CFB, n=n, rng=rng)


NCAAF = NCAAFStats(key="ncaaf", name="College Football (top 30)", simulate=_sim,
                   rating_params=RatingParams(multiplicative=False, home_adv=2.5, shrink=3, half_life_days=120, mov_cap=28),
                   halflife=5, chunk_days=7, first_season=2014, adjust_schedule=True,
                   experts=ExpertConfig(rf_min_leaf=30))


class NCAAFPbpStats(NCAAFStats):
    """Same model fed by free play-by-play (no CFBD key): yards per play, EPA, success rate, explosiveness,
    third downs, red zone, field position, turnovers and more for every FBS team, plus closing lines."""

    extra_cols: list = []

    def load(self, cache: Path, seasons: list[int], current: int | None = None):
        from ..data import cfb_pbp
        return cfb_pbp.load(cache, seasons, current)


NCAAF_PBP = NCAAFPbpStats(key="ncaaf", name="College Football (top 30, play-by-play stats)", simulate=_sim,
                          rating_params=NCAAF.rating_params, halflife=5, chunk_days=7, first_season=2014,
                          adjust_schedule=True, experts=ExpertConfig(rf_min_leaf=30))
