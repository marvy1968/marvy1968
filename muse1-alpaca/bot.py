#!/usr/bin/env python3
"""Stock Bot — top-10 experts + top-5 hedge-fund principles overlay.
Three sleeves in one runner: scalp (<=30 min), day-trade (09:45-15:45 ET,
flatten 15:45), swing (daily, fills at next open, 2R TP).

PAPER ONLY. --live ALWAYS refuses with exit code 2. --paper and --dry-run
work with no keys: without keys the runner uses a paper broker stub and
deterministic synthetic demo bars, clearly labelled SYNTHETIC, and places
no orders. With paper keys it fetches real Alpaca bars and places paper
orders only. Keys come only from ALPACA_API_KEY / ALPACA_API_SECRET env
or a local .env file — never hard-coded, never printed, and no .env file
is shipped in this directory.

UNVALIDATED: this bot has no out-of-sample backtest yet. Do NOT run it on
the same Alpaca paper account as the existing bots — that would break
max-open / daily-trade limits. Do NOT deploy it live.
"""
import argparse
import math
import os
import random
import sys
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import yaml  # noqa: E402

from experts import scan, SLEEVE_EXPERTS  # noqa: E402
from hedge_fund_overlay import (  # noqa: E402
    vol_scaled_size, costs, check_portfolio_limits, stress_test,
    exit_policy, combine_signals,
)
from risk import RiskState  # noqa: E402

BOT_NAME = "muse1/alpaca"
VERSION = "0.1.0-experts-hedgefund-overlay (paper only, unvalidated)"

# ---------------------------------------------------------------- config

def load_config(path=None):
    path = Path(path) if path else HERE / "config.yaml"
    with open(path) as f:
        return yaml.safe_load(f)

def load_dotenv():
    env_file = HERE / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

# ---------------------------------------------------------------- demo bars (dry-run / no keys)

def synthetic_bars(symbol, n=60, seed=42):
    """Deterministic demo bars so --dry-run can exercise the experts with
    no keys and no network. Labelled SYNTHETIC everywhere it is used.
    A seeded random walk with a mild trend and a squeeze — not real data."""
    rng = random.Random(seed + sum(ord(ch) for ch in symbol))
    bars = []
    price = 100.0 + rng.random() * 200
    for i in range(n):
        drift = 0.15 if i > 40 else 0.0
        o = price
        c = o + rng.gauss(drift, 1.0)
        h = max(o, c) + rng.random() * 0.5
        l = min(o, c) - rng.random() * 0.5
        bars.append({"o": round(o, 2), "h": round(h, 2), "l": round(l, 2),
                     "c": round(c, 2), "v": 1000 + rng.randint(0, 5000)})
        price = c
    return bars

# ---------------------------------------------------------------- brokers

class PaperBrokerStub:
    """No-keys paper broker: records what WOULD be ordered, places nothing.
    Used for --dry-run and for --paper with no keys."""
    def __init__(self, equity=100000.0):
        self.equity = equity
        self.would_orders = []

    def positions(self):
        return {}

    def shortable(self, symbol):
        return True

    def submit(self, symbol, side, qty, entry, stop, sleeve, expert):
        order = {"symbol": symbol, "side": side, "qty": qty, "entry": entry,
                 "stop": stop, "sleeve": sleeve, "expert": expert,
                 "status": "WOULD_PLACE (stub, no keys / dry-run)"}
        self.would_orders.append(order)
        return order

