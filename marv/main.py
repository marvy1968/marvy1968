"""Command line: python -m marv {run,backtest,results,test-telegram,get-chat-id,sports}."""

import argparse
import os
import json
import logging
import sys
import time
import traceback
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np
import pandas as pd

from . import alertday, betcard, bridge, linemove, ou_tags, sharpgap
from .config import Settings
from .engine import grade, predict
from .markets import no_vig
from .plugins import AuditDatabase
from .sources import load_for_run, load_games
from .stats import registry as stats_registry
from .sports import SPORTS, Sport
from .state import Store
from .telegram import format_card, get_chat_ids, send_document, send_message

log = logging.getLogger("marv")
ET = ZoneInfo("America/New_York")


def _alert_failure(s: Settings, what: str) -> None:
    try:
        tb = traceback.format_exc()[-1500:].replace("&", "&amp;").replace("<", "&lt;")
        send_message(s.telegram_bot_token, s.telegram_chat_id, f"⚠️ Marv bot: {what} failed\n<pre>{tb}</pre>", mirror=False)
    except Exception:
        log.exception("could not send failure alert")


def run_sport(sport: Sport, s: Settings, store: Store, now: datetime, hours: int, dry_run: bool,
              label: str = "", day_only: bool = False):
    """Run one sport; returns (predictions, projections) for the daily bet card, or None."""
    history, slate, ctx = load_for_run(sport, s, now, hours)
    if store.resolve(sport.key, history):
        log.info("%s: graded finished picks", sport.key)
    try:
        ou_tags.grade(Path(s.state_dir), sport.key, history)
        sharpgap.grade(Path(s.state_dir), sport.key, history)
        betcard.grade(Path(s.state_dir), sport.key, history)
    except Exception:
        log.exception("over/under tag / gap grading failed")
    if not slate:
        log.info("%s: no games in the next %dh", sport.key, hours)
        return
    store.remember_lines(sport.key, slate)
    projections = None
    if s.stats_model and sport.key in stats_registry.MODULES:
        projections = stats_registry.project(sport.key, slate, s, now, history=history)
        log.info("%s: stats experts projected %d/%d games", sport.key, len(projections), len(slate))
    preds = predict(sport, history, slate, now, s.simulations, ctx, veto=Settings.veto_for(sport.key),
                    model_weight=Settings.model_weight(sport.key), projections=projections)
    if not preds:
        log.info("%s: %d games found but none had odds and rated teams", sport.key, len(slate))
        return [], projections, slate  # trend tags can still cover them (bet card)
    for pred in preds:
        moved = linemove.note(pred.game)
        if moved:
            pred.notes.append(moved)
    if projections is not None and getattr(projections, "tags", None) and not dry_run:
        ou_tags.log(Path(s.state_dir), sport.key, slate, projections.tags)
    if sport.key == "nfl":  # situational tags on the card (tracked in paper mode, never used to pick)
        try:
            from . import situational
            from .data.nflverse import URL as NFL_GAMES
            from .stats.base import fetch
            games = pd.read_csv(fetch(NFL_GAMES, Path(s.state_dir) / "cache" / "nflverse_games.csv", 3), low_memory=False)
            season = int(games.loc[games["result"].notna(), "season"].max())
            situational.attach(preds, games[games["season"] == season], Path(s.state_dir))
        except Exception:
            log.exception("situational tags failed")
    bridge.export(Path(s.state_dir), sport.key, preds)  # lets the odds bot ask Marv about any game
    AuditDatabase(Path(s.state_dir) / "marv_bot_audit.db").log_predictions(sport.key, preds)
    # Projections for every upcoming game stay available for queries (/game, /board); the card that gets
    # sent only covers games starting today.
    card_preds = [p for p in preds if alertday.is_today(p.game.start, now)] if day_only else preds
    text = format_card(sport, card_preds, store.record(sport.key), now, paper=s.paper_mode, label=label)
    print(text + "\n")
    fresh = store.new_picks(sport.key, card_preds)
    if not dry_run and not fresh and not s.send_empty_cards:
        log.info("%s: no new qualified plays, nothing sent", sport.key)
    elif not dry_run:
        send_message(s.telegram_bot_token, s.telegram_chat_id, text)
        store.log_picks(sport.key, card_preds)
        if sport.key in s.pdf_sports and card_preds:
            from .reports import weekly_chart
            pdf = weekly_chart(sport, card_preds, Path(s.state_dir) / f"{sport.key}_weekly_chart.pdf", now, s.paper_mode)
            send_document(s.telegram_bot_token, s.telegram_chat_id, pdf, f"Marv {sport.name} weekly ML / O-U chart")
        log.info("%s: sent %d predictions", sport.key, len(preds))
    return preds, projections, slate


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
    card_cands = []
    for key in keys:
        sport = SPORTS[key]
        hours = args.hours or (max(sport.schedule.values()) if args.force else sport.schedule.get(weekday))
        if not hours:
            log.info("%s: not scheduled today", key)
            continue
        if sport.source == "cfbd" and not s.cfbd_api_key:
            log.info("cfb: no CFBD_API_KEY, using ESPN schedules and free play-by-play stats")
        try:
            # Day-of alerts: manual runs (--force / --hours) show everything in the window.
            res = run_sport(sport, s, store, now, hours, args.dry_run, args.label,
                            day_only=alertday.day_only() and not args.force and not args.hours)
            if res:
                preds_, projections_, slate_ = res
                card_cands += betcard.candidates(key, preds_, projections_)
                card_cands += betcard.tag_candidates(key, slate_, getattr(projections_, "tags", {}) or {})
        except Exception:
            failures += 1
            log.exception("%s failed", key)
            if not args.dry_run:
                _alert_failure(s, f"{sport.name} run")
    if (args.sport == "all" or args.card) and not args.label:  # the daily pregame bet card (10:00 run)
        try:
            if s.odds_api_key:
                card_cands += betcard.gap_candidates(sharpgap.scan(s, [k for k in keys if k in sharpgap.SPORT_KEYS]))
            if alertday.day_only() and not args.force and not args.hours:
                card_cands = [b for b in card_cands if alertday.is_today(b.start, now)]
            bets = betcard.select(card_cands)
            text = betcard.text(bets, now.astimezone(ET))
            print(text + "\n")
            if not args.dry_run:
                prev = betcard.already_sent(Path(s.state_dir), now.astimezone(ET))
                if prev and os.environ.get("BETCARD_RESEND", "").lower() not in ("1", "true", "yes", "on"):
                    log.info("bet card already sent today at %s; not resending", prev.get("sent_at"))
                else:
                    betcard.mark_sent(Path(s.state_dir), bets, now.astimezone(ET))
                    send_message(s.telegram_bot_token, s.telegram_chat_id, text)
                    betcard.log(Path(s.state_dir), bets, now.astimezone(ET))
        except Exception:
            log.exception("bet card failed")
            if not args.dry_run:
                _alert_failure(s, "daily bet card")
    if args.sport == "all" and weekday == 0 and not args.dry_run and not args.label:
        send_message(s.telegram_bot_token, s.telegram_chat_id, results_text(s, store, 7))
        if "nfl" in keys:
            try:
                class _S: send = True
                cmd_situational(s, _S)
            except Exception:
                log.exception("situational grading failed")
    # Keep the quarter-by-quarter models fresh (weekly retrain; the live service picks them up on restart).
    from .ingame import model as ingame
    for isport in [k for k in keys if k in ingame.SPECS]:
        path = ingame.model_path(Path(s.state_dir), isport)
        if not args.dry_run and (not path.exists() or time.time() - path.stat().st_mtime > 7 * 86400):
            try:
                class _A: action, sport, seasons = "train", isport, ""
                cmd_ingame(s, _A)
            except Exception:
                log.exception("in-game %s training failed", isport)
    # Off by default: the real-line backtest (2025, 3,040 bets vs Bovado/DK/FD) went 48.7-49.1%, ROI -5%,
    # below always-under (52.5%). PROPS=on brings the prop cards back.
    props_on = os.environ.get("PROPS", "off").lower() in ("1", "on", "true", "yes")
    for psport, window in (("nfl", 36), ("cfb", 48), ("ncaab", 30), ("ncaaw", 30), ("wnba", 30)):
        if psport in keys and s.odds_api_key and props_on:
            try:  # player props from Bovado lines (paper mode); quiet when nothing clears the filter
                props = _props_runner(psport)
                if weekday == 0:
                    log.info(props.grade(s))
                log.info(props.run_live(s, window, args.dry_run))
            except Exception:
                log.exception("%s props failed", psport)
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
        if not s.cfbd_api_key:  # free play-by-play stats instead of the CFBD API
            from .stats.ncaaf import NCAAF_PBP as module
    first, last = (int(x) for x in args.seasons.split("-"))
    seasons = list(range(first, last + 1))
    val_to = args.val_to or seasons[len(seasons) * 2 // 3 - 1]
    result = report.run(module, Path(s.state_dir) / "cache", seasons, val_to, Path(s.state_dir) / "reports",
                        current=module.season_of(datetime.now()))
    print(result["text"])
    return 0


def cmd_h2h_backtest(s: Settings, args) -> int:
    """Max Pick method: stat-by-stat head-to-head tally + last-3 trend + Monte Carlo, walk-forward."""
    from .stats import h2h, registry
    module = registry.module_for(args.sport)
    if args.sport == "cfb":
        from .stats.ncaaf import NCAAF_PBP as module
    first, last = (int(x) for x in args.seasons.split("-"))
    seasons = list(range(first, last + 1))
    cache = Path(s.state_dir) / "cache"
    games, tg = module.load(cache, list(range(max(first - 2, module.first_season), last + 1)),
                            module.season_of(datetime.now()))
    if "neutral" not in games:
        games["neutral"] = False
    df = h2h.walk_forward(module, games, tg, seasons)
    print(h2h.report(df, args.held_out_from or seasons[len(seasons) // 2]))
    return 0


PROPS_SPORTS = ["nfl", "cfb", "ncaab", "ncaaw", "wnba"]


class _BasketballProps:
    """Binds a basketball league to the shared basketball props runner."""

    def __init__(self, sport: str):
        from .props import basketball_run
        self.sport, self.m = sport, basketball_run

    def run_live(self, s, hours, dry_run):
        return self.m.run_live(s, hours, dry_run, sport=self.sport)

    def grade(self, s):
        return self.m.grade(s, sport=self.sport)

    def backtest_free(self, cache, seasons):
        return self.m.backtest_free(cache, seasons, sport=self.sport)

    def backtest_real(self, s, seasons, max_credits):
        return self.m.backtest_real(s, seasons, max_credits, sport=self.sport)


def _props_runner(sport: str):
    if sport in ("ncaab", "ncaaw", "wnba"):
        return _BasketballProps(sport)
    if sport == "cfb":
        from .props import cfb_run as runner
    else:
        from .props import run as runner
    return runner


def cmd_props(s: Settings, args) -> int:
    props = _props_runner(args.sport)
    print(props.grade(s) if args.grade else props.run_live(s, args.hours or 36, args.dry_run))
    return 0


def cmd_props_backtest(s: Settings, args) -> int:
    props = _props_runner(args.sport)
    first, last = (int(x) for x in args.seasons.split("-"))
    seasons = list(range(first, last + 1))
    if args.real:
        if not s.odds_api_key:
            print("ODDS_API_KEY is not set.")
            return 1
        print(props.backtest_real(s, seasons, args.max_credits))
    else:
        print(props.backtest_free(Path(s.state_dir) / "cache", seasons))
    return 0


def cmd_situational(s: Settings, args) -> int:
    """Grade the logged NFL situational tags and print (or send) their running record."""
    from . import situational
    from .data.nflverse import URL as NFL_GAMES
    from .stats.base import fetch
    games = pd.read_csv(fetch(NFL_GAMES, Path(s.state_dir) / "cache" / "nflverse_games.csv", 3), low_memory=False)
    text = situational.report(situational.grade(Path(s.state_dir), games))
    text += "\n\n" + ou_tags.report(ou_tags.record(Path(s.state_dir)))
    text += "\n\n" + sharpgap.report(sharpgap.record(Path(s.state_dir)))
    from .live_monitor import live_record
    text += "\n\n" + live_record(Path(s.state_dir))
    text += "\n\n" + betcard.record(Path(s.state_dir))
    print(text)
    if args.send:
        send_message(s.telegram_bot_token, s.telegram_chat_id, text)
    return 0


def cmd_gaps(s: Settings, args) -> int:
    """Bovado vs Pinnacle: games where Bovado's college/NFL spread or total is off the sharp number."""
    if not s.odds_api_key:
        print("Set ODDS_API_KEY in .env first.")
        return 1
    sports = [args.sport] if args.sport != "all" else ["cfb", "nfl"]
    entries = sharpgap.scan(s, sports, args.min_gap)
    sharpgap.log_gaps(Path(s.state_dir), entries)
    text = sharpgap.text(entries)
    print(text)
    print(sharpgap.coverage_text())
    if args.send:
        send_message(s.telegram_bot_token, s.telegram_chat_id, text)
    return 0


def cmd_ingame(s: Settings, args) -> int:
    """Train or backtest the quarter-by-quarter model (game stats at each period -> ML and O/U)."""
    from .ingame import model as ingame
    from .live_monitor import STRUCTURE  # noqa: F401  (sports with a live feed)
    cache = Path(s.state_dir) / "cache"
    if args.action == "backtest":
        first, last = (int(x) for x in args.seasons.split("-"))
        df = ingame.backtest(cache, args.sport, list(range(first, last + 1)))
        print(ingame.report(df, args.sport))
    else:
        from .props.basketball_run import season_of as bb_season
        now = datetime.now(timezone.utc)
        current = bb_season(now, args.sport) if args.sport in ("ncaab", "ncaaw", "wnba") else \
            (now.year if now.month >= 3 else now.year - 1)
        ingame.train(Path(s.state_dir), args.sport, current)
        print(f"in-game {args.sport} model saved to {ingame.model_path(Path(s.state_dir), args.sport)}")
    return 0


def cmd_edges(s: Settings, args) -> int:
    """Edge board: Marv vs every current price (games and props). --watch = alerts + Telegram queries."""
    from . import board, querybot
    if args.watch:
        querybot.watch(s, s.sports, args.refresh)
        return 0
    if args.query:
        print(querybot.answer(s, args.query))
        return 0
    print(board.text(board.build(s, s.sports, args.hours), limit=40))
    return 0


def _check_args(src) -> dict:
    def num(k):
        v = src.get(k)
        return float(v) if v not in (None, "") else None
    return dict(sport=src["sport"], team=src["team"], market=src["market"], side=src["side"],
                price=float(src["price"]), line=num("line"), other=src.get("other") or None,
                home_score=num("home_score"), away_score=num("away_score"), minutes_left=num("minutes_left"))


def cmd_check(s: Settings, args) -> int:
    v = bridge.check(Path(s.state_dir), **_check_args(vars(args)))
    print(v.line())
    print(bridge.verdict_json(v))
    return 0


def cmd_overlay(s: Settings, args) -> int:
    o = bridge.overlay(Path(s.state_dir), args.sport, args.market, args.side, args.line, args.team, args.other,
                       args.text, args.price, args.live)
    print(o.line() or "Marv: no H2H read for this game")
    print(bridge.overlay_json(o))
    return 0


def cmd_serve(s: Settings, args) -> int:
    """Local HTTP endpoint for the odds bot: /check?sport=nfl&team=Lions&market=total&side=under&line=67.5&price=-110..."""
    from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
    from urllib.parse import parse_qs, urlparse

    state = Path(s.state_dir)
    from . import defense
    import threading
    from . import hybrid as _hy
    def _warm_hybrid():
        if "euroleague" in s.sports:
            try:
                _hy.refresh_euroleague(state)  # EuroLeague box + names (network, background only)
            except Exception:  # noqa: BLE001
                logging.getLogger(__name__).warning("hybrid EuroLeague refresh failed", exc_info=True)
        for s_ in _hy.SPORTS:
            _hy.league(state, s_)
    threading.Thread(target=_warm_hybrid, daemon=True).start()
    defense.warm(state)  # pre-load defense tables so the first /overlay stays inside March_edge's 1.5 s timeout

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                if url.path == "/check":
                    body, code = bridge.verdict_json(bridge.check(state, **_check_args(q))), 200
                elif url.path == "/overlay":  # H2H overlay line for a March_edge alert (no Telegram, read-only)
                    o = bridge.overlay(state, q.get("sport", ""), q.get("market", ""), q.get("side", ""),
                                       float(q["line"]) if q.get("line") not in (None, "") else None,
                                       q.get("team") or None, q.get("other") or None, q.get("text") or None,
                                       float(q["price"]) if q.get("price") not in (None, "") else None,
                                       q.get("live", "").lower() in ("1", "true", "yes"))
                    body, code = bridge.overlay_json(o), 200
                    if o.found and o.verdict in ("not", "mixed") and not q.get("live", "").lower() in ("1", "true", "yes"):
                        # heavy favourite with off metrics -> standalone UPSET ALERT (own thread, deduped per game/day);
                        # the March_edge response above is unchanged
                        import threading
                        from . import upset
                        threading.Thread(target=upset.from_overlay, daemon=True, args=(
                            s, q.get("sport", ""), q.get("market", ""), q.get("side", ""),
                            float(q["price"]) if q.get("price") not in (None, "") else None,
                            float(q["line"]) if q.get("line") not in (None, "") else None,
                            q.get("team") or None, q.get("other") or None, q.get("text") or None)).start()
                elif url.path == "/h2h":  # game-level H2H pick for queries (/game in March_edge): no Telegram, read-only
                    o = bridge.game_h2h(state, q.get("sport", ""), q.get("team") or None, q.get("other") or None,
                                        q.get("text") or None)
                    body, code = bridge.overlay_json(o), 200
                elif url.path == "/predictions":
                    path = state / "predictions.json"
                    body, code = (path.read_text() if path.exists() else "{}"), 200
                elif url.path == "/prop":  # /prop?player=Josh Allen&market=pass  -> latest priced props
                    from .props.run import lookup
                    body, code = json.dumps(lookup(state, q["player"], q.get("market"))), 200
                elif url.path == "/grade":  # /grade?player=CeeDee Lamb&market=rec&side=over&line=71.5&price=-170&other=140
                    from . import propgrade
                    now = datetime.now(timezone.utc)
                    g = propgrade.grade_prop(propgrade.load(state / "cache", now.year if now.month >= 3 else now.year - 1),
                                             q["player"], q["market"], q["side"], float(q["line"]), float(q["price"]),
                                             float(q["other"]) if q.get("other") else None)
                    body, code = json.dumps(g), 200
                elif url.path == "/board":  # current edges (games + props) for the odds bot
                    path = state / "board.json"
                    body, code = (path.read_text() if path.exists() else '{"entries": []}'), 200
                else:
                    body, code = '{"error": "use /check, /overlay, /h2h, /grade, /predictions or /board"}', 404
            except (KeyError, ValueError) as exc:
                body, code = json.dumps({"error": f"bad parameters: {exc}"}), 400
            data = body.encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, *a):
            pass

    log.info("Marv bridge listening on http://127.0.0.1:%d", args.port)
    ThreadingHTTPServer(("127.0.0.1", args.port), Handler).serve_forever()
    return 0


def cmd_report(s: Settings, args) -> int:
    """PDFs in the Marv templates: weekly ML / O-U chart (live run) and/or the sport backtest sheet."""
    from .reports import sport_sheet, weekly_chart
    sport = SPORTS[args.sport]
    out_dir = Path(s.state_dir) / "reports"
    out_dir.mkdir(parents=True, exist_ok=True)
    files = []
    if args.sheet:
        files.append(sport_sheet(sport, out_dir / f"{sport.key}_predict_max.pdf", s.simulations))
    if args.chart:
        now = datetime.now(timezone.utc)
        hours = args.hours or max(sport.schedule.values())
        history, slate, ctx = load_for_run(sport, s, now, hours)
        projections = stats_registry.project(sport.key, slate, s, now, history=history) \
            if s.stats_model and sport.key in stats_registry.MODULES and slate else None
        preds = predict(sport, history, slate, now, s.simulations, ctx, veto=Settings.veto_for(sport.key),
                        model_weight=Settings.model_weight(sport.key), projections=projections)
        files.append(weekly_chart(sport, preds, out_dir / f"{sport.key}_weekly_chart.pdf", now, s.paper_mode))
    for f in files:
        print(f)
        if args.send:
            send_document(s.telegram_bot_token, s.telegram_chat_id, f, f.stem.replace("_", " "))
    return 0


def cmd_live(s: Settings, args) -> int:
    """In-game monitor: re-price ML and O/U at the end of every quarter / half."""
    from .live_monitor import LiveMonitor

    def send(text: str) -> None:
        print(text + "\n")
        if not args.dry_run:
            send_message(s.telegram_bot_token, s.telegram_chat_id, text)

    mon = LiveMonitor(s, s.sports)
    if args.once:
        mon.tick(send)
    else:
        mon.run(send, args.interval)
    return 0


def cmd_notify(s: Settings, args) -> int:
    """Send a text file (e.g. a bet card) to the owner's Telegram chat."""
    from html import escape
    text = Path(args.file).read_text()[:3800]
    send_message(s.telegram_bot_token, s.telegram_chat_id, f"<pre>{escape(text)}</pre>" if args.mono else escape(text),
                 mirror=args.mirror)
    print("sent")
    return 0


def cmd_live_props_probe(s: Settings, args) -> int:
    """During a live NFL game: do the prop lines move in-game, from which books, and does the ESPN box score
    parse? Saves a snapshot to state/live_props_log.json (stage 1 of live props: record, don't alert)."""
    from . import live_props
    from .data.espn import ESPNClient, parse_event
    from .props import nfl as P, odds as O
    if not s.odds_api_key:
        print("Set ODDS_API_KEY first.")
        return 1
    now = datetime.now(timezone.utc)
    evs = O.events(s.odds_api_key, "americanfootball_nfl")
    live = [e for e in evs if pd.Timestamp(e["commence_time"]).to_pydatetime() <= now]
    nxt = sorted((e for e in evs if pd.Timestamp(e["commence_time"]).to_pydatetime() > now), key=lambda e: e["commence_time"])
    if not live:
        print("No NFL game is live right now." + (f" Next kickoff: {nxt[0]['away_team']} @ {nxt[0]['home_team']} at {nxt[0]['commence_time']}" if nxt else ""))
        return 0
    client = ESPNClient()
    games = [g for g in (parse_event(e, "nfl") for e in client.scoreboard("football/nfl", now - timedelta(days=1), now)) if g]
    book = ",".join(args.books.split(","))
    for ev in live:
        g = next((g for g in games if g.home in ev["home_team"] or ev["home_team"] in g.home), None)
        box, quarter, score = {}, "?", "?"
        if g:
            box = live_props.box_stats(client.summary("football/nfl", str(g.id)))
            quarter = f"Q{g.info.get('period')} {g.info.get('clock')}"
            score = f"{g.info.get('live_away')}-{g.info.get('live_home')}"
        data = O.event_props(s.odds_api_key, "americanfootball_nfl", ev["id"], [m.key for m in P.MARKETS.values()], book)
        rows = live_props.live_prop_snapshot(data, box)
        print(f"{ev['away_team']} @ {ev['home_team']} · {quarter} · score {score} · ESPN box players parsed: {len(box)}")
        by_book = {}
        for r in rows:
            by_book.setdefault(r["book"], []).append(r)
        for b, rs in by_book.items():
            stamps = sorted({r["updated"] for r in rs if r["updated"]})
            print(f"  {b}: {len(rs)} prop lines; last_update range {stamps[0] if stamps else '?'} .. {stamps[-1] if stamps else '?'}")
        for r in sorted(rows, key=lambda r: -(r["so_far"] or 0))[:12]:
            print(f"    {r['book']:11s} {r['player']:22s} {r['market'][7:]:14s} line {r['line']:g} (O {r['over']} / U {r['under']}) so far {r['so_far']}")
        live_props.log_snapshot(Path(s.state_dir), ev, quarter, score, rows)
    return 0


def cmd_grade_prop(s: Settings, args) -> int:
    """Marv's probability and grade for one NFL player prop (form-only second opinion)."""
    from . import propgrade
    now = datetime.now(timezone.utc)
    season = now.year if now.month >= 3 else now.year - 1
    g = propgrade.grade_prop(propgrade.load(Path(s.state_dir) / "cache", season), args.player, args.market, args.side,
                             args.line, args.price, args.other)
    print(propgrade.text(g))
    return 0 if g["ok"] else 1


def cmd_mirror_status(s: Settings, args) -> int:
    """Is Marv mirroring its alerts into another bot's chat? Prints that bot's username, never the token."""
    import requests as rq
    from .telegram import mirror_token, token_shape
    token = mirror_token()
    if not token:
        print("Mirror: NOT set up (no MIRROR_BOT_TOKEN in Marv's .env). Marv's alerts only go to the Marv chat.")
        return 0
    try:
        me = rq.get(f"https://api.telegram.org/bot{token}/getMe", timeout=20).json()
    except Exception as exc:
        print(f"Mirror: token present but Telegram couldn't be reached: {exc}")
        return 1
    if not me.get("ok"):
        print("Mirror: a token is set but Telegram rejects it (wrong, cut off, or revoked). Paste it again.")
        print(f"  What's saved: {token_shape(token)}")
        print(f"  Same as Marv's own bot token: {'YES (that is the wrong bot)' if token == s.telegram_bot_token else 'no'}")
        return 1
    chat = os.environ.get("MIRROR_CHAT_ID") or s.telegram_chat_id
    print(f"Mirror: ON, alerts also go to @{me['result'].get('username')} (chat {'same as Marv chat' if chat == s.telegram_chat_id else chat}).")
    return 0


def cmd_injuries(s: Settings, args) -> int:
    """Injury and roster availability report for a sport (optionally a few teams)."""
    from .data.injuries import report_text
    from .stats import registry
    now = datetime.now(timezone.utc)
    cache = Path(s.state_dir) / "cache"
    week = None
    season = now.year if now.month >= 3 else now.year - 1
    if args.sport == "nfl":
        from .data.nflverse import URL as NFL_GAMES
        from .stats.base import fetch
        g = pd.read_csv(fetch(NFL_GAMES, cache / "nflverse_games.csv", 3), low_memory=False)
        up = g[(g["season"] == season) & g["result"].isna()]
        week = int(up["week"].min()) if not up.empty else None
    elif args.sport in ("nba", "ncaab", "ncaaw"):
        season = registry.module_for(args.sport).season_of(pd.Timestamp(now))
    teams = [t.strip() for t in args.team.split(",")] if args.team else None
    text = report_text(args.sport, cache, season, teams, week, now)
    print(text)
    return 0


def cmd_live_probe(s: Settings, args) -> int:
    """Check the live in-game feeds from this machine: scoreboard, statuses, and (football/basketball)
    whether the play-by-play parser reads a real ESPN game summary; EuroLeague prints the raw header."""
    from .live_monitor import STRUCTURE
    now = datetime.now(timezone.utc)
    path = STRUCTURE[args.sport][3]
    if path is None:
        from .data import euroleague_live as EL
        from .stats.euroleague import schedule_games
        games = schedule_games(now - timedelta(days=args.days), now + timedelta(hours=12))
        print(f"EuroLeague schedule: {len(games)} games in the window")
        for g in games[-6:]:
            season, code = g.id[1:].split("-")
            try:
                h = EL.header(int(season), int(code))
                EL.apply_header(g, h)
                keys = {k: h.get(k) for k in ("Live", "ScoreA", "ScoreB", "Quarter", "RemainingPartialTime", "TeamA", "TeamB")}
                print(f"{g.away} @ {g.home} {g.start:%Y-%m-%d}: parsed state={g.info.get('state')} period={g.info.get('period')} "
                      f"clock={g.info.get('clock')!r} score={g.info.get('live_away')}-{g.info.get('live_home')} | raw {keys}")
            except Exception as exc:
                print(f"{g.id}: header failed: {exc}")
        return 0
    from .data.espn import ESPNClient, parse_event
    client = ESPNClient()
    events = client.scoreboard(path, now - timedelta(days=args.days), now)
    games = [g for g in (parse_event(e, args.sport) for e in events) if g]
    print(f"ESPN {args.sport}: {len(events)} events, states " +
          str({st: sum(g.info.get('state') == st for g in games) for st in ('pre', 'in', 'post')}))
    for g in games[:5]:
        print(f"  {g.away} @ {g.home} state={g.info.get('state')} {g.info.get('status_name')} period={g.info.get('period')} "
              f"clock={g.info.get('clock')} score={g.info.get('live_away')}-{g.info.get('live_home')} preseason={g.info.get('preseason', False)}")
    done = [g for g in games if g.info.get("state") in ("post", "in")]
    if done:
        from .ingame import plays as PL
        g = done[-1]
        summary = client.summary(path, g.id)
        parse = PL.espn_football if args.sport in ("nfl", "cfb") else PL.espn_basketball
        plays = parse(summary, str(g.id))
        print(f"Play-by-play parser on {g.away} @ {g.home}: {len(plays)} plays, columns {list(plays.columns)[:14]}")
        if not plays.empty:
            print(plays.tail(3).to_string()[:1500])
        else:
            print(f"summary keys: {list(summary)[:20]}")
    return 0


def cmd_probe_ewl(s: Settings, args) -> int:
    """Check the EuroLeague Women (FIBA) sources from this machine and save samples for debugging."""
    import requests as rq
    from .data import fiba
    out = Path(s.state_dir) / "probe"
    out.mkdir(parents=True, exist_ok=True)
    urls = [u.strip() for u in (args.url or os.environ.get("EWL_EVENT_URLS", "")).split(",") if u.strip()] or \
        ["https://www.fiba.basketball/en/events/euroleague-women-25-26/games"]
    ids = []
    for url in urls:
        try:
            r = rq.get(url, headers=fiba.UA, timeout=30)
            (out / "ewl_event_page.html").write_text(r.text)
            found = fiba.match_ids(r.text)
            print(f"{url}: HTTP {r.status_code}, {len(found)} match ids found")
            ids += found
        except Exception as exc:
            print(f"{url}: FAILED {exc}")
    mid = args.match_id or (ids[0] if ids else None)
    if not mid:
        print("No match id found. Open the event page in a browser, click a game's live stats link, and rerun with "
              "--match-id <the number in the fibalivestats URL>. Send me state/probe/ewl_event_page.html.")
        return 1
    data = fiba.fetch_match(int(mid))
    if not data:
        print(f"Match {mid}: data.json not reachable")
        return 1
    (out / f"ewl_match_{mid}.json").write_text(json.dumps(data)[:2_000_000])
    rows = fiba.parse_totals(data, int(mid))
    print(f"Match {mid}: keys {sorted(data)[:15]}")
    if rows:
        r0 = rows[0]
        print(f"Parsed OK: {r0['team_display_name']} {r0.get('team_score')} vs {r0['opponent_team_display_name']} "
              f"{r0.get('opponent_team_score')}; {len(r0)} stats; final={fiba.is_final(data)}; date={fiba.match_date(data)}")
        print("EuroLeague Women can be enabled: add euroleague_women to SPORTS and set EWL_EVENT_URLS.")
    else:
        print("Format differs from expected. Send me state/probe/ewl_match_*.json and I'll adapt the parser.")
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
    run.add_argument("--card", action="store_true", help="also build the bet card for a single-sport run")
    run.add_argument("--label", default="", help="tag shown on the card, e.g. 'Late update'")

    bt = sub.add_parser("backtest", help="walk-forward backtest over a date range")
    bt.add_argument("--sport", required=True, choices=list(SPORTS))
    bt.add_argument("--start", required=True, help="YYYY-MM-DD")
    bt.add_argument("--end", required=True, help="YYYY-MM-DD")
    bt.add_argument("--seed", type=int, default=7)

    sb = sub.add_parser("stats-backtest", help="walk-forward backtest of the stats experts (tunes + reports)")
    sb.add_argument("--sport", required=True, choices=["nfl", "cfb", "nba", "wnba", "mlb", "ncaab", "ncaaw", "euroleague"])
    sb.add_argument("--seasons", required=True, help="e.g. 2016-2025 (test seasons; training uses 6 prior years)")
    sb.add_argument("--val-to", type=int, help="last season used for tuning (default: first two thirds)")

    hb = sub.add_parser("h2h-backtest", help="backtest the head-to-head stat tally + last-3 trend + Monte Carlo")
    hb.add_argument("--sport", required=True, choices=["nfl", "cfb", "nba", "wnba", "ncaab", "ncaaw", "euroleague"])
    hb.add_argument("--seasons", required=True, help="e.g. 2016-2025 (each season is fit on the ones before it)")
    hb.add_argument("--held-out-from", dest="held_out_from", type=int, help="first season to report (default: middle)")

    pp = sub.add_parser("props", help="player-prop picks from Bovado lines (paper mode), or --grade")
    pp.add_argument("--sport", default="nfl", choices=PROPS_SPORTS)
    pp.add_argument("--hours", type=int, help="games starting within this many hours (default 36)")
    pp.add_argument("--dry-run", action="store_true")
    pp.add_argument("--grade", action="store_true", help="grade earlier picks and print the record")

    pb = sub.add_parser("props-backtest", help="player props walk-forward backtest (--real: against past Bovado lines)")
    pb.add_argument("--sport", default="nfl", choices=PROPS_SPORTS)
    pb.add_argument("--seasons", required=True, help="e.g. 2020-2025 (real lines exist from 2023)")
    pb.add_argument("--real", action="store_true", help="use The Odds API historical prop lines (paid credits)")
    pb.add_argument("--max-credits", dest="max_credits", type=int, default=40000)

    sit = sub.add_parser("situational", help="grade the NFL situational and over/under trend tags (paper tracking)")
    sit.add_argument("--send", action="store_true")

    ig = sub.add_parser("ingame", help="quarter-by-quarter model: train it, or backtest it")
    ig.add_argument("action", choices=["train", "backtest"])
    ig.add_argument("--sport", required=True, choices=["nfl", "cfb", "ncaab", "ncaaw", "wnba"])
    ig.add_argument("--seasons", default="2020-2025", help="backtest seasons, e.g. 2020-2025")

    gp = sub.add_parser("gaps", help="Bovado vs Pinnacle: college/NFL lines where Bovado is off the sharp number")
    gp.add_argument("--sport", default="all", choices=["all", "cfb", "nfl"])
    gp.add_argument("--min-gap", type=float, default=0.5)
    gp.add_argument("--send", action="store_true")
    eb = sub.add_parser("edges", help="edge board vs current odds; --watch for alerts + Telegram queries")
    eb.add_argument("--watch", action="store_true")
    eb.add_argument("--refresh", type=int, default=30, help="minutes between board refreshes (--watch)")
    eb.add_argument("--hours", type=int, default=36)
    eb.add_argument("--query", help='answer one Telegram-style query, e.g. "/game Lions"')

    ck = sub.add_parser("check", help="Marv's fair price for an odds-bot alert (pregame or live)")
    ck.add_argument("--sport", required=True, choices=list(SPORTS))
    ck.add_argument("--team", required=True, help="any team in the game")
    ck.add_argument("--other", help="the other team (optional, sharpens matching)")
    ck.add_argument("--market", required=True, choices=["total", "ml"])
    ck.add_argument("--side", required=True, help="over / under, or the team name for ml")
    ck.add_argument("--price", required=True, type=float, help="American odds, e.g. -110")
    ck.add_argument("--line", type=float, help="total line (for --market total)")
    ck.add_argument("--home-score", dest="home_score", type=float)
    ck.add_argument("--away-score", dest="away_score", type=float)
    ck.add_argument("--minutes-left", dest="minutes_left", type=float, help="game minutes left (innings for MLB)")

    ov = sub.add_parser("overlay", help="Marv H2H overlay (good / not + why) for a March_edge alert")
    ov.add_argument("--sport", required=True, help="cfb/nfl/... or a March_edge league key (ncaaf)")
    ov.add_argument("--market", required=True, help="h2h/ml, spreads, totals, player_*")
    ov.add_argument("--side", required=True, help="team name, Over/Under")
    ov.add_argument("--line", type=float)
    ov.add_argument("--team")
    ov.add_argument("--other")
    ov.add_argument("--text", help="the alert text (used to find the game)")
    ov.add_argument("--price", type=float)
    ov.add_argument("--live", action="store_true")

    sv = sub.add_parser("serve", help="local HTTP bridge for the odds bot (127.0.0.1)")
    sv.add_argument("--port", type=int, default=8787)

    rp = sub.add_parser("report", help="PDF weekly chart and/or sport backtest sheet (Marv templates)")
    rp.add_argument("--sport", required=True, choices=list(SPORTS))
    rp.add_argument("--chart", action="store_true", help="weekly ML / O-U chart from a live run")
    rp.add_argument("--sheet", action="store_true", help="sport one-pager with backtest results")
    rp.add_argument("--hours", type=int)
    rp.add_argument("--send", action="store_true", help="send the PDFs to Telegram")

    lv = sub.add_parser("live", help="in-game monitor: update ML and O/U after every quarter/half")
    lv.add_argument("--interval", type=int, default=120, help="seconds between scoreboard checks")
    lv.add_argument("--once", action="store_true", help="one pass, then exit")
    lv.add_argument("--dry-run", action="store_true")

    nt = sub.add_parser("notify", help="send a text file (e.g. a bet card) to Telegram")
    nt.add_argument("--file", required=True)
    nt.add_argument("--mono", action="store_true", help="keep the layout (monospace)")
    nt.add_argument("--mirror", action="store_true", help="also send to the mirror chat (MIRROR_BOT_TOKEN)")
    lpp = sub.add_parser("live-props-probe", help="check live in-game prop lines + ESPN box score during an NFL game")
    lpp.add_argument("--books", default="bovada,draftkings,fanduel,betmgm")
    gp2 = sub.add_parser("grade-prop", help="Marv's probability + grade for one NFL player prop")
    gp2.add_argument("--player", required=True)
    gp2.add_argument("--market", required=True, help="pass, rush, rec or receptions")
    gp2.add_argument("--side", required=True, choices=["over", "under"])
    gp2.add_argument("--line", required=True, type=float)
    gp2.add_argument("--price", required=True, type=float)
    gp2.add_argument("--other", type=float, help="the other side's price (for the no-vig market probability)")
    sub.add_parser("mirror-status", help="is Marv mirroring its alerts into another bot's chat?")
    ij = sub.add_parser("injuries", help="injury report + roster availability (key players starred)")
    ij.add_argument("--sport", required=True, choices=["nfl", "nba", "wnba", "cfb", "ncaab", "ncaaw", "euroleague"])
    ij.add_argument("--team", help="comma-separated team names, e.g. Cowboys,Buccaneers")
    lp = sub.add_parser("live-probe", help="check the live in-game feeds (ESPN, EuroLeague) and the play parser")
    lp.add_argument("--sport", required=True, choices=["nfl", "cfb", "wnba", "nba", "ncaab", "ncaaw", "euroleague"])
    lp.add_argument("--days", type=int, default=3)
    pe = sub.add_parser("probe-ewl", help="check EuroLeague Women (FIBA LiveStats) sources on this machine")
    pe.add_argument("--url", help="FIBA event games page(s), comma-separated")
    pe.add_argument("--match-id", type=int)

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
               "stats-backtest": cmd_stats_backtest, "h2h-backtest": cmd_h2h_backtest, "props": cmd_props, "props-backtest": cmd_props_backtest, "ingame": cmd_ingame, "edges": cmd_edges, "gaps": cmd_gaps, "situational": cmd_situational, "check": cmd_check, "overlay": cmd_overlay, "report": cmd_report, "live": cmd_live, "probe-ewl": cmd_probe_ewl, "live-probe": cmd_live_probe, "injuries": cmd_injuries, "mirror-status": cmd_mirror_status, "grade-prop": cmd_grade_prop, "live-props-probe": cmd_live_props_probe, "notify": cmd_notify, "serve": cmd_serve, "test-telegram": cmd_test_telegram, "get-chat-id": cmd_get_chat_id, "sports": cmd_sports}
    try:
        return handler[args.command](s, args)
    except Exception:
        log.exception("%s failed", args.command)
        if args.command == "run" and not args.dry_run:
            _alert_failure(s, "run")
        return 1


if __name__ == "__main__":
    sys.exit(main())
