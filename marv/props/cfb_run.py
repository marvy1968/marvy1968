"""College football props: live picks (paper, games with a top-30 team), grading and backtests."""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..data import oddsapi
from . import cfb as C
from . import nfl as P
from . import odds as O
from .basketball_run import _team
from .run import card, evaluate, payout, picks, save_priced

log = logging.getLogger(__name__)
SPORT = "americanfootball_ncaaf"
ET = ZoneInfo("America/New_York")
HOW = "player form + usage + opponent yards allowed + implied team total"
TOP = 30


def season_of(d: datetime) -> int:
    return d.year if d.month >= 7 else d.year - 1


def _models(rows: pd.DataFrame, season: int, before: int | None = None) -> dict:
    out = {}
    for key, mk in C.MARKETS.items():
        data = P.eligible(rows, mk)
        train = data[(data["season"] >= season - 4) & data[mk.stat].notna()]
        if before is not None:
            train = train[train["season"] < before]
        if len(train) >= 2000:
            out[key] = P.PropModel(mk).fit(train)
    return out


def _top_teams(games: pd.DataFrame, n: int = TOP) -> set[str]:
    el = pd.concat([games[["date", "home", "home_elo"]].set_axis(["date", "team", "elo"], axis=1),
                    games[["date", "away", "away_elo"]].set_axis(["date", "team", "elo"], axis=1)]).dropna()
    latest = el.sort_values("date").groupby("team")["elo"].last()
    return set(latest.sort_values(ascending=False).head(n).index)


def run_live(settings, hours: int = 48, dry_run: bool = False) -> str:
    from ..telegram import send_message
    if not settings.odds_api_key:
        return "ODDS_API_KEY is not set."
    cache = Path(settings.state_dir) / "cache"
    now = datetime.now(timezone.utc)
    season = season_of(now)
    games, players = C.load(cache, list(range(season - 4, season + 1)), season)
    top = _top_teams(games[games["season"] == season] if (games["season"] == season).any() else games)
    teams = sorted(set(players["team"]))
    evs = []
    for e in O.events(settings.odds_api_key, SPORT):
        start = pd.Timestamp(e["commence_time"]).to_pydatetime()
        home, away = _team(e["home_team"], teams), _team(e["away_team"], teams)
        if now <= start <= now + timedelta(hours=hours) and home and away and ({home, away} & top):
            evs.append((e, home, away))
    if not evs:
        return "No top-30 college football games in the window."
    # Closing-style spread and total for the implied team totals (2 credits for the whole slate).
    lines = {}
    try:
        for ev in oddsapi.fetch(settings.odds_api_key, SPORT):
            lines[ev["id"]] = oddsapi.consensus(ev, getattr(settings, "odds_book", "bovada") or "bovada")
    except Exception as exc:
        log.warning("ncaaf game lines: %s", exc)
    fut_games, fut_players = [], []
    last = players.merge(games[["game_id", "date"]], on="game_id").sort_values("date").groupby(["team", "player"]).tail(1)
    for e, home, away in evs:
        day = pd.Timestamp(e["commence_time"]).tz_convert(ET).tz_localize(None).normalize()
        o = lines.get(e["id"])
        fut_games.append({"game_id": e["id"], "date": day, "season": season, "home": home, "away": away,
                          "neutral": False, "home_points": np.nan, "away_points": np.nan,
                          "spread": getattr(o, "spread", np.nan), "total": getattr(o, "total", np.nan),
                          "home_elo": np.nan, "away_elo": np.nan, "home_fbs": True, "away_fbs": True})
        for team, opp in ((home, away), (away, home)):
            for r in last[(last["team"] == team) & (last["date"] >= day - pd.Timedelta(days=300))].itertuples():
                fut_players.append({"game_id": e["id"], "team": team, "opponent_team": opp, "player": r.player,
                                    "season": season})
    book = getattr(settings, "odds_book", "bovada") or "bovada"
    budget = O.Budget(int(os.environ.get("PROPS_MAX_CREDITS", "200")))
    got = []
    for e, _, _ in evs:
        try:
            data = O.event_props(settings.odds_api_key, SPORT, e["id"], [m.key for m in C.MARKETS.values()], book, budget)
        except RuntimeError:
            break
        lr = O.prop_rows(data, book)
        if not lr.empty:
            got.append(lr.assign(game_id=e["id"]))
    if not got:  # nothing posted: skip the (slow) model build
        log.info("cfb props: no posted prop lines yet, %s credits", budget.used)
        return card(pd.DataFrame(), settings.paper_mode, "🏈 COLLEGE FOOTBALL PLAYER PROPS (top 30)", HOW)
    allg = pd.concat([games, pd.DataFrame(fut_games)], ignore_index=True)
    rows = C.build_rows(allg, pd.concat([players, pd.DataFrame(fut_players)], ignore_index=True))
    models = _models(C.build_rows(games, players), season)  # history only: future rows carry no outcomes
    fut = rows[rows["game_id"].isin([e["id"] for e, _, _ in evs])]
    df = evaluate(pd.concat(got, ignore_index=True), fut, models, C.MARKETS)
    save_priced(Path(settings.state_dir), "cfb", df)
    chosen = picks(df)
    text = card(chosen, settings.paper_mode, "🏈 COLLEGE FOOTBALL PLAYER PROPS (top 30)", HOW)
    if not dry_run and not chosen.empty:
        _save(Path(settings.state_dir), chosen)
        send_message(settings.telegram_bot_token, settings.telegram_chat_id, text)
    log.info("cfb props: %d lines priced, %d picks, %s credits", len(df), len(chosen), budget.used)
    return text


