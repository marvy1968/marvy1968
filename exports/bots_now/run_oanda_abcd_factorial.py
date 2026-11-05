#!/usr/bin/env python3
"""
OANDA MicroBeast Offline Factorial Harness (Strict H4 Dedicated Loader)
Locked strictly to /home/moonrider1968/bots/oanda_data/h4/*.csv
Enforces true 4-hour bar verification, tranche exits, and quote currency scaling.
"""

import os
import math
from pathlib import Path
from dataclasses import dataclass
from collections import deque

import numpy as np
import pandas as pd

DATA_DIR = Path("/home/moonrider1968/bots/oanda_data/h4")

INITIAL_NAV = 100_000.0
RISK_PCT = 0.0050
MAX_POSITIONS = 3

SPREAD_BPS = 1.0
SLIP_BPS = 1.0
DAILY_SWAP_PCT = 0.00015

BB_LEN = 20
ATR_LEN = 14
ADX_LEN = 14
STATIC_SIGMA = 2.0


def sigma_from_adx(adx):
    if pd.isna(adx):
        return 2.0
    if adx < 20.0:
        return 1.8
    if adx <= 30.0:
        return 2.0
    return 2.5


@dataclass
class TradeRecord:
    variant: str
    symbol: str
    entry_time: pd.Timestamp
    exit_time: pd.Timestamp
    entry_px: float
    exit_px: float
    risk_usd: float
    pnl_usd: float
    r: float
    outcome: str
    reason: str


def load_h4_data():
    found = {}
    if not DATA_DIR.exists():
        raise RuntimeError(f"Directory {DATA_DIR} does not exist.")

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
        
        # Verify 4-hour dominant interval
        deltas = df.index.to_series().diff().dropna().dt.total_seconds() / 3600.0
        if len(deltas) == 0 or abs(deltas.mode().iloc[0] - 4.0) > 0.1:
            print(f"Skipping {p.name}: interval is not 4 hours.")
            continue

        name = p.stem.upper()
        bits = name.replace("-", "_").split("_")
        symbol = None
        for i in range(len(bits) - 1):
            if len(bits[i]) == 3 and len(bits[i+1]) == 3 and bits[i].isalpha() and bits[i+1].isalpha():
                symbol = f"{bits[i]}_{bits[i+1]}"
                break
        if not symbol:
            symbol = name

        found[symbol] = df

    if not found:
        raise RuntimeError(f"No valid H4 datasets found in {DATA_DIR}")
    return found


def add_indicators(df):
    d = df.copy()
    d["ma20"] = d["close"].rolling(BB_LEN).mean()
    d["std20"] = d["close"].rolling(BB_LEN).std()

    pc = d["close"].shift(1)
    tr = pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)
    d["atr"] = tr.rolling(ATR_LEN).mean()

    up = d["high"].diff()
    dn = -d["low"].diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0), index=d.index)
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0), index=d.index)

    atr_sum = tr.rolling(ADX_LEN).sum()
    plus_di = 100 * plus_dm.rolling(ADX_LEN).sum() / atr_sum.replace(0, np.nan)
    minus_di = 100 * minus_dm.rolling(ADX_LEN).sum() / atr_sum.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    d["adx"] = dx.rolling(ADX_LEN).mean()

    d["sigma_local"] = d["adx"].apply(sigma_from_adx)
    d["lower_dynamic"] = d["ma20"] - (d["sigma_local"] * d["std20"])
    d["lower_static"] = d["ma20"] - (STATIC_SIGMA * d["std20"])
    return d


def classify(r):
    if r > 0.05:
        return "WIN"
    if r < -0.05:
        return "LOSS"
    return "SCRATCH"


def execute_price(px, side):
    fr = (SPREAD_BPS + SLIP_BPS) / 10000.0
    return px * (1.0 + fr if side == "BUY" else 1.0 - fr)


def calc_usd_pnl(sym, entry_px, exit_px, qty):
    # Quote currency conversion to USD
    raw_delta = (exit_px - entry_px) * qty
    if sym.endswith("_USD"):
        return raw_delta
    elif sym.endswith("_JPY"):
        return raw_delta / exit_px
    elif sym.endswith("_CAD") or sym.endswith("_CHF"):
        return raw_delta / exit_px
    return raw_delta


