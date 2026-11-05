from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict
import pandas as pd
import numpy as np
import itertools
import time

DATA_DIR = Path("/home/moonrider1968/bots/data/elite20_v31")
SECTOR_MAP = {
    "AAPL": "TECH", "AMZN": "TECH", "AVGO": "TECH", "GOOGL": "TECH",
    "META": "TECH", "MSFT": "TECH", "NFLX": "TECH", "NVDA": "TECH", "QQQ": "TECH",
    "COP": "ENERGY", "CVX": "ENERGY", "XOM": "ENERGY",
    "CAT": "INDUSTRIAL", "GE": "INDUSTRIAL",
    "GS": "FINANCIAL", "JPM": "FINANCIAL",
    "COST": "STAPLES", "PG": "STAPLES", "WMT": "STAPLES",
    "SPY": "BROAD"
}
SYMBOLS = list(SECTOR_MAP.keys())
EVAL_START = "2021-09-01"
EVAL_END = "2026-09-04"
FRICTION_BPS = 0.0005

def load_and_preprocess():
    dfs = {}
    for s in SYMBOLS:
        p = DATA_DIR / f"{s}.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()

        # Daily Breakouts & Channel Exits
        df["high_20"] = df["high"].shift(1).rolling(20).max()
        df["high_55"] = df["high"].shift(1).rolling(55).max()
        df["low_20"] = df["low"].shift(1).rolling(20).min()
        
        # ATR14
        prev_c = df["close"].shift(1)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_c).abs(),
            (df["low"] - prev_c).abs()
        ], axis=1).max(axis=1)
        df["atr14"] = tr.rolling(14).mean()
        df["natr"] = df["atr14"] / df["close"]

        # RVOL (20D Vol / 60D Vol)
        vol_sma20 = df["volume"].rolling(20).mean()
        vol_sma60 = df["volume"].rolling(60).mean()
        df["rvol"] = vol_sma20 / vol_sma60.replace(0, np.nan)

        # Daily Trend Alignment: Close > 20 SMA & 20 SMA > 50 SMA
        df["sma20_d"] = df["close"].rolling(20).mean()
        df["sma50_d"] = df["close"].rolling(50).mean()
        df["daily_trend_ok"] = (df["close"] > df["sma20_d"]) & (df["sma20_d"] > df["sma50_d"])

        # Weekly Alignment (Point-in-Time: Last Completed Friday)
        weekly_df = df["close"].resample("W-FRI").last().to_frame()
        weekly_df["w_sma10"] = weekly_df["close"].rolling(10).mean()
        weekly_df["w_sma30"] = weekly_df["close"].rolling(30).mean()
        weekly_df["w_ok"] = (weekly_df["close"] > weekly_df["w_sma10"]) & (weekly_df["w_sma10"] > weekly_df["w_sma30"])
        weekly_df["w_ok_pit"] = weekly_df["w_ok"].shift(1)  # Strictly completed prior week
        df["weekly_trend_ok"] = weekly_df["w_ok_pit"].reindex(df.index, method="ffill").fillna(False)

        # Monthly Alignment (Point-in-Time: Last Completed Month-End)
        monthly_df = df["close"].resample("ME").last().to_frame()
        monthly_df["m_sma10"] = monthly_df["close"].rolling(10).mean()
        monthly_df["m_ok"] = monthly_df["close"] > monthly_df["m_sma10"]
        monthly_df["m_ok_pit"] = monthly_df["m_ok"].shift(1)  # Strictly completed prior month
        df["monthly_trend_ok"] = monthly_df["m_ok_pit"].reindex(df.index, method="ffill").fillna(False)

        # Momentum for Sector Ranking: 50-day rolling returns
        df["ret50"] = df["close"].pct_change(50)

        dfs[s] = df
    return dfs

def precompute_market_state(dfs):
    all_dates = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
    eval_dates = [d for d in all_dates if pd.Timestamp(EVAL_START) <= d <= pd.Timestamp(EVAL_END)]
    
    daily_cache = {}
    for dt in eval_dates:
        # 1. Sector Momentum Ranking on Day T Close
        sector_rets = {}
        for sec in set(SECTOR_MAP.values()):
            syms = [s for s, m in SECTOR_MAP.items() if m == sec and s in dfs and dt in dfs[s].index]
            if syms:
                r_vals = [dfs[s].loc[dt, "ret50"] for s in syms if not pd.isna(dfs[s].loc[dt, "ret50"])]
                sector_rets[sec] = np.mean(r_vals) if r_vals else -999.0
            else:
                sector_rets[sec] = -999.0
        ranked_sectors = [s[0] for s in sorted(sector_rets.items(), key=lambda x: x[1], reverse=True)]

        # 2. Cross-Sectional Volatility Percentile on Day T Close
        natrs = {s: dfs[s].loc[dt, "natr"] for s in SYMBOLS if dt in dfs[s].index and not pd.isna(dfs[s].loc[dt, "natr"])}
        
        daily_cache[dt] = {
            "ranked_sectors": ranked_sectors,
            "natrs": natrs
        }
    return eval_dates, daily_cache

