"""NFL props: live picks (Telegram, paper mode), grading, and backtests.

`python -m marv props`                 picks for games in the next N hours (needs ODDS_API_KEY)
`python -m marv props --grade`         grade earlier picks from nflverse box scores
`python -m marv props-backtest`        walk-forward projection test (free data)
`python -m marv props-backtest --real` win rate and ROI against real past Bovado lines (paid credits)
"""

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from ..data.teams import NFL_TEAMS
from . import nfl as P
from . import odds as O

log = logging.getLogger(__name__)
SPORT = "americanfootball_nfl"
ET = ZoneInfo("America/New_York")
MARKET_BY_KEY = {m.key: k for k, m in P.MARKETS.items()}


def implied(price: float) -> float:
    return 100 / (price + 100) if price > 0 else -price / (-price + 100)


def payout(price: float) -> float:
    """Profit per 1 unit staked."""
    return price / 100 if price > 0 else 100 / -price


def shrink(p: np.ndarray) -> np.ndarray:
    """Pull probabilities toward 50% (the walk-forward test showed overconfidence at the extremes) and cap
    them at 15-85%: near-certain unders on low-count stats (e.g. 0.5 threes for a rare shooter) won 97%+ in
    the stress test only because a real book wouldn't offer them at a normal price."""
    k = float(os.environ.get("PROPS_SHRINK", "0.7"))
    return np.clip(0.5 + k * (np.asarray(p, float) - 0.5), 0.15, 0.85)


def _data(cache: Path, current: int):
    players = P.load_players(cache, list(range(current - 7, current + 1)), current)
    games = P.load_games(cache)
    return players, games


def _models(rows: pd.DataFrame, season: int) -> dict:
    out = {}
    for key, mk in P.MARKETS.items():
        data = P.eligible(rows, mk)
        train = data[data["season"] >= season - 6]
        if len(train) >= 500:
            out[key] = P.PropModel(mk).fit(train)
    return out


def upcoming_rows(players: pd.DataFrame, games: pd.DataFrame, game_ids: list[str]) -> pd.DataFrame:
    """Pre-game feature rows for every recent regular on the teams in `game_ids`."""
    sched = games[games["game_id"].isin(game_ids)]
    season = int(sched["season"].max())
    recent = players[players["season"] == season].sort_values("week")
    last = recent.groupby("player_id").tail(1)
    last = last[last["week"] >= recent["week"].max() - 3]  # still active
    fut = []
    for g in sched.itertuples():
        for team, opp in ((g.home_team, g.away_team), (g.away_team, g.home_team)):
            for r in last[last["team"] == team].itertuples():
                fut.append({"player_id": r.player_id, "player_display_name": r.player_display_name,
                            "position": r.position, "season": g.season, "week": g.week, "game_id": g.game_id,
                            "team": team, "opponent_team": opp})
    if not fut:
        return pd.DataFrame()
    rows = P.build_rows(pd.concat([players, pd.DataFrame(fut)], ignore_index=True), games)
    return rows[rows["game_id"].isin(game_ids)]


def _match_games(games: pd.DataFrame, evs: list[dict]) -> dict:
    """Odds API event id -> nflverse game id (same home team, kickoff within a day)."""
    out = {}
    g = games.assign(home_full=games["home_team"].map(lambda t: NFL_TEAMS.get(t, t)),
                     day=pd.to_datetime(games["gameday"]))
    for ev in evs:
        start = pd.Timestamp(ev["commence_time"]).tz_convert(ET).tz_localize(None).normalize()
        m = g[(g["home_full"] == ev["home_team"]) & ((g["day"] - start).abs() <= pd.Timedelta(days=1))]
        if not m.empty:
            out[ev["id"]] = m.iloc[0]["game_id"]
    return out


