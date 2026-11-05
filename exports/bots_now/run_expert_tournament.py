#!/usr/bin/env python3
import math
import sys
import numpy as np
import pandas as pd
from pathlib import Path

# --- Configuration & Friction Constants ---
DATA_DIR = Path("/home/moonrider1968/bots/oanda_data")
PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "NZD_USD", "USD_CHF", "EUR_JPY", "GBP_JPY"]

INITIAL_NAV = 100000.0
RISK_PCT = 0.0050          # 0.50% MTM NAV
MAX_POSITIONS = 3
MAX_FACTOR_EXPOSURE = 2    # Factor firewall

SPREAD_PIPS_MAP = {
    "EUR_USD": 1.0, "GBP_USD": 1.4, "USD_JPY": 1.1, "AUD_USD": 1.2,
    "USD_CAD": 1.5, "NZD_USD": 1.6, "USD_CHF": 1.5, "EUR_JPY": 1.6, "GBP_JPY": 2.2
}
ENTRY_SLIPPAGE_PIPS = 0.25
EXIT_SLIPPAGE_PIPS = 0.25

def get_pip_value(pair: str) -> float:
    return 0.01 if "JPY" in pair else 0.0001

from run_h4_filter_sweep import load_local_dataset

print("Loading OANDA datasets via certified loader...")
dfs = {sym: load_local_dataset(sym, "H4") for sym in PAIRS}

# Point-In-Time Quote to USD lookup
pit_quotes = {
    "USD_JPY": dfs["USD_JPY"]["close"].shift(1),
    "USD_CAD": dfs["USD_CAD"]["close"].shift(1),
    "USD_CHF": dfs["USD_CHF"]["close"].shift(1),
}

def get_quote_to_usd_rate(pair: str, t: pd.Timestamp) -> float:
    base, quote = pair.split("_")
    if quote == "USD":
        return 1.0
    elif quote == "JPY":
        r = pit_quotes["USD_JPY"].asof(t)
        return 1.0 / r if r and r > 0 else 1.0 / 150.0
    elif quote == "CAD":
        r = pit_quotes["USD_CAD"].asof(t)
        return 1.0 / r if r and r > 0 else 1.0 / 1.35
    elif quote == "CHF":
        r = pit_quotes["USD_CHF"].asof(t)
        return 1.0 / r if r and r > 0 else 1.0 / 0.88
    return 1.0

# Precompute Base Indicators
for sym, df in dfs.items():
    c = df["close"]
    h = df["high"]
    l = df["low"]

    # ATR
    c_prev = c.shift(1)
    tr = pd.concat([h - l, (h - c_prev).abs(), (l - c_prev).abs()], axis=1).max(axis=1)
    df["atr14"] = tr.rolling(14).mean()
    df["atr20"] = tr.rolling(20).mean()

    # Moving Averages
    df["sma20"] = c.rolling(20).mean()
    df["std20"] = c.rolling(20).std()
    df["ema20"] = c.ewm(span=20, adjust=False).mean()
    df["ema80"] = c.ewm(span=80, adjust=False).mean()
    df["sma100"] = c.rolling(100).mean()
    df["std100"] = c.rolling(100).std()
    df["sma120"] = c.rolling(120).mean()
    df["sma200"] = c.rolling(200).mean()

    # Donchian Channels
    df["donch55_h"] = h.shift(1).rolling(55).max()
    df["donch55_l"] = l.shift(1).rolling(55).min()
    df["donch20_h"] = h.shift(1).rolling(20).max()
    df["donch20_l"] = l.shift(1).rolling(20).min()

    # Bollinger Bands
    df["f0_upper_25s"] = df["sma100"] + 2.5 * df["std100"]
    df["f0_lower_25s"] = df["sma100"] - 2.5 * df["std100"]
    df["f0_upper_35s"] = df["sma100"] + 3.5 * df["std100"]
    df["f0_lower_35s"] = df["sma100"] - 3.5 * df["std100"]

    df["bb20_upper_20s"] = df["sma20"] + 2.0 * df["std20"]
    df["bb20_lower_20s"] = df["sma20"] - 2.0 * df["std20"]
    df["bb20_upper_30s"] = df["sma20"] + 3.0 * df["std20"]
    df["bb20_lower_30s"] = df["sma20"] - 3.0 * df["std20"]

    # RSI 14
    delta = c.diff()
    gain = delta.clip(lower=0).rolling(14).mean()
    loss = (-delta.clip(upper=0)).rolling(14).mean()
    rs = gain / (loss + 1e-9)
    df["rsi14"] = 100 - (100 / (1 + rs))

    # MACD 12,26,9
    ema12 = c.ewm(span=12, adjust=False).mean()
    ema26 = c.ewm(span=26, adjust=False).mean()
    df["macd"] = ema12 - ema26
    df["macd_sig"] = df["macd"].ewm(span=9, adjust=False).mean()
    df["macd_hist"] = df["macd"] - df["macd_sig"]

    # ADX 14
    up_move = h - h.shift(1)
    down_move = l.shift(1) - l
    pos_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    neg_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    pos_di = pd.Series(pos_dm, index=df.index).rolling(14).mean() / (df["atr14"] + 1e-9) * 100.0
    neg_di = pd.Series(neg_dm, index=df.index).rolling(14).mean() / (df["atr14"] + 1e-9) * 100.0
    dx = (pos_di - neg_di).abs() / ((pos_di + neg_di).abs() + 1e-9) * 100.0
    df["adx14"] = dx.rolling(14).mean()