def simulate(data, timeline, variant):
    dynamic_entry = variant in ("B", "D")
    safety = variant in ("C", "D")

    nav = INITIAL_NAV
    open_pos = []
    trade_log = []
    equity_curve = []
    loss_times = deque()
    daily_start_nav = {}
    last_swap_date = None

    for ts in timeline:
        day = ts.date()
        if day not in daily_start_nav:
            daily_start_nav[day] = nav

        # Financing carry drag
        if last_swap_date is not None and day > last_swap_date:
            days_passed = (day - last_swap_date).days
            for p in open_pos:
                swap_cost = p["qty"] * (DAILY_SWAP_PCT * days_passed)
                p["accumulated_swap"] += swap_cost
                nav -= swap_cost
        last_swap_date = day

        # Exits
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
            current_stop = p["effective_stop"]
            one_r = p["risk_per_unit"]

            if not safety:
                hit_stop = low <= current_stop
                hit_target = high >= (p["entry_px"] + 1.5 * one_r)
                if hit_stop and hit_target:
                    hit_target = False

                if hit_stop:
                    exit_px = execute_price(min(float(bar["open"]), current_stop), "SELL")
                    pnl = calc_usd_pnl(sym, p["entry_px"], exit_px, p["qty"]) - p["accumulated_swap"]
                    nav += pnl
                    r = pnl / p["risk_usd"] if p["risk_usd"] else 0.0
                    trade_log.append(TradeRecord(variant, sym, p["entry_time"], ts, p["entry_px"], exit_px, p["risk_usd"], pnl, r, classify(r), "STOP"))
                    if classify(r) == "LOSS":
                        loss_times.append(ts)
                    continue
                elif hit_target:
                    exit_px = execute_price(max(float(bar["open"]), p["entry_px"] + 1.5 * one_r), "SELL")
                    pnl = calc_usd_pnl(sym, p["entry_px"], exit_px, p["qty"]) - p["accumulated_swap"]
                    nav += pnl
                    r = pnl / p["risk_usd"] if p["risk_usd"] else 0.0
                    trade_log.append(TradeRecord(variant, sym, p["entry_time"], ts, p["entry_px"], exit_px, p["risk_usd"], pnl, r, classify(r), "TARGET_1.5R"))
                    continue
            else:
                if low <= current_stop:
                    exit_px = execute_price(min(float(bar["open"]), current_stop), "SELL")
                    rem_qty = p["qty"] * p["rem_pct"]
                    pnl = calc_usd_pnl(sym, p["entry_px"], exit_px, rem_qty) - p["accumulated_swap"]
                    nav += pnl
                    p["realized_pnl"] += pnl
                    r = p["realized_pnl"] / p["risk_usd"] if p["risk_usd"] else 0.0
                    trade_log.append(TradeRecord(variant, sym, p["entry_time"], ts, p["entry_px"], exit_px, p["risk_usd"], p["realized_pnl"], r, classify(r), "RATCHET_STOP"))
                    if classify(r) == "LOSS":
                        loss_times.append(ts)
                    continue

                if not p["t1_hit"] and high >= (p["entry_px"] + 1.0 * one_r):
                    t1_px = execute_price(p["entry_px"] + 1.0 * one_r, "SELL")
                    t1_pnl = calc_usd_pnl(sym, p["entry_px"], t1_px, p["qty"] * 0.50)
                    nav += t1_pnl
                    p["realized_pnl"] += t1_pnl
                    p["rem_pct"] -= 0.50
                    p["t1_hit"] = True
                    p["next_armed_stop"] = max(p["next_armed_stop"], p["entry_px"] + 0.01 * one_r)

                if p["t1_hit"] and not p["t2_hit"] and high >= (p["entry_px"] + 2.0 * one_r):
                    t2_px = execute_price(p["entry_px"] + 2.0 * one_r, "SELL")
                    t2_pnl = calc_usd_pnl(sym, p["entry_px"], t2_px, p["qty"] * 0.25)
                    nav += t2_pnl
                    p["realized_pnl"] += t2_pnl
                    p["rem_pct"] -= 0.25
                    p["t2_hit"] = True
                    p["runner_armed"] = True

                if p["runner_armed"]:
                    trail = close - 2.0 * atr
                    p["next_armed_stop"] = max(p["next_armed_stop"], trail)

                p["effective_stop"] = p["next_armed_stop"]

            retained.append(p)
        open_pos = retained

        while loss_times and (ts - loss_times[0]) > pd.Timedelta(hours=24):
            loss_times.popleft()

        # Mark-to-market
        mtm = nav
        for p in open_pos:
            df = data[p["symbol"]]
            if ts in df.index:
                cur_c = float(df.loc[ts]["close"])
                rem_qty = p["qty"] * (p["rem_pct"] if safety else 1.0)
                mtm += calc_usd_pnl(p["symbol"], p["entry_px"], cur_c, rem_qty)
        equity_curve.append((ts, mtm))

        # Circuit breakers
        if safety:
            if len(loss_times) >= 3:
                continue
            s_nav = daily_start_nav[day]
            if s_nav > 0 and (mtm - s_nav) / s_nav <= -0.02:
                continue

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
            if not isinstance(idx, (int, np.integer)) or idx < 25:
                continue

            cur = df.iloc[idx]
            prev = df.iloc[idx - 1]
            if any(pd.isna(cur[x]) for x in ["ma20", "std20", "atr", "adx"]):
                continue

            cur_env = float(cur["lower_dynamic"] if dynamic_entry else cur["lower_static"])
            prev_env = float(prev["lower_dynamic"] if dynamic_entry else prev["lower_static"])

            signal = (
                float(prev["close"]) < prev_env
                and float(cur["close"]) > cur_env
                and float(cur["close"]) < float(cur["ma20"])
            )
            if not signal:
                continue

            entry_px = execute_price(float(cur["close"]), "BUY")
            
            # Enforce minimum stop floor (1.5x ATR or minimum 0.15% price buffer)
            min_buffer = entry_px * 0.0015
            risk_unit = max(1.5 * float(cur["atr"]), min_buffer)
            stop_px = entry_px - risk_unit

            risk_usd = mtm * RISK_PCT
            
            # Account for quote currency scaling when computing quantity
            if sym.endswith("_JPY") or sym.endswith("_CAD") or sym.endswith("_CHF"):
                qty = (risk_usd * entry_px) / risk_unit
            else:
                qty = risk_usd / risk_unit

            open_pos.append({
                "symbol": sym,
                "entry_time": ts,
                "entry_px": entry_px,
                "risk_per_unit": risk_unit,
                "risk_usd": risk_usd,
                "qty": qty,
                "atr_entry": float(cur["atr"]),
                "effective_stop": stop_px,
                "next_armed_stop": stop_px,
                "rem_pct": 1.0,
                "realized_pnl": 0.0,
                "accumulated_swap": 0.0,
                "t1_hit": False,
                "t2_hit": False,
                "runner_armed": False
            })
            active_symbols.add(sym)

    # Terminal liquidation
    terminal_nav = nav
    last_ts = timeline[-1] if timeline else None
    for p in open_pos:
        df = data[p["symbol"]]
        if last_ts in df.index:
            px = execute_price(float(df.loc[last_ts]["close"]), "SELL")
            rem_qty = p["qty"] * (p["rem_pct"] if safety else 1.0)
            pnl = calc_usd_pnl(p["symbol"], p["entry_px"], px, rem_qty) - p["accumulated_swap"]
            terminal_nav += pnl
            tot_pnl = (p["realized_pnl"] + pnl) if safety else pnl
            r = tot_pnl / p["risk_usd"] if p["risk_usd"] else 0.0
            trade_log.append(TradeRecord(variant, p["symbol"], p["entry_time"], last_ts, p["entry_px"], px, p["risk_usd"], tot_pnl, r, classify(r), "TERMINAL_MTM"))

    eq = pd.Series([x[1] for x in equity_curve], index=[x[0] for x in equity_curve], dtype=float)
    max_dd = abs(((eq - eq.cummax()) / eq.cummax()).min()) * 100.0 if not eq.empty else 0.0
    return trade_log, terminal_nav, max_dd