def simulate_config(dfs, eval_dates, daily_cache, top_sectors, tf_mode, breakout_days, rvol_thresh, vol_pct):
    active = {}
    cash = 100_000.0
    completed_trades = []
    equity_curve = []
    hwm = 100_000.0
    max_dd = 0.0

    bo_col = "high_20" if breakout_days == 20 else "high_55"

    for i in range(len(eval_dates) - 1):
        dt = eval_dates[i]
        next_dt = eval_dates[i + 1]

        # 1. Process Channel-Low Exits on T+1 Open
        closed_syms = []
        for sym, pos in active.items():
            if next_dt not in dfs[sym].index:
                continue
            next_open = float(dfs[sym].loc[next_dt]["open"])

            if pos["pending_exit_ch"]:
                exit_px = next_open * (1.0 - FRICTION_BPS)
                pnl = (exit_px - pos["entry_px"]) * pos["units"]
                cash += pnl
                r_mult = pnl / pos["dollar_risk"] if pos["dollar_risk"] > 0 else 0.0
                completed_trades.append(r_mult)
                closed_syms.append(sym)

        for s in closed_syms:
            del active[s]

        # 2. Process Stops on T+1 Intraday & Signal Channel Exits on Close
        closed_intra = []
        for sym, pos in active.items():
            if next_dt not in dfs[sym].index:
                continue
            bar = dfs[sym].loc[next_dt]
            o, l, c = float(bar["open"]), float(bar["low"]), float(bar["close"])
            low_20 = float(bar["low_20"])

            if l <= pos["stop_px"]:
                fill_px = min(o, pos["stop_px"]) * (1.0 - FRICTION_BPS)
                pnl = (fill_px - pos["entry_px"]) * pos["units"]
                cash += pnl
                r_mult = pnl / pos["dollar_risk"] if pos["dollar_risk"] > 0 else 0.0
                completed_trades.append(r_mult)
                closed_intra.append(sym)
                continue

            if c < low_20:
                pos["pending_exit_ch"] = True

        for s in closed_intra:
            del active[s]

        # 3. Mark-to-Market EOD NAV
        mtm_eod = 0.0
        for s, pos in active.items():
            if next_dt in dfs[s].index:
                close_px = float(dfs[s].loc[next_dt]["close"])
                mtm_eod += (close_px - pos["entry_px"]) * pos["units"]
        eod_nav = cash + mtm_eod

        if eod_nav > hwm:
            hwm = eod_nav
        dd = (hwm - eod_nav) / hwm if hwm > 0 else 0.0
        if dd > max_dd:
            max_dd = dd
        equity_curve.append(eod_nav)

        # 4. Scan Breakout Candidates on Day T+1 Close -> Stage for T+2 Open
        if (i + 2) < len(eval_dates):
            t2_dt = eval_dates[i + 2]
            t2_dt_str = str(t2_dt.date())

            if len(active) >= 3:
                continue

            cache_data = daily_cache[next_dt]
            allowed_sectors = cache_data["ranked_sectors"][:top_sectors]
            day_natrs = cache_data["natrs"]
            vol_cutoff = np.percentile(list(day_natrs.values()), vol_pct) if vol_pct < 100 else 999.0

            active_sectors = {SECTOR_MAP[s] for s in active}

            candidates = []
            for sym in SYMBOLS:
                if sym in active or len(active) >= 3:
                    continue
                sec = SECTOR_MAP[sym]
                if sec not in allowed_sectors or sec in active_sectors:
                    continue

                df = dfs[sym]
                if next_dt not in df.index or t2_dt not in df.index:
                    continue

                idx = df.index.get_loc(next_dt)
                if idx < 60:
                    continue

                cur = df.iloc[idx]
                prev = df.iloc[idx - 1]

                # Trend Entry Breakout
                if not (cur["close"] > cur[bo_col] and prev["close"] <= prev[bo_col]):
                    continue

                # Multi-Timeframe Filter
                if not cur["daily_trend_ok"]:
                    continue
                if tf_mode in ["D+W", "D+W+M"] and not cur["weekly_trend_ok"]:
                    continue
                if tf_mode == "D+W+M" and not cur["monthly_trend_ok"]:
                    continue

                # RVOL Filter
                if cur["rvol"] < rvol_thresh:
                    continue

                # Volatility Percentile Filter
                if cur["natr"] > vol_cutoff:
                    continue

                # Valid candidate identified
                atr = float(cur["atr14"])
                if pd.isna(atr) or atr <= 0:
                    continue

                r_dist = 1.5 * atr
                dollar_risk = eod_nav * 0.0050
                units = int(dollar_risk / r_dist)
                if units < 1:
                    continue

                candidates.append((sym, sec, cur["rvol"], r_dist, dollar_risk, units))

            # Deterministic Signal Ranking: Rank by highest RVOL
            candidates.sort(key=lambda x: x[2], reverse=True)

            for sym, sec, rvol, r_dist, dollar_risk, units in candidates:
                if len(active) >= 3 or sec in active_sectors:
                    continue

                t2_open = float(dfs[sym].loc[t2_dt]["open"])
                fill_px = t2_open * (1.0 + FRICTION_BPS)

                active[sym] = {
                    "entry_date": t2_dt_str,
                    "entry_px": fill_px,
                    "r_dist": r_dist,
                    "units": units,
                    "stop_px": fill_px - r_dist,
                    "dollar_risk": dollar_risk,
                    "pending_exit_ch": False
                }
                active_sectors.add(sec)

    # Compile Configuration Performance
    num_trades = len(completed_trades)
    if num_trades == 0:
        return {"trades": 0, "cagr": 0.0, "max_dd": 0.0, "sharpe": 0.0, "net_r": 0.0, "wr": 0.0, "pf": 0.0}

    wins = [r for r in completed_trades if r > 0]
    losses = [r for r in completed_trades if r <= 0]
    wr = len(wins) / num_trades * 100.0

    gp = sum(wins)
    gl = abs(sum(losses))
    pf = gp / gl if gl > 0 else 999.0

    final_nav = equity_curve[-1] if equity_curve else 100_000.0
    years = (eval_dates[-1] - eval_dates[0]).days / 365.25
    cagr = ((final_nav / 100_000.0) ** (1.0 / years) - 1.0) * 100.0

    eq_series = pd.Series(equity_curve)
    rets = eq_series.pct_change().dropna()
    sharpe = (rets.mean() / rets.std()) * np.sqrt(252) if rets.std() > 0 else 0.0

    return {
        "trades": num_trades,
        "cagr": round(cagr, 2),
        "max_dd": round(max_dd * 100.0, 2),
        "sharpe": round(sharpe, 2),
        "net_r": round(sum(completed_trades), 2),
        "wr": round(wr, 1),
        "pf": round(pf, 2)
    }