# --- Generalized Forward Path Evaluator ---
def evaluate_path(df, entry_idx, side, entry_px, stop_px, target_px, pip_val, half_spread, exit_slip, trail_atr=None, trail_dist=0.0):
    stop_dist = abs(entry_px - stop_px)
    max_hold = 80
    curr_stop = stop_px

    for i in range(entry_idx + 1, min(entry_idx + max_hold + 1, len(df))):
        bar = df.iloc[i]
        o, h, l, c = bar["open"], bar["high"], bar["low"], bar["close"]
        t = df.index[i]
        count = i - entry_idx

        # Dynamic trailing stop update
        if trail_atr is not None:
            atr_v = df["atr20"].iloc[i - 1]
            if side == "BUY":
                curr_stop = max(curr_stop, h - (trail_dist * atr_v))
            else:
                curr_stop = min(curr_stop, l + (trail_dist * atr_v))

        if side == "BUY":
            if o <= curr_stop:
                return o - half_spread - exit_slip, count, t
            if target_px and o >= target_px:
                return o - half_spread - exit_slip, count, t
            if l <= curr_stop:
                return curr_stop - half_spread - exit_slip, count, t
            if target_px and h >= target_px:
                return target_px - half_spread - exit_slip, count, t
        else:
            if o >= curr_stop:
                return o + half_spread + exit_slip, count, t
            if target_px and o <= target_px:
                return o + half_spread + exit_slip, count, t
            if h >= curr_stop:
                return curr_stop + half_spread + exit_slip, count, t
            if target_px and l <= target_px:
                return target_px + half_spread + exit_slip, count, t

    last_bar = df.iloc[min(entry_idx + max_hold, len(df) - 1)]
    px = last_bar["close"] - half_spread - exit_slip if side == "BUY" else last_bar["close"] + half_spread + exit_slip
    return px, max_hold, df.index[min(entry_idx + max_hold, len(df) - 1)]

# --- Strategy Signal Generators ---
def gen_signals_f0(sym, df):
    pip_val = get_pip_value(sym)
    events = []
    for i in range(105, len(df) - 5):
        c = df["close"].iloc[i]
        c_prev = df["close"].iloc[i-1]
        u25_prev = df["f0_upper_25s"].iloc[i-1]
        l25_prev = df["f0_lower_25s"].iloc[i-1]

        # Buy: reclaim lower 2.5s
        if c_prev < l25_prev and c >= df["f0_lower_25s"].iloc[i]:
            stop_px = df["f0_lower_35s"].iloc[i]
            target_px = df["sma100"].iloc[i]
            events.append((df.index[i], "BUY", stop_px, target_px, None, 0.0))
        # Sell: reclaim upper 2.5s
        elif c_prev > u25_prev and c <= df["f0_upper_25s"].iloc[i]:
            stop_px = df["f0_upper_35s"].iloc[i]
            target_px = df["sma100"].iloc[i]
            events.append((df.index[i], "SELL", stop_px, target_px, None, 0.0))
    return events