def _ledger(state: Path) -> Path:
    return state / "props_cfb_picks.json"


def _save(state: Path, chosen: pd.DataFrame) -> None:
    path = _ledger(state)
    book = json.loads(path.read_text()) if path.exists() else []
    seen = {(b["date"], b["player"], b["market"]) for b in book}
    for r in chosen.itertuples():
        key = (str(pd.Timestamp(r.date).date()), r.player, r.market)
        if key not in seen:
            book.append({"date": key[0], "player": r.player, "team": r.team, "market": r.market, "stat": r.stat,
                         "side": r.side, "line": r.line, "price": float(r.price), "p": float(r.p_side),
                         "proj": float(r.proj), "result": None})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(book, indent=1))


def grade(settings) -> str:
    state = Path(settings.state_dir)
    path = _ledger(state)
    if not path.exists():
        return "No college football props picks yet."
    book = json.loads(path.read_text())
    season = season_of(datetime.now(timezone.utc))
    games, players = C.load(state / "cache", [season], season)
    p = players.merge(games[["game_id", "date"]], on="game_id")
    p["_key"] = list(zip(p["date"].dt.strftime("%Y-%m-%d"), p["team"], p["player"].map(O.norm_name)))
    stats = p.drop_duplicates("_key").set_index("_key")
    for b in book:
        k = (b["date"], b["team"], O.norm_name(b["player"]))
        if b["result"] is None and k in stats.index:
            val = float(stats.loc[[k], b["stat"]].fillna(0).iloc[0])
            b["actual"] = val
            b["result"] = "push" if val == b["line"] else ("win" if (val > b["line"]) == (b["side"] == "Over") else "loss")
    path.write_text(json.dumps(book, indent=1))
    done = [b for b in book if b["result"] in ("win", "loss")]
    if not done:
        return "No graded college football props yet."
    wins = sum(b["result"] == "win" for b in done)
    units = sum(payout(b["price"]) if b["result"] == "win" else -1 for b in done)
    return f"CFB props record: {wins}-{len(done) - wins} ({wins / len(done):.1%}), {units:+.1f} units"


