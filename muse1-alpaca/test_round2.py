#!/usr/bin/env python3
"""Unit tests for the 2026-11-05 Claude fix list, round two (muse1/alpaca v0.3.0).

Run:  python3 test_round2.py
No keys, no network, no orders. Each test prints PASS/FAIL; the script
exits non-zero if any test fails.

Covers items 1-5 and 7a of the round-two list:
  1. No-target signals never produce a TP-at-entry order (entry+stop only).
  2. Swing uses its own 2R TP; day/scalp use 2.5R.
  3. Brooks/Dalton submit exactly the target they checked for reward:risk.
  4. A failed stop replace re-submits a standalone stop, verifies it, and
     records the position as unprotected (blocking new trades) if no stop
     can be confirmed.
  5. The duplicate check sees ALL broker positions; unknown broker
     positions are adopted into state (broker entry price, stop-derived 1R).
  7a. The VM schedule runs through 15:50 ET and a 15:50 run flattens day/scalp.
  + SPY swing filter fix (v0.3.1): daily bars use the last bar's own open
    vs close — the old code compared the last close to the open ~90 days
    ago and read short_only while SPY was up.
"""
import copy
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import experts
from experts import brooks_range_fade, dalton_value, raschke_soup, atr
import bot
from bot import (validate_target, resolve_target, default_state,
                 run_once, manage_positions, adopt_broker_positions,
                 PaperBrokerStub, spy_side, spy_flat_pct,
                 alpaca_keys_from_env, paper_base_url_from_env,
                 AlpacaBroker)
from bot import ET

PASS, FAIL = [], []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail and not cond else ""))

def bars_from_closes(closes, vol=1000.0):
    out = []
    now = datetime.now(timezone.utc)
    for i, c in enumerate(closes):
        out.append({"t": now - timedelta(minutes=5 * (len(closes) - i)),
                    "o": c, "h": c + 0.5, "l": c - 0.5, "c": c, "v": vol})
    return out

def bars_ohlc(rows):
    out = []
    now = datetime.now(timezone.utc)
    for i, (o, h, l, c) in enumerate(rows):
        out.append({"t": now - timedelta(minutes=5 * (len(rows) - i)),
                    "o": o, "h": h, "l": l, "c": c, "v": 1000.0})
    return out

def soup_long_bars():
    """52 bars that fire a raschke_soup LONG: 50 flat bars at 100
    (range 99-101), a poke below the 20-bar low (98), then a close back
    inside at 100.5. Entry 100.5, stop 97.0, no fixed TP."""
    rows = [(100, 101, 99, 100.0)] * 50
    rows.append((99, 100, 98, 99.0))
    rows.append((100.5, 101, 99.5, 100.5))
    return bars_ohlc(rows)

def _test_cfg(no_sleeve_tp=False):
    """Minimal config for run_once tests: day sleeve live with
    raschke_soup only, SPY filter off, watchlist TEST."""
    overlay = {"breakeven_after_r": 1.0, "time_exit_bars": 12,
               "costs_bps_per_side": 3.0, "sec_fee_bps": 0.1}
    if not no_sleeve_tp:
        overlay["take_profit_r_day"] = 2.5
    return {
        "strategy": {"min_reward_risk": 1.5},
        "rollout": {"live": ["raschke_soup"], "log_only": []},
        "risk": {"daily_loss_cap_pct": 0.02, "max_open_day_scalp": 5,
                 "max_open_swing": 5, "max_new_day_per_day": 5,
                 "max_per_sector": 2, "max_notional_pct": 0.25,
                 "max_net_exposure_pct": 0.60},
        "sectors": {"TEST": "tech", "HELD": "tech"},
        "hedge_fund_overlay": overlay,
        "spy_filter": {"enabled": False},
        "sleeves": {
            "scalp": {"enabled": False, "timeframe": "5min",
                      "window_et": "10:00-15:45", "risk_pct": 0.0025,
                      "experts": []},
            "day": {"enabled": True, "timeframe": "5min",
                    "window_et": "10:00-15:45", "risk_pct": 0.005,
                    "experts": ["raschke_soup"]},
            "swing": {"enabled": False, "timeframe": "daily",
                      "risk_pct": 0.005, "take_profit_r": 2.0,
                      "max_hold_days": 10, "experts": []},
        },
        "watchlist": ["TEST"],
        "data": {"stale_bar_minutes": 30},
    }