def run_grid():
    dfs = load_and_preprocess()
    eval_dates, daily_cache = precompute_market_state(dfs)

    sector_opts = [2, 3, 4]
    tf_opts = ["D", "D+W", "D+W+M"]
    breakout_opts = [20, 55]
    rvol_opts = [1.0, 1.2, 1.5]
    vol_opts = [100, 60, 40, 25]  # 100 = No filter

    configs = list(itertools.product(sector_opts, tf_opts, breakout_opts, rvol_opts, vol_opts))
    total_runs = len(configs)
    print(f"Launching Phase 1 Grid Search: {total_runs} Configurations...")

    results = []
    t0 = time.time()
    for idx, (sec, tf, bo, rvol, vol) in enumerate(configs):
        res = simulate_config(dfs, eval_dates, daily_cache, sec, tf, bo, rvol, vol)
        res.update({
            "top_sectors": sec,
            "tf": tf,
            "breakout": f"{bo}D",
            "rvol": rvol,
            "vol_cap": "None" if vol == 100 else f"<={vol}%"
        })
        results.append(res)
        if (idx + 1) % 36 == 0:
            print(f"  Processed {idx + 1}/{total_runs} ({(idx + 1)/total_runs*100:.1f}%) in {time.time() - t0:.1f}s")

    res_df = pd.DataFrame(results)
    res_df.to_csv("/home/moonrider1968/bots/str_v1_discovery_results.csv", index=False)

    print("\n" + "=" * 90)
    print("PHASE 1: TOP 10 CONFIGURATIONS BY DAILY SHARPE RATIO")
    print("=" * 90)
    top_sharpe = res_df.sort_values(by="sharpe", ascending=False).head(10)
    print(top_sharpe[["top_sectors", "tf", "breakout", "rvol", "vol_cap", "trades", "cagr", "max_dd", "sharpe", "net_r", "wr", "pf"]].to_string(index=False))

    print("\n" + "=" * 90)
    print("PHASE 1: TOP 10 CONFIGURATIONS BY NET R EDGE")
    print("=" * 90)
    top_netr = res_df.sort_values(by="net_r", ascending=False).head(10)
    print(top_netr[["top_sectors", "tf", "breakout", "rvol", "vol_cap", "trades", "cagr", "max_dd", "sharpe", "net_r", "wr", "pf"]].to_string(index=False))

    print("\n" + "=" * 90)
    print("PARAMETER NEIGHBORHOOD AVERAGES")
    print("=" * 90)
    print("By Timeframe Alignment:")
    print(res_df.groupby("tf")[["cagr", "max_dd", "sharpe", "net_r", "trades"]].mean().round(2).to_string())
    print("\nBy Breakout Window:")
    print(res_df.groupby("breakout")[["cagr", "max_dd", "sharpe", "net_r", "trades"]].mean().round(2).to_string())
    print("\nBy Volatility Filter:")
    print(res_df.groupby("vol_cap")[["cagr", "max_dd", "sharpe", "net_r", "trades"]].mean().round(2).to_string())
    print("\nBy Sector Breadth:")
    print(res_df.groupby("top_sectors")[["cagr", "max_dd", "sharpe", "net_r", "trades"]].mean().round(2).to_string())
    print("=" * 90)

if __name__ == "__main__":
    run_grid()
