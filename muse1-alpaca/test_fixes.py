#!/usr/bin/env python3
"""Unit tests for the 2026-11-05 Claude fix list (muse1/alpaca v0.2.0).

Run:  python3 test_fixes.py
No keys, no network, no orders. Each test prints PASS/FAIL; the script
exits non-zero if any test fails.
"""
import json
import os
import subprocess
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import experts
from experts import (crabel_idnr4, ponsi_trend, bollinger_squeeze,
                     brooks_range_fade, dalton_value, ema, atr)
import bot
from bot import (virtual_stop_levels, virtual_exit_check, load_state,
                 save_state, rollover_state, default_state, run_lock,
                 spy_side, spy_flat_pct, in_et_window)
from hedge_fund_overlay import check_portfolio_limits
from risk import RiskState

PASS, FAIL = [], []

def check(name, cond, detail=""):
    (PASS if cond else FAIL).append(name)
    print(f"{'PASS' if cond else 'FAIL'}  {name}" + (f" — {detail}" if detail and not cond else ""))

def bars_from_closes(closes, vol=1000.0):
    """Bars with a given close series; h/l bracket the close by 0.5."""
    out = []
    now = datetime.now(timezone.utc)
    for i, c in enumerate(closes):
        out.append({"t": now - timedelta(minutes=5 * (len(closes) - i)),
                    "o": c, "h": c + 0.5, "l": c - 0.5, "c": c, "v": vol})
    return out

def bars_ohlc(rows):
    """rows: (o, h, l, c). Timestamps 5 min apart, ending now."""
    out = []
    now = datetime.now(timezone.utc)
    for i, (o, h, l, c) in enumerate(rows):
        out.append({"t": now - timedelta(minutes=5 * (len(rows) - i)),
                    "o": o, "h": h, "l": l, "c": c, "v": 1000.0})
    return out

# ------------------------------------------------ 1. Crabel: setup alone is not a trade
# Bars 0-3 range 4; bar 4 range 2; bar 5 (setup) narrow inside day range 0.6.
# 8 leading bars are padding: atr() needs >= 15 bars (14 TRs) to return a
# value, as in production where hundreds of bars are fetched.
pad_rows = [(100, 102, 98, 100.0)] * 8
setup_rows = pad_rows + [(100, 102, 98, 100.0), (100, 102, 98, 100.0),
              (100, 102, 98, 100.0), (100, 102, 98, 100.0),
              (100, 101, 99, 100.0),
              (100, 100.3, 99.7, 100.0)]  # narrow + inside, bars[-2]
# Current bar closes INSIDE the narrow bar's range -> no breakout, no signal.
rows = setup_rows + [(100.0, 100.2, 99.8, 100.1)]
check("crabel: setup bar alone (no breakout close) -> no signal",
      crabel_idnr4(bars_ohlc(rows)) is None)
# Breakout bar closes above the narrow bar's high -> long.
rows2 = setup_rows + [(100.1, 101.0, 100.0, 100.8)]
sig = crabel_idnr4(bars_ohlc(rows2))
check("crabel: breakout close above narrow high -> long",
      sig is not None and sig["side"] == 1 and sig["stop"] == 99.7,
      f"got {sig}")
# Breakout bar closes below the narrow bar's low -> short (old code never shorted).
rows3 = setup_rows + [(99.9, 100.0, 99.0, 99.2)]
sig = crabel_idnr4(bars_ohlc(rows3))
check("crabel: breakout close below narrow low -> short",
      sig is not None and sig["side"] == -1 and sig["stop"] == 100.3,
      f"got {sig}")

# ------------------------------------------------ 2. Ponsi EMA timing
# Uptrend, then a bar whose close (101) is ABOVE the EMA10 of its own
# bar (~100.1) — no pullback happened — followed by a jump to 110.
# The OLD code (today's EMA10 ~101.8) would fire; the fix must not.
closes = [100.0] * 50 + [101.0, 110.0]
check("ponsi: no pullback (prior close above prior EMA10) -> no signal",
      ponsi_trend(bars_from_closes(closes)) is None)