class FakeBroker:
    """Broker fake for run_once / manage_positions tests. Maintains a
    list of open orders (so stop verification sees what a real broker
    would), and records every order call. Flags control failure paths."""
    def __init__(self, positions=None, open_orders=None, equity=100000.0):
        self._positions = positions or {}
        self._open_orders = list(open_orders or [])
        self._equity = equity
        self.submitted = []    # submit_bracket records
        self.stops = []        # submit_stop attempts (incl. failed ones)
        self.replaced = []     # replace_stop calls
        self.cancelled = []    # cancel_order calls
        self.closed = []       # close_market calls
        self.replace_ok = True
        self.cancel_ok = True
        self.submit_stop_ok = True
        self.closed_list = []  # closed_orders() return value

    def positions(self):
        return dict(self._positions)

    def open_orders(self, symbols=None):
        return list(self._open_orders)

    def open_stop_orders(self, symbol=None):
        out = []
        for o in self._open_orders:
            if "stop" not in str(getattr(o, "order_type", "") or "").lower():
                continue
            if symbol is not None and str(getattr(o, "symbol", "")) != symbol:
                continue
            out.append(o)
        return out

    def equity(self):
        return self._equity

    def shortable(self, symbol):
        return True

    def get_order(self, order_id):
        return None

    def get_order_by_client_id(self, client_order_id):
        return None

    def closed_orders(self, limit=50):
        return list(self.closed_list)

    def get_calendar(self, start, end):
        return []

    def get_clock(self):
        return None

    def submit_bracket(self, symbol, side, qty, entry_price, stop_price,
                       target_price, sleeve, expert, tif="DAY",
                       client_order_id=None):
        # Mirror the real brokers: same validate_target, same signature.
        validate_target(float(entry_price), side, target_price)
        rec = {"symbol": symbol, "side": side, "qty": qty,
               "entry_price": entry_price, "stop_price": stop_price,
               "target_price": target_price, "sleeve": sleeve,
               "expert": expert, "status": "FAKE_SUBMITTED",
               "order_id": f"fake-{symbol}-{len(self.submitted)}",
               "stop_order_id": f"fake-stop-{symbol}",
               "target_order_id": f"fake-tp-{symbol}",
               "client_order_id": client_order_id or f"fake-cid-{symbol}"}
        self.submitted.append(rec)
        return rec

    def replace_stop(self, order_id, new_stop_price):
        self.replaced.append((order_id, new_stop_price))
        return (self.replace_ok,
                "fake-ok" if self.replace_ok else "fake-replace-failed")

    def cancel_order(self, order_id):
        self.cancelled.append(order_id)
        if self.cancel_ok:
            self._open_orders = [o for o in self._open_orders
                                 if str(getattr(o, "id", "")) != order_id]
        return (self.cancel_ok,
                "fake-ok" if self.cancel_ok else "fake-cancel-failed")

    def submit_stop(self, symbol, order_side, qty, stop_price,
                    tif="GTC", client_order_id=None):
        self.stops.append({"symbol": symbol, "side": order_side, "qty": qty,
                           "stop_price": stop_price})
        if not self.submit_stop_ok:
            raise RuntimeError("fake submit_stop failed")
        new = SimpleNamespace(symbol=symbol, order_type="stop",
                              id=f"fake-newstop-{symbol}-{len(self.stops)}",
                              stop_price=stop_price)
        self._open_orders.append(new)
        return new

    def close_market(self, symbol, qty, order_side):
        self.closed.append((symbol, qty, order_side))
        return {"symbol": symbol, "qty": qty, "side": order_side,
                "status": "FAKE_CLOSED", "order_id": f"fake-close-{symbol}"}

def _pos(symbol, side, qty, entry, one_r, stop_price, sleeve="day",
         stop_order_id="old-stop-1", minutes_ago=10, now_et=None):
    now_et = now_et or datetime.now(ET)
    return {"symbol": symbol, "side": side, "qty": qty, "entry": entry,
            "one_r": one_r, "stop_price": stop_price,
            "target_price": None, "sleeve": sleeve, "expert": "raschke_soup",
            "entry_filled": True, "stop_order_id": stop_order_id,
            "peak": entry, "bars_held": 0, "be_moved": False,
            "entry_time": (now_et - timedelta(minutes=minutes_ago)).isoformat()}

def _mgmt_cfg():
    return {"hedge_fund_overlay": {"breakeven_after_r": 1.0,
                                   "time_exit_bars": 12},
            "sleeves": {"scalp": {"intended_hold_minutes": 30},
                        "swing": {"max_hold_days": 10}}}

def _noon_et():
    return datetime.now(ET).replace(hour=12, minute=0, second=0, microsecond=0)

# ------------------------------------------------ 1. validate_target
for side, entry, tp, ok, label in [
        (1, 100.0, 105.0, True, "long TP above entry ok"),
        (-1, 100.0, 95.0, True, "short TP below entry ok"),
        (1, 100.0, 100.0, False, "long TP == entry refused"),
        (-1, 100.0, 100.0, False, "short TP == entry refused"),
        (1, 100.0, 99.0, False, "long TP below entry refused"),
        (-1, 100.0, 101.0, False, "short TP above entry refused")]:
    try:
        validate_target(entry, side, tp)
        raised = False
    except ValueError:
        raised = True
    check(f"validate_target: {label}", raised != ok, f"tp={tp} side={side}")
check("validate_target: None (no fixed TP) is valid",
      validate_target(100.0, 1, None) is True)

