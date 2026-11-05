#!/usr/bin/env python3
"""
OANDA H4 Volatility Breakout Factorial Harness (Two-Sided Long/Short)
A: 2.0σ Band Breakout | No Filter | 2.0 ATR Trail
B: 2.0σ Band Breakout | 200 SMA Trend Gate | 2.0 ATR Trail
C: 20-bar Donchian Breakout | No Filter | 2.0 ATR Trail
D: 20-bar Donchian Breakout | 200 SMA Trend Gate | 2.0 ATR Trail
"""

import math
from pathlib import Path
from dataclasses import dataclass
import numpy as np
import pandas as pd

DATA_DIR = Path("/home/moonrider1968/bots/oanda_data/h4")

INITIAL_NAV = 100_000.0
RISK_PCT = 0.0050
MAX_POSITIONS = 3

SPREAD_BPS = 1.0
SLIP_BPS = 1.0
DAILY_FINANCING_BPS = 1.5  # 1.5 bps/day (~5.47% annual carry)

LOOKBACK = 20
ATR_LEN = 14
STATIC_SIGMA = 2.0


@dataclass
class TradeRecord:
    variant: str
    symbol: str
    side: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_px: float
    exit_px: float
    risk_usd: float
    pnl_usd: float
    r: float
    outcome: str
    reason: str


def load_h4_clean():
    found = {}
    for p in sorted(DATA_DIR.glob("*.csv")):
        try:
            df = pd.read_csv(p)
        except Exception:
            continue
        df.columns = [str(c).strip().lower() for c in df.columns]
        tc = next((c for c in ["timestamp", "time", "datetime", "date"] if c in df.columns), df.columns[0])
        df["time"] = pd.to_datetime(df[tc], errors="coerce", utc=True)
        needed = ["open", "high", "low", "close"]
        if not all(c in df.columns for c in needed):
            continue
        for c in needed:
            df[c] = pd.to_numeric(df[c], errors="coerce")
        df = df.dropna(subset=["time"] + needed).sort_values("time").drop_duplicates("time").set_index("time")

        deltas = df.index.to_series().diff().dropna().dt.total_seconds() / 3600.0
        if len(deltas) == 0 or abs(deltas.mode().iloc[0] - 4.0) > 0.1:
            continue

        name = p.stem.upper()
        bits = name.replace("-", "_").split("_")
        sym = f"{bits[0]}_{bits[1]}" if len(bits) >= 2 and len(bits[0]) == 3 and len(bits[1]) == 3 else name
        found[sym] = df
    return found


def add_indicators(df):
    d = df.copy()
    d["sma20"] = d["close"].rolling(LOOKBACK).mean()
    d["sma200"] = d["close"].rolling(200).mean()
    d["std20"] = d["close"].rolling(LOOKBACK).std()

    pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(ATR_LEN).mean()

    d["upper_band"] = d["sma20"] + (STATIC_SIGMA * d["std20"])
    d["lower_band"] = d["sma20"] - (STATIC_SIGMA * d["std20"])

    # Donchian 20 Channels based strictly on prior completed bars
    d["donchian_high"] = d["high"].shift(1).rolling(LOOKBACK).max()
    d["donchian_low"] = d["low"].shift(1).rolling(LOOKBACK).min()

    d["sma200_slope5"] = d["sma200"] - d["sma200"].shift(5)
    return d


def execute_price(px, side):
    fr = (SPREAD_BPS + SLIP_BPS) / 10000.0
    return px * (1.0 + fr if side == "BUY" else 1.0 - fr)


def get_usd_conversion_factor(sym, ts, bar_close, data):
    base, quote = sym.split("_")
    if quote == "USD":
        return 1.0, bar_close
    if base == "USD":
        return 1.0 / bar_close, 1.0
    if quote == "JPY" and "USD_JPY" in data and ts in data["USD_JPY"].index:
        usdjpy_px = float(data["USD_JPY"].loc[ts]["close"])
        base_usd_sym = f"{base}_USD"
        if base_usd_sym in data and ts in data[base_usd_sym].index:
            notional = float(data[base_usd_sym].loc[ts]["close"])
        else:
            notional = bar_close / usdjpy_px
        return 1.0 / usdjpy_px, notional
    return 1.0 / bar_close, 1.0