class AlpacaBroker:
    """Real Alpaca paper broker, only constructed when keys are present
    and --paper was given. Live is blocked before this is ever built."""
    def __init__(self, key, secret):
        from alpaca.trading.client import TradingClient
        self.client = TradingClient(key, secret, paper=True)

    def equity(self):
        return float(self.client.get_account().equity)

    def positions(self):
        return {p.symbol: p for p in self.client.get_all_positions()}

    def shortable(self, symbol):
        try:
            asset = self.client.get_asset(symbol)
            return bool(asset.shortable)
        except Exception:
            return False

    def submit(self, symbol, side, qty, entry, stop, sleeve, expert):
        from alpaca.trading.requests import MarketOrderRequest
        from alpaca.trading.enums import OrderSide, TimeInForce
        req = MarketOrderRequest(symbol=symbol, qty=qty,
                                 side=OrderSide.BUY if side == 1 else OrderSide.SELL,
                                 time_in_force=TimeInForce.DAY)
        order = self.client.submit_order(req)
        return {"symbol": symbol, "side": side, "qty": qty, "entry": entry,
                "stop": stop, "sleeve": sleeve, "expert": expert,
                "status": "SUBMITTED_PAPER", "order_id": str(order.id)}

# ---------------------------------------------------------------- SPY filter
# Owner rule 2026-11-05: if the S&P 500 is up, trade longs only;
# if it is down, trade shorts only.
# vs_open: SPY last close vs today's open (first bar's open).
#   side = +1 (longs only) if close > open, -1 (shorts only) if close < open,
#   0 (both allowed) if equal / not enough bars.
# sma20_regime: previous daily close vs rising/falling SMA20 (daily bars only;
#   falls back to vs_open on 5-min bars).

def spy_side(spy_bars, mode="vs_open"):
    if not spy_bars or len(spy_bars) < 2:
        return 0
    if mode == "sma20_regime":
        closes = [b["c"] for b in spy_bars]
        if len(closes) < 22:
            return spy_side(spy_bars, "vs_open")
        sma20 = sum(closes[-20:]) / 20
        sma20_prev = sum(closes[-21:-1]) / 20
        prev_close = closes[-2]
        if prev_close > sma20 and sma20 > sma20_prev:
            return 1
        if prev_close < sma20 and sma20 < sma20_prev:
            return -1
        return 0
    # vs_open (default)
    o = float(spy_bars[0]["o"])
    c = float(spy_bars[-1]["c"])
    if c > o:
        return 1
    if c < o:
        return -1
    return 0

def in_et_window(window="10:00-15:45", now=None):
    """True if `now` (default: now, US/Eastern) is inside window_et.
    Window is HH:MM-HH:MM Eastern, day/scalp sleeves only."""
    try:
        start_s, end_s = window.split("-")
        sh, sm = map(int, start_s.split(":"))
        eh, em = map(int, end_s.split(":"))
    except Exception:
        return True
    now = now or datetime.now(ZoneInfo("America/New_York"))
    if now.tzinfo is None:
        now = now.replace(tzinfo=ZoneInfo("America/New_York"))
    now_et = now.astimezone(ZoneInfo("America/New_York"))
    t = (now_et.hour, now_et.minute)
    return (sh, sm) <= t < (eh, em)

def available_equity(equity, open_positions):
    """Total AVAILABLE equity: account equity minus notional already
    committed to open positions. Sizing is computed on this, not on the
    full account equity, so a new trade does not size as if money already
    in other trades were still free."""
    committed = sum(abs(float(p.get("notional", 0.0))) for p in open_positions)
    return max(float(equity) - committed, 0.0)

# ---------------------------------------------------------------- virtual trail + stop
# Last night's logic (Trend Stock / Velez, as refined 2026-11-02):
# - Hard stop = -1R from the ACTUAL fill entry, where 1R is the sized
#   stop distance (signal entry to signal stop). If the fill is better
#   than the signal entry, the stop moves with it — the risk stays 1R,
#   it does not inflate because the fill was better.
# - Virtual stair trail: once the trade has moved +0.5R in your favour,
#   the stop trails the peak by 0.5R, stepping up every +0.5R
#   (trail = peak_R - 0.5R). No fixed target caps the runner.
# - This is VIRTUAL: no broker stop order is placed. The monitor / next
#   scan exits with a market order when price crosses the trail/stop.
# For a short, "favourable" is price falling; peak = lowest price seen.