def metrics(trades, terminal_nav, max_dd):
    rs = np.array([t.r for t in trades], dtype=float)
    wins = rs[rs > 0.05]
    scratches = rs[(rs >= -0.05) & (rs <= 0.05)]
    losses = rs[rs < -0.05]
    gross_win = wins.sum()
    gross_loss = abs(losses.sum())
    pf = (gross_win / gross_loss) if gross_loss > 0 else (np.inf if gross_win > 0 else 0.0)
    ret_pct = (terminal_nav / INITIAL_NAV - 1.0) * 100.0

    return {
        "Trades": len(rs),
        "Wins": len(wins),
        "Scratches": len(scratches),
        "Losses": len(losses),
        "Win%": len(wins) / len(rs) * 100 if len(rs) else 0.0,
        "Scratch%": len(scratches) / len(rs) * 100 if len(rs) else 0.0,
        "MeanR": rs.mean() if len(rs) else 0.0,
        "NetR": rs.sum() if len(rs) else 0.0,
        "PF": pf,
        "Return%": ret_pct,
        "MaxDD%": max_dd,
        "Return/DD": ret_pct / max_dd if max_dd > 0 else np.nan,
        "TerminalNAV": terminal_nav,
    }


def main():
    print("=" * 145)
    print(" OANDA MICROBEAST DEDICATED H4 FACTORIAL HARNESS (A/B/C/D)")
    print(f" Source Directory: {DATA_DIR}")
    print("=" * 145)

    raw = load_h4_data()
    data = {sym: add_indicators(df) for sym, df in raw.items()}

    common_start = max(df.index.min() for df in data.values())
    common_end = min(df.index.max() for df in data.values())

    print(f"Synchronized Pairs ({len(data)}): {', '.join(sorted(data.keys()))}")
    print(f"Strict Common Window: {common_start} -> {common_end}")

    timeline = sorted(set.intersection(*[set(df[(df.index >= common_start) & (df.index <= common_end)].index) for df in data.values()]))
    print(f"Synchronized H4 Candles: {len(timeline)}")
    print()

    labels = {
        "A": "Static 2.0σ + Baseline (Fixed -1R/+1.5R)",
        "B": "Dynamic σ + Baseline (Fixed -1R/+1.5R)",
        "C": "Static 2.0σ + Safety (50%@1R, 25%@2R, 25% Runner + BE)",
        "D": "Dynamic σ + Safety (50%@1R, 25%@2R, 25% Runner + BE)",
    }

    rows = []
    saved = {}

    for v in ["A", "B", "C", "D"]:
        trades, terminal_nav, max_dd = simulate(data, timeline, v)
        saved[v] = trades
        m = metrics(trades, terminal_nav, max_dd)
        rows.append({"Variant": v, "Architecture": labels[v], **m})

    out = pd.DataFrame(rows)
    pd.set_option("display.max_columns", None)
    pd.set_option("display.width", 220)

    print("=" * 145)
    print(" CLEAN H4 FACTORIAL MATRIX RESULTS")
    print("=" * 145)

    show = out.copy()
    for c in ["Win%", "Scratch%", "MeanR", "NetR", "Return%", "MaxDD%", "Return/DD"]:
        show[c] = show[c].map(lambda x: f"{x:.3f}" if pd.notna(x) else "N/A")
    show["PF"] = show["PF"].map(lambda x: "inf" if np.isinf(x) else f"{x:.3f}")
    show["TerminalNAV"] = show["TerminalNAV"].map(lambda x: f"${x:,.2f}")

    print(show.to_string(index=False))

    out.to_csv("/home/moonrider1968/bots/oanda_abcd_h4_clean_summary.csv", index=False)
    detail = [t.__dict__ for v in saved for t in saved[v]]
    pd.DataFrame(detail).to_csv("/home/moonrider1968/bots/oanda_abcd_h4_clean_trades.csv", index=False)
    print("\nClean H4 artifacts saved to /home/moonrider1968/bots/oanda_abcd_h4_clean_*.csv")


if __name__ == "__main__":
    main()