def simulate(data, timeline, variant):
    use_donchian = variant in ("C", "D")
    use_trend_filter = variant in ("B", "D")

    nav = INITIAL_NAV
    open_pos = []
    trade_log = []
    equity_curve = []
    last_swap_date = None

    for ts in timeline:
        day = ts.date()

        # Financing carry drag
        if last_swap_date is not None and day > last_swap_date:
            days = (day - last_swap_date).days
            daily_rate = (DAILY_FINANCING_BPS / 10000.0) * days
            for p in open_pos:
                fee = p["usd_notional"] * daily_rate
                p["accumulated_financing"] += fee
                nav -= fee
        last_swap_date = day

        # Exits & Trailing Stop Updates
        retained = []
        for p in open_pos:
            sym = p["symbol"]
            df = data[sym]
            if ts not in df.index:
                retained.append(p)
                continue

            bar = df.loc[ts]
            high, low, close = float(bar["high"]), float(bar["low"]), float(bar["close"])
            atr = float(bar["atr"]) if not pd.isna(bar["atr"]) else p["atr_entry"]
            pnl_factor, _ = get_usd_conversion_factor(sym, ts, close, data)

            hit_stop = False
            raw_exit = 0.0

            if p["side"] == "LONG":
                if low <= p["effective_stop"]:
                    hit_stop = True
                    raw_exit = min(float(bar["open"]), p["effective_stop"])
            else:  # SHORT
                if high >= p["effective_stop"]:
                    hit_stop = True
                    raw_exit = max(float(bar["open"]), p["effective_stop"])

            if hit_stop:
                exit_side = "SELL" if p["side"] == "LONG" else "BUY"
                exit_px = execute_price(raw_exit, exit_side)
                
                if p["side"] == "LONG":
                    gross_usd = (exit_px - p["entry_px"]) * p["qty"] * pnl_factor
                else:
                    gross_usd = (p["entry_px"] - exit_px) * p["qty"] * pnl_factor
                
                net_pnl = gross_usd - p["accumulated_financing"]
                nav += net_pnl
                r = net_pnl / p["risk_usd"] if p["risk_usd"] else 0.0
                outcome = "WIN" if r > 0.05 else ("LOSS" if r < -0.05 else "SCRATCH")
                trade_log.append(TradeRecord(variant, sym, p["side"], p["entry_time"], ts, p["entry_px"], exit_px, p["risk_usd"], net_pnl, r, outcome, "TRAIL_STOP"))
                continue

            # Ratchet trailing stop armed for subsequent bar (t+1)
            if p["side"] == "LONG":
                new_trail = close - (2.0 * atr)
                p["effective_stop"] = max(p["effective_stop"], new_trail)
            else:
                new_trail = close + (2.0 * atr)
                p["effective_stop"] = min(p["effective_stop"], new_trail)

            retained.append(p)
        open_pos = retained

        # MTM
        mtm = nav
        for p in open_pos:
            df = data[p["symbol"]]
            if ts in df.index:
                cur_close = float(df.loc[ts]["close"])
                pnl_factor, _ = get_usd_conversion_factor(p["symbol"], ts, cur_close, data)
                if p["side"] == "LONG":
                    mtm += (cur_close - p["entry_px"]) * p["qty"] * pnl_factor
                else:
                    mtm += (p["entry_px"] - cur_close) * p["qty"] * pnl_factor
        equity_curve.append((ts, mtm))

        if len(open_pos) >= MAX_POSITIONS:
            continue

        # Entries
        active_symbols = {p["symbol"] for p in open_pos}

        for sym in sorted(data):
            if len(open_pos) >= MAX_POSITIONS:
                break
            if sym in active_symbols:
                continue

            df = data[sym]
            if ts not in df.index:
                continue

            idx = df.index.get_loc(ts)
            if not isinstance(idx, (int, np.integer)) or idx < 205:
                continue

            cur = df.iloc[idx]
            prev = df.iloc[idx - 1]

            if any(pd.isna(cur[x]) for x in ["sma20", "sma200", "std20", "atr", "upper_band", "lower_band", "donchian_high", "donchian_low", "sma200_slope5"]):
                continue

            long_trigger = False
            short_trigger = False

            if not use_donchian:
                # Bollinger Volatility Expansion (cross outside bands)
                long_trigger = float(prev["close"]) <= float(prev["upper_band"]) and float(cur["close"]) > float(cur["upper_band"])
                short_trigger = float(prev["close"]) >= float(prev["lower_band"]) and float(cur["close"]) < float(cur["lower_band"])
            else:
                # 20-bar Donchian Breakout
                long_trigger = float(cur["close"]) > float(cur["donchian_high"])
                short_trigger = float(cur["close"]) < float(cur["donchian_low"])

            # 200 SMA Macro Trend Alignment Filter
            if use_trend_filter:
                c = float(cur["close"])
                s200 = float(cur["sma200"])
                s200_slope = float(cur["sma200_slope5"])

                long_trigger = long_trigger and (c > s200 and s200_slope > 0)
                short_trigger = short_trigger and (c < s200 and s200_slope < 0)

            if not (long_trigger or short_trigger):
                continue

            # Mutually exclusive side selection
            side = "LONG" if long_trigger else "SHORT"

            entry_side = "BUY" if side == "LONG" else "SELL"
            entry_px = execute_price(float(cur["close"]), entry_side)

            min_buffer = entry_px * 0.0015
            risk_unit = max(2.0 * float(cur["atr"]), min_buffer)

            if side == "LONG":
                initial_stop = entry_px - risk_unit
            else:
                initial_stop = entry_px + risk_unit

            risk_usd = mtm * RISK_PCT
            pnl_factor, notional_factor = get_usd_conversion_factor(sym, ts, entry_px, data)

            qty = risk_usd / (risk_unit * pnl_factor)
            usd_notional = qty * notional_factor

            open_pos.append({
                "symbol": sym,
                "side": side,
                "entry_time": ts,
                "entry_px": entry_px,
                "effective_stop": initial_stop,
                "risk_per_unit": risk_unit,
                "risk_usd": risk_usd,
                "qty": qty,
                "usd_notional": usd_notional,
                "accumulated_financing": 0.0,
                "atr_entry": float(cur["atr"]),
            })
            active_symbols.add(sym)

    # Terminal Liquidation
    last_ts = timeline[-1]
    for p in open_pos:
        df = data[p["symbol"]]
        if last_ts in df.index:
            exit_side = "SELL" if p["side"] == "LONG" else "BUY"
            exit_px = execute_price(float(df.loc[last_ts]["close"]), exit_side)
            pnl_factor, _ = get_usd_conversion_factor(p["symbol"], last_ts, exit_px, data)
            
            if p["side"] == "LONG":
                gross_usd = (exit_px - p["entry_px"]) * p["qty"] * pnl_factor
            else:
                gross_usd = (p["entry_px"] - exit_px) * p["qty"] * pnl_factor
            
            net_pnl = gross_usd - p["accumulated_financing"]
            nav += net_pnl
            r = net_pnl / p["risk_usd"] if p["risk_usd"] else 0.0
            outcome = "WIN" if r > 0.05 else ("LOSS" if r < -0.05 else "SCRATCH")
            trade_log.append(TradeRecord(variant, p["symbol"], p["side"], p["entry_time"], last_ts, p["entry_px"], exit_px, p["risk_usd"], net_pnl, r, outcome, "TERMINAL_LIQ"))

    eq = pd.Series([x[1] for x in equity_curve], index=[x[0] for x in equity_curve], dtype=float)
    max_dd = abs(((eq - eq.cummax()) / eq.cummax()).min()) * 100.0 if not eq.empty else 0.0
    return trade_log, nav, max_dd


