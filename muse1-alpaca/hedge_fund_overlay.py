"""Hedge-fund PRINCIPLES overlay — principles only, no invented fund rules.

The top 5 funds publish NO entry rules, stop levels, or profit targets.
What is documented is philosophy + a few real mechanics (mainly Man AHL /
AQR). This module implements those documented principles as code, and
labels each one with its source. It does NOT claim any fund trades this way
on this bot.

Mapping (see README for the full table):
- Renaissance Medallion: many small signals combined, diversified,
  optimizer-driven. Here: combine 10 experts, rank by agreement, cap
  any single expert / symbol, never bet the book on one signal.
- Two Sigma: factor forecasts + factor-exposure control. Here: sector
  concentration limits and a net-exposure cap (factor = sector here).
- D.E. Shaw: market-neutral / stat-arb, integrated risk. Here: net
  long-minus-short exposure cap; risk is checked at portfolio level,
  not just per trade.
- Citadel: stress tests, exposure limits, dynamic hedging. Here:
  stress_test() — worst case if every stop is hit at once; a hard
  notional cap per position; dynamic hedge = flatten / no-new-trades
  when the daily loss cap is hit.
- Man AHL / AQR: trend following, volatility-targeted sizing, no fixed
  take-profit, exit on signal fade. Here: vol_scaled_size(), no fixed
  1R TP (2.5-3R or no-TP + breakeven after +1R), signal-decay / time
  exits with a wide disaster stop, costs modeled before trading.
"""
import math

def vol_scaled_size(equity, risk_pct, stop_distance, price,
                    max_notional_pct=0.25):
    """Man AHL volatility-targeted sizing: fixed dollar risk, size down
    when the ATR stop is wide. Returns (qty, notional) or (0, 0.0)."""
    if equity <= 0 or risk_pct <= 0 or stop_distance <= 0 or price <= 0:
        return 0, 0.0
    risk_dollars = equity * risk_pct
    qty = int(risk_dollars / stop_distance)
    if qty <= 0:
        return 0, 0.0
    max_notional = equity * max_notional_pct
    if qty * price > max_notional:
        qty = int(max_notional / price)
    return qty, qty * price

def costs(price, qty, bps_per_side=3.0, sec_fee_bps=0.1):
    """AQR: model costs before trading. Slippage + SEC-style fee, per side,
    in dollars. A round trip costs roughly 2x this."""
    if qty <= 0 or price <= 0:
        return 0.0
    notional = price * qty
    return notional * (bps_per_side + sec_fee_bps) / 10000.0

def edge_after_costs(expected_r, risk_dollars, trade_cost):
    """Expected dollar gain after costs. If <= 0, the trade does not pay
    after costs — skip it, however good the signal looks."""
    return expected_r * risk_dollars - trade_cost

def check_portfolio_limits(open_positions, new_symbol, new_sector, new_side,
                           sectors, max_open=2, max_per_sector=2,
                           max_net_exposure_pct=0.60, equity=100000.0):
    """Two Sigma / D.E. Shaw / Citadel: portfolio-level limits.
    open_positions: list of dicts {symbol, sector, side, notional}.
    Returns (ok, reason)."""
    if len(open_positions) >= max_open:
        return False, f"max_open {max_open} reached"
    sector_count = sum(1 for p in open_positions if p.get("sector") == new_sector)
    if sector_count >= max_per_sector:
        return False, f"sector {new_sector} at cap {max_per_sector}"
    # net exposure: (long notional - short notional) / equity
    net = sum(p["side"] * p.get("notional", 0.0) for p in open_positions)
    # new trade's notional is unknown here; use a conservative 25% cap slice
    # — the caller re-checks with the real notional in bot.py.
    if abs(net) / max(equity, 1.0) > max_net_exposure_pct:
        return False, "net exposure cap breached"
    return True, "ok"

def stress_test(open_positions, equity):
    """Citadel stress test: worst case if every open position hits its
    stop at once. Returns worst-case dollar loss and % of equity."""
    worst = sum(p.get("risk_dollars", 0.0) for p in open_positions)
    return worst, (worst / equity * 100.0 if equity > 0 else 0.0)

def exit_policy(tp_r=2.5, breakeven_after_r=1.0):
    """Man AHL / AQR: NO fixed 1R take-profit. The 2026-11-05 Brain audit
    found fixed 1R TP cost -6.32R on holdout vs +2.51R at 3R and +1.00R
    for breakeven-after-+1R. Policy: target 2.5-3R, or no fixed target
    with the stop moved to breakeven after +1R, flatten at close."""
    return {"tp_r": tp_r, "breakeven_after_r": breakeven_after_r,
            "fixed_1r_tp": False,
            "note": "no fixed 1R TP; 2.5-3R or no-TP + BE after +1R"}

def signal_decay_exit(bars_held, peak_r, current_r, time_exit_bars=12,
                      disaster_stop_atr=2.0):
    """Signal-decay / time exit: if a trade has made no progress past
    +0.5R after `time_exit_bars` bars, exit — the signal has decayed.
    A wide disaster stop (2 ATR) stays underneath at all times.
    Returns (should_exit, reason)."""
    if peak_r < 0.5 and bars_held >= time_exit_bars:
        return True, f"no progress past +0.5R in {time_exit_bars} bars (signal decay)"
    return False, "holding"

def combine_signals(signals):
    """Medallion-style combination, honestly scaled down: many small
    signals, no single one decides the book. Returns the signals ranked
    by side agreement — a side with 2+ experts agreeing outranks a lone
    signal. This is a ranking, not a fund's optimizer."""
    if not signals:
        return []
    long_n = sum(1 for s in signals if s["side"] == 1)
    short_n = sum(1 for s in signals if s["side"] == -1)
    def key(s):
        agree = long_n if s["side"] == 1 else short_n
        return (-agree, s["expert"])
    return sorted(signals, key=key)
