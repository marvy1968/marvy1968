"""Live/paper runner.  python bot.py            (paper trading, real orders on paper account)
                     python bot.py --dry-run  (signals only, no orders)"""
import argparse
import csv
import json
import logging
import os
import time
from dataclasses import asdict
from datetime import datetime, timedelta, timezone

import pandas as pd
from alpaca.data.timeframe import TimeFrame

import config
import broker as brk
from broker import Broker
from common import Position, allowed_dirs, check_management, confirm_dirs, vol_filter_ok
from common import session_limits as _limits
from data import BarCache, fetch_bars
from experts import build_experts
from indicators import NY, prepare_intraday, to_arrays
from regime import (apply_intraday_overlay, build_heatmap, daily_frame, print_heatmap,
                    select_universe, write_heatmap_html)

log = logging.getLogger("scalpbot")


def rotate(lst, k):
    if not lst:
        return lst
    k %= len(lst)
    return lst[k:] + lst[:k]


def session_limits(close_time):
    """[FIX] (last_entry_mod, flatten_mod) from Alpaca's real close time (13:00 on half days)."""
    ct = pd.Timestamp(close_time).tz_convert(NY)
    return _limits(ct.hour * 60 + ct.minute - 570)


class State:
    def __init__(self, path):
        self.path, self.positions, self.unmanaged = path, {}, set()

    def load(self):
        if not os.path.exists(self.path):
            return
        try:
            with open(self.path) as fh:
                d = json.load(fh)
            self.positions = {k: Position(**v) for k, v in d.get("positions", {}).items()}
            self.unmanaged = set(d.get("unmanaged", []))
        except Exception as e:   # noqa: BLE001  [FIX] never brick on a bad state file
            bad = f"{self.path}.corrupt-{int(time.time())}"
            os.replace(self.path, bad)
            log.error("state file unreadable (%s) - moved to %s; positions will be recovered from "
                      "Alpaca order tags", e, bad)
            self.positions, self.unmanaged = {}, set()

    def save(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump({"positions": {k: asdict(v) for k, v in self.positions.items()},
                       "unmanaged": sorted(self.unmanaged)}, fh, indent=2)
        os.replace(tmp, self.path)


class ScalpBot:
    def __init__(self, dry_run=False):
        self.broker = Broker()
        self.experts = build_experts()
        self.by_id = {e.id: e for e in self.experts}
        self.state = State(config.STATE_FILE)
        self.state.load()
        self.dry_run = dry_run
        if config.RUNNER_MODE:
            raise SystemExit("RUNNER_MODE is backtest-only: the live order path for target-less trades "
                             "is not built yet. Set RUNNER_MODE = False in config.py for live/paper.")
        self.day = None
        self.tick_n = 0
        self.last_entry_mod, self.flatten_mod = config.LAST_ENTRY_MOD, config.FLATTEN_MOD

    # ---------------- daily setup ----------------
    def start_day(self, today):
        log.info("=== starting session %s ===", today)
        for e in self.experts:
            e.new_day()
        self.last_entry_mod, self.flatten_mod = session_limits(self.broker.clock().next_close)
        log.info("entries until mod %d, flatten at mod %d", self.last_entry_mod, self.flatten_mod)
        dc = self.broker.dc
        now = datetime.now(timezone.utc)
        hist_end = now - timedelta(minutes=16)
        cands = [s for spec in config.SECTORS.values() for s in spec["candidates"]]
        daily = fetch_bars(dc, cands, TimeFrame.Day, now - timedelta(days=45), hist_end,
                           config.HIST_FEED, "all")
        self.universe = select_universe(daily, today, config.STOCKS_PER_SECTOR)
        self.sym_sector = {s: sec for sec, syms in self.universe.items() for s in syms}
        self.etfs = {sec: spec["etf"] for sec, spec in config.SECTORS.items()}
        etf_daily = fetch_bars(dc, list(self.etfs.values()), TimeFrame.Day, now - timedelta(days=400),
                               hist_end, config.HIST_FEED, "all")
        self.frames = {k: daily_frame(v) for k, v in etf_daily.items()}
        self.heat = build_heatmap(self.frames, today)
        self.base_regime = self.heat["regime"].to_dict()
        self.regime = dict(self.base_regime)
        print_heatmap(self.heat, self.universe)
        write_heatmap_html(self.heat, config.HEATMAP_HTML, self.universe)
        self.cache = BarCache(dc, list(self.sym_sector) + list(self.etfs.values()), config.LIVE_FEED)
        self.cache.warmup(5)
        self.day = today

    # ---------------- per-minute tick ----------------
    def tick(self):
        self.tick_n += 1
        now = pd.Timestamp.now(tz="UTC")
        ny = now.tz_convert(NY)
        mod = ny.hour * 60 + ny.minute - 570
        self.cache.update()
        arrays = {}
        for s in self.cache.symbols:
            df = self.cache.completed(s)
            if df is None or len(df) < 60:
                continue
            p = prepare_intraday(df)
            if len(p) >= 60:
                arrays[s] = to_arrays(p)

        if config.INTRADAY_OVERLAY and mod % config.REGIME_REFRESH_MIN == 0:
            self.refresh_regimes(arrays)

        acct = self.broker.account()
        if not self.dry_run:
            self.reconcile(arrays)
            self.manage(arrays, mod)
        self.scan(arrays, acct, mod, now)
        self.state.save()

    def refresh_regimes(self, arrays):
        changed = False
        for sec, etf in self.etfs.items():
            a = arrays.get(etf)
            new = apply_intraday_overlay(self.base_regime[sec], a, a["n"] - 1 if a else None)
            if new != self.regime[sec]:
                log.info("regime %s: %s -> %s", sec, self.regime[sec], new)
                self.regime[sec], changed = new, True
        if changed:
            self.heat["live"] = pd.Series(self.regime)
            print_heatmap(self.heat, self.universe)
            write_heatmap_html(self.heat, config.HEATMAP_HTML, self.universe)

    def reconcile(self, arrays):
        bpos = self.broker.positions()
        now = pd.Timestamp.now(tz="UTC")
        for sym, pos in list(self.state.positions.items()):
            if sym in bpos:
                q, avg = bpos[sym]
                if not pos.confirmed:
                    pos.confirmed, pos.qty, pos.entry = True, abs(q), avg
                    pos.risk = abs(avg - pos.stop) or pos.risk
                continue
            age = (now - pd.Timestamp(pos.opened_at)).total_seconds() / 60
            if not pos.confirmed and age < 3:
                continue
            a = arrays.get(sym)
            last = float(a["close"][-1]) if a else float("nan")
            self.log_trade(pos, last, "closed_at_broker" if pos.confirmed else "entry_not_filled")
            del self.state.positions[sym]
        self.state.unmanaged &= set(bpos)
        for sym, (q, avg) in bpos.items():
            if sym in self.state.positions or sym in self.state.unmanaged:
                continue
            eid = self.broker.recover_expert(sym)
            a = arrays.get(sym)
            if eid in self.by_id and a is not None and sym in self.sym_sector:
                side = 1 if q > 0 else -1
                atr = float(a["atr"][-1])
                stop = self.broker.open_stop_price(sym) or round(avg - side * 1.5 * atr, 2)
                risk = abs(avg - stop) or atr
                self.state.positions[sym] = Position(
                    sym, eid, self.sym_sector[sym], side, abs(q), avg, stop,
                    round(avg + side * 2 * risk, 2), risk, now.isoformat(), reason="recovered")
                log.warning("recovered %s position in %s", eid, sym)
            else:
                self.state.unmanaged.add(sym)
                log.warning("%s is not a bot position - it will not be touched", sym)

    def manage(self, arrays, mod):
        for sym, pos in list(self.state.positions.items()):
            if not pos.confirmed:
                continue
            a = arrays.get(sym)
            if mod >= self.flatten_mod:
                act = ("exit", "eod_flatten")
            elif a is None:
                continue
            elif not pos.target_live and pos.side * (a["close"][-1] - pos.target) >= 0:
                act = ("exit", "target_bot_managed")
            else:
                act = check_management(pos, self.by_id[pos.expert], a, a["n"] - 1, self.flatten_mod)
            if not act:
                continue
            last = float(a["close"][-1]) if a else float("nan")
            if act[0] == "exit":
                if self.broker.close(sym):
                    log.info("[%s] EXIT %s %s", pos.expert, sym, act[1])
                    self.log_trade(pos, last, act[1])
                    del self.state.positions[sym]
                else:   # [FIX] legs were cancelled but the close failed -> re-arm a protective stop
                    log.error("close %s failed - re-arming protective stop, will retry next minute", sym)
                    self.broker.protect(sym, pos)
            elif act[0] == "breakeven":
                self.apply_stop_move(sym, pos, act[1], last)

    def apply_stop_move(self, sym, pos, new_stop, last):
        res = self.broker.move_stop(sym, pos, new_stop, last)
        log.info("[%s] %s breakeven %.2f -> %s", pos.expert, sym, new_stop, res)
        if res in (brk.REPLACED, brk.OCO, brk.STOP_ONLY):
            pos.stop, pos.be_moved = new_stop, True
            pos.target_live = res != brk.STOP_ONLY
        elif res == brk.BREACHED:
            if self.broker.close(sym):
                self.log_trade(pos, last, "breakeven_breached")
                del self.state.positions[sym]
            else:
                self.broker.protect(sym, pos)
        elif res == brk.FLATTENED:
            self.log_trade(pos, last, "emergency_flatten")
            del self.state.positions[sym]
        # GONE -> reconcile() books it next tick;  FAILED -> old orders kept, retried next tick

    def scan(self, arrays, acct, mod, now):
        if not (config.FIRST_ENTRY_MOD <= mod <= self.last_entry_mod):
            return
        equity, last_eq = float(acct.equity), float(acct.last_equity)
        if equity <= last_eq * (1 - config.DAILY_LOSS_LIMIT_PCT):
            log.warning("daily loss limit hit - no new entries")
            return
        if getattr(acct, "trading_blocked", False):
            return
        bp = float(acct.buying_power)
        locked = set(self.state.positions) | self.state.unmanaged
        symbols = rotate(list(self.sym_sector), self.tick_n)
        fresh = now.floor("min") - pd.Timedelta(minutes=config.STALE_BAR_MIN)
        for e in rotate(self.experts, self.tick_n):
            n_open = sum(1 for p in self.state.positions.values() if p.expert == e.id)
            for sym in symbols:
                if n_open >= config.MAX_POSITIONS_PER_EXPERT:
                    break
                if sym in locked:
                    continue
                sector = self.sym_sector[sym]
                dirs = allowed_dirs(e.style, self.regime[sector])
                a = arrays.get(sym)
                if not dirs or a is None or a["ts"][-1] < fresh:
                    continue
                if config.DIRECTION_CONFIRM:
                    dirs = dirs & confirm_dirs(e.style, a, a["n"] - 1)
                    if not dirs:
                        continue
                if config.VOL_FILTER and not vol_filter_ok(a, a["n"] - 1):
                    continue
                sig = e.entry(sym, a, a["n"] - 1, dirs)
                if sig is None:
                    continue
                qty = int(config.POSITION_PCT * equity // sig.price)
                if qty < 1 or qty * sig.price > bp:
                    continue
                if not self.broker.can_trade(sym, sig.side, acct):
                    continue
                if config.CHECK_SPREAD and not self.broker.spread_ok(sym):
                    continue
                side_txt = "LONG" if sig.side > 0 else "SHORT"
                log.info("[%s] %s %s x%d @~%.2f stop %.2f tgt %.2f (%s | %s)", e.id, side_txt, sym, qty,
                         sig.price, sig.stop, sig.target, sig.reason, self.regime[sector])
                if self.dry_run:
                    e.on_open(sym)
                    locked.add(sym)
                    continue
                try:
                    order = self.broker.open_bracket(sig, qty)
                except Exception as ex:   # noqa: BLE001
                    log.error("order rejected %s: %s", sym, ex)
                    continue
                self.state.positions[sym] = Position(
                    sym, e.id, sector, sig.side, qty, sig.price, sig.stop, sig.target, sig.risk,
                    now.isoformat(), regime=self.regime[sector], reason=sig.reason,
                    confirmed=False, order_id=str(order.id))
                e.on_open(sym)
                locked.add(sym)
                bp -= qty * sig.price
                n_open += 1

    def log_trade(self, pos, exit_px, reason):
        new = not os.path.exists(config.LIVE_TRADES_CSV)
        pnl = pos.side * (exit_px - pos.entry) * pos.qty
        with open(config.LIVE_TRADES_CSV, "a", newline="") as fh:
            w = csv.writer(fh)
            if new:
                w.writerow(["closed_at", "expert", "symbol", "sector", "regime", "side", "qty",
                            "entry", "exit_approx", "pnl_approx", "reason"])
            w.writerow([pd.Timestamp.now(tz="UTC").isoformat(), pos.expert, pos.symbol, pos.sector,
                        pos.regime, pos.side, pos.qty, pos.entry, exit_px, round(pnl, 2), reason])

    # ---------------- main loop ----------------
    def run(self):
        while True:
            try:
                clock = self.broker.clock()
                if not clock.is_open:
                    self.day = None
                    wait = (clock.next_open - clock.timestamp).total_seconds()
                    log.info("market closed; next open %s", clock.next_open)
                    time.sleep(min(max(wait - 90, 30), 1800))
                    continue
                today = pd.Timestamp.now(tz=NY).date()
                if self.day != today:
                    self.start_day(today)
                time.sleep(60 - (time.time() % 60) + 6)   # run ~6 s after each bar closes
                self.tick()
            except KeyboardInterrupt:
                self.state.save()
                log.info("stopped; open positions keep their broker-side bracket stops/targets")
                return
            except Exception:   # noqa: BLE001
                log.exception("tick error")
                time.sleep(10)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="log signals only, place no orders")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    if not config.API_KEY:
        raise SystemExit("Set APCA_API_KEY_ID and APCA_API_SECRET_KEY")
    ScalpBot(dry_run=args.dry_run).run()