def evaluate(lines: pd.DataFrame, rows: pd.DataFrame, models: dict, markets: dict | None = None) -> pd.DataFrame:
    """Join posted lines to projections and price both sides."""
    markets = markets or P.MARKETS
    if lines.empty or rows.empty:
        return pd.DataFrame()
    rows = rows.assign(_name=rows["player_display_name"].map(O.norm_name))
    lines = lines.assign(_name=lines["player"].map(O.norm_name),
                         mkey=lines["market"].map({m.key: k for k, m in markets.items()}))
    out = []
    for key, part in lines.dropna(subset=["mkey"]).groupby("mkey"):
        if key not in models:
            continue
        mk = markets[key]
        cand = rows[rows["position"].isin(mk.positions)] if mk.positions else rows
        j = part.merge(cand, on=["_name", "game_id"], how="inner")
        if j.empty:
            continue
        j["proj"] = models[key].project(j)
        j["p_over"] = shrink(models[key].p_over(j["proj"].to_numpy(), j["line"].to_numpy()))
        j["stat"] = mk.stat
        j["label"] = mk.label
        out.append(j)
    if not out:
        return pd.DataFrame()
    df = pd.concat(out, ignore_index=True)
    over_ok = df["over_price"].notna()
    under_ok = df["under_price"].notna()
    df["edge_over"] = np.where(over_ok, df["p_over"] - df["over_price"].fillna(-110).map(implied), -1)
    df["edge_under"] = np.where(under_ok, (1 - df["p_over"]) - df["under_price"].fillna(-110).map(implied), -1)
    df["side"] = np.where(df["edge_over"] >= df["edge_under"], "Over", "Under")
    df["edge"] = df[["edge_over", "edge_under"]].max(axis=1)
    df["p_side"] = np.where(df["side"] == "Over", df["p_over"], 1 - df["p_over"])
    df["price"] = np.where(df["side"] == "Over", df["over_price"], df["under_price"])
    return df


def save_priced(state: Path, sport: str, df: pd.DataFrame) -> None:
    """Every priced prop from the latest run (not just picks), for /prop queries and the odds bot."""
    if df.empty:
        return
    cols = ["player", "label", "market", "line", "proj", "p_over", "over_price", "under_price", "book_title", "game_id"]
    rows = df[[c for c in cols if c in df]].copy()
    rows["sport"] = sport
    rows["updated"] = datetime.now(timezone.utc).isoformat()
    (state / f"props_priced_{sport}.json").write_text(rows.to_json(orient="records"))


def lookup(state: Path, player: str, market: str | None = None) -> list[dict]:
    """Latest priced props for a player (any sport), best name match first."""
    out = []
    for f in state.glob("props_priced_*.json"):
        try:
            rows = json.loads(f.read_text())
        except json.JSONDecodeError:
            continue
        want = O.norm_name(player)
        for r in rows:
            name = O.norm_name(r["player"])
            if (want in name or name in want) and (not market or market.lower() in (r["market"] + r.get("label", "")).lower()):
                out.append(r)
    return out


def picks(df: pd.DataFrame, min_edge: float | None = None, min_p: float = 0.55) -> pd.DataFrame:
    min_edge = float(os.environ.get("PROPS_MIN_EDGE", "0.05")) if min_edge is None else min_edge
    if df.empty:
        return df
    return df[(df["edge"] >= min_edge) & (df["p_side"] >= min_p)].sort_values("edge", ascending=False)


def card(p: pd.DataFrame, paper: bool, title: str = "🏈 NFL PLAYER PROPS",
         how: str = "player form + usage + opponent vs position + implied team total") -> str:
    head = title + (" (paper)" if paper else "")
    if p.empty:
        return head + "\nNo props cleared the edge filter."
    lines = [head]
    for r in p.head(15).itertuples():
        k = max(r.p_side - (1 - r.p_side) / payout(r.price), 0) / 4  # quarter Kelly
        lines.append(f"{r.player} {r.label} {r.side} {r.line:g} ({int(r.price):+d} {r.book_title}) | "
                     f"proj {r.proj:.1f}, {r.p_side:.0%} | edge {r.edge:+.1%} | stake {min(k, 0.02):.1%}")
    lines.append(f"Projection: {how}; Monte Carlo of past misses.")
    return "\n".join(lines)


