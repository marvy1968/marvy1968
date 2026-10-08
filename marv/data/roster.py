"""Basketball roster availability before a game: the share of a team's regular minutes from players who
sat out its most recent game (a proxy for injuries that's known before tip-off).

Backtest (ANALYSIS.md): NCAA men's favorites missing 15%+ of regular minutes won 77.9% vs 83.3% for
full-strength favorites when Marv was 70%+ confident.
"""

from pathlib import Path

import numpy as np
import pandas as pd


def missing_share(cache: Path, sport: str, season: int, teams: list[str]) -> dict[str, float]:
    from ..props.ncaab import load_box
    try:
        b = load_box(cache, [season], season, sport)
    except Exception:
        return {}
    if b.empty:
        return {}
    b = b.sort_values("date")
    out = {}
    for team in set(teams):
        from .teams import similarity
        names = b["team"].unique()
        best = max(names, key=lambda n: similarity(team, n), default=None)
        if best is None or similarity(team, best) < 0.75:
            continue
        tb = b[b["team"] == best]
        last_game = tb["game_id"].iloc[-1]
        recent = tb[tb["game_id"].isin(tb["game_id"].drop_duplicates().tail(6))]
        usual = recent[recent["game_id"] != last_game].groupby("player_id")["minutes"].mean()
        regulars = usual[usual >= 15]
        if regulars.empty:
            continue
        played_last = set(tb.loc[(tb["game_id"] == last_game) & (tb["minutes"] > 0), "player_id"])
        missing = regulars[~regulars.index.isin(played_last)]
        out[team] = float(missing.sum() / regulars.sum())
    return out