def evaluate_metrics(trades, terminal_nav, max_dd):
    rs = np.array([t.r for t in trades], dtype=float)
    wins = rs[rs > 0.05]
    scratches = rs[(rs >= -0.05) & (rs <= 0.05)]
    losses = rs[rs < -0.05]

    gross_win = wins.sum()
    gross_loss = abs(losses.sum())
    pf = (gross_win / gross_loss) if gross_loss > 0 else (np.inf if gross_win > 0 else 0.0)
    ret_pct = (terminal_nav / INITIAL_NAV - 1.0) * 100.0

    # Top-3 trade removal
    rs_sorted = sorted(rs, reverse=True)
    rs_no_top3 = rs_sorted[3:] if len(rs_sorted) > 3 else []
    wins_no_top3 = [r for r in rs_no_top3 if r > 0.05]
    losses_no_top3 = [abs(r) for r in rs_no_top3 if r < -0.05]
    pf_no_top3 = (sum(wins_no_top3) / sum(losses_no_top3)) if losses_no_top3 else 0.0
    netr_no_top3 = sum(rs_no_top3)

    return {
        "Trades": len(rs),
        "Win%": (len(wins) / len(rs) * 100) if len(rs) else 0.0,
        "PF": pf,
        "MeanR": rs.mean() if len(rs) else 0.0,
        "NetR": rs.sum() if len(rs) else 0.0,
        "PF_NoTop3": pf_no_top3,
        "NetR_NoTop3": netr_no_top3,
        "Return%": ret_pct,
        "MaxDD%": max_dd,
        "TerminalNAV": terminal_nav,
    }