def gen_signals_velez(sym, df):
    pip_val = get_pip_value(sym)
    events = []
    for i in range(205, len(df) - 5):
        s20 = df["sma20"].iloc[i]
        s200 = df["sma200"].iloc[i]
        s20_prev = df["sma20"].iloc[i-3]
        c, o, h, l = df["close"].iloc[i], df["open"].iloc[i], df["high"].iloc[i], df["low"].iloc[i]
        atr = df["atr14"].iloc[i]

        # Velez Bullish: 20 > 200, 20 sloping up, bar touches 20 SMA with bullish reversal
        if s20 > s200 and s20 > s20_prev:
            if l <= s20 and c > s20 and c > o:
                stop_dist = max(1.5 * atr, (c - l) + 2.0 * pip_val)
                stop_px = c - stop_dist
                target_px = c + (2.0 * stop_dist)
                events.append((df.index[i], "BUY", stop_px, target_px, None, 0.0))

        # Velez Bearish: 20 < 200, 20 sloping down, bar touches 20 SMA with bearish reversal
        elif s20 < s200 and s20 < s20_prev:
            if h >= s20 and c < s20 and c < o:
                stop_dist = max(1.5 * atr, (h - c) + 2.0 * pip_val)
                stop_px = c + stop_dist
                target_px = c - (2.0 * stop_dist)
                events.append((df.index[i], "SELL", stop_px, target_px, None, 0.0))
    return events

def gen_signals_turtle(sym, df):
    events = []
    for i in range(60, len(df) - 5):
        c = df["close"].iloc[i]
        h55 = df["donch55_h"].iloc[i]
        l55 = df["donch55_l"].iloc[i]
        atr = df["atr20"].iloc[i]

        if c > h55:
            stop_px = c - (2.0 * atr)
            events.append((df.index[i], "BUY", stop_px, None, "TRAIL_DONCH", 2.0))
        elif c < l55:
            stop_px = c + (2.0 * atr)
            events.append((df.index[i], "SELL", stop_px, None, "TRAIL_DONCH", 2.0))
    return events

def gen_signals_carver(sym, df):
    events = []
    for i in range(85, len(df) - 5):
        e20 = df["ema20"].iloc[i]
        e80 = df["ema80"].iloc[i]
        e20_p = df["ema20"].iloc[i-1]
        e80_p = df["ema80"].iloc[i-1]
        c = df["close"].iloc[i]
        atr = df["atr20"].iloc[i]

        # Crossover trigger
        if e20_p <= e80_p and e20 > e80:
            stop_px = c - (2.5 * atr)
            events.append((df.index[i], "BUY", stop_px, None, "TRAIL_ATR", 2.5))
        elif e20_p >= e80_p and e20 < e80:
            stop_px = c + (2.5 * atr)
            events.append((df.index[i], "SELL", stop_px, None, "TRAIL_ATR", 2.5))
    return events

def gen_signals_lien(sym, df):
    events = []
    for i in range(125, len(df) - 5):
        c = df["close"].iloc[i]
        s120 = df["sma120"].iloc[i]
        e20 = df["ema20"].iloc[i]
        rsi = df["rsi14"].iloc[i]
        rsi_p = df["rsi14"].iloc[i-1]
        macd_h = df["macd_hist"].iloc[i]
        atr = df["atr14"].iloc[i]

        # Uptrend: Close > 120 SMA, RSI crosses back above 50, MACD positive
        if c > s120 and rsi_p < 50 and rsi >= 50 and macd_h > 0:
            stop_dist = 1.5 * atr
            stop_px = c - stop_dist
            target_px = c + (2.0 * stop_dist)
            events.append((df.index[i], "BUY", stop_px, target_px, None, 0.0))
        elif c < s120 and rsi_p > 50 and rsi <= 50 and macd_h < 0:
            stop_dist = 1.5 * atr
            stop_px = c + stop_dist
            target_px = c - (2.0 * stop_dist)
            events.append((df.index[i], "SELL", stop_px, target_px, None, 0.0))
    return events