def virtual_stop_levels(entry, one_r, side, peak_price=None):
    """Returns (hard_stop_price, trail_stop_price or None).
    peak_price: most favourable price seen so far (low for shorts,
    high for longs). If the trade never reached +0.5R, trail is None."""
    if side == 1:  # long
        hard = entry - one_r
        if peak_price is None:
            return hard, None
        peak_r = (peak_price - entry) / one_r
        if peak_r < 0.5:
            return hard, None
        trail = peak_price - 0.5 * one_r
        return hard, max(trail, hard)
    else:  # short
        hard = entry + one_r
        if peak_price is None:
            return hard, None
        peak_r = (entry - peak_price) / one_r
        if peak_r < 0.5:
            return hard, None
        trail = peak_price + 0.5 * one_r
        return hard, min(trail, hard)

def virtual_exit_check(entry, one_r, side, current_price, peak_price=None):
    """Returns (should_exit, reason, hard_stop, trail_stop).
    Exit when price crosses the hard stop or the active trail."""
    hard, trail = virtual_stop_levels(entry, one_r, side, peak_price)
    if side == 1:
        if current_price <= hard:
            return True, f"hard stop hit at ${hard:.2f} (-1R)", hard, trail
        if trail is not None and current_price <= trail:
            return True, f"virtual trail hit at ${trail:.2f}", hard, trail
    else:
        if current_price >= hard:
            return True, f"hard stop hit at ${hard:.2f} (-1R)", hard, trail
        if trail is not None and current_price >= trail:
            return True, f"virtual trail hit at ${trail:.2f}", hard, trail
    return False, "holding", hard, trail

def swing_should_carry(bars, side):
    """Swing carryover: carry to the next day if warranted — price still
    on the right side of EMA20 and the trade has not hit stop/TP.
    Returns True to carry, False to exit at the next open."""
    from experts import ema
    closes = [float(b["c"]) for b in bars]
    e20 = ema(closes, 20)
    if e20 is None or not closes:
        return False
    last = closes[-1]
    return last > e20 if side == 1 else last < e20

def spy_flat_pct(spy_bars):
    """|last close - today's open| / open, in percent. None if no bars."""
    if not spy_bars or len(spy_bars) < 2:
        return None
    o = float(spy_bars[0]["o"])
    c = float(spy_bars[-1]["c"])
    return abs(c - o) / o * 100.0 if o else None

# ---------------------------------------------------------------- runner

def fetch_bars(symbol, timeframe, broker, use_real):
    """Returns (bars, source). Real bars only when a real broker is present."""
    if not use_real:
        return synthetic_bars(symbol), "SYNTHETIC"
    # Real Alpaca bars (paper keys present). Free feed is IEX (default);
    # full SIP feed needs a paid Alpaca subscription.
    from alpaca.data.historical import StockHistoricalDataClient
    from alpaca.data.requests import StockBarsRequest
    from alpaca.data.timeframe import TimeFrame, TimeFrameUnit
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_API_SECRET") or os.environ.get("ALPACA_SECRET_KEY")
    client = StockHistoricalDataClient(key, secret)
    tf = TimeFrame(5, TimeFrameUnit.Minute) if timeframe == "5min" else TimeFrame(1, TimeFrameUnit.Day)
    req = StockBarsRequest(symbol_or_symbols=symbol, timeframe=tf, limit=100)
    bars = client.get_stock_bars(req).df
    out = []
    if bars is not None and len(bars):
        df = bars.reset_index()
        for _, r in df.iterrows():
            out.append({"o": float(r["open"]), "h": float(r["high"]),
                        "l": float(r["low"]), "c": float(r["close"]),
                        "v": float(r["volume"])})
    return out, "ALPACA"