def backtest_free(cache: Path, seasons: list[int]) -> str:
    games, players = C.load(cache, list(range(min(seasons) - 4, max(seasons) + 1)), None)
    rows = C.build_rows(games, players)
    out = [f"College football props walk-forward {seasons[0]}-{seasons[-1]} (no real lines here; use --real)"]
    for key, mk in C.MARKETS.items():
        df, _ = P.walk_forward(rows, mk, seasons, train_years=4)
        if not df.empty:
            s = mk.stat
            out.append(f"{mk.label}: {len(df)} player-games | average miss: model {np.mean(abs(df.proj - df[s])):.1f}, "
                       f"season average {np.nanmean(abs(df[f'szn_{s}'] - df[s])):.1f}")
    return "\n".join(out)


def backtest_real(settings, seasons: list[int], max_credits: int) -> str:
    """Real past lines for games with a top-30 team (by pregame Elo), ~40 credits per game."""
    cache = Path(settings.state_dir) / "cache"
    pcache = cache / "props_hist_cfb"
    budget = O.Budget(max_credits)
    games, players = C.load(cache, list(range(min(seasons) - 4, max(seasons) + 1)), None)
    rows = C.build_rows(games, players)
    book = getattr(settings, "odds_book", "bovada") or "bovada"
    graded = []
    for season in seasons:
        models = _models(rows, season, before=season)
        sg = games[games["season"] == season]
        lines = []
        try:
            for day, part in sg.groupby(sg["date"].dt.strftime("%Y-%m-%d")):
                top = _top_teams(games[(games["season"] == season) & (games["date"] <= day)])
                part = part[part["home"].isin(top) | part["away"].isin(top)]
                if part.empty:
                    continue
                snap = pd.Timestamp(day + " 11:00").tz_localize(ET).tz_convert("UTC").strftime("%Y-%m-%dT%H:%M:%SZ")
                for ev in O.historical_events(settings.odds_api_key, SPORT, snap, pcache, budget):
                    home = _team(ev["home_team"], list(part["home"]))
                    if not home:
                        continue
                    gid = part[part["home"] == home]["game_id"].iloc[0]
                    kick = pd.Timestamp(ev["commence_time"]) - pd.Timedelta(hours=1)
                    data = O.historical_event_props(settings.odds_api_key, SPORT, ev["id"], kick.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                    [m.key for m in C.MARKETS.values()], f"{book},draftkings,fanduel",
                                                    pcache, budget)
                    lr = O.prop_rows(data, book)
                    if not lr.empty:
                        lines.append(lr.assign(game_id=gid))
        except RuntimeError as exc:
            log.warning("%s: stopped early (%s); %s credits used", season, exc, budget.used)
        if lines:
            df = evaluate(pd.concat(lines, ignore_index=True), rows[rows["season"] == season], models, C.MARKETS)
            if not df.empty:
                df["actual"] = [r[s] for r, s in zip(df.to_dict("records"), df["stat"])]
                graded.append(df.dropna(subset=["actual"]))
    if not graded:
        return f"No historical college football prop lines retrieved ({budget.used} credits used)."
    g = pd.concat(graded, ignore_index=True)
    g["won"] = np.where(g["side"] == "Over", g["actual"] > g["line"], g["actual"] < g["line"])
    g["push"] = g["actual"] == g["line"]
    out = [f"CFB props vs real lines ({book} first), seasons {seasons}: {len(g)} priced props, {budget.used} credits used"]
    for label, part in [("all markets", g)] + list(g.groupby("label")):
        for e in (0.0, 0.05, 0.10):
            b = part[(part["edge"] >= e) & ~part["push"]]
            if len(b):
                units = np.where(b["won"], b["price"].map(payout), -1.0).sum()
                out.append(f"  {label}, edge >= {e:.0%}: {b['won'].mean():.1%} of {len(b)} bets, ROI {units / len(b):+.1%}")
    out.append(f"  (always-under baseline: {(g['actual'] < g['line']).mean():.1%})")
    reports = Path(settings.state_dir) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    g.to_csv(reports / "props_cfb_backtest_real.csv", index=False)
    return "\n".join(out)