def gen_signals_bollinger(sym, df):
    events = []
    for i in range(25, len(df) - 5):
        c = df["close"].iloc[i]
        c_prev = df["close"].iloc[i-1]
        u20 = df["bb20_upper_20s"].iloc[i]
        l20 = df["bb20_lower_20s"].iloc[i]
        u20_p = df["bb20_upper_20s"].iloc[i-1]
        l20_p = df["bb20_lower_20s"].iloc[i-1]

        # 2.0 Sigma Reclaim
        if c_prev < l20_p and c >= l20:
            stop_px = df["bb20_lower_30s"].iloc[i]
            target_px = df["sma20"].iloc[i]
            events.append((df.index[i], "BUY", stop_px, target_px, None, 0.0))
        elif c_prev > u20_p and c <= u20:
            stop_px = df["bb20_upper_30s"].iloc[i]
            target_px = df["sma20"].iloc[i]
            events.append((df.index[i], "SELL", stop_px, target_px, None, 0.0))
    return events

def gen_signals_adx(sym, df):
    events = []
    for i in range(25, len(df) - 5):
        adx = df["adx14"].iloc[i]
        adx_p = df["adx14"].iloc[i-1]
        c = df["close"].iloc[i]
        h20 = df["donch20_h"].iloc[i]
        l20 = df["donch20_l"].iloc[i]
        atr = df["atr14"].iloc[i]

        if adx > 25 and adx > adx_p:
            if c > h20:
                stop_dist = 1.5 * atr
                stop_px = c - stop_dist
                target_px = c + (3.0 * stop_dist)
                events.append((df.index[i], "BUY", stop_px, target_px, None, 0.0))
            elif c < l20:
                stop_dist = 1.5 * atr
                stop_px = c + stop_dist
                target_px = c - (3.0 * stop_dist)
                events.append((df.index[i], "SELL", stop_px, target_px, None, 0.0))
    return events

def gen_signals_session(sym, df):
    events = []
    # London Open is 08:00 UTC bar. Asian range is 00:00 to 08:00 UTC (preceding 2 H4 bars)
    for i in range(10, len(df) - 5):
        t = df.index[i]
        if t.hour == 8:
            asian_h = max(df["high"].iloc[i-2], df["high"].iloc[i-1])
            asian_l = min(df["low"].iloc[i-2], df["low"].iloc[i-1])
            c = df["close"].iloc[i]
            atr = df["atr14"].iloc[i]

            if c > asian_h:
                stop_dist = max(1.5 * atr, c - asian_l)
                stop_px = c - stop_dist
                target_px = c + (2.0 * stop_dist)
                events.append((t, "BUY", stop_px, target_px, None, 0.0))
            elif c < asian_l:
                stop_dist = max(1.5 * atr, asian_h - c)
                stop_px = c + stop_dist
                target_px = c - (2.0 * stop_dist)
                events.append((t, "SELL", stop_px, target_px, None, 0.0))
    return events

SYSTEMS = [
    ("F0 Control (Frozen)", gen_signals_f0),
    ("Oliver Velez 20/200", gen_signals_velez),
    ("Turtle Donchian (55/20)", gen_signals_turtle),
    ("Carver Systematic Trend", gen_signals_carver),
    ("Kathy Lien Momentum", gen_signals_lien),
    ("Bollinger 20/2.0 Reversion", gen_signals_bollinger),
    ("ADX Trend Breakout", gen_signals_adx),
    ("London Session Breakout", gen_signals_session)
]

# --- Chronological Portfolio Simulation Engine ---
all_times = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
time_to_idx = {p: {t: i for i, t in enumerate(dfs[p].index)} for p in dfs}

