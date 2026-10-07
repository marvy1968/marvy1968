"""Command line: python -m marv {run,backtest,results,test-telegram,get-chat-id,sports}."""

import argparse
import logging
import sys
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

from .config import Settings
from .engine import grade, predict
from .markets import no_vig
from .sources import load_for_run, load_games
from .stats import registry as stats_registry
from .sports import SPORTS, Sport
from .state import Store
from .telegram import format_card, get_chat_ids, send_message

log = logging.getLogger("marv")
ET = ZoneInfo("America/New_York")


def _alert_failure(s: Settings, what: str) -> None:
    try:
        tb = traceback.format_exc()[-1500:].replace("&", "&amp;").replace("<", "&lt;")
        send_message(s.telegram_bot_token, s.telegram_chat_id, f"⚠️ Marv bot: {what} failed\n<pre>{tb}</pre>")
    except Exception:
        log.exception("could not send failure alert")


def run_sport(sport: Sport, s: Settings, store: Store, now: datetime, hours: int, dry_run: bool) -> None:
    history, slate, ctx = load_for_run(sport, s, now, hours)
    if store.resolve(sport.key, history):
        log.info("%s: graded finished picks", sport.key)
    if not slate:
        log.info("%s: no games in the next %dh", sport.key, hours)
        return
    store.remember_lines(sport.key, slate)
    projections = None
    if s.stats_model and sport.key in stats_registry.MODULES:
        projections = stats_registry.project(sport.key, slate, s, now)
        log.info("%s: stats experts projected %d/%d games", sport.key, len(projections), len(slate))
    preds = predict(sport, history, slate, now, s.simulations, ctx, veto=Settings.veto_for(sport.key),
                    model_weight=Settings.model_weight(sport.key), projections=projections)
    if not preds:
        log.info("%s: %d games found but none had odds and rated teams", sport.key, len(slate))
        return
    text = format_card(sport, preds, store.record(sport.key), now, paper=s.paper_mode)
    print(text + "\n")
    if not dry_run:
        send_message(s.telegram_bot_token, s.telegram_chat_id, text)
        store.log_picks(sport.key, preds)
        log.info("%s: sent %d predictions", sport.key, len(preds))


def results_text(s: Settings, store: Store, days: int) -> str:
    lines = [f"📊 <b>Marv Predict Max record — last {days} days</b>"]
    for key in s.sports:
        rec = store.record(key, days)
        if not rec:
            continue
        sport = SPORTS[key]
        w = sum(r[0] for r in rec.values())
        l = sum(r[1] for r in rec.values())
        units = sum(r[3] for r in rec.values())
        detail = ", ".join(f"{m} {r[0]}-{r[1]}" for m, r in sorted(rec.items()))
        lines.append(f"{sport.emoji} {sport.name}: {w}-{l} ({units:+.1f}u) · {detail}")
    if len(lines) == 1:
        lines.append("No graded picks yet.")
    return "\n".join(lines)


def cmd_run(s: Settings, args) -> int:
    store = Store(Path(s.state_dir))
    now = datetime.now(timezone.utc)
    weekday = now.astimezone(ET).weekday()
    keys = s.sports if args.sport == "all" else [args.sport]
    failures = 0
    for key in keys:
        sport = SPORTS[key]
        hours = args.hours or (max(sport.schedule.values()) if args.force else sport.schedule.get(weekday))
        if not hours:
            log.info("%s: not scheduled today", key)
            continue
        if sport.source == "cfbd" and not s.cfbd_api_key:
            log.warning("cfb: CFBD_API_KEY not set, skipping")
            continue
        try:
            run_sport(sport, s, store, now, hours, args.dry_run)
        except Exception:
            failures += 1
            log.exception("%s failed", key)
            if not args.dry_run:
                _alert_failure(s, f"{sport.name} run")
    if args.sport == "all" and weekday == 0 and not args.dry_run:
        send_message(s.telegram_bot_token, s.telegram_chat_id, results_text(s, store, 7))
    return 1 if failures else 0


