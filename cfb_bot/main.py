"""Command line entry point: python -m cfb_bot {run,backtest,test-telegram,get-chat-id}."""

import argparse
import logging
import sys
import traceback
from datetime import datetime, timezone

from .cfbd import CFBDClient, current_week
from .config import Settings
from .predict import grade, predict_slate
from .ratings import fit_ratings, g, pace_factors
from .telegram import format_slate, get_chat_ids, send_message

log = logging.getLogger("cfb_bot")


def _completed(game: dict) -> bool:
    return bool(game.get("completed")) and g(game, "homePoints", "home_points") is not None


def build_ratings(client: CFBDClient, year: int, history: list[dict], end_week: int | None):
    ratings = fit_ratings([gm for gm in history if _completed(gm)])
    try:
        ratings.pace = pace_factors(client.season_stats(year, end_week=end_week))
    except Exception as exc:  # pace is a refinement; never fail the slate over it
        log.warning("pace stats unavailable, using neutral pace: %s", exc)
    return ratings


def cmd_run(s: Settings, args) -> int:
    client = CFBDClient(s.cfbd_api_key)
    year = args.year or datetime.now(timezone.utc).year
    if args.week:
        week, season_type = args.week, args.season_type
    else:
        found = current_week(client.calendar(year))
        if not found:
            log.info("No upcoming %s week found; season is over.", year)
            return 0
        week, season_type = found

    regular = client.games(year)
    if season_type == "regular":
        history = [gm for gm in regular if gm.get("week", 0) < week]
        slate = [gm for gm in regular if gm.get("week") == week and not _completed(gm)]
        end_week = week - 1 if week > 1 else None
    else:
        post = client.games(year, season_type="postseason")
        history = regular + [gm for gm in post if _completed(gm)]
        slate = [gm for gm in post if not _completed(gm)]
        end_week = None

    ratings = build_ratings(client, year, history, end_week)
    preds = predict_slate(slate, client.lines(year, week, season_type), ratings, s)
    text = format_slate(year, week, preds)
    print(text)
    if not args.dry_run:
        send_message(s.telegram_bot_token, s.telegram_chat_id, text)
        log.info("Sent %d game predictions to Telegram", len(preds))
    return 0


def cmd_backtest(s: Settings, args) -> int:
    """Walk forward through a finished season: each week uses only games played before it."""
    client = CFBDClient(s.cfbd_api_key)
    games = client.games(args.year)
    last = args.end_week or max(gm.get("week", 0) for gm in games)
    tally = {key: {"win": 0, "loss": 0, "push": 0} for key in
             ("spread", "total", "ml", "spread (no veto)", "total (no veto)")}
    candidates = passed = 0

    for week in range(args.start_week, last + 1):
        history = [gm for gm in games if gm.get("week", 0) < week]
        week_games = [gm for gm in games if gm.get("week") == week and _completed(gm)]
        if not week_games:
            continue
        ratings = build_ratings(client, args.year, history, week - 1)
        preds = predict_slate(week_games, client.lines(args.year, week), ratings, s)
        finals = {gm.get("id"): gm for gm in week_games}
        for pred in preds:
            final = finals[pred.game_id]
            hp, ap = g(final, "homePoints", "home_points"), g(final, "awayPoints", "away_points")
            for pick in pred.picks:
                result = grade(pick, pred, hp, ap)
                if pick.market in ("spread", "total"):
                    candidates += 1
                    passed += not pick.active
                    tally[f"{pick.market} (no veto)"][result] += 1
                if pick.active:
                    tally[pick.market][result] += 1
        print(f"week {week}: {len(preds)} games simulated", file=sys.stderr)

    print(f"\nBacktest {args.year} weeks {args.start_week}-{last} (top {s.max_games} games per week)")
    print(f"Veto pass rate (spread + total): {passed / candidates:.0%}" if candidates else "No games graded.")
    for key, t in tally.items():
        decided = t["win"] + t["loss"]
        rate = f"{t['win'] / decided:.1%}" if decided else "n/a"
        print(f"  {key:<17} {t['win']:>4}-{t['loss']:<4} pushes {t['push']:<3} win rate {rate}")
    print("Break-even at -110 is 52.4%.")
    return 0


def cmd_test_telegram(s: Settings, args) -> int:
    send_message(s.telegram_bot_token, s.telegram_chat_id, "🏈 Marv CFB Predict Max is connected.")
    print("Test message sent.")
    return 0


def cmd_get_chat_id(s: Settings, args) -> int:
    chats = get_chat_ids(s.telegram_bot_token)
    if not chats:
        print("No messages found. Send your bot any message in Telegram, then run this again.")
    for chat_id, name in chats:
        print(f"{chat_id}\t{name}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="cfb_bot", description="Marv College Football Predict Max")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="predict the current (or given) week and alert Telegram")
    run.add_argument("--year", type=int)
    run.add_argument("--week", type=int)
    run.add_argument("--season-type", default="regular", choices=["regular", "postseason"])
    run.add_argument("--dry-run", action="store_true", help="print instead of sending to Telegram")

    bt = sub.add_parser("backtest", help="walk-forward backtest of a completed season")
    bt.add_argument("--year", type=int, required=True)
    bt.add_argument("--start-week", type=int, default=4)
    bt.add_argument("--end-week", type=int)

    sub.add_parser("test-telegram", help="send a test message")
    sub.add_parser("get-chat-id", help="list chat ids that have messaged your bot")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    s = Settings.from_env()
    handler = {"run": cmd_run, "backtest": cmd_backtest,
               "test-telegram": cmd_test_telegram, "get-chat-id": cmd_get_chat_id}[args.command]
    try:
        return handler(s, args)
    except Exception:
        log.exception("%s failed", args.command)
        if args.command == "run" and not getattr(args, "dry_run", False):
            try:  # let the user know the scheduled run broke
                send_message(s.telegram_bot_token, s.telegram_chat_id,
                             "⚠️ Marv CFB bot run failed:\n<pre>" + traceback.format_exc()[-1500:]
                             .replace("&", "&amp;").replace("<", "&lt;") + "</pre>")
            except Exception:
                log.exception("could not send failure alert")
        return 1


if __name__ == "__main__":
    sys.exit(main())