def run_once(cfg, broker, dry_run, use_real, equity):
    """One scan over the watchlist, all three sleeves. Returns a report dict."""
    risk = RiskState(
        equity=equity,
        daily_loss_cap_pct=cfg["risk"]["daily_loss_cap_pct"],
        max_open_day_scalp=cfg["risk"]["max_open_day_scalp"],
        max_open_swing=cfg["risk"]["max_open_swing"],
        max_new_day_per_day=cfg["risk"]["max_new_day_per_day"],
    )
    sectors = cfg.get("sectors", {})
    overlay = cfg.get("hedge_fund_overlay", {})
    policy = exit_policy(tp_r=overlay.get("take_profit_r_day", 2.5),
                         breakeven_after_r=overlay.get("breakeven_after_r", 1.0))
    report = {"mode": "DRY_RUN" if dry_run else "PAPER",
              "data": "SYNTHETIC" if not use_real else "ALPACA",
              "policy": policy, "signals": [], "would_orders": [],
              "skipped": []}
    open_positions = []  # for portfolio-limit checks in this scan
    # Seed with real broker positions when keys are present, so available
    # equity reflects money already committed on the account, not just
    # what this scan opened.
    if use_real:
        try:
            for sym, pos in broker.positions().items():
                mv = float(getattr(pos, "market_value", 0.0) or 0.0)
                side = 1 if str(getattr(pos, "side", "long")).lower().startswith("l") else -1
                open_positions.append({"symbol": sym, "sector": sectors.get(sym, "unknown"),
                                       "side": side, "notional": abs(mv),
                                       "risk_dollars": 0.0, "source": "broker"})
        except Exception:
            pass
    report["account_equity"] = round(float(equity), 2)
    report["available_equity_start"] = round(available_equity(equity, open_positions), 2)

    spy_cfg = cfg.get("spy_filter", {})
    spy_enabled = bool(spy_cfg.get("enabled", False))
    spy_mode = spy_cfg.get("mode", "vs_open")
    for sleeve in ("scalp", "day", "swing"):
        sleeve_cfg = cfg["sleeves"][sleeve]
        if not sleeve_cfg.get("enabled", True):
            continue
        # Day/scalp window: 10:00-15:45 ET. Outside it, no new intraday
        # trades. Swing has no window — it can carry over (see below).
        if sleeve in ("scalp", "day"):
            window = sleeve_cfg.get("window_et", "10:00-15:45")
            if not in_et_window(window):
                report["skipped"].append(
                    ("--", sleeve, "all",
                     f"outside window {window} ET, no new {sleeve} trades"))
                continue
        timeframe = sleeve_cfg["timeframe"]
        risk_pct = sleeve_cfg["risk_pct"]
        # SPY filter, once per sleeve: SPY up = longs only, down = shorts only.
        # FLAT: no trend, so range-trade / scalp only (see config spy_filter).
        spy = 0
        flat_mode = False
        range_experts = set(spy_cfg.get("range_experts", []) or [])
        if spy_enabled:
            spy_bars, _src = fetch_bars("SPY", timeframe, broker, use_real)
            spy = spy_side(spy_bars, spy_mode)
            flat_pct = spy_flat_pct(spy_bars)
            threshold = float(spy_cfg.get("flat_threshold_pct", 0.15))
            flat_mode = bool(
                spy_cfg.get("flat_mode") == "range_scalp"
                and flat_pct is not None and flat_pct < threshold)
            report.setdefault("spy_filter", {})[sleeve] = {
                "mode": spy_mode,
                "side": "range_only" if flat_mode else "long_only" if spy == 1 else "short_only" if spy == -1 else "both",
                "flat_pct": round(flat_pct, 3) if flat_pct is not None else None,
                "flat_mode": flat_mode,
            }
            if flat_mode and sleeve == "swing":
                report["skipped"].append(
                    ("SPY", sleeve, "all",
                     f"spy_filter: SPY flat ({flat_pct:.3f}% < {threshold}%), "
                     f"range/scalp only, swing off"))
                continue
        for symbol in cfg["watchlist"]:
            bars, source = fetch_bars(symbol, timeframe, broker, use_real)
            report["data"] = source
            if len(bars) < 51:
                continue
            signals = scan(bars, sleeve_cfg["experts"])
            signals = combine_signals(signals)
            for sig in signals:
                if flat_mode and sig["expert"] not in range_experts:
                    report["skipped"].append(
                        (symbol, sleeve, sig["expert"],
                         "spy_filter: SPY flat, range experts only "
                         f"({sig['expert']} is trend/breakout)"))
                    continue
                if spy_enabled and not flat_mode and spy != 0 and sig["side"] != spy:
                    report["skipped"].append(
                        (symbol, sleeve, sig["expert"],
                         f"spy_filter: SPY {'up' if spy == 1 else 'down'} "
                         f"({spy_mode}), {'longs' if spy == 1 else 'shorts'} only"))
                    continue
                entry = sig["entry"]
                stop_dist = abs(entry - sig["stop"])
                if stop_dist <= 0:
                    continue
                # Sizing on AVAILABLE equity, not full account equity.
                avail = available_equity(equity, open_positions)
                qty, notional = vol_scaled_size(
                    avail, risk_pct, stop_dist, entry,
                    max_notional_pct=cfg["risk"]["max_notional_pct"])
                if qty <= 0:
                    report["skipped"].append((symbol, sleeve, sig["expert"], "size 0 after vol scaling"))
                    continue
                trade_cost = costs(entry, qty,
                                   bps_per_side=overlay.get("costs_bps_per_side", 3.0),
                                   sec_fee_bps=overlay.get("sec_fee_bps", 0.1))
                # AQR: model costs before trading. A signal must clear its
                # own round-trip cost at 1R to be worth taking.
                if trade_cost * 2 >= avail * risk_pct:
                    report["skipped"].append((symbol, sleeve, sig["expert"], "costs exceed 1R risk"))
                    continue
                sector = sectors.get(symbol, "unknown")
                ok, reason = check_portfolio_limits(
                    open_positions, symbol, sector, sig["side"], sectors,
                    max_open=cfg["risk"]["max_open_day_scalp"] if sleeve != "swing" else cfg["risk"]["max_open_swing"],
                    max_per_sector=cfg["risk"]["max_per_sector"],
                    max_net_exposure_pct=cfg["risk"]["max_net_exposure_pct"],
                    equity=equity)
                if not ok:
                    report["skipped"].append((symbol, sleeve, sig["expert"], reason))
                    continue
                # Hard total cap: up to 5 open trades at once, all sleeves.
                total_cap = int(cfg["risk"].get("max_open_total", 5))
                if len(open_positions) >= total_cap:
                    report["skipped"].append((symbol, sleeve, sig["expert"], f"total max open {total_cap} reached"))
                    continue
                ok, reason = risk.can_open(sleeve)
                if not ok:
                    report["skipped"].append((symbol, sleeve, sig["expert"], reason))
                    continue
                sig_out = {**sig, "symbol": symbol, "sleeve": sleeve,
                           "qty": qty, "notional": round(notional, 2),
                           "available_equity": round(avail, 2),
                           "cost_one_side": round(trade_cost, 2),
                           "tp_r": sig["tp_r"] if sig["tp_r"] is not None else policy["tp_r"]}
                # Swing carryover: no day-end flatten. Carry if price is
                # still on the right side of EMA20; exit on stop / TP /
                # EMA20 break / signal decay.
                if sleeve == "swing":
                    sig_out["carryover"] = swing_should_carry(bars, sig["side"])
                report["signals"].append(sig_out)
                risk.record_open(sleeve)
                open_positions.append({"symbol": symbol, "sector": sector,
                                       "side": sig["side"], "notional": notional,
                                       "risk_dollars": round(qty * stop_dist, 2)})
                if dry_run or not use_real:
                    report["would_orders"].append(
                        broker.submit(symbol, sig["side"], qty, entry, sig["stop"], sleeve, sig["expert"]))
                else:
                    report["would_orders"].append(
                        broker.submit(symbol, sig["side"], qty, entry, sig["stop"], sleeve, sig["expert"]))
    worst, worst_pct = stress_test(open_positions, equity)
    report["risk"] = risk.summary()
    report["stress_worst_case_loss"] = round(worst, 2)
    report["stress_worst_case_pct"] = round(worst_pct, 2)
    return report

