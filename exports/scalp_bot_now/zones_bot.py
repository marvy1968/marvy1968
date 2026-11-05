"""Paper bot for the Weinstein / Velez 365-day zone strategy (zones.py rules).

    python zones_bot.py                 # plan: setups, triggers, management (no orders)
    python zones_bot.py --submit        # PAPER orders (paper account only)
    add --market-filter to require SPY in Stage 2 for new entries

Run it every ~15 minutes during market hours (cron). Each run:
  1. once per day: find setups on the last completed daily candle (Stage 2 + Velez bar inside the 365-day
     buy zone) -> pending triggers, valid for 3 sessions
  2. a pending stock trading at/above its trigger -> two GTC orders, protected from the moment they fill:
       lot A (half): bracket  = market entry + TP1 limit + stop
       lot B (rest): OTO      = market entry + stop
  3. TP1 filled -> lot B stop moved to breakeven (cancel leg, new stop; OTO legs cannot be modified)
  4. first session after a week closes below the 30-week SMA -> cancel this bot's orders, sell its shares
  5. any stop older than 80 days is renewed (Alpaca cancels GTC orders after 90 days)
Only orders tagged ZN_ are ever touched. Live fills happen at the market price when the trigger is seen,
so they can be a little worse than the backtest's fill at max(open, trigger).
"""
import argparse
import json
import os
import uuid
from datetime import date, timedelta

import numpy as np
import pandas as pd

import weinstein as ws
import zones

PREFIX = "ZN"
STATE_FILE = "zones_state.json"
STOP_RENEW_DAYS = 80
OPEN = {"new", "accepted", "held", "partially_filled", "pending_new", "pending_replace", "accepted_for_bidding"}


def _val(x):
    return str(getattr(x, "value", x)).lower()


def _cid(kind, sym):
    return f"{PREFIX}_{kind}_{sym}_{uuid.uuid4().hex[:6]}"


def load_state():
    if os.path.exists(STATE_FILE):
        try:
            return json.load(open(STATE_FILE))
        except Exception:   # noqa: BLE001
            os.replace(STATE_FILE, STATE_FILE + ".corrupt")
    return {"setup_day": None, "pending": {}, "positions": {}}


def save_state(st):
    tmp = STATE_FILE + ".tmp"
    json.dump(st, open(tmp, "w"), indent=2, default=str)
    os.replace(tmp, STATE_FILE)


