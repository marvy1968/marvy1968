"""CLI: python -m ajbet {run,scan,report,replay}"""
from __future__ import annotations

import argparse
import logging
import sys
import time
from pathlib import Path

from .alerts import Notifier
from .config import ROOT, load_config, resolve_path
from .db import DB
from .oddsapi import OddsAPI
from .paper import report
from .replay import run_replay
from .scanner import Scanner, format_summary
from .value_scan import ValueScanner, format_value_summary
from .budget import budget_report, schedule
from .features import FeatureStore, TeamModel


def setup_logging(cfg) -> None:
    lp = resolve_path(cfg, cfg.get("log_path", "logs/ajbet.log"))
    lp.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        handlers=[logging.FileHandler(lp), logging.StreamHandler(sys.stderr)])
    # urllib3 debug lines would include the query string (apiKey); keep them quiet.
    logging.getLogger("urllib3").setLevel(logging.WARNING)


def build(cfg, args, store=None):
    db = DB(str(resolve_path(cfg, args.db or cfg.get("db_path", "ajbet.db"))))
    odds = OddsAPI(cfg, db)
    if cfg.get("providers"):
        from .providers.hub import build_hub
        odds = build_hub(cfg, db, odds_client=odds)     # The Odds API primary + fallbacks/merge
    notifier = Notifier(cfg, dry_run=getattr(args, "dry_run", True))
    notifier.attach_outbox(db, float((cfg.get("telegram") or {}).get("outbox_max_age_s", 900)))
    store = store or FeatureStore(cfg)
    sc = Scanner(cfg, db, odds, notifier)
    sc.team_model = TeamModel(store, cfg)
    vs = None
    if cfg.get("strategies", {}).get("value_finder", {}).get("enabled", True):
        vs = ValueScanner(cfg, db, odds, notifier, store=store)
        sc.value_scanner = vs
    sc.store = store
    if (cfg.get("soccer_live_props") or {}).get("enabled", False):
        from .soccer_live_props import SoccerLiveShadow   # Oct 16 2026: SHADOW only, never sent (send_telegram false)
        sc.soccer_live_shadow = SoccerLiveShadow(cfg, db)
        if vs is not None:
            sc.soccer_live_shadow.emit = vs.emit_shadow   # 2026-10-30 Marvin: under AJ alert
    if (cfg.get("mlb_live_props") or {}).get("enabled", False):
        from .mlb_live_props import MlbLiveShadow   # Oct 16 2026: SHADOW only, never sent (send_telegram false)
        sc.mlb_live_shadow = MlbLiveShadow(cfg, db)
        if vs is not None:
            sc.mlb_live_shadow.emit = vs.emit_shadow   # 2026-10-30 Marvin: under AJ alert
    tc = cfg.get("strategies", {}).get("context") or {}
    if tc.get("enabled", True):
        from .context import Context
        ctx = Context()
        sc.trends.context = ctx
        sc.total_swing.context, sc.total_swing.trend_store = ctx, sc.trend_store
        if vs is not None:
            vs.context, vs.trend_store = ctx, sc.trend_store
            ea = tc.get("edge_adjust") or {}
            if ea.get("enabled", False):
                from .context import EdgeAdjuster
                vs.edge_adjuster = EdgeAdjuster(ea, ctx)
        from .wx_ledger import WeatherLedger
        sc.wx_ledger = WeatherLedger(db, ctx)
    ic = cfg.get("strategies", {}).get("injuries") or {}
    if ic.get("enabled", True):
        from .injuries import InjuryChecker, InjuryWatch, SnapCounts
        ck = InjuryChecker(cfg, store=store, snaps=SnapCounts())
        sc.injuries = ck
        sc.trends.injuries = ck
        sc.total_swing.injuries = ck
        if vs is not None:
            vs.injuries = ck
        sc.injury_watch = InjuryWatch(db, ck, notifier, every_s=float(ic.get("watch_every_s", 600)))
    return db, sc, vs