# ------------------------------------------------ 1+2. resolve_target
CFG_TP = {"hedge_fund_overlay": {"take_profit_r_day": 2.5},
          "sleeves": {"swing": {"take_profit_r": 2.0}}}
CFG_NO_TP = {"hedge_fund_overlay": {}, "sleeves": {"swing": {}}}

def _sig(side, entry, stop, tp_r=None, target=None):
    return {"expert": "x", "side": side, "entry": entry, "stop": stop,
            "tp_r": tp_r, "target": target}

tp, tpr = resolve_target(_sig(1, 100.0, 98.0), "day", CFG_TP)
check("resolve_target: day sleeve, no expert TP -> 2.5R",
      tp == 105.0 and tpr == 2.5, f"got ({tp}, {tpr})")
tp, tpr = resolve_target(_sig(1, 100.0, 98.0), "swing", CFG_TP)
check("resolve_target: swing sleeve, no expert TP -> 2.0R (not 2.5R)",
      tp == 104.0 and tpr == 2.0, f"got ({tp}, {tpr})")
tp, tpr = resolve_target(_sig(-1, 100.0, 103.0), "day", CFG_TP)
check("resolve_target: day SHORT -> entry - 2.5R",
      tp == 92.5 and tpr == 2.5, f"got ({tp}, {tpr})")
tp, tpr = resolve_target(_sig(-1, 100.0, 103.0), "swing", CFG_TP)
check("resolve_target: swing SHORT -> entry - 2.0R",
      tp == 94.0 and tpr == 2.0, f"got ({tp}, {tpr})")
tp, tpr = resolve_target(_sig(1, 100.0, 98.0, tp_r=2.0), "day", CFG_TP)
check("resolve_target: expert tp_r 2.0 wins over sleeve 2.5R",
      tp == 104.0 and tpr == 2.0, f"got ({tp}, {tpr})")
tp, tpr = resolve_target(_sig(1, 100.0, 98.0, target=110.0), "day", CFG_TP)
check("resolve_target: expert target price wins, tp_r derived from it",
      tp == 110.0 and abs(tpr - 5.0) < 1e-9, f"got ({tp}, {tpr})")
tp, tpr = resolve_target(_sig(1, 100.0, 98.0), "day", CFG_NO_TP)
check("resolve_target: no sleeve TP configured -> (None, None), no TP leg",
      tp is None and tpr is None, f"got ({tp}, {tpr})")
try:
    resolve_target(_sig(1, 100.0, 98.0, target=100.0), "day", CFG_TP)
    raised = False
except ValueError:
    raised = True
check("resolve_target: expert target == entry -> ValueError, signal skipped",
      raised)

# ------------------------------------------------ 1. submit_bracket (stub)
stub = PaperBrokerStub(100000.0)
rec = stub.submit_bracket("AAPL", 1, 10, 100.0, 98.0, 105.0, "day", "lien_fader")
check("stub bracket: valid TP -> WOULD_PLACE_BRACKET with the TP",
      rec["target_price"] == 105.0 and "WOULD_PLACE_BRACKET" in rec["status"])
rec = stub.submit_bracket("AAPL", 1, 10, 100.0, 98.0, None, "day", "raschke_soup")
check("stub bracket: target None -> OTO stop-only, never TP-at-entry",
      rec["target_price"] is None and "OTOSTOP" in rec["status"],
      f"got {rec['status']}")
for args, label in [
        (("AAPL", 1, 10, 100.0, 98.0, 100.0, "day", "x"), "long TP == entry"),
        (("AAPL", -1, 10, 100.0, 102.0, 100.0, "day", "x"), "short TP == entry"),
        (("AAPL", -1, 10, 100.0, 102.0, 101.0, "day", "x"), "short TP above entry")]:
    try:
        stub.submit_bracket(*args)
        raised = False
    except ValueError:
        raised = True
    check(f"stub bracket: {label} -> ValueError, no order recorded", raised)
check("stub bracket: refused orders were not recorded",
      len(stub.would_orders) == 2, f"would_orders={len(stub.would_orders)}")

# ------------------------------------------------ 3. Brooks / Dalton: checked target == submitted target
# Brooks SHORT (setup from test_fixes.py §4): range 90-110, last bar pokes
# to 111 and closes 108.5; midpoint 100.
bars = bars_from_closes([100.0] * 21)
for b in bars[:6]:
    b["h"], b["l"] = 110.0, 90.0
for b in bars[6:20]:
    b["h"], b["l"] = 101.0, 99.0
bars[-1] = {"t": bars[-1]["t"], "o": 108.5, "h": 111.0, "l": 107.0, "c": 108.5, "v": 1000.0}
sig = brooks_range_fade(bars)
check("brooks short: signal carries target == range midpoint 100",
      sig is not None and sig.get("target") == 100.0, f"got {sig}")
if sig is not None:
    tp, tpr = resolve_target(sig, "day", CFG_TP)
    check("brooks short: resolved target == checked target (same number)",
          tp == sig["target"] and abs(tpr - abs(100.0 - 108.5) / abs(108.5 - sig["stop"])) < 1e-9,
          f"got ({tp}, {tpr})")