def run_live(settings, hours: int = 36, dry_run: bool = False) -> str:
    from ..telegram import send_message
    if not settings.odds_api_key:
        return "ODDS_API_KEY is not set."
    cache = Path(settings.state_dir) / "cache"
    now = datetime.now(timezone.utc)
    season = now.year if now.month >= 3 else now.year - 1
    players, games = _data(cache, season)
    evs = [e for e in O.events(settings.odds_api_key, SPORT)
           if now <= pd.Timestamp(e["commence_time"]).to_pydatetime() <= now + timedelta(hours=hours)]
    match = _match_games(games, evs)
    if not match:
        return "No NFL games in the window."
    budget = O.Budget(int(os.environ.get("PROPS_MAX_CREDITS", "200")))
    lines = []
    for ev in evs:  # lines first: the models only get built when there's something to price
        if ev["id"] not in match:
            continue
        data = O.event_props(settings.odds_api_key, SPORT, ev["id"], [m.key for m in P.MARKETS.values()],
                             getattr(settings, "odds_book", "bovada") or "bovada", budget)
        lr = O.prop_rows(data, getattr(settings, "odds_book", "bovada") or "bovada")
        if not lr.empty:
            lines.append(lr.assign(game_id=match[ev["id"]]))
    if not lines:
        log.info("props: no posted prop lines yet, %s credits used", budget.used)
        return card(pd.DataFrame(), settings.paper_mode)
    hist = P.build_rows(players, games)
    models = _models(hist[hist["season"] <= season], season)
    rows = upcoming_rows(players, games, list(match.values()))
    df = evaluate(pd.concat(lines, ignore_index=True) if lines else pd.DataFrame(), rows, models)
    save_priced(Path(settings.state_dir), "nfl", df)
    chosen = picks(df)
    text = card(chosen, settings.paper_mode)
    if not dry_run and not chosen.empty:
        _save(Path(settings.state_dir), chosen)
        send_message(settings.telegram_bot_token, settings.telegram_chat_id, text)
    log.info("props: %d lines priced, %d picks, %s credits used", len(df), len(chosen), budget.used)
    return text


def _ledger(state: Path) -> Path:
    return state / "props_picks.json"


def _save(state: Path, chosen: pd.DataFrame) -> None:
    path = _ledger(state)
    book = json.loads(path.read_text()) if path.exists() else []
    seen = {(b["game_id"], b["player_id"], b["market"]) for b in book}
    for r in chosen.itertuples():
        if (r.game_id, r.player_id, r.market) in seen:
            continue
        book.append({"game_id": r.game_id, "player_id": r.player_id, "player": r.player, "market": r.market,
                     "stat": r.stat, "side": r.side, "line": r.line, "price": float(r.price), "p": float(r.p_side),
                     "proj": float(r.proj), "result": None})
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(book, indent=1))


def grade(settings) -> str:
    state = Path(settings.state_dir)
    path = _ledger(state)
    if not path.exists():
        return "No props picks yet."
    book = json.loads(path.read_text())
    now = datetime.now(timezone.utc)
    season = now.year if now.month >= 3 else now.year - 1
    players = P.load_players(state / "cache", [season - 1, season], season)
    stats = players.set_index(["game_id", "player_id"])
    for b in book:
        if b["result"] is not None or (b["game_id"], b["player_id"]) not in stats.index:
            continue
        val = float(stats.loc[(b["game_id"], b["player_id"]), b["stat"]])
        b["actual"] = val
        b["result"] = "push" if val == b["line"] else ("win" if (val > b["line"]) == (b["side"] == "Over") else "loss")
    path.write_text(json.dumps(book, indent=1))
    done = [b for b in book if b["result"] in ("win", "loss")]
    if not done:
        return "No graded props yet."
    wins = sum(b["result"] == "win" for b in done)
    units = sum(payout(b["price"]) if b["result"] == "win" else -1 for b in done)
    return f"NFL props record: {wins}-{len(done) - wins} ({wins / len(done):.1%}), {units:+.1f} units at the posted prices"