def feature_status(store) -> str:
    out = []
    for s in ("nfl", "ncaaf", "wnba"):
        t, p = store.teams(s), store.players(s) if s != "ncaaf" else None
        out.append(f"{s}: teams={'ok as of ' + t['as_of_utc'][:16] + 'Z' if t else 'MISSING/REJECTED'}"
                   + ("" if s == "ncaaf" else f", players={'ok (' + str(len(p['players'])) + ')' if p else 'MISSING/REJECTED'}"))
    return "; ".join(out)


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="ajbet", description="AJ Bet Bot (alert-only, paper tracking)")
    ap.add_argument("--config", default=None)
    ap.add_argument("--db", default=None, help="override SQLite path")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="poll loop: ESPN every N s, Odds API only on candidates")
    r.add_argument("--dry-run", action="store_true", help="print alerts instead of Telegram")
    r.add_argument("--interval", type=int, default=None)
    r.add_argument("--max-iterations", type=int, default=0, help="0 = forever")
    s = sub.add_parser("scan", help="single scan")
    s.add_argument("--once", action="store_true", default=True)
    s.add_argument("--dry-run", action="store_true")
    rp = sub.add_parser("report", help="daily paper P&L vs goal")
    rp.add_argument("--days", type=int, default=7)
    v = sub.add_parser("value", help="Strategy C value finder (pregame windows + props)")
    v.add_argument("--dry-run", action="store_true")
    v.add_argument("--force", action="store_true", help="ignore scan windows (manual window)")
    v.add_argument("--sports", default=None, help="comma list, e.g. nfl")
    v.add_argument("--props-events", type=int, default=None, help="override props events for this run")
    v.add_argument("--props-markets", default=None, help="comma list applied to the selected sports")
    v.add_argument("--no-team-props", action="store_true", help="skip team props this run")
    v.add_argument("--no-featured", action="store_true", help="skip the featured call (props only)")
    b = sub.add_parser("budget", help="credit usage by strategy + monthly projection")
    b.add_argument("--offline", action="store_true", help="use config game-days instead of ESPN schedule")
    rf = sub.add_parser("refresh", help="refresh ratings + player features (free public data, 0 credits)")
    rf.add_argument("--sports", default=None, help="comma list (nfl,ncaaf,wnba)")
    rf.add_argument("--calibrate", action="store_true", help="force walk-forward recalibration")
    rf.add_argument("--no-players", action="store_true")
    tr = sub.add_parser("trends", help="rebuild team trend stats (free ESPN/nflverse data, 0 credits)")
    tr.add_argument("--sports", default=None, help="comma list (nfl,ncaaf,wnba)")
    op = sub.add_parser("oddspapi", help="OddsPapi paper-only Bovada props cross-check: status (unmetered) or one run")
    op.add_argument("--run", action="store_true", help="run one cross-check now (uses <= 2 of the hard-capped requests)")
    pa = sub.add_parser("parlayapi", help="ParlayAPI paper-only Bovada props cross-check: status (free) or one run")
    pa.add_argument("--run", action="store_true", help="run one cross-check now (3 credits per sport, hard-capped)")
    ro = sub.add_parser("rapidodds", help="RapidOddsAPI paper-only Bovada cross-check: status (free) or one run")
    ro.add_argument("--run", action="store_true", help="run one cross-check now (1 credit per market type; one-time 250 allowance)")
    sh = sub.add_parser("sharpapi", help="SharpAPI paper-only DK/FD no-vig check of open prop bets: status or one run")
    sh.add_argument("--run", action="store_true", help="run one check now (1 request per open prop bet, rate-capped)")
    rr = sub.add_parser("replay", help="run saved fixtures through the pipeline (no network)")
    rr.add_argument("--fixtures", default=str(ROOT / "fixtures" / "replay"))
    args = ap.parse_args(argv)
    cfg = load_config(args.config)
    setup_logging(cfg)

    if args.cmd == "replay":
        run_replay(cfg, Path(args.fixtures))
        return 0
    if args.cmd == "trends":
        from .trends import TrendStore
        ts = TrendStore(cfg, resolve_path(cfg, (cfg.get("strategies", {}).get("team_trends") or {})
                                          .get("data_dir", "data/trends")))
        for k, v in ts.build(args.sports.split(",") if args.sports else None).items():
            print(f"{k}: {v}")
        return 0
    if args.cmd == "oddspapi":
        from .oddspapi import OddsPapiCrossCheck
        xc = OddsPapiCrossCheck(cfg, str(resolve_path(cfg, args.db or cfg.get("db_path", "ajbet.db"))))
        cl = xc.client
        if not cl.key:
            print("oddspapi: no key in env/.env (ODDSPAPI_API_KEY)")
            return 1
        try:
            acct = cl.account()          # unmetered
        except Exception as e:  # noqa: BLE001
            print(f"oddspapi account check failed: {cl.scrub(str(e))}")
            return 1
        print(f"oddspapi status: {cl.status()}")
        if args.run:
            if not xc.enabled:
                print("oddspapi.enabled is false in config; not running")
                return 0
            print(f"oddspapi run: {xc.run_once()}")
        cl.close()
        return 0
    if args.cmd == "parlayapi":
        from .parlayapi import ParlayCrossCheck
        xp = ParlayCrossCheck(cfg, str(resolve_path(cfg, args.db or cfg.get("db_path", "ajbet.db"))))
        cp = xp.client
        if not cp.key:
            print("parlayapi: no key in env/.env (PARLAY_API_KEY)")
            return 1
        try:
            print(f"parlayapi usage: {cp.usage()}")          # free
        except Exception as e:  # noqa: BLE001
            print(f"parlayapi usage check failed: {cp.scrub(str(e))}")
            return 1
        print(f"parlayapi status: {cp.status()}")
        if args.run:
            if not xp.enabled:
                print("parlayapi.enabled is false in config; not running")
                return 0
            print(f"parlayapi run: {xp.run_once()}")
        cp.close()
        return 0
    if args.cmd == "rapidodds":
        from .rapidodds import RapidCrossCheck
        xr = RapidCrossCheck(cfg, str(resolve_path(cfg, args.db or cfg.get("db_path", "ajbet.db"))))
        cr = xr.client
        if not cr.key:
            print("rapidodds: no key in env/.env (RAPIDODDS_API_KEY)")
            return 1
        try:
            print(f"rapidodds usage: {cr.usage()}")          # free
        except Exception as e:  # noqa: BLE001
            print(f"rapidodds usage check failed: {cr.scrub(str(e))}")
            return 1
        print(f"rapidodds status: {cr.status()}")
        if args.run:
            if not xr.enabled:
                print("rapidodds.enabled is false in config; not running")
                return 0
            print(f"rapidodds run: {xr.run_once()}")
        cr.close()
        return 0
    if args.cmd == "sharpapi":
        from .sharpapi import SharpCrossCheck
        xs = SharpCrossCheck(cfg, str(resolve_path(cfg, args.db or cfg.get("db_path", "ajbet.db"))))
        cs = xs.client
        if not cs.key:
            print("sharpapi: no key in env/.env (SHARPAPI_API_KEY)")
            return 1
        try:
            print(f"sharpapi account: {cs.account()}")
        except Exception as e:  # noqa: BLE001
            print(f"sharpapi account check failed: {cs.scrub(str(e))}")
            return 1
        print(f"sharpapi status: {cs.status()}")
        if args.run:
            if not xs.enabled:
                print("sharpapi.enabled is false in config; not running")
                return 0
            print(f"sharpapi run: {xs.run_once()}")
        cs.close()
        return 0
    if args.cmd == "report":
        db = DB(str(resolve_path(cfg, args.db or cfg.get("db_path", "ajbet.db"))))
        unit = cfg.get("strategies", {}).get("value_finder", {}).get("unit", 100)
        print(report(db, cfg.get("paper", {}).get("goal_per_day", 120), days=args.days, unit=unit))
        return 0
    if args.cmd == "refresh":
        store = FeatureStore(cfg)
        st = store.refresh(sports=args.sports.split(",") if args.sports else None,
                           calibrate=True if args.calibrate else None, players=not args.no_players)
        for k, v in st.items():
            print(f"{k}: {v}")
        cal = store.calibration()
        for k, v in cal.items():
            f = v.get("fit") or {}
            print(f"calibration {k}: params {v.get('params')} w_srs {f.get('w_srs')} w_epa {f.get('w_epa')} "
                  f"hfa {f.get('hfa')} sigma {f.get('sigma')} rmse {f.get('rmse')} (hfa-only {f.get('baseline_rmse_hfa_only')}) "
                  f"n {f.get('n')} SU {f.get('straight_up_hit_rate')}")
        print(feature_status(store))
        return 0
    if args.cmd == "budget":
        db, _, vs = build(cfg, args)
        gd = None
        if not args.offline and vs is not None:
            try:
                vs.load_top30()
                gd = schedule(cfg, vs.fetch, vs.top30)
            except Exception as e:
                logging.warning("schedule fetch failed, using config defaults: %s", e)
        print(budget_report(cfg, db, gd))
        if gd:
            print("ESPN in-scope games next 7 days (CT): " + "; ".join(
                f"{k}: {v['games']} games on {', '.join(sorted(v['days'])) or 'none'}" for k, v in gd.items()))
        return 0
    db, scanner, vs = build(cfg, args)
    if args.cmd == "value":
        sports = args.sports.split(",") if args.sports else None
        pm = {s: args.props_markets.split(",") for s in (sports or vs.sports)} if args.props_markets else None
        print(format_value_summary(vs.run(force=args.force, sports=sports, props_events=args.props_events,
                                          props_markets=pm, featured=not args.no_featured,
                                          team_props_markets={} if args.no_team_props else None)))
        print("\n".join(scanner.settle({})) or "no settlements")
        return 0
    if args.cmd == "scan":
        print(format_summary(scanner.scan_once()))
        if vs is not None:
            print(format_value_summary(vs.run()))
        return 0
    interval = args.interval or cfg.get("poll_interval_s", 60)
    store = scanner.store
    log = logging.getLogger("ajbet.run")
    xcheck = None
    try:
        from .oddspapi import OddsPapiCrossCheck
        xcheck = OddsPapiCrossCheck(cfg, str(resolve_path(cfg, args.db or cfg.get("db_path", "ajbet.db"))))
        log.info("oddspapi props cross-check (paper-only, never alerts): %s",
                 "enabled" if xcheck.enabled else "disabled (config flag or key missing)")
    except Exception:  # noqa: BLE001
        log.exception("oddspapi cross-check init failed (ignored)")
        xcheck = None
    scanner.oddspapi_xcheck = xcheck
    try:
        from .parlayapi import ParlayCrossCheck
        pxc = ParlayCrossCheck(cfg, str(resolve_path(cfg, args.db or cfg.get("db_path", "ajbet.db"))))
        log.info("parlayapi props cross-check (paper-only, never alerts): %s",
                 "enabled" if pxc.enabled else "disabled (config flag or key missing)")
    except Exception:  # noqa: BLE001
        log.exception("parlayapi cross-check init failed (ignored)")
        pxc = None
    scanner.parlayapi_xcheck = pxc
    try:
        from .rapidodds import RapidCrossCheck
        rxc = RapidCrossCheck(cfg, str(resolve_path(cfg, args.db or cfg.get("db_path", "ajbet.db"))))
        log.info("rapidodds cross-check (paper-only, never alerts): %s",
                 "enabled" if rxc.enabled else "disabled (config flag or key missing)")
    except Exception:  # noqa: BLE001
        log.exception("rapidodds cross-check init failed (ignored)")
        rxc = None
    scanner.rapidodds_xcheck = rxc
    try:
        from .sharpapi import SharpCrossCheck
        sxc = SharpCrossCheck(cfg, str(resolve_path(cfg, args.db or cfg.get("db_path", "ajbet.db"))))
        log.info("sharpapi DK/FD no-vig check (paper-only, never alerts): %s",
                 "enabled" if sxc.enabled else "disabled (config flag or key missing)")
    except Exception:  # noqa: BLE001
        log.exception("sharpapi cross-check init failed (ignored)")
        sxc = None
    scanner.sharpapi_xcheck = sxc
    log.info("AJ Bet Bot run loop starting (dry_run=%s, interval=%ss, alert-only/paper-only; features: %s)",
             args.dry_run, interval, feature_status(store))
    bot = None
    if (cfg.get("telegram", {}).get("bot", {}) or {}).get("enabled", True):
        from .telegram_bot import TelegramBot
        bot = TelegramBot(cfg, db, scanner, vs, scanner.notifier)
        if not bot.start():
            bot = None
    import signal

    def _term(*_a):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM, _term)       # systemctl restart: answer queued commands, then exit
    try:
        return _loop(args, cfg, scanner, vs, store, bot, interval, log)
    finally:
        if bot is not None:
            bot.stop()
            try:
                n = bot.drain()
                if n:
                    log.info("telegram: answered %d queued command(s) before shutdown", n)
            except BaseException:  # noqa: BLE001
                pass