# Real pullback: price dips to the EMA10, then closes back above it.
closes = [100.0] * 50 + [99.0, 101.0]
sig = ponsi_trend(bars_from_closes(closes))
check("ponsi: genuine pullback to EMA10 then back -> long",
      sig is not None and sig["side"] == 1, f"got {sig}")

# ------------------------------------------------ 3. Bollinger squeeze (same-window bandwidth)
# Shrinking-amplitude closes (squeeze forms), then a close above the band.
closes = [100.0 + (0.5 - i * 0.015) * (1 if i % 2 == 0 else -1) for i in range(30)]
closes += [100.0, 102.5]
sig = bollinger_squeeze(bars_from_closes(closes))
check("bollinger: squeeze + expansion close -> signal",
      sig is not None and sig["side"] == 1, f"got {sig}")
# Growing-amplitude closes (bandwidth NOT at its low) then a big close
# above the band -> no signal.
closes = [100.0 + (0.1 + i * 0.06) * (1 if i % 2 == 0 else -1) for i in range(30)]
closes += [100.0, 108.0]
check("bollinger: no squeeze (bandwidth rising, not at low) -> no signal",
      bollinger_squeeze(bars_from_closes(closes)) is None)

# ------------------------------------------------ 4. Brooks / Dalton min reward:risk (1.5)
# Brooks short: bars 0-5 set the 20-bar range 90-110 (outside the last-14
# TR window, so ATR stays ~2.6); last bar pokes to 111 and closes 108.5.
# stop 113.64, risk 5.14, reward to mid 8.5 -> R:R 1.65 -> signal.
bars = bars_from_closes([100.0] * 21)
for b in bars[:6]:
    b["h"], b["l"] = 110.0, 90.0
for b in bars[6:20]:
    b["h"], b["l"] = 101.0, 99.0
bars[-1] = {"t": bars[-1]["t"], "o": 108.5, "h": 111.0, "l": 107.0, "c": 108.5, "v": 1000.0}
sig = brooks_range_fade(bars)
check("brooks: R:R 1.65 >= 1.5 -> signal",
      sig is not None and sig["side"] == -1, f"got {sig}")
# Same setup but close at 104: risk 9.64, reward 4 -> R:R 0.41 -> no signal.
bars[-1] = {"t": bars[-1]["t"], "o": 104.0, "h": 111.0, "l": 103.0, "c": 104.0, "v": 1000.0}
check("brooks: R:R 0.41 < 1.5 -> no signal",
      brooks_range_fade(bars) is None)
# Dalton short: mean 100, ATR ~2.1, value edges ~102.1/97.9. Last bar
# pokes to 103, closes 100.5: stop 105.11, risk 4.61, reward 2.61
# -> R:R 0.57 < 1.5 -> no signal.
bars = bars_from_closes([100.0] * 21)
for b in bars:
    b["h"], b["l"] = 101.0, 99.0
bars[-1] = {"t": bars[-1]["t"], "o": 100.5, "h": 103.0, "l": 99.5, "c": 100.5, "v": 1000.0}
check("dalton: R:R 0.57 < 1.5 -> no signal",
      dalton_value(bars) is None)
# Dalton with a small poke (102.3) and a close near the edge (101.9):
# stop 104.36, risk 2.46, reward 3.96 -> R:R 1.61 >= 1.5 -> signal.
bars[-1] = {"t": bars[-1]["t"], "o": 101.9, "h": 102.3, "l": 99.5, "c": 101.9, "v": 1000.0}
sig = dalton_value(bars)
check("dalton: R:R 1.61 >= 1.5 -> signal",
      sig is not None and sig["side"] == -1, f"got {sig}")