def main():
    print("=" * 155)
    print(" OANDA H4 VOLATILITY BREAKOUT FACTORIAL TEST (2023 - 2026)")
    print(" Target Standards: PF >= 1.20 | MeanR > +0.10R | NetR_NoTop3 > 0 | MaxDD Reasonable")
    print("=" * 155)

    raw = load_h4_clean()
    data = {sym: add_indicators(df) for sym, df in raw.items()}

    common_start = max(df.index.min() for df in data.values())
    common_end = min(df.index.max() for df in data.values())
    timeline = sorted(set.intersection(*[set(df[(df.index >= common_start) & (df.index <= common_end)].index) for df in data.values()]))

    print(f"Evaluated Pairs ({len(data)}): {', '.join(sorted(data.keys()))}")
    print(f"Synchronized H4 Bars: {len(timeline)} ({common_start.date()} to {common_end.date()})\n")

    variants = {
        "A": "2.0σ Band Breakout (No Filter)",
        "B": "2.0σ Band Breakout (200 SMA Gated)",
        "C": "20-bar Donchian (No Filter)",
        "D": "20-bar Donchian (200 SMA Gated)",
    }

    results = []
    saved_trades = {}

    for v, desc in variants.items():
        t_log, nav, dd = simulate(data, timeline, v)
        saved_trades[v] = t_log
        m = evaluate_metrics(t_log, nav, dd)
        results.append({"Variant": v, "Architecture": desc, **m})

    res_df = pd.DataFrame(results)

    show = res_df.copy()
    for c in ["Win%", "MeanR", "NetR", "NetR_NoTop3", "Return%", "MaxDD%"]:
        show[c] = show[c].map(lambda x: f"{x:.2f}" if pd.notna(x) else "N/A")
    show["PF"] = show["PF"].map(lambda x: f"{x:.3f}")
    show["PF_NoTop3"] = show["PF_NoTop3"].map(lambda x: f"{x:.3f}")
    show["TerminalNAV"] = show["TerminalNAV"].map(lambda x: f"${x:,.2f}")

    print("=" * 155)
    print(" SUMMARY MATRIX")
    print("=" * 155)
    print(show.to_string(index=False))

    # Long vs Short breakdown
    print("\n" + "=" * 155)
    print(" LONG VS. SHORT DISAGGREGATION")
    print("=" * 155)
    ls_rows = []
    for v in ["A", "B", "C", "D"]:
        trades = saved_trades[v]
        for side in ["LONG", "SHORT"]:
            sub = [t for t in trades if t.side == side]
            rs = [t.r for t in sub]
            wins = [r for r in rs if r > 0.05]
            losses = [abs(r) for r in rs if r < -0.05]
            pf = (sum(wins) / sum(losses)) if losses else 0.0
            ls_rows.append({
                "Variant": v,
                "Side": side,
                "Trades": len(sub),
                "Win%": round(len(wins) / len(sub) * 100, 1) if sub else 0.0,
                "PF": round(pf, 3),
                "NetR": round(sum(rs), 2),
                "MeanR": round(np.mean(rs), 3) if rs else 0.0
            })
    print(pd.DataFrame(ls_rows).to_string(index=False))

    # Save artifacts
    all_trades = [t.__dict__ for v in saved_trades for t in saved_trades[v]]
    pd.DataFrame(all_trades).to_csv("/home/moonrider1968/bots/oanda_breakout_factorial_trades.csv", index=False)
    res_df.to_csv("/home/moonrider1968/bots/oanda_breakout_factorial_summary.csv", index=False)
    print("\nArtifacts saved: /home/moonrider1968/bots/oanda_breakout_factorial_*.csv")


if __name__ == "__main__":
    main()