def cmd_backtest(s: Settings, args) -> int:
    """Walk forward: every slate is predicted using only games that finished before it."""
    sport = SPORTS[args.sport]
    start = datetime.fromisoformat(args.start).replace(tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc) + timedelta(days=1)
    lookback = 200 if sport.source == "cfbd" else sport.history_days
    games, ctx = load_games(sport, s, start - timedelta(days=lookback), end, lines_from=start)
    ctx = {}  # season-long pace stats would leak future information
    if sport.source == "cfbd":
        season_start = datetime(start.year if start.month >= 7 else start.year - 1, 7, 1, tzinfo=timezone.utc)
        games = [g for g in games if g.start >= season_start]

    groups = defaultdict(list)
    for g in games:
        if g.completed and g.odds and start <= g.start < end:
            if sport.key in ("nfl", "cfb"):
                groups[(g.info.get("season", g.start.year), g.league, g.week)].append(g)
            else:
                groups[g.start.astimezone(ET).date()].append(g)

    veto = Settings.veto_for(sport.key)
    rng = np.random.default_rng(args.seed)
    tally = defaultdict(lambda: [0, 0, 0, 0.0])  # wins, losses, pushes, units
    candidates = passed = 0
    brier, brier_market, n_brier = 0.0, 0.0, 0
    for key in sorted(groups, key=lambda k: min(g.start for g in groups[k])):
        slate = groups[key]
        as_of = min(g.start for g in slate)
        history = [g for g in games if g.completed and g.start < as_of and g.start >= as_of - timedelta(days=lookback)]
        preds = predict(sport, history, slate, as_of, s.simulations, ctx, rng=rng, veto=veto,
                        model_weight=Settings.model_weight(sport.key))
        for pred in preds:
            g = pred.game
            if not sport.three_way and g.home_score != g.away_score:
                outcome = 1.0 if g.home_score > g.away_score else 0.0
                brier += (pred.home_win - outcome) ** 2
                n_brier += 1
                if g.odds.home_ml and g.odds.away_ml:
                    brier_market += (no_vig(g.odds.home_ml, g.odds.away_ml)[0] - outcome) ** 2
            for pick in pred.picks:
                result = grade(pick, g)
                candidates += 1
                passed += not pick.active
                for bucket in ([pick.market, f"{pick.market} (no veto)"] if pick.active else [f"{pick.market} (no veto)"]):
                    t = tally[bucket]
                    t[0] += result > 0
                    t[1] += result < 0
                    t[2] += result == 0
                    t[3] += result
        print(f"{key}: {len(preds)} games", file=sys.stderr)

    print(f"\n{sport.name} walk-forward backtest {args.start} → {args.end}")
    print(f"Veto pass rate: {passed / candidates:.0%}" if candidates else "No games with odds to grade.")
    for name in sorted(tally):
        w, l, p, u = tally[name]
        bets = w + l + p
        rate = f"{w / (w + l):.1%}" if w + l else "n/a"
        print(f"  {name:<18} {w:>4}-{l:<4} push {p:<3} win {rate:>6}  units {u:+7.1f}  ROI {u / bets if bets else 0:+.1%}")
    if n_brier:
        line = f"Win-probability Brier score: model {brier / n_brier:.4f}"
        if brier_market:
            line += f" vs market {brier_market / n_brier:.4f} (lower is better)"
        print(line)
    print("Break-even at -110 is 52.4%. Treat anything above ~60% over a small sample with suspicion.")
    return 0