# ------------------------------------------------ 5. Exposure counts the NEW order
open_pos = [{"symbol": "AAPL", "sector": "tech", "side": 1, "notional": 40000.0}]
# Existing net 40% — under the 60% cap. A new $25k long would take it to 65%.
ok, reason = check_portfolio_limits(open_pos, "MSFT", "tech", 1, {"MSFT": "tech"},
                                    max_open=5, max_per_sector=2,
                                    max_net_exposure_pct=0.60, equity=100000.0,
                                    new_notional=25000.0)
check("exposure: new long taking net to 65% -> blocked",
      ok is False, reason)
# A new $25k SHORT takes net to 15% -> allowed.
ok, reason = check_portfolio_limits(open_pos, "MSFT", "tech", -1, {"MSFT": "tech"},
                                    max_open=5, max_per_sector=2,
                                    max_net_exposure_pct=0.60, equity=100000.0,
                                    new_notional=25000.0)
check("exposure: new short taking net to 15% -> allowed",
      ok is True, reason)

# ------------------------------------------------ 6. Virtual trail / stop math (unchanged)
hard, trail = virtual_stop_levels(537.05, 2.78, -1, 533.05)
check("virtual: MSFT short peak 533.05 -> hard 539.83, trail 534.44",
      abs(hard - 539.83) < 0.01 and abs(trail - 534.44) < 0.01,
      f"hard={hard} trail={trail}")
should, reason, _, _ = virtual_exit_check(537.05, 2.78, -1, 534.41, 533.05)
check("virtual: MSFT at 534.41 below trail 534.44 -> hold",
      should is False, reason)
should, reason, _, _ = virtual_exit_check(537.05, 2.78, -1, 534.50, 533.05)
check("virtual: MSFT at 534.50 above trail 534.44 -> exit",
      should is True, reason)

# ------------------------------------------------ 7. Lock: second holder is refused
results = []
def try_lock():
    with run_lock() as fd:
        results.append(fd is not None)
with run_lock() as fd:
    assert fd is not None
    t = threading.Thread(target=try_lock)
    t.start(); t.join()
check("lock: overlapping run is refused while lock is held",
      results == [False], f"got {results}")
with run_lock() as fd:
    check("lock: free after release", fd is not None)

# ------------------------------------------------ 8. State save/load/rollover
# Back up the real state.json first — the test must not wipe it.
_state_backup = None
if bot.STATE_FILE.exists():
    _state_backup = bot.STATE_FILE.read_text()
st = default_state()
st["day_pnl_dollars"] = -81.67
st["trades_today"] = 2
st["positions"]["MSFT"] = {"symbol": "MSFT", "side": -1, "qty": 17}
save_state(st)
st2, corrupt = load_state()
check("state: save/load round-trips",
      not corrupt and st2["day_pnl_dollars"] == -81.67 and st2["trades_today"] == 2
      and "MSFT" in st2["positions"])
# Rollover keeps positions, resets the day counters.
st2["day"] = "2026-11-04"
st2 = rollover_state(st2)
check("state: rollover keeps positions, resets day counters",
      "MSFT" in st2["positions"] and st2["day_pnl_dollars"] == 0.0
      and st2["trades_today"] == 0)
# Restore the real state file byte-for-byte.
if _state_backup is not None:
    bot.STATE_FILE.write_text(_state_backup)
    check("state: real state.json restored",
          load_state()[0]["positions"].get("MSFT", {}).get("qty") == 17)
else:
    save_state(default_state())
    check("state: no real state existed, clean default restored",
          load_state()[0]["positions"] == {})

# ------------------------------------------------ 9. SPY filter with timestamps
# Today's session: open 765.47, closes falling -> short only.
now = datetime.now(timezone.utc)
spy = [{"t": now - timedelta(minutes=5 * (6 - i)), "o": 765.47, "h": 766.0,
        "l": 730.0, "c": 765.47 - i * 5.0, "v": 1000.0} for i in range(6)]