# Brooks LONG: range 90-110, last bar pokes to 89 and closes 91.5.
bars = bars_from_closes([100.0] * 21)
for b in bars[:6]:
    b["h"], b["l"] = 110.0, 90.0
for b in bars[6:20]:
    b["h"], b["l"] = 101.0, 99.0
bars[-1] = {"t": bars[-1]["t"], "o": 91.5, "h": 92.0, "l": 89.0, "c": 91.5, "v": 1000.0}
sig = brooks_range_fade(bars)
check("brooks long: signal carries target == range midpoint 100",
      sig is not None and sig.get("target") == 100.0, f"got {sig}")
if sig is not None:
    tp, tpr = resolve_target(sig, "day", CFG_TP)
    check("brooks long: resolved target == checked target (same number)",
          tp == sig["target"], f"got ({tp}, {tpr})")
# Dalton SHORT (setup from test_fixes.py §4): mean ~100, ATR ~2.1,
# value edges ~102.1/97.9; last bar pokes to 102.3, closes 101.9.
bars = bars_from_closes([100.0] * 21)
for b in bars:
    b["h"], b["l"] = 101.0, 99.0
bars[-1] = {"t": bars[-1]["t"], "o": 101.9, "h": 102.3, "l": 99.5, "c": 101.9, "v": 1000.0}
sig = dalton_value(bars)
closes = [b["c"] for b in bars]
lo_edge = sum(closes) / len(closes) - atr(bars)
check("dalton short: signal carries target == lower value edge",
      sig is not None and abs(sig.get("target", 0) - lo_edge) < 0.01,
      f"target={sig.get('target') if sig else None} lo={lo_edge:.2f}")
if sig is not None:
    tp, tpr = resolve_target(sig, "day", CFG_TP)
    check("dalton short: resolved target == checked target (same number)",
          tp == sig["target"], f"got ({tp}, {tpr})")
# Dalton LONG: last bar pokes below the lower edge (97.7) and closes 98.1.
bars = bars_from_closes([100.0] * 21)
for b in bars:
    b["h"], b["l"] = 101.0, 99.0
bars[-1] = {"t": bars[-1]["t"], "o": 98.1, "h": 99.5, "l": 97.7, "c": 98.1, "v": 1000.0}
sig = dalton_value(bars)
closes = [b["c"] for b in bars]
up_edge = sum(closes) / len(closes) + atr(bars)
check("dalton long: signal carries target == upper value edge",
      sig is not None and abs(sig.get("target", 0) - up_edge) < 0.01,
      f"target={sig.get('target') if sig else None} up={up_edge:.2f}")
if sig is not None:
    tp, tpr = resolve_target(sig, "day", CFG_TP)
    check("dalton long: resolved target == checked target (same number)",
          tp == sig["target"], f"got ({tp}, {tpr})")

# ------------------------------------------------ 4. Stop-replace failure path
def _stop_test_broker():
    return FakeBroker(
        positions={"TEST": SimpleNamespace(symbol="TEST", side="long", qty=10,
                                          current_price=103.0, market_value=1030.0)},
        open_orders=[SimpleNamespace(symbol="TEST", order_type="stop",
                                     id="old-stop-1", stop_price=98.0)])

# (a) Replace fails, cancel ok, standalone stop succeeds -> re-established.
br = _stop_test_broker()
br.replace_ok = False
state = default_state()
state["positions"]["TEST"] = _pos("TEST", 1, 10, 100.0, 2.0, 98.0)
notes = []
actions = manage_positions(_mgmt_cfg(), br, state, False, _noon_et(), "15:50", notes)
check("stop fail (a): standalone stop attempted after replace failure",
      len(br.stops) >= 1, f"stops={br.stops}")
check("stop fail (a): stop re-established and verified on the broker",
      state["positions"]["TEST"]["stop_price"] == 102.0
      and state.get("unprotected_positions", {}) == {},
      f"stop={state['positions']['TEST'].get('stop_price')} unprotected={state.get('unprotected_positions')}")
check("stop fail (a): actions report the re-established stop",
      any("re-established" in a for a in actions), f"actions={actions}")

# (b) Replace fails AND standalone stop fails -> unprotected, recorded.
br = _stop_test_broker()
br.replace_ok = False
br.submit_stop_ok = False
state = default_state()
state["positions"]["TEST"] = _pos("TEST", 1, 10, 100.0, 2.0, 98.0)
actions = manage_positions(_mgmt_cfg(), br, state, False, _noon_et(), "15:50", [])
check("stop fail (b): standalone stop attempted even though it fails",
      len(br.stops) >= 1, f"stops={br.stops}")
check("stop fail (b): position recorded in state.unprotected_positions",
      "TEST" in state.get("unprotected_positions", {}),
      f"unprotected={state.get('unprotected_positions')}")