def find_setups(panel, spy_daily, signal_day, equity, market_filter=False):
    """Same rules as zones.run: Stage 2 + zones from the week BEFORE the signal day's week,
    Velez bullish bar on the signal day's candle touching the buy zone."""
    import velez
    sd = pd.Timestamp(signal_day)
    prev_wk = sd.to_period("W-FRI") - 1
    if market_filter and not bool(ws.spy_stage2(spy_daily).get(prev_wk, False)):
        print("market filter: SPY not in Stage 2 - no new setups")
        return []
    spy_w = ws.weekly(pd.DataFrame({"open": spy_daily, "high": spy_daily, "low": spy_daily,
                                    "close": spy_daily, "volume": 0.0}))["close"]
    out = []
    for s, d in panel.items():
        d = d[d.index <= sd]
        if len(d) < 260 or d.index[-1] != sd:
            continue
        adv = float((d["close"] * d["volume"]).rolling(20).mean().iloc[-2])
        if not (adv >= ws.MIN_ADV and d["close"].iloc[-2] >= ws.MIN_PRICE):
            continue
        f = ws.stage_frame(ws.weekly(d), spy_w)
        if prev_wk not in f.index:
            continue
        r = f.loc[prev_wk]
        if not (r["close"] > r["ma30"] and r["slope_ok"] and r["mansfield"] > 0):
            continue
        per = d.index.to_period("W-FRI")
        i_wk = int(np.flatnonzero(per == prev_wk)[-1])
        z = zones.zones_at(d, i_wk, float(r["ma30"]), float(zones._atr(d).iloc[i_wk]))
        if z is None:
            continue
        lo, hi, cl = float(d["low"].iloc[-1]), float(d["high"].iloc[-1]), float(d["close"].iloc[-1])
        if not (lo <= z["zone_hi"] and cl >= z["zone_lo"]):
            continue
        a = {k: d[k].to_numpy() for k in ("open", "high", "low", "close")}
        a["vz_body_avg"] = (d["close"] - d["open"]).abs().rolling(20).mean().shift(1).to_numpy()
        kind, side = velez.pattern(a, len(d) - 1)
        if side != 1:
            continue
        trig, stop = round(hi + 0.01, 2), round(min(lo, z["zone_lo"]) - 0.01, 2)
        if stop <= 0 or (trig - stop) / trig > zones.MAX_STOP_PCT:
            continue
        open_air = not np.isfinite(z["tp1"])
        if not open_air and (z["tp1"] - trig) < zones.MIN_RR * (trig - stop):
            continue
        tp1 = round(trig + zones.OPEN_AIR_R * (trig - stop), 2) if open_air else round(float(z["tp1"]), 2)
        qty = int(min(zones.RISK_PCT * equity / (trig - stop), zones.MAX_POS_PCT * equity / trig))
        if qty >= 2:
            out.append(dict(symbol=s, trigger=trig, stop=stop, tp1=tp1, qty=qty, pattern=kind, open_air=open_air,
                            signal_day=str(sd.date()), zone=f"{z['zone_lo']:.2f}-{z['zone_hi']:.2f}"))
    return out


