"""Basketball props (NCAA men, NCAA women, WNBA): live picks (paper), grading and backtests.
Mirrors props/run.py (NFL). Every function takes `sport` in ("ncaab", "ncaaw", "wnba")."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..data.teams import similarity
from . import nfl as P
from . import ncaab as B
from . import odds as O
from .run import card, evaluate, payout, picks

log = logging.getLogger(__name__)
ODDS_KEYS = {"ncaab": "basketball_ncaab", "ncaaw": "basketball_wncaab", "wnba": "basketball_wnba"}
TITLES = {"ncaab": "🏀 NCAAB PLAYER PROPS", "ncaaw": "🏀 NCAAW PLAYER PROPS", "wnba": "🏀 WNBA PLAYER PROPS"}
ET = ZoneInfo("America/New_York")
HOW = "player form + minutes and shot volume + opponent vs position + both teams' pace"


def season_of(d: datetime, sport: str = "ncaab") -> int:
    if sport == "wnba":
        return d.year
    return d.year + 1 if d.month >= 8 else d.year  # 2025-26 season = 2026


def _team(name: str, teams: list[str]) -> str | None:
    best = max(teams, key=lambda t: similarity(name, t), default=None)
    return best if best and similarity(name, best) >= 0.75 else None


def _models(rows: pd.DataFrame, season: int, sport: str = "ncaab") -> dict:
    out = {}
    for key, mk in B.MARKETS.items():
        data = P.eligible(rows, mk)
        train = data[(data["season"] >= season - 4) & data[mk.stat].notna()]
        if len(train) >= (2000 if sport != "wnba" else 800):
            out[key] = P.PropModel(mk).fit(train)
    return out


def upcoming(box: pd.DataFrame, games: list[dict], sport: str = "ncaab") -> pd.DataFrame:
    """Feature rows for each recent player on the teams in `games` (dicts: id, date, home, away)."""
    teams = sorted(box["team"].unique())
    last = box.sort_values("date").groupby("player_id").tail(1)
    fut = []
    for g in games:
        home, away = _team(g["home"], teams), _team(g["away"], teams)
        if not home or not away:
            continue
        for team, opp, h in ((home, away, 1.0), (away, home, 0.0)):
            roster = last[(last["team"] == team) & (last["date"] >= g["date"] - pd.Timedelta(days=240))]
            for r in roster.itertuples():
                fut.append({"player_id": r.player_id, "player_display_name": r.player_display_name,
                            "position": r.position, "season": season_of(g["date"].to_pydatetime(), sport),
                            "game_id": g["id"], "date": g["date"], "team": team, "opponent_team": opp,
                            "is_home": h, "team_score": np.nan, "opponent_team_score": np.nan})
    if not fut:
        return pd.DataFrame()
    rows = B.build_rows(pd.concat([box, pd.DataFrame(fut)], ignore_index=True), sport=sport)
    return rows[rows["game_id"].isin([g["id"] for g in games])]


def run_live(settings, hours: int = 30, dry_run: bool = False, sport: str = "ncaab") -> str:
    from ..telegram import send_message
    if not settings.odds_api_key:
        return "ODDS_API_KEY is not set."
    cache = Path(settings.state_dir) / "cache"
    now = datetime.now(timezone.utc)
    season = season_of(now, sport)
    evs = [e for e in O.events(settings.odds_api_key, ODDS_KEYS[sport])
           if now <= pd.Timestamp(e["commence_time"]).to_pydatetime() <= now + timedelta(hours=hours)]
    if not evs:
        return f"No {sport.upper()} games in the window."
    box = B.load_box(cache, list(range(season - 4, season + 1)), season, sport)
    rows = B.build_rows(box, sport=sport)
    models = _models(rows, season, sport)
    games = [{"id": e["id"], "home": e["home_team"], "away": e["away_team"],
              "date": pd.Timestamp(e["commence_time"]).tz_convert(ET).tz_localize(None).normalize()} for e in evs]
    fut = upcoming(box, games, sport)
    book = getattr(settings, "odds_book", "bovado") or "bovado"
    budget = O.Budget(int(os.environ.get("PROPS_MAX_CREDITS", "300")))
    lines = []
    for ev in evs:
        try:
            data = O.event_props(settings.odds_api_key, ODDS_KEYS[sport], ev["id"], [m.key for m in B.MARKETS.values()], book, budget)
        except RuntimeError:
            break
        lr = O.prop_rows(data, book)
        if not lr.empty:
            lines.append(lr.assign(game_id=ev["id"]))
    df = evaluate(pd.concat(lines, ignore_index=True) if lines else pd.DataFrame(), fut, models, B.MARKETS)
    chosen = picks(df)
    text = card(chosen, settings.paper_mode, TITLES[sport], HOW)
    if not dry_run and not chosen.empty:
        _save(Path(settings.state_dir), chosen, sport)
        send_message(settings.telegram_bot_token, settings.telegram_chat_id, text)
    log.info("%s props: %d lines priced, %d picks, %s credits", sport, len(df), len(chosen), budget.used)
    return text


def _ledger(state: Path, sport: str) -> Path:
    return state / f"props_{sport}_picks.json"


def _save(state: Path, chosen: pd.DataFrame, sport: str) -> None:
    path = _ledger(state, sport)
    book = json.loads(path.read_text()) if path.exists() else []
    seen = {(b["date"], b["player"], b["market"]) for b in book}
    for r in chosen.itertuples():
        key = (str(pd.Timestamp(r.date).date()), r.player, r.market)
        if key in seen:
            continue
        book.append({"date": key[0], "player": r.player, "team": r.team, "market": r.market, "stat": r.stat,
                     "side": r.side, "line": r.line, "price": float(r.price), "p": float(r.p_side),
                     "proj": float(r.proj), "result": None})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(book, indent=1))


def grade(settings, sport: str = "ncaab") -> str:
    state = Path(settings.state_dir)
    path = _ledger(state, sport)
    if not path.exists():
        return f"No {sport.upper()} props picks yet."
    book = json.loads(path.read_text())
    season = season_of(datetime.now(timezone.utc), sport)
    box = B.load_box(state / "cache", [season], season, sport)
    box["_key"] = list(zip(box["date"].dt.strftime("%Y-%m-%d"), box["player_display_name"].map(O.norm_name)))
    stats = box.drop_duplicates("_key").set_index("_key")
    for b in book:
        k = (b["date"], O.norm_name(b["player"]))
        if b["result"] is None and k in stats.index:
            val = float(stats.loc[[k], b["stat"]].iloc[0])
            b["actual"] = val
            b["result"] = "push" if val == b["line"] else ("win" if (val > b["line"]) == (b["side"] == "Over") else "loss")
    path.write_text(json.dumps(book, indent=1))
    done = [b for b in book if b["result"] in ("win", "loss")]
    if not done:
        return f"No graded {sport.upper()} props yet."
    wins = sum(b["result"] == "win" for b in done)
    units = sum(payout(b["price"]) if b["result"] == "win" else -1 for b in done)
    return f"{sport.upper()} props record: {wins}-{len(done) - wins} ({wins / len(done):.1%}), {units:+.1f} units"


def backtest_free(cache: Path, seasons: list[int], sport: str = "ncaab") -> str:
    box = B.load_box(cache, list(range(min(seasons) - 4, max(seasons) + 1)), None, sport)
    rows = B.build_rows(box, sport=sport)
    out = [f"{sport.upper()} props walk-forward {seasons[0]}-{seasons[-1]} (no real lines here; use --real for those)"]
    for key, mk in B.MARKETS.items():
        df, _ = P.walk_forward(rows, mk, seasons, train_years=4)
        if df.empty:
            continue
        s = mk.stat
        out.append(f"{mk.label}: {len(df)} player-games | average miss: model {np.mean(abs(df.proj - df[s])):.2f}, "
                   f"season average {np.nanmean(abs(df[f'szn_{s}'] - df[s])):.2f}, "
                   f"recent form {np.nanmean(abs(df[f'ewm_{s}'] - df[s])):.2f}")
    return "\n".join(out)


def backtest_real(settings, seasons: list[int], max_credits: int, top: int = 50, sport: str = "ncaab") -> str:
    """Real past lines for games involving a top-`top` team (by pre-game scoring margin); the
    Odds API charges ~40 credits per game for 4 markets, so a full D1 season would cost ~200k."""
    cache = Path(settings.state_dir) / "cache"
    pcache = cache / f"props_hist_{sport}"
    budget = O.Budget(max_credits)
    box = B.load_box(cache, list(range(min(seasons) - 4, max(seasons) + 1)), None, sport)
    rows = B.build_rows(box, sport=sport)
    book = getattr(settings, "odds_book", "bovado") or "bovado"
    graded = []
    for season in seasons:
        models = {}
        for key, mk in B.MARKETS.items():
            data = P.eligible(rows, mk)
            train = data[(data["season"] < season) & (data["season"] >= season - 4)]
            if len(train) >= (2000 if sport != "wnba" else 800):
                models[key] = P.PropModel(mk).fit(train)
        sr = rows[rows["season"] == season]
        tg = sr.drop_duplicates(["game_id", "team"]).sort_values("date")
        tg["margin"] = tg["ctx_team_pts"] - tg["ctx_opp_pts"]
        lines = []
        try:
            for day, part in tg.groupby(tg["date"].dt.strftime("%Y-%m-%d")):
                before = tg[tg["date"] < day].groupby("team")["margin"].last()
                good = set(before.rank(ascending=False).loc[lambda r: r <= top].index)
                if not good or part[part["team"].isin(good)].empty:
                    continue
                snap = pd.Timestamp(day + " 11:00").tz_localize(ET).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
                evs = O.historical_events(settings.odds_api_key, ODDS_KEYS[sport], snap, pcache, budget)
                for ev in evs:
                    home, away = _team(ev["home_team"], list(part["team"])), _team(ev["away_team"], list(part["team"]))
                    if not home or not away or not ({home, away} & good):
                        continue
                    gid = part[part["team"] == home]["game_id"].iloc[0]
                    kick = pd.Timestamp(ev["commence_time"]) - pd.Timedelta(hours=1)
                    data = O.historical_event_props(settings.odds_api_key, ODDS_KEYS[sport], ev["id"],
                                                    kick.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                    [m.key for m in B.MARKETS.values()],
                                                    f"{book},draftkings,fanduel", pcache, budget)
                    lr = O.prop_rows(data, book)
                    if not lr.empty:
                        lines.append(lr.assign(game_id=gid))
        except RuntimeError as exc:
            log.warning("%s: stopped early (%s); %s credits used", season, exc, budget.used)
        if lines:
            df = evaluate(pd.concat(lines, ignore_index=True), sr, models, B.MARKETS)
            if not df.empty:
                df["actual"] = [r[s] for r, s in zip(df.to_dict("records"), df["stat"])]
                graded.append(df.dropna(subset=["actual"]))
    if not graded:
        return f"No historical {sport.upper()} prop lines retrieved ({budget.used} credits used)."
    g = pd.concat(graded, ignore_index=True)
    g["won"] = np.where(g["side"] == "Over", g["actual"] > g["line"], g["actual"] < g["line"])
    g["push"] = g["actual"] == g["line"]
    out = [f"{sport.upper()} props vs real lines ({book} first), seasons {seasons}: {len(g)} priced props, {budget.used} credits used"]
    for label, part in [("all markets", g)] + list(g.groupby("label")):
        for e in (0.0, 0.05, 0.10):
            b = part[(part["edge"] >= e) & ~part["push"]]
            if len(b):
                units = np.where(b["won"], b["price"].map(payout), -1.0).sum()
                out.append(f"  {label}, edge >= {e:.0%}: {b['won'].mean():.1%} of {len(b)} bets, ROI {units / len(b):+.1%}")
    out.append(f"  (always-under baseline: {(g['actual'] < g['line']).mean():.1%})")
    reports = Path(settings.state_dir) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    g.to_csv(reports / f"props_{sport}_backtest_real.csv", index=False)
    return "\n".join(out)