def cmd_stats_backtest(s: Settings, args) -> int:
    from .stats import registry, report
    module = registry.module_for(args.sport)
    if args.sport == "cfb":
        module.api_key = s.cfbd_api_key
    first, last = (int(x) for x in args.seasons.split("-"))
    seasons = list(range(first, last + 1))
    val_to = args.val_to or seasons[len(seasons) * 2 // 3 - 1]
    result = report.run(module, Path(s.state_dir) / "cache", seasons, val_to, Path(s.state_dir) / "reports",
                        current=module.season_of(datetime.now()))
    print(result["text"])
    return 0


def cmd_results(s: Settings, args) -> int:
    text = results_text(s, Store(Path(s.state_dir)), args.days)
    print(text)
    if not args.dry_run:
        send_message(s.telegram_bot_token, s.telegram_chat_id, text)
    return 0


def cmd_test_telegram(s: Settings, args) -> int:
    send_message(s.telegram_bot_token, s.telegram_chat_id,
                 "✅ Marv Predict Max is connected. Sports: " + ", ".join(SPORTS[k].name for k in s.sports))
    print("Test message sent.")
    return 0


def cmd_get_chat_id(s: Settings, args) -> int:
    chats = get_chat_ids(s.telegram_bot_token)
    if not chats:
        print("No messages found. Send your bot any message in Telegram, then run this again.")
    for chat_id, name in chats:
        print(f"{chat_id}\t{name}")
    return 0


def cmd_sports(s: Settings, args) -> int:
    days = "Mon Tue Wed Thu Fri Sat Sun".split()
    for key, sport in SPORTS.items():
        sched = ", ".join(f"{days[d]} (+{h}h)" for d, h in sorted(sport.schedule.items()))
        on = "on " if key in s.sports else "off"
        print(f"[{on}] {key:<7} {sport.name:<17} source={sport.source:<9} runs: {sched}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="marv", description="Marv Predict Max multi-sport bot")
    sub = parser.add_subparsers(dest="command", required=True)
    choices = ["all", *SPORTS]

    run = sub.add_parser("run", help="predict upcoming games and alert Telegram")
    run.add_argument("--sport", default="all", choices=choices)
    run.add_argument("--dry-run", action="store_true", help="print instead of sending")
    run.add_argument("--force", action="store_true", help="ignore the weekday schedule")
    run.add_argument("--hours", type=int, help="look this many hours ahead (overrides the schedule)")

    bt = sub.add_parser("backtest", help="walk-forward backtest over a date range")
    bt.add_argument("--sport", required=True, choices=list(SPORTS))
    bt.add_argument("--start", required=True, help="YYYY-MM-DD")
    bt.add_argument("--end", required=True, help="YYYY-MM-DD")
    bt.add_argument("--seed", type=int, default=7)

    sb = sub.add_parser("stats-backtest", help="walk-forward backtest of the stats experts (tunes + reports)")
    sb.add_argument("--sport", required=True, choices=["nfl", "cfb", "nba", "wnba", "mlb"])
    sb.add_argument("--seasons", required=True, help="e.g. 2016-2025 (test seasons; training uses 6 prior years)")
    sb.add_argument("--val-to", type=int, help="last season used for tuning (default: first two thirds)")

    res = sub.add_parser("results", help="send the graded track record")
    res.add_argument("--days", type=int, default=30)
    res.add_argument("--dry-run", action="store_true")

    sub.add_parser("test-telegram", help="send a test message")
    sub.add_parser("get-chat-id", help="list chat ids that have messaged your bot")
    sub.add_parser("sports", help="list sports, sources and schedules")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = Settings.from_env()
    handler = {"run": cmd_run, "backtest": cmd_backtest, "results": cmd_results,
               "stats-backtest": cmd_stats_backtest, "test-telegram": cmd_test_telegram, "get-chat-id": cmd_get_chat_id, "sports": cmd_sports}
    try:
        return handler[args.command](s, args)
    except Exception:
        log.exception("%s failed", args.command)
        if args.command == "run" and not args.dry_run:
            _alert_failure(s, "run")
        return 1


if __name__ == "__main__":
    sys.exit(main())