def run_bot(tc, panel, spy_daily, price_fn, submit=False, today=None, market_filter=False):
    import config
    from broker import call
    from alpaca.trading.enums import OrderClass, OrderSide, QueryOrderStatus, TimeInForce
    from alpaca.trading.requests import (GetOrderByIdRequest, GetOrdersRequest, MarketOrderRequest,
                                         StopLossRequest, StopOrderRequest, TakeProfitRequest)
    if submit and not config.PAPER:
        raise SystemExit("zones bot is paper-only: APCA_PAPER must be true")
    today = pd.Timestamp(today or date.today()).normalize()
    st = load_state()
    clock = call(tc.get_clock, what="clock")
    if submit and not clock.is_open:
        print("market closed - planning only, nothing submitted")
        submit = False
    equity = float(call(tc.get_account, what="account").equity)
    bpos = {p.symbol: abs(float(p.qty)) for p in call(tc.get_all_positions, what="positions")}
    orders = call(tc.get_orders, filter=GetOrdersRequest(status=QueryOrderStatus.OPEN), what="orders")
    mine, parents = {}, {}
    for o in orders:                                   # standalone orders this bot placed (tagged ZN_)
        if (o.client_order_id or "").startswith(PREFIX + "_") and _val(o.status) in OPEN:
            mine.setdefault(o.symbol, []).append(o)
    for s, p in st["positions"].items():               # bracket / OTO legs: found through OUR parent orders
        for key in ("a_id", "b_id"):
            if p.get(key):
                try:
                    par = call(tc.get_order_by_id, p[key], filter=GetOrderByIdRequest(nested=True), what=f"order {s}")
                except Exception as e:   # noqa: BLE001
                    print(f"WARN  {s}: could not load order {p[key]} ({e}) - management of it skipped this run")
                    continue
                parents[p[key]] = par
                for leg in (par.legs or []):
                    if _val(leg.status) in OPEN and all(leg.id != x.id for x in mine.get(s, [])):
                        mine.setdefault(s, []).append(leg)
    sessions = spy_daily.index[spy_daily.index < today]
    last_session = sessions[-1] if len(sessions) else None
    print(f"\n=== ZONES BOT {'(SUBMITTING - PAPER)' if submit else '(PLAN - no orders)'} === equity ${equity:,.0f}")

    # 0) reconcile: positions gone at the broker (stopped / sold) leave the state
    for s in list(st["positions"]):
        if s not in bpos and st["positions"][s].get("filled"):
            print(f"{s}: position closed at broker - removed")
            st["positions"].pop(s)

    # 1) setups once per completed session
    if last_session is not None and st["setup_day"] != str(last_session.date()):
        new = find_setups(panel, spy_daily, last_session, equity, market_filter)
        for s in list(st["pending"]):                 # a new session started: count down old triggers
            st["pending"][s]["sessions_left"] -= 1
            if st["pending"][s]["sessions_left"] <= 0:
                print(f"{s}: trigger expired unfilled")
                st["pending"].pop(s)
        for s in new:
            if s["symbol"] in st["positions"] or s["symbol"] in bpos:
                continue
            s["sessions_left"] = zones.TRIGGER_DAYS
            st["pending"][s["symbol"]] = s
        st["setup_day"] = str(last_session.date())
        print(f"setups from {last_session.date()}: {len(new)} | pending triggers: {len(st['pending'])}")

    # 2) triggers -> two protected lots
    slots = zones.MAX_POSITIONS - len(st["positions"])
    for s, p in list(st["pending"].items()):
        if slots <= 0:
            break
        if s in bpos or s in mine:
            continue
        px = price_fn(s)
        if px is None or px < p["trigger"]:
            continue
        if px <= p["stop"]:
            st["pending"].pop(s)
            continue
        qa = p["qty"] // 2
        qb = p["qty"] - qa
        print(f"BUY   {s}: {p['qty']} sh @~{px:.2f} (trigger {p['trigger']}) stop {p['stop']} TP1 {p['tp1']} "
              f"[{p['pattern']}, zone {p['zone']}{', open air' if p['open_air'] else ''}]")
        slots -= 1
        if not submit:
            continue
        a = call(tc.submit_order, order_data=MarketOrderRequest(
            symbol=s, qty=qa, side=OrderSide.BUY, time_in_force=TimeInForce.GTC, order_class=OrderClass.BRACKET,
            take_profit=TakeProfitRequest(limit_price=p["tp1"]), stop_loss=StopLossRequest(stop_price=p["stop"]),
            client_order_id=_cid("A", s)), what=f"lot A {s}")
        b = call(tc.submit_order, order_data=MarketOrderRequest(
            symbol=s, qty=qb, side=OrderSide.BUY, time_in_force=TimeInForce.GTC, order_class=OrderClass.OTO,
            stop_loss=StopLossRequest(stop_price=p["stop"]), client_order_id=_cid("B", s)), what=f"lot B {s}")
        st["positions"][s] = dict(qa=qa, qb=qb, stop=p["stop"], tp1=p["tp1"], entry_ref=px, a_id=str(a.id),
                                  b_id=str(b.id), be_moved=False, filled=True, since=str(today.date()))
        st["pending"].pop(s)

    # 3) management of held positions
    new_week = last_session is not None and len(sessions) >= 2 and \
        sessions[-1].to_period("W-FRI") != today.to_period("W-FRI")
    for s, p in list(st["positions"].items()):
        if s not in bpos:
            continue
        ords = mine.get(s, [])
        # 3a) weekly exit (first session after the week closed below the 30-week SMA)
        if new_week and s not in panel:
            print(f"WARN  {s}: no daily data this run - weekly exit check skipped (broker stop still protects it)")
        elif new_week:
            f = ws.stage_frame(ws.weekly(panel[s][panel[s].index < today]),
                               ws.weekly(pd.DataFrame({"open": spy_daily, "high": spy_daily, "low": spy_daily,
                                                       "close": spy_daily, "volume": 0.0}))["close"])
            wk = today.to_period("W-FRI") - 1
            if wk in f.index and bool(f.at[wk, "exit"]):
                held = min(bpos[s], p["qa"] + p["qb"] if not p.get("tp1_done") else p["qb"])
                print(f"EXIT  {s}: weekly close below 30-week SMA -> cancel ZN orders, sell {held:g}")
                if submit:
                    for o in ords:
                        call(tc.cancel_order_by_id, o.id, what=f"cancel {s}")
                    call(tc.submit_order, order_data=MarketOrderRequest(
                        symbol=s, qty=held, side=OrderSide.SELL, time_in_force=TimeInForce.DAY,
                        client_order_id=_cid("X", s)), what=f"exit {s}")
                    st["positions"].pop(s)
                continue
        # 3b) TP1 done? lot A's take-profit filled -> lot B stop to breakeven
        if not p["be_moved"]:
            a = parents.get(p["a_id"])
            tp_filled = a is not None and any(_val(l.status) == "filled" and getattr(l, "limit_price", None) for l in (a.legs or []))
            if tp_filled:
                be = round(float(p["entry_ref"]), 2)
                print(f"TP1   {s}: target hit -> lot B stop to breakeven {be}")
                p["tp1_done"] = True
                if submit:
                    for o in ords:
                        if getattr(o, "stop_price", None) and _val(o.status) in OPEN:
                            call(tc.cancel_order_by_id, o.id, what=f"cancel lot B stop {s}")
                    call(tc.submit_order, order_data=StopOrderRequest(
                        symbol=s, qty=p["qb"], side=OrderSide.SELL, stop_price=be, time_in_force=TimeInForce.GTC,
                        client_order_id=_cid("S", s)), what=f"BE stop {s}")
                    p["be_moved"], p["stop"] = True, be
        # 3c) renew stops older than 80 days (Alpaca cancels GTC after 90)
        now = pd.Timestamp.now(tz="UTC")
        for o in ords:
            made = getattr(o, "created_at", None)
            if getattr(o, "stop_price", None) and made is not None and \
                    (now - pd.Timestamp(made).tz_convert("UTC")).days >= STOP_RENEW_DAYS:
                q = float(getattr(o, "qty", 0) or 0)
                print(f"RENEW {s}: stop {o.stop_price} is {(now - pd.Timestamp(made).tz_convert('UTC')).days} days old")
                if submit and q > 0:
                    call(tc.cancel_order_by_id, o.id, what=f"cancel old stop {s}")
                    call(tc.submit_order, order_data=StopOrderRequest(
                        symbol=s, qty=q, side=OrderSide.SELL, stop_price=float(o.stop_price),
                        time_in_force=TimeInForce.GTC, client_order_id=_cid("S", s)), what=f"renew stop {s}")
    if st["pending"]:
        print("pending triggers: " + ", ".join(f"{k}@{v['trigger']}" for k, v in st["pending"].items()))
    save_state(st) if submit else None
    return st


def main():
    import config
    import swing
    from alpaca.data.enums import DataFeed
    from alpaca.data.requests import StockLatestTradeRequest
    from alpaca.trading.client import TradingClient
    from data import make_data_client
    ap = argparse.ArgumentParser()
    ap.add_argument("--submit", action="store_true")
    ap.add_argument("--market-filter", action="store_true")
    ap.add_argument("--universe-file")
    a = ap.parse_args()
    today = date.today()
    syms = swing._symbols(a.universe_file)
    panel = swing.load_panel(syms, today - timedelta(days=800), today)
    spy = swing.load_panel(["SPY"], today - timedelta(days=800), today)["SPY"]["close"]
    dc = make_data_client()

    def price(sym):
        try:
            return float(dc.get_stock_latest_trade(StockLatestTradeRequest(
                symbol_or_symbols=sym, feed=DataFeed(config.LIVE_FEED)))[sym].price)
        except Exception:   # noqa: BLE001
            return None
    run_bot(TradingClient(config.API_KEY, config.API_SECRET, paper=True), panel, spy, price,
            submit=a.submit, market_filter=a.market_filter)


if __name__ == "__main__":
    main()