check("stop fail (b): actions report UNPROTECTED loudly",
      any(a.startswith("UNPROTECTED: TEST") for a in actions), f"actions={actions}")

# (c) Control: replace succeeds -> verified, no standalone stop, not unprotected.
br = _stop_test_broker()
state = default_state()
state["positions"]["TEST"] = _pos("TEST", 1, 10, 100.0, 2.0, 98.0)
actions = manage_positions(_mgmt_cfg(), br, state, False, _noon_et(), "15:50", [])
check("stop ok (c): replace succeeds, no standalone stop needed",
      len(br.stops) == 0 and len(br.replaced) >= 1,
      f"stops={len(br.stops)} replaced={len(br.replaced)}")
check("stop ok (c): not unprotected",
      state.get("unprotected_positions", {}) == {},
      f"unprotected={state.get('unprotected_positions')}")

# ------------------------------------------------ 4. run_once blocks new trades while unprotected
orig_fetch = bot.fetch_bars
try:
    bot.fetch_bars = lambda symbol, timeframe, broker, use_real, cfg: (soup_long_bars(), "FAKE")
    # Control: same setup, NO unprotected flag -> the soup signal trades.
    br = FakeBroker(
        positions={"HELD": SimpleNamespace(symbol="HELD", side="long", qty=1,
                                           current_price=100.0, market_value=100.0)},
        open_orders=[])
    state = default_state()
    state["positions"]["HELD"] = _pos("HELD", 1, 1, 100.0, 0.0, 0.0, sleeve="unknown")
    rep = run_once(_test_cfg(), br, dry_run=False, use_real=True, equity=100000.0,
                   state=state, now_et=_noon_et(), flatten_et="15:50")
    check("unprotected block (control): soup signal places an order when protected",
          len(br.submitted) == 1 and br.submitted[0]["symbol"] == "TEST",
          f"submitted={br.submitted}")
    # Blocked: HELD is unprotected (no stop on the broker) -> no new trades.
    br = FakeBroker(
        positions={"HELD": SimpleNamespace(symbol="HELD", side="long", qty=1,
                                           current_price=100.0, market_value=100.0)},
        open_orders=[])
    state = default_state()
    state["positions"]["HELD"] = _pos("HELD", 1, 1, 100.0, 0.0, 0.0, sleeve="unknown")
    state["unprotected_positions"] = {"HELD": {"reason": "stop move failed",
                                               "since": _noon_et().isoformat(),
                                               "last_stop_price": 98.0}}
    rep = run_once(_test_cfg(), br, dry_run=False, use_real=True, equity=100000.0,
                   state=state, now_et=_noon_et(), flatten_et="15:50")
    check("unprotected block: no new order while a position is unprotected",
          len(br.submitted) == 0, f"submitted={br.submitted}")
    check("unprotected block: skip reason says unprotected",
          any("unprotected" in s[3] for s in rep["skipped"]),
          f"skipped={rep['skipped']}")
    check("unprotected block: flag survives the run (still recorded)",
          "HELD" in state.get("unprotected_positions", {}))
finally:
    bot.fetch_bars = orig_fetch

# ------------------------------------------------ 5. Adopt ALL broker positions
# (a) Unknown broker position WITH an open stop (the MSFT case).
br = FakeBroker(open_orders=[SimpleNamespace(symbol="MSFT", order_type="stop",
                                            id="9a5b7fb5-ada0-4c34-b143-43411cca822b",
                                            stop_price=539.83)])
state = default_state()
bp = {"MSFT": SimpleNamespace(symbol="MSFT", side="short", qty=17,
                              avg_entry_price=537.05, market_value=-9129.85,
                              current_price=530.0)}
notes = []
adopt_broker_positions(state, bp, br, br.open_orders(), notes)
pos = state["positions"].get("MSFT", {})
check("adopt unknown: MSFT adopted from the broker (entry $537.05)",
      pos.get("entry") == 537.05 and pos.get("qty") == 17 and pos.get("side") == -1,
      f"pos={pos}")
check("adopt unknown: PROTECTED + monitor-only, one_r from the open stop",
      pos.get("protected") is True and abs(pos.get("one_r", 0) - 2.78) < 0.01
      and pos.get("stop_price") == 539.83
      and pos.get("stop_order_id") == "9a5b7fb5-ada0-4c34-b143-43411cca822b",
      f"pos={pos}")
# (b) Muse1's own position: recovered from the entry order's legs.
leg_stop = SimpleNamespace(order_type="stop", id="leg-stop-1", stop_price=539.83)
leg_tp = SimpleNamespace(order_type="limit", id="leg-tp-1", limit_price=530.0)
entry_order = SimpleNamespace(client_order_id="muse1-2026-11-05-MSFT-day-abc123",
                              symbol="MSFT", status="FILLED", filled_qty=17,
                              filled_avg_price=537.05, id="entry-1",
                              legs=[leg_stop, leg_tp])
