"""EuroLeague Women stats module: FIBA LiveStats box scores (FIBA runs the competition).

Match ids come from FIBA's competition pages (EWL_EVENT_URLS in .env, comma-separated, one per
season, e.g. https://www.fiba.basketball/en/events/euroleague-women-25-26/games) or from a
state/ewl_matches.csv file (match_id[,date]). `python -m marv probe-ewl` verifies the sources on
the VM. No sportsbook feed lists this league, so it produces win probabilities, not priced edges.
"""

import logging
import os
from pathlib import Path

import pandas as pd
import requests

from ..data import fiba
from ..ratings import RatingParams
from ..sims import basketball
from .basketball import BasketballStats, _sim_for
from .experts import ExpertConfig

log = logging.getLogger(__name__)


def event_urls() -> list[str]:
    return [u.strip() for u in os.environ.get("EWL_EVENT_URLS", "").split(",") if u.strip()]


def discover_match_ids(cache: Path) -> list[tuple[int, object]]:
    ids: dict[int, object] = {}
    csv = cache.parent / "ewl_matches.csv"
    if csv.exists():
        df = pd.read_csv(csv)
        for r in df.itertuples():
            ids[int(r.match_id)] = pd.to_datetime(getattr(r, "date", None), errors="coerce")
    for url in event_urls():
        try:
            html = requests.get(url, headers=fiba.UA, timeout=30).text
            for mid in fiba.match_ids(html):
                ids.setdefault(mid, pd.NaT)
        except requests.RequestException as exc:
            log.warning("FIBA event page %s unavailable: %s", url, exc)
    return sorted(ids.items())


class EuroLeagueWomenStats(BasketballStats):
    league = "euroleague_women"
    backfill_league = False

    def read_box(self, cache: Path, seasons: list[int], current: int | None) -> list[pd.DataFrame]:
        rows = []
        for mid, known_date in discover_match_ids(cache):
            data = fiba.fetch_match(mid, cache)
            if not data or not fiba.is_final(data):
                continue
            date = known_date if pd.notna(known_date) else fiba.match_date(data)
            for row in fiba.parse_totals(data, mid):
                row["game_date"] = date
                row["_mid"] = mid
                rows.append(row)
        if not rows:
            return []
        box = pd.DataFrame(rows)
        # FIBA match ids increase through a season; fill any missing dates in id order (2-3 games a week).
        missing = box["game_date"].isna()
        if missing.any():
            order = box.loc[missing, "_mid"].rank(method="dense")
            box.loc[missing, "game_date"] = pd.Timestamp("2025-10-01") + pd.to_timedelta(order * 3, unit="D")
        box["game_date"] = pd.to_datetime(box["game_date"])
        box["season"] = [self.season_of(d) for d in box["game_date"]]
        box["season_type"] = 2
        return [box.drop(columns="_mid")]

    def season_of(self, date):
        return date.year if date.month >= 8 else date.year - 1


EUROLEAGUE_WOMEN = EuroLeagueWomenStats(
    key="euroleague_women", name="EuroLeague Women", simulate=_sim_for(basketball.NCAAW),
    rating_params=RatingParams(multiplicative=False, home_adv=3.0, shrink=4, half_life_days=120, mov_cap=25),
    halflife=6, chunk_days=14, first_season=2018, has_lines=False, adjust_schedule=True,
    experts=ExpertConfig(rf_min_leaf=15, rf_trees=100, min_train_rows=150))