def simulate_system(system_name, gen_func):
    raw_trades = []
    for sym in PAIRS:
        df = dfs[sym]
        pip_val = get_pip_value(sym)
        half_spread = (SPREAD_PIPS_MAP.get(sym, 1.5) / 2.0) * pip_val
        entry_slip = ENTRY_SLIPPAGE_PIPS * pip_val
        exit_slip = EXIT_SLIPPAGE_PIPS * pip_val

        sigs = gen_func(sym, df)
        for t, side, stop_px, target_px, trail_type, trail_param in sigs:
            idx = df.index.get_loc(t)
            if idx >= len(df) - 5:
                continue

            entry_idx = idx + 1
            entry_time = df.index[entry_idx]
            raw_open = float(df["open"].iloc[entry_idx])

            if side == "BUY":
                entry_px = raw_open + half_spread + entry_slip
                if stop_px >= entry_px or (target_px and target_px <= entry_px):
                    continue
            else:
                entry_px = raw_open - half_spread - entry_slip
                if stop_px <= entry_px or (target_px and target_px >= entry_px):
                    continue

            stop_dist = abs(entry_px - stop_px)
            exit_px, bars_held, exit_time = evaluate_path(
                df, entry_idx, side, entry_px, stop_px, target_px, pip_val, half_spread, exit_slip,
                trail_atr=trail_type, trail_dist=trail_param
            )

            quote_to_usd = get_quote_to_usd_rate(sym, entry_time)
            target_dollar_risk = 500.0
            usd_risk_per_unit = stop_dist * quote_to_usd
            units = target_dollar_risk / usd_risk_per_unit if usd_risk_per_unit > 0 else 0
            quote_pnl = (exit_px - entry_px) * units if side == "BUY" else (entry_px - exit_px) * units
            net_pnl_usd = quote_pnl * quote_to_usd
            r_mult = net_pnl_usd / target_dollar_risk

            raw_trades.append({
                "pair": sym, "side": side, "entry_time": entry_time, "exit_time": exit_time,
                "entry_px": entry_px, "exit_px": exit_px, "stop_dist": stop_dist,
                "r_mult": r_mult, "bars_held": bars_held
            })

    if not raw_trades:
        return {"System": system_name, "N": 0, "CAGR": "+0.00%", "MaxDD": "0.00%", "Calmar": 0.0, "PF": 0.0, "Mean_R": "+0.000R", "WR": "0.0%"}

    events_df = pd.DataFrame(raw_trades).sort_values("entry_time").reset_index(drop=True)

    # Master Chronological Simulation with 0.50% NAV & Factor Firewall
    cash = INITIAL_NAV
    active_pos = {}
    closed_trades = []
    equity_curve = []
    pid_c = 0

    entry_map = {}
    for _, row in events_df.iterrows():
        entry_map.setdefault(row["entry_time"], []).append(row)

    for current_time in all_times:
        # Exits
        for pid in list(active_pos.keys()):
            pos = active_pos[pid]
            if current_time >= pos["exit_time"]:
                quote_to_usd = get_quote_to_usd_rate(pos["pair"], pos["exit_time"])
                pnl_per_unit = (pos["exit_px"] - pos["entry_px"]) if pos["side"] == "BUY" else (pos["entry_px"] - pos["exit_px"])
                pnl_usd = pnl_per_unit * pos["units"] * quote_to_usd
                cash += pos["margin"] + pnl_usd
                closed_trades.append({**pos, "pnl_usd": pnl_usd})
                del active_pos[pid]

        # MTM Valuation
        unrealized = 0.0
        active_margin = 0.0
        curr_factors = {}

        for pid, pos in active_pos.items():
            pair = pos["pair"]
            active_margin += pos["margin"]
            if current_time in time_to_idx[pair]:
                c_idx = time_to_idx[pair][current_time]
                cur_close = float(dfs[pair]["close"].iloc[c_idx])
                quote_to_usd = get_quote_to_usd_rate(pair, current_time)
                pnl_u = (cur_close - pos["entry_px"]) if pos["side"] == "BUY" else (pos["entry_px"] - cur_close)
                unrealized += pnl_u * pos["units"] * quote_to_usd

            base, quote = pair.split("_")
            d = 1 if pos["side"] == "BUY" else -1
            curr_factors[base] = curr_factors.get(base, 0) + d
            curr_factors[quote] = curr_factors.get(quote, 0) - d

        mtm_nav = cash + active_margin + unrealized
        equity_curve.append({"time": current_time, "mtm_nav": mtm_nav})

        # Entries (Deterministic Tie-Break on Pair/Side)
        if current_time in entry_map:
            cands = sorted(entry_map[current_time], key=lambda x: (x["pair"], x["side"]))
            for cand in cands:
                if len(active_pos) >= MAX_POSITIONS:
                    break
                pair = cand["pair"]
                if any(p["pair"] == pair for p in active_pos.values()):
                    continue

                base, quote = pair.split("_")
                d = 1 if cand["side"] == "BUY" else -1
                if abs(curr_factors.get(base, 0) + d) > MAX_FACTOR_EXPOSURE:
                    continue
                if abs(curr_factors.get(quote, 0) - d) > MAX_FACTOR_EXPOSURE:
                    continue

                quote_to_usd = get_quote_to_usd_rate(pair, current_time)
                risk_usd = mtm_nav * RISK_PCT
                risk_unit_usd = cand["stop_dist"] * quote_to_usd
                if risk_unit_usd <= 0:
                    continue

                units = risk_usd / risk_unit_usd
                margin_req = (units * cand["entry_px"] * quote_to_usd) * 0.0333
                if cash >= margin_req and units > 0:
                    cash -= margin_req
                    pid_c += 1
                    active_pos[pid_c] = {
                        "pair": pair, "side": cand["side"], "entry_time": cand["entry_time"],
                        "exit_time": cand["exit_time"], "entry_px": cand["entry_px"],
                        "exit_px": cand["exit_px"], "units": units, "margin": margin_req,
                        "r_mult": cand["r_mult"]
                    }
                    curr_factors[base] = curr_factors.get(base, 0) + d
                    curr_factors[quote] = curr_factors.get(quote, 0) - d

    nav_df = pd.DataFrame(equity_curve).set_index("time")
    nav_df["peak"] = nav_df["mtm_nav"].cummax()
    nav_df["dd_pct"] = (nav_df["mtm_nav"] - nav_df["peak"]) / nav_df["peak"] * 100.0

    t_df = pd.DataFrame(closed_trades)
    if t_df.empty:
        return {"System": system_name, "N": 0, "CAGR": "+0.00%", "MaxDD": "0.00%", "Calmar": 0.0, "PF": 0.0, "Mean_R": "+0.000R", "WR": "0.0%"}

    wins = t_df[t_df["r_mult"] > 0]["r_mult"].sum()
    losses = abs(t_df[t_df["r_mult"] <= 0]["r_mult"].sum())
    pf = wins / losses if losses > 0 else float("inf")

    start = nav_df["mtm_nav"].iloc[0]
    end = nav_df["mtm_nav"].iloc[-1]
    years = (nav_df.index[-1] - nav_df.index[0]).days / 365.25
    cagr = ((end / start) ** (1.0 / years) - 1.0) * 100.0 if years > 0 else 0.0
    max_dd = nav_df["dd_pct"].min()
    calmar = cagr / abs(max_dd) if max_dd != 0 else 0.0
    mean_r = t_df["r_mult"].mean()
    wr = (t_df["r_mult"] > 0).mean() * 100.0

    return {
        "System": system_name,
        "N": len(t_df),
        "CAGR": f"{cagr:+6.2f}%",
        "MaxDD": f"{max_dd:6.2f}%",
        "Calmar": f"{calmar:5.2f}",
        "PF": f"{pf:4.2f}",
        "Mean_R": f"{mean_r:+6.3f}R",
        "WR": f"{wr:4.1f}%"
    }

print("\n" + "=" * 95)
print("RUNNING FOREX EXPERT-SYSTEM TOURNAMENT (OANDA H4 | 2023 - 2026)")
print("=" * 95)

results = []
for name, func in SYSTEMS:
    res = simulate_system(name, func)
    results.append(res)
    print(f"Finished: {name:<28} | Trades: {res['N']:<4} | PF: {res['PF']} | CAGR: {res['CAGR']} | MaxDD: {res['MaxDD']}")

print("\n" + "=" * 95)
print("FINAL TOURNAMENT LEADERBOARD (0.50% MTM NAV | Max 3 Pos | Factor Firewall)")
print("=" * 95)
res_df = pd.DataFrame(results)
print(res_df.to_string(index=False))
print("=" * 95)