_RECAL = None


def _loop(args, cfg, scanner, vs, store, bot, interval, log):
    i = 0
    while True:
        i += 1
        if store.needs_refresh() and store.refresh_async():
            log.info("feature refresh started in background")
        try:
            if scanner.trend_store.maybe_refresh(scanner.db):
                log.info("team trend rebuild started in background (free ESPN/nflverse data)")
        except Exception:  # noqa: BLE001
            log.exception("trend refresh scheduling failed")
        try:
            global _RECAL
            if _RECAL is None:
                from .recalibrate import Recalibrator
                _RECAL = Recalibrator(cfg)
            if _RECAL.maybe_run(scanner.db, getattr(scanner.trend_store, "_thread", None)):
                log.info("weekly re-backtest + recalibration started in background (0 credits)")
        except Exception:  # noqa: BLE001
            log.exception("weekly recalibration scheduling failed")
        try:
            xc = getattr(scanner, "oddspapi_xcheck", None)
            if xc is not None and xc.maybe_start():
                log.info("oddspapi props cross-check started in background (paper-only)")
            px = getattr(scanner, "parlayapi_xcheck", None)
            if px is not None and px.maybe_start():
                log.info("parlayapi props cross-check started in background (paper-only)")
            rx = getattr(scanner, "rapidodds_xcheck", None)
            if rx is not None and rx.maybe_start():
                log.info("rapidodds cross-check started in background (paper-only)")
            sx = getattr(scanner, "sharpapi_xcheck", None)
            if sx is not None and sx.maybe_start():
                log.info("sharpapi no-vig check started in background (paper-only)")
        except Exception:  # noqa: BLE001
            log.exception("oddspapi cross-check scheduling failed (ignored)")
        try:                                   # review fix #1: resend Telegram messages that failed earlier
            scanner.notifier.flush_outbox()
        except Exception:  # noqa: BLE001
            log.exception("telegram outbox flush failed")
        try:
            s = scanner.scan_once()
            print(format_summary(s), flush=True)
            v = vs.run() if vs is not None else None   # windows gate any credits
            if v is not None:
                print(format_value_summary(v), flush=True)
            live = sum(x["live"] for x in s["leagues"].values())
            if bot is not None:
                bot.ensure_polling()
                bot.push_results(s.get("settled") or [])
                bot.maybe_grade()          # 8:45 CT report card for yesterday (before the 9:00 digest)
                bot.maybe_digest()
            sw = s.get("swings") or {}
            log.info("heartbeat iter=%d leagues=%d live_games=%d candidates=%d alerts=%d value_alerts=%d "
                     "swing_live=%d swing_odds=%d swing_no_odds=%d swing_alerts=%d "
                     "tot_live=%d tot_priced=%d tot_alerts=%d trend_matches=%d trend_alerts=%d inj_updates=%d odds_calls=%d live_priced=%d credits_remaining=%s key=%s tg_last_poll_s=%s tg_handled=%s", i, len(s["leagues"]), live, len(s["candidates"]),
                     len(s["alerts"]), len(v["alerts"]) if v else 0, sw.get("live", 0), sw.get("with_live_odds", 0),
                     sw.get("no_live_odds", 0), len(sw.get("alerts") or []),
                     (s.get("tswings") or {}).get("live", 0), (s.get("tswings") or {}).get("priced", 0),
                     len((s.get("tswings") or {}).get("alerts") or []),
                     (s.get("trends") or {}).get("matches", 0), len((s.get("trends") or {}).get("alerts") or []),
                     len(s.get("injury_updates") or []), s["odds_calls"] + (len(v["calls"]) if v else 0), s.get("live_priced", 0),
                     (v or s).get("credits_remaining"), scanner.odds.key_label,
                     f"{time.time() - bot.last_poll:.0f}" if bot is not None and bot.last_poll else "-",
                     bot.handled if bot is not None else "-")
        except Exception:
            logging.exception("scan failed")
        if args.max_iterations and i >= args.max_iterations:
            if bot is not None:
                bot.stop()
            return 0
        if bot is not None:
            bot.serve_until(time.time() + interval)    # answer commands between scans (main thread)
        else:
            time.sleep(interval)


if __name__ == "__main__":
    raise SystemExit(main())