check("spy: down vs today's open -> short only",
      spy_side(spy, "vs_open", "5min") == -1)
check("spy: flat pct ~3.27% (not flat)",
      spy_flat_pct(spy, "5min") is not None and abs(spy_flat_pct(spy, "5min") - 3.27) < 0.2)

# ------------------------------------------------ 10. Window helper
from zoneinfo import ZoneInfo
ET = ZoneInfo("America/New_York")
check("window: 12:57 ET inside 10:00-15:45",
      in_et_window("10:00-15:45", datetime(2026, 11, 5, 12, 57, tzinfo=ET)) is True)
check("window: 09:30 ET outside",
      in_et_window("10:00-15:45", datetime(2026, 11, 5, 9, 30, tzinfo=ET)) is False)
check("window: 16:00 ET outside",
      in_et_window("10:00-15:45", datetime(2026, 11, 5, 16, 0, tzinfo=ET)) is False)

# ------------------------------------------------ 11. CLI: --help, no-flag, --live
r = subprocess.run([sys.executable, "bot.py", "--help"], capture_output=True, text=True)
check("cli: --help exits 0", r.returncode == 0 and "--paper" in r.stdout)
r = subprocess.run([sys.executable, "bot.py"], capture_output=True, text=True)
check("cli: no flag does nothing, exit 0",
      r.returncode == 0 and "doing nothing" in r.stdout, r.stdout[:200])
r = subprocess.run([sys.executable, "bot.py", "--live"], capture_output=True, text=True)
check("cli: --live refuses, exit 2",
      r.returncode == 2 and "BLOCKED" in (r.stdout + r.stderr))

# ------------------------------------------------ 12. RiskState seeding
rs = RiskState(equity=50000.0, daily_loss_cap_pct=0.02)
rs.seed(-81.67, 2, 1, 0)
check("risk: seed restores day P&L and open counts",
      rs.day_pnl_dollars == -81.67 and rs.trades_today == 2 and rs.open_day_scalp == 1)
rs.seed(-1500.0, 2, 1, 0)
check("risk: cap hit at -$1500 on $50k (2% = $1000)",
      rs.cap_hit() is True)
rs.seed(-81.67, 2, 1, 0, day_pnl_r=-1.31)
check("risk: seed restores day P&L in R too",
      rs.day_pnl_r == -1.31)

# ------------------------------------------------ 13. Market calendar (Alpaca first, static fallback)
class _FakeCalBroker:
    def __init__(self, days):
        self._days = days  # list of objects with .close (a time) or []
    def get_calendar(self, start, end):
        return self._days

from datetime import time as _time
class _Day:
    def __init__(self, close):
        self.close = close
from bot import market_status
check("calendar: Alpaca open day -> open",
      market_status(_FakeCalBroker([_Day(_time(16, 0))]), datetime(2026, 11, 5).date())[0] == "open")
check("calendar: Alpaca 13:00 close -> half_day",
      market_status(_FakeCalBroker([_Day(_time(13, 0))]), datetime(2026, 11, 27).date())[0] == "half_day")
check("calendar: Alpaca empty list -> holiday",
      market_status(_FakeCalBroker([]), datetime(2026, 11, 26).date())[0] == "holiday")
class _FailingCalBroker:
    def get_calendar(self, start, end):
        raise RuntimeError("API down")
check("calendar: Alpaca failure -> static fallback still answers",
      market_status(_FailingCalBroker(), datetime(2026, 11, 5).date())[2] == "static-fallback"
      and market_status(_FailingCalBroker(), datetime(2026, 11, 5).date())[0] == "open")
check("calendar: static fallback knows Thanksgiving",
      market_status(_FailingCalBroker(), datetime(2026, 11, 26).date())[0] == "holiday")

print()
print(f"{len(PASS)} passed, {len(FAIL)} failed")
if FAIL:
    print("FAILED:", FAIL)
    sys.exit(1)
print("ALL TESTS PASSED")