br = FakeBroker()
br.closed_list = [entry_order]
state = default_state()
adopt_broker_positions(state, bp, br, [], notes)
pos = state["positions"].get("MSFT", {})
check("adopt muse1: entry + stop/target recovered from the order legs",
      pos.get("entry") == 537.05 and pos.get("stop_order_id") == "leg-stop-1"
      and pos.get("target_order_id") == "leg-tp-1"
      and abs(pos.get("one_r", 0) - 2.78) < 0.01,
      f"pos={pos}")
check("adopt muse1: own position is NOT marked protected",
      pos.get("protected") is not True, f"pos={pos}")
# (c) Unknown broker position with NO open stop -> adopted, one_r 0.
br = FakeBroker()
state = default_state()
adopt_broker_positions(state, bp, br, [], notes)
pos = state["positions"].get("MSFT", {})
check("adopt unknown no-stop: adopted as protected with one_r 0 (management skipped)",
      pos.get("protected") is True and pos.get("one_r") == 0.0,
      f"pos={pos}")

# (d) run_once: state empty + broker holding TEST -> no new order on TEST.
try:
    bot.fetch_bars = lambda symbol, timeframe, broker, use_real, cfg: (soup_long_bars(), "FAKE")
    br = FakeBroker(
        positions={"TEST": SimpleNamespace(symbol="TEST", side="long", qty=5,
                                           current_price=100.0, market_value=500.0)},
        open_orders=[])
    # avg_entry_price missing -> adoption cannot price it, but the
    # duplicate check must still see the broker position.
    state = default_state()
    rep = run_once(_test_cfg(), br, dry_run=False, use_real=True, equity=100000.0,
                   state=state, now_et=_noon_et(), flatten_et="15:50")
    check("duplicate (broker): state empty + broker holding TEST -> no new order",
          len(br.submitted) == 0, f"submitted={br.submitted}")
    check("duplicate (broker): skip reason names the broker position",
          any("broker already holds" in s[3] for s in rep["skipped"]),
          f"skipped={rep['skipped']}")
finally:
    bot.fetch_bars = orig_fetch

# ------------------------------------------------ 1 (end-to-end). run_once dry-run, no sleeve TP -> OTO would-order
try:
    bot.fetch_bars = lambda symbol, timeframe, broker, use_real, cfg: (soup_long_bars(), "FAKE")
    stub = PaperBrokerStub(100000.0)
    state = default_state()
    rep = run_once(_test_cfg(no_sleeve_tp=True), stub, dry_run=True, use_real=False,
                   equity=100000.0, state=state, now_et=_noon_et(), flatten_et="15:50")
    check("e2e no-target: dry-run would-order is entry+stop only (no TP leg)",
          len(stub.would_orders) == 1
          and stub.would_orders[0]["target_price"] is None
          and "OTOSTOP" in stub.would_orders[0]["status"],
          f"would_orders={stub.would_orders}")
    check("e2e no-target: signal tp_r is None (runs on stop/trail only)",
          len(rep["signals"]) == 1 and rep["signals"][0]["tp_r"] is None,
          f"signals={rep['signals']}")
    # And with the sleeve TP present, the same signal gets the 2.5R target.
    stub = PaperBrokerStub(100000.0)
    state = default_state()
    rep = run_once(_test_cfg(), stub, dry_run=True, use_real=False,
                   equity=100000.0, state=state, now_et=_noon_et(), flatten_et="15:50")
    check("e2e sleeve TP: same soup signal gets the 2.5R day target",
          len(stub.would_orders) == 1
          and abs(stub.would_orders[0]["target_price"] - (100.5 + 2.5 * 3.5)) < 0.01,
          f"would_orders={stub.would_orders}")
finally:
    bot.fetch_bars = orig_fetch

# ------------------------------------------------ 7a. Flatten at 15:50 ET
import yaml
cfg = yaml.safe_load((HERE / "config.yaml").read_text())
check("flatten config: day flatten_et is 15:50",
      str(cfg["sleeves"]["day"].get("flatten_et")) == "15:50",
      f"got {cfg['sleeves']['day'].get('flatten_et')}")
deploy = (HERE / "DEPLOY.md").read_text()
check("flatten deploy: cron example runs every 5 min through hour 15 (covers 15:50)",
      "*/5 10-15 * * 1-5" in deploy, "cron line missing or narrowed")
check("flatten deploy: doc states the 15:50 run is the flatten run",
      "15:50" in deploy and "flatten run" in deploy)

br = FakeBroker(
    positions={"TEST": SimpleNamespace(symbol="TEST", side="long", qty=10,
                                       current_price=100.5, market_value=1005.0)},
    open_orders=[])