def main(argv=None):
    p = argparse.ArgumentParser(
        description="Stock bot: top-10 experts + hedge-fund overlay. PAPER ONLY.")
    p.add_argument("--paper", action="store_true", help="paper trading (keys via env/.env)")
    p.add_argument("--dry-run", action="store_true", help="print what it would do, place no orders")
    p.add_argument("--live", action="store_true", help="BLOCKED: live trading is never enabled")
    p.add_argument("--config", default=None, help="path to config.yaml")
    p.add_argument("--equity", type=float, default=100000.0, help="paper equity for sizing (default 100000)")
    args = p.parse_args(argv)

    if args.live:
        print("LIVE TRADING IS BLOCKED for this bot. Use --paper or --dry-run. "
              "This bot is unvalidated and paper-only.", file=sys.stderr)
        return 2

    load_dotenv()
    cfg = load_config(args.config)
    if cfg.get("allow_live"):
        print("config allow_live must stay false", file=sys.stderr)
        return 2

    # Alpaca docs: keys via env/.env, never in code. Secret is also
    # documented as ALPACA_SECRET_KEY in some Alpaca guides — accept both,
    # preferring ALPACA_API_SECRET (the name in our .env.example).
    key = os.environ.get("ALPACA_API_KEY")
    secret = os.environ.get("ALPACA_API_SECRET") or os.environ.get("ALPACA_SECRET_KEY")
    use_real = bool(key and secret) and not args.dry_run
    if args.paper and not use_real and not args.dry_run:
        print("No Alpaca keys found (ALPACA_API_KEY / ALPACA_API_SECRET). "
              "Running in stub mode with SYNTHETIC demo bars — no orders, no network. "
              "Add keys via env or a local .env to fetch real bars.")
    if args.dry_run:
        print("DRY RUN — no orders will be placed. Data: SYNTHETIC demo bars.")

    broker = AlpacaBroker(key, secret) if use_real else PaperBrokerStub(args.equity)
    equity = broker.equity() if use_real else args.equity

    print(f"{BOT_NAME} {VERSION}")
    print(f"mode={'DRY_RUN' if args.dry_run else 'PAPER'} data={'ALPACA' if use_real else 'SYNTHETIC'} "
          f"equity=${equity:,.0f} time={datetime.now(timezone.utc).isoformat()}")
    report = run_once(cfg, broker, dry_run=args.dry_run or not use_real,
                      use_real=use_real, equity=equity)
    if report.get("spy_filter"):
        for sleeve, f in report["spy_filter"].items():
            flat = f" FLAT {f.get('flat_pct')}% -> range/scalp only" if f.get("flat_mode") else ""
            print(f"SPY filter [{sleeve}]: mode={f['mode']} side={f['side']}{flat}")
    print(f"signals found: {len(report['signals'])}")
    for s in report["signals"]:
        side = "LONG" if s["side"] == 1 else "SHORT"
        print(f"  [{s['sleeve']}] {s['symbol']} {side} {s['expert']} "
              f"entry ${s['entry']:.2f} stop ${s['stop']:.2f} qty {s['qty']} "
              f"notional ${s['notional']:,.0f} tp {s['tp_r']}R exit {s['exit']} "
              f"({s['reason']})")
    if args.dry_run or not use_real:
        for o in report["would_orders"]:
            print(f"  WOULD PLACE (no order): {o['sleeve']} {o['symbol']} "
                  f"{'LONG' if o['side'] == 1 else 'SHORT'} x{o['qty']} "
                  f"via {o['expert']} — {o['status']}")
    else:
        for o in report["would_orders"]:
            print(f"  PAPER ORDER: {o}")
    for sk in report["skipped"]:
        print(f"  skipped: {sk[0]} {sk[1]} {sk[2]} — {sk[3]}")
    print(f"risk: {report['risk']}")
    print(f"stress test (all stops hit at once): -${report['stress_worst_case_loss']:,.2f} "
          f"({report['stress_worst_case_pct']:.2f}% of equity)")
    print("Done. No live trading. This bot is UNVALIDATED — backtest + paper forward test required before any live use.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