def backtest_free(cache: Path, seasons: list[int]) -> str:
    """Projection accuracy and calibration from free data (no sportsbook lines involved)."""
    players = P.load_players(cache, list(range(min(seasons) - 7, max(seasons) + 1)), max(seasons))
    rows = P.build_rows(players, P.load_games(cache))
    out = [f"NFL props walk-forward {seasons[0]}-{seasons[-1]} (no real lines here; use --real for those)"]
    for key, mk in P.MARKETS.items():
        df, models = P.walk_forward(rows, mk, seasons)
        if df.empty:
            continue
        s = mk.stat
        out.append(f"{mk.label}: {len(df)} player-games | average miss: model {np.mean(abs(df.proj - df[s])):.1f}, "
                   f"season average {np.nanmean(abs(df[f'szn_{s}'] - df[s])):.1f}, "
                   f"recent form {np.nanmean(abs(df[f'ewm_{s}'] - df[s])):.1f}")
    return "\n".join(out)


def backtest_real(settings, seasons: list[int], max_credits: int) -> str:
    """Grade the model against real past lines (Bovado preferred) one hour before kickoff."""
    cache = Path(settings.state_dir) / "cache"
    pcache = cache / "props_hist"
    budget = O.Budget(max_credits)
    players = P.load_players(cache, list(range(min(seasons) - 7, max(seasons) + 1)), max(seasons))
    games = P.load_games(cache)
    rows = P.build_rows(players, games)
    book = getattr(settings, "odds_book", "bovada") or "bovada"
    graded = []
    for season in seasons:
        models = {}
        for key, mk in P.MARKETS.items():
            data = P.eligible(rows, mk)
            train = data[(data["season"] < season) & (data["season"] >= season - 6)]
            if len(train) >= 500:
                models[key] = P.PropModel(mk).fit(train)
        sched = games[(games["season"] == season) & games["home_score"].notna()]
        lines = []
        try:
            for day, part in sched.groupby("gameday"):
                snap = (pd.Timestamp(day + " 12:00").tz_localize(ET).tz_convert("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")
                evs = O.historical_events(settings.odds_api_key, SPORT, snap, pcache, budget)
                match = _match_games(part, evs)
                for ev in evs:
                    if ev["id"] not in match:
                        continue
                    kick = pd.Timestamp(ev["commence_time"]) - pd.Timedelta(hours=1)
                    data = O.historical_event_props(settings.odds_api_key, SPORT, ev["id"],
                                                    kick.strftime("%Y-%m-%dT%H:%M:%SZ"),
                                                    [m.key for m in P.MARKETS.values()], f"{book},draftkings,fanduel",
                                                    pcache, budget)
                    lr = O.prop_rows(data, book)
                    if not lr.empty:
                        lines.append(lr.assign(game_id=match[ev["id"]]))
        except RuntimeError as exc:
            log.warning("%s: stopped early (%s); %s credits used", season, exc, budget.used)
        if not lines:
            continue
        season_rows = rows[rows["season"] == season]
        df = evaluate(pd.concat(lines, ignore_index=True), season_rows, models)
        if df.empty:
            continue
        df["actual"] = [r[s] for r, s in zip(df.to_dict("records"), df["stat"])]
        graded.append(df.dropna(subset=["actual"]))
    if not graded:
        return f"No historical prop lines retrieved ({budget.used} credits used)."
    g = pd.concat(graded, ignore_index=True)
    g["won"] = np.where(g["side"] == "Over", g["actual"] > g["line"], g["actual"] < g["line"])
    g["push"] = g["actual"] == g["line"]
    out = [f"NFL props vs real lines ({book} first), seasons {seasons}: {len(g)} priced props, {budget.used} credits used"]
    for label, part in [("all markets", g)] + [(m, x) for m, x in g.groupby("label")]:
        for e in (0.0, 0.05, 0.10):
            b = part[(part["edge"] >= e) & ~part["push"]]
            if len(b):
                units = np.where(b["won"], b["price"].map(payout), -1.0).sum()
                out.append(f"  {label}, edge >= {e:.0%}: {b['won'].mean():.1%} of {len(b)} bets, ROI {units / len(b):+.1%}")
    under = (g["actual"] < g["line"]).mean()
    out.append(f"  (always-under baseline: {under:.1%})")
    reports = Path(settings.state_dir) / "reports"
    reports.mkdir(parents=True, exist_ok=True)
    g.to_csv(reports / "props_backtest_real.csv", index=False)
    return "\n".join(out)