state = default_state()
t1550 = datetime.now(ET).replace(hour=15, minute=50, second=0, microsecond=0)
# Anchor the entry to the SIMULATED run time, not real now: entry_time is
# used for bars_held / signal decay, and a real-now entry makes the test
# time-of-day dependent (it failed in the morning, when 15:50 is hours
# after a real-now entry and decay fires before the flatten check).
state["positions"]["TEST"] = _pos("TEST", 1, 10, 100.0, 2.0, 98.0, minutes_ago=5, now_et=t1550)
actions = manage_positions(_mgmt_cfg(), br, state, False, t1550, "15:50", [])
check("flatten 15:50: day position closed at the 15:50 run",
      len(br.closed) == 1 and br.closed[0][0] == "TEST",
      f"closed={br.closed} actions={actions}")
check("flatten 15:50: action names the flatten",
      any("flatten 15:50 ET" in a for a in actions), f"actions={actions}")

br = FakeBroker(
    positions={"TEST": SimpleNamespace(symbol="TEST", side="long", qty=10,
                                       current_price=100.5, market_value=1005.0)},
    open_orders=[])
state = default_state()
t1549 = datetime.now(ET).replace(hour=15, minute=49, second=0, microsecond=0)
# Same anchoring as the 15:50 test above: without it, a morning run
# computes ~300 min held and signal decay closes the position before
# the flatten check ever runs.
state["positions"]["TEST"] = _pos("TEST", 1, 10, 100.0, 2.0, 98.0, minutes_ago=5, now_et=t1549)
actions = manage_positions(_mgmt_cfg(), br, state, False, t1549, "15:50", [])
check("flatten 15:49: day position NOT closed before 15:50",
      len(br.closed) == 0, f"closed={br.closed}")

# ------------------------------------------------ SPY swing filter (v0.3.1)
# Bug 2026-11-06: swing uses daily bars (90 days fetched) and spy_side /
# spy_flat_pct compared bars[0]["o"] — the open ~90 days ago — to the last
# close, so swing read short_only while SPY was up. The fix: DAILY uses
# the last bar's own open vs close; 5-MIN still uses today's open
# (bars[0]["o"], _today_bars cuts to today) vs the latest close. The
# first version of the fix wrongly used bars[-1]["o"] for 5-min too and
# measured only the last 5 minutes — caught by the monitor 2026-11-06
# before any push; the regression tests below pin both paths.
# Synthetic bars only, no live data.
def _daily_bars(first_open, last_open, last_close):
    now = datetime.now(timezone.utc)
    rows = []
    for i in range(90):
        if i == 0:
            o = c = first_open
        elif i == 89:
            o, c = last_open, last_close
        else:
            o = c = 735.0
        rows.append({"t": now - timedelta(days=90 - i),
                     "o": o, "h": max(o, c) + 0.5, "l": min(o, c) - 0.5,
                     "c": c, "v": 1000.0})
    return rows

up = _daily_bars(750.00, 730.00, 732.78)
check("swing spy_side daily: last bar UP reads long_only (+1)",
      spy_side(up, "vs_open", "daily") == 1,
      f"got {spy_side(up, 'vs_open', 'daily')}")
fpct_up = spy_flat_pct(up, "daily")
check("swing spy_flat_pct daily: uses last bar's open (~0.381%, not 2.296%)",
      fpct_up is not None and abs(fpct_up - 0.381) < 0.01,
      f"got {fpct_up}")
down = _daily_bars(750.00, 740.00, 732.78)
check("swing spy_side daily: last bar DOWN reads short_only (-1)",
      spy_side(down, "vs_open", "daily") == -1,
      f"got {spy_side(down, 'vs_open', 'daily')}")
check("swing spy_side daily: down day is not flat",
      (spy_flat_pct(down, "daily") or 0) > 0.15,
      f"got {spy_flat_pct(down, 'daily')}")

five_min_up = bars_ohlc([(730.0, 731.0, 729.0, 730.5),
                         (730.5, 732.0, 730.0, 731.5),
                         (731.5, 733.0, 731.0, 732.78)])
check("spy_side 5min: up day still reads long_only (+1) after the fix",
      spy_side(five_min_up, "vs_open", "5min") == 1,
      f"got {spy_side(five_min_up, 'vs_open', '5min')}")
fpct_5 = spy_flat_pct(five_min_up, "5min")
check("spy_flat_pct 5min: ~0.381% day move",
      fpct_5 is not None and abs(fpct_5 - 0.381) < 0.01,
      f"got {fpct_5}")
# Regression (caught by the 10:42 ET monitor check 2026-11-06): the
# FIRST version of the v0.3.1 fix used bars[-1]["o"] for 5-min too,
# measuring only the last 5 minutes — it reported FLAT 0.096% /
# range_only while SPY was +0.48% on the day. Day up ~0.48%
# (730.00 -> 733.50) with the last 5-min bar nearly flat
# (733.40 -> 733.50) must still read +1 with flat_pct ~0.48% (the
# DAY's move), not ~0.01% (the last bar's move).
five_min_regr = bars_ohlc([(730.0, 731.0, 729.0, 730.5),
                           (730.5, 732.0, 730.0, 731.5),
                           (731.5, 733.0, 731.0, 732.0),
                           (732.0, 733.6, 731.8, 733.4),
                           (733.4, 733.6, 733.3, 733.5)])
check("spy_side 5min regression: up day reads +1 even when the last bar is flat",
      spy_side(five_min_regr, "vs_open", "5min") == 1,
      f"got {spy_side(five_min_regr, 'vs_open', '5min')}")
fpct_regr = spy_flat_pct(five_min_regr, "5min")
check("spy_flat_pct 5min regression: ~0.48% day move, not ~0.01% last-bar move",
      fpct_regr is not None and abs(fpct_regr - 0.479) < 0.01,
      f"got {fpct_regr}")
five_min_down = bars_ohlc([(740.0, 740.5, 739.0, 739.5),
                           (739.5, 740.0, 738.5, 739.0),
                           (739.0, 739.5, 738.0, 738.5)])
check("spy_side 5min: down day reads short_only (-1)",
      spy_side(five_min_down, "vs_open", "5min") == -1,
      f"got {spy_side(five_min_down, 'vs_open', '5min')}")

# v0.3.2: env base URL + APCA_ key aliases.
# The Monday deploy note stores APCA_API_KEY_ID / APCA_API_SECRET_KEY /
# APCA_API_BASE_URL on the VM. The code must actually read them: the
# alpaca-py SDK reads NONE of those env vars itself, and the old code
# passed paper=True only, so the env base URL was silently ignored and
# the APCA_ key names were silently ignored too.
_KEY_VARS = ("ALPACA_API_KEY", "ALPACA_API_SECRET", "ALPACA_SECRET_KEY",
             "APCA_API_KEY_ID", "APCA_API_SECRET_KEY", "APCA_API_BASE_URL")

def _with_env(pairs, fn):
    saved = {k: os.environ.get(k) for k in _KEY_VARS}
    try:
        for k in _KEY_VARS:
            os.environ.pop(k, None)
        os.environ.update(pairs)
        return fn()
    finally:
        for k in _KEY_VARS:
            os.environ.pop(k, None)
        for k, v in saved.items():
            if v is not None:
                os.environ[k] = v

check("keys: APCA_API_KEY_ID / APCA_API_SECRET_KEY are accepted",
      _with_env({"APCA_API_KEY_ID": "AKID", "APCA_API_SECRET_KEY": "SEC"},
                lambda: alpaca_keys_from_env() == ("AKID", "SEC")),
      f"got {alpaca_keys_from_env()}")
check("keys: ALPACA_API_* names take precedence over APCA_*",
      _with_env({"ALPACA_API_KEY": "K1", "ALPACA_API_SECRET": "S1",
                 "APCA_API_KEY_ID": "K2", "APCA_API_SECRET_KEY": "S2"},
                lambda: alpaca_keys_from_env() == ("K1", "S1")),
      f"got {alpaca_keys_from_env()}")
check("keys: nothing set -> (None, None)",
      _with_env({}, lambda: alpaca_keys_from_env() == (None, None)),
      f"got {alpaca_keys_from_env()}")

check("base URL: unset -> None (SDK defaults to paper)",
      _with_env({}, lambda: paper_base_url_from_env() is None),
      f"got {paper_base_url_from_env()}")
check("base URL: paper URL is returned",
      _with_env({"APCA_API_BASE_URL": "https://paper-api.alpaca.markets"},
                lambda: paper_base_url_from_env() == "https://paper-api.alpaca.markets"),
      f"got {paper_base_url_from_env()}")

def _live_url_refused():
    try:
        paper_base_url_from_env()
    except SystemExit:
        return True
    return False

check("base URL: live URL https://api.alpaca.markets is refused",
      _with_env({"APCA_API_BASE_URL": "https://api.alpaca.markets"},
                _live_url_refused))
check("base URL: any other host is refused",
      _with_env({"APCA_API_BASE_URL": "https://evil.example.com"},
                _live_url_refused))

def _broker_base_url():
    b = AlpacaBroker("DUMMYKEY", "DUMMYSECRET")
    u = getattr(b.client, "_base_url", "")
    # Unset env -> the SDK stores a BaseURL enum, not a plain string.
    return getattr(u, "value", str(u))

check("broker: paper URL env -> client points at paper",
      _with_env({"APCA_API_BASE_URL": "https://paper-api.alpaca.markets"},
                lambda: "paper-api.alpaca.markets" in _broker_base_url()),
      f"got {_broker_base_url()}")
check("broker: no env -> client still points at paper",
      _with_env({}, lambda: "paper-api.alpaca.markets" in _broker_base_url()),
      f"got {_broker_base_url()}")

def _broker_live_refused():
    try:
        AlpacaBroker("DUMMYKEY", "DUMMYSECRET")
    except SystemExit:
        return True
    return False

check("broker: live URL env -> construction refused",
      _with_env({"APCA_API_BASE_URL": "https://api.alpaca.markets"},
                _broker_live_refused))

print()
print(f"{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", ", ".join(FAIL))
    sys.exit(1)
print("ALL TESTS PASSED")
