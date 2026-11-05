from pathlib import Path
from dataclasses import dataclass
from typing import List, Dict
import pandas as pd
import numpy as np

DATA_DIR = Path("/home/moonrider1968/bots/data/elite20_v31")
SYMBOLS = [
    "AAPL", "AMZN", "AVGO", "CAT", "COP", "COST", "CVX", "GE",
    "GOOGL", "GS", "JPM", "META", "MSFT", "NFLX", "NVDA", "PG",
    "QQQ", "SPY", "WMT", "XOM"
]

EVAL_START = "2021-09-01"
EVAL_END = "2026-09-04"
FRICTION_BPS = 0.0005

@dataclass
class TradeRecord:
    symbol: str
    entry_date: str
    exit_date: str
    net_pnl: float
    initial_risk: float
    scaled_in: bool
    exit_reason: str

class CanonicalG3B5:
    """Canonical G3-B5 Portfolio Capital Governor (Stepped strictly once per session)."""
    def __init__(self, initial_nav: float = 100_000.0):
        self.hwm = initial_nav
        self.current_tier = 0  # 0: 1.0x, 1: 0.85x, 2: 0.50x, 3: 0.15x
        self.dwell_days = 0
        self.mults = {0: 1.00, 1: 0.85, 2: 0.50, 3: 0.15}

    def get_mult(self) -> float:
        return self.mults[self.current_tier]

    def step_session(self, current_nav: float):
        if current_nav > self.hwm:
            self.hwm = current_nav

        dd = (self.hwm - current_nav) / self.hwm if self.hwm > 0 else 0.0

        target_tier = 0
        if dd >= 0.125:
            target_tier = 3
        elif dd >= 0.085:
            target_tier = 2
        elif dd >= 0.050:
            target_tier = 1

        if target_tier > self.current_tier:
            # Escalation is immediate
            self.current_tier = target_tier
            self.dwell_days = 0
        elif target_tier < self.current_tier:
            # De-escalation: requires 2.5% recovery buffer + 2 consecutive days
            hysteresis_met = False
            if self.current_tier == 3 and dd < (0.125 - 0.025):
                hysteresis_met = True
            elif self.current_tier == 2 and dd < (0.085 - 0.025):
                hysteresis_met = True
            elif self.current_tier == 1 and dd < (0.050 - 0.025):
                hysteresis_met = True

            if hysteresis_met:
                self.dwell_days += 1
                if self.dwell_days >= 2:
                    self.current_tier = target_tier
                    self.dwell_days = 0
            else:
                self.dwell_days = 0

def load_clean_data():
    dfs = {}
    for s in SYMBOLS:
        p = DATA_DIR / f"{s}.csv"
        if not p.exists():
            continue
        df = pd.read_csv(p)
        df["date"] = pd.to_datetime(df["date"])
        df = df.set_index("date").sort_index()

        df["high_55"] = df["high"].shift(1).rolling(55).max()
        df["low_20"] = df["low"].shift(1).rolling(20).min()

        prev_c = df["close"].shift(1)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - prev_c).abs(),
            (df["low"] - prev_c).abs()
        ], axis=1).max(axis=1)
        df["atr14"] = tr.rolling(14).mean()
        dfs[s] = df
    return dfs

def run_simulation(dfs: Dict[str, pd.DataFrame], mode: str = "A", use_governor: bool = False):
    all_dates = sorted(list(set.union(*[set(df.index) for df in dfs.values()])))
    eval_dates = [d for d in all_dates if pd.Timestamp(EVAL_START) <= d <= pd.Timestamp(EVAL_END)]

    completed_trades: List[TradeRecord] = []
    active: Dict[str, dict] = {}
    
    cash = 100_000.0
    equity_curve = []
    gov = CanonicalG3B5(cash) if use_governor else None

    for i in range(len(eval_dates) - 1):
        dt = eval_dates[i]
        next_dt = eval_dates[i + 1]
        dt_str = str(dt.date())
        next_dt_str = str(next_dt.date())

        # Current MTM NAV calculation
        mtm_open = 0.0
        for s, pos in active.items():
            s_df = dfs[s]
            cur_c = float(s_df.loc[dt]["close"]) if dt in s_df.index else pos["entry_px_l1"]
            mtm_open += (cur_c - pos["entry_px_l1"]) * pos["units_l1"]
            if pos["units_l2"] > 0:
                mtm_open += (cur_c - pos["entry_px_l2"]) * pos["units_l2"]
        current_nav = cash + mtm_open

        # Step governor once per session
        if gov:
            gov.step_session(current_nav)
        active_mult = gov.get_mult() if gov else 1.0

        # -------------------------------------------------------------
        # 1. PROCESS STAGED ORDERS ON NEXT OPEN (T+1)
        # -------------------------------------------------------------
        # A. Fills for pending exits staged at Day T close
        closed_syms = []
        for sym, pos in active.items():
            df = dfs[sym]
            if next_dt not in df.index:
                continue
            next_open = float(df.loc[next_dt]["open"])

            # Channel exit triggered yesterday: execute on open today
            if pos["pending_exit_ch"]:
                exit_px = next_open * (1.0 - FRICTION_BPS)
                pnl_l1 = (exit_px - pos["entry_px_l1"]) * pos["units_l1"]
                pnl_l2 = (exit_px - pos["entry_px_l2"]) * pos["units_l2"] if pos["units_l2"] > 0 else 0.0
                tot_pnl = pnl_l1 + pnl_l2 + pos["realized_l2_pnl"]
                tot_risk = pos["dollar_risk_l1"] + pos.get("dollar_risk_l2", 0.0)

                cash += (pnl_l1 + pnl_l2)
                completed_trades.append(TradeRecord(
                    symbol=sym,
                    entry_date=pos["entry_date"],
                    exit_date=next_dt_str,
                    net_pnl=tot_pnl,
                    initial_risk=tot_risk,
                    scaled_in=pos["scaled_in"],
                    exit_reason="CH_20D"
                ))
                closed_syms.append(sym)
                continue

            # Scale-in fill staged yesterday
            if mode == "B" and pos["pending_scale_in"]:
                fill_px_l2 = next_open * (1.0 + FRICTION_BPS)
                risk_l2 = current_nav * 0.0050 * active_mult
                units_l2 = int(risk_l2 / pos["r_dist"])
                if units_l2 > 0:
                    pos["units_l2"] = units_l2
                    pos["entry_px_l2"] = fill_px_l2
                    pos["stop_l1"] = pos["entry_px_l1"] * (1.0 + FRICTION_BPS)
                    pos["stop_l2"] = pos["entry_px_l1"]
                    pos["dollar_risk_l2"] = risk_l2
                    pos["scaled_in"] = True
                pos["pending_scale_in"] = False

        for s in closed_syms:
            del active[s]

        # -------------------------------------------------------------
        # 2. EVALUATE INTRADAY STOPS & EOD SIGNALS ON DAY T+1
        # -------------------------------------------------------------
        closed_intra = []
        for sym, pos in active.items():
            df = dfs[sym]
            if next_dt not in df.index:
                continue

            bar = df.loc[next_dt]
            o, h, l, c = float(bar["open"]), float(bar["high"]), float(bar["low"]), float(bar["close"])
            low_20 = float(bar["low_20"])
            r_dist = pos["r_dist"]

            # Check Lot 2 Stop
            if pos["units_l2"] > 0:
                if l <= pos["stop_l2"]:
                    # Handle adverse open gap
                    fill_l2 = min(o, pos["stop_l2"]) * (1.0 - FRICTION_BPS)
                    pnl_l2 = (fill_l2 - pos["entry_px_l2"]) * pos["units_l2"]
                    cash += pnl_l2
                    pos["realized_l2_pnl"] += pnl_l2
                    pos["units_l2"] = 0

            # Check Lot 1 Stop (Liquidates container)
            if l <= pos["stop_l1"]:
                fill_l1 = min(o, pos["stop_l1"]) * (1.0 - FRICTION_BPS)
                pnl_l1 = (fill_l1 - pos["entry_px_l1"]) * pos["units_l1"]
                cash += pnl_l1

                # If Lot 2 was still active when Lot 1 stopped
                if pos["units_l2"] > 0:
                    fill_l2 = min(o, pos["stop_l2"]) * (1.0 - FRICTION_BPS)
                    pnl_l2_rem = (fill_l2 - pos["entry_px_l2"]) * pos["units_l2"]
                    cash += pnl_l2_rem
                    pos["realized_l2_pnl"] += pnl_l2_rem
                    pos["units_l2"] = 0

                tot_pnl = pnl_l1 + pos["realized_l2_pnl"]
                tot_risk = pos["dollar_risk_l1"] + pos.get("dollar_risk_l2", 0.0)
                completed_trades.append(TradeRecord(
                    symbol=sym,
                    entry_date=pos["entry_date"],
                    exit_date=next_dt_str,
                    net_pnl=tot_pnl,
                    initial_risk=tot_risk,
                    scaled_in=pos["scaled_in"],
                    exit_reason="STOP"
                ))
                closed_intra.append(sym)
                continue

            # Check for Day T+1 Channel Exit Trigger (Stages exit for next open)
            if c < low_20:
                pos["pending_exit_ch"] = True

            # Check for Scale-In Signal on Day T+1 Close
            if mode == "B" and not pos["scaled_in"] and not pos["pending_scale_in"]:
                if c >= (pos["entry_px_l1"] + r_dist):
                    pos["pending_scale_in"] = True

        for s in closed_intra:
            del active[s]

        # -------------------------------------------------------------
        # 3. TRUE END-OF-SESSION T+1 MARK-TO-MARKET NAV
        # -------------------------------------------------------------
        mtm_eod = 0.0
        for s, pos in active.items():
            s_df = dfs[s]
            if next_dt not in s_df.index:
                continue
            close_px = float(s_df.loc[next_dt]["close"])
            mtm_eod += (close_px - pos["entry_px_l1"]) * pos["units_l1"]
            if pos["units_l2"] > 0:
                mtm_eod += (close_px - pos["entry_px_l2"]) * pos["units_l2"]

        eod_nav = cash + mtm_eod
        equity_curve.append({"date": next_dt_str, "nav": eod_nav})

        # -------------------------------------------------------------
        # 4. SCAN NEW BREAKOUTS ON DAY T+1 CLOSE -> STAGE FOR T+2 OPEN
        # -------------------------------------------------------------
        if (i + 2) < len(eval_dates):
            t2_dt = eval_dates[i + 2]
            t2_dt_str = str(t2_dt.date())

            for sym in SYMBOLS:
                if sym in active or len(active) >= 3:
                    continue
                df = dfs.get(sym)
                if df is None or next_dt not in df.index or t2_dt not in df.index:
                    continue

                idx = df.index.get_loc(next_dt)
                if idx < 1:
                    continue

                cur_bar = df.iloc[idx]
                prev_bar = df.iloc[idx - 1]

                if cur_bar["close"] > cur_bar["high_55"] and prev_bar["close"] <= prev_bar["high_55"]:
                    atr = float(cur_bar["atr14"])
                    if pd.isna(atr) or atr <= 0:
                        continue

                    r_dist = 1.5 * atr
                    dollar_risk = eod_nav * 0.0050 * active_mult
                    units = int(dollar_risk / r_dist)
                    if units < 1:
                        continue

                    t2_open = float(dfs[sym].loc[t2_dt]["open"])
                    fill_px = t2_open * (1.0 + FRICTION_BPS)

                    active[sym] = {
                        "entry_date": t2_dt_str,
                        "entry_px_l1": fill_px,
                        "r_dist": r_dist,
                        "units_l1": units,
                        "stop_l1": fill_px - r_dist,
                        "units_l2": 0,
                        "entry_px_l2": 0.0,
                        "stop_l2": 0.0,
                        "dollar_risk_l1": dollar_risk,
                        "scaled_in": False,
                        "pending_scale_in": False,
                        "pending_exit_ch": False,
                        "realized_l2_pnl": 0.0
                    }

    return completed_trades, pd.DataFrame(equity_curve)

def print_audit(trades: List[TradeRecord], eq_df: pd.DataFrame, label: str):
    tdf = pd.DataFrame([t.__dict__ for t in trades])
    if tdf.empty:
        print(f"{label}: No trades.")
        return

    wins = tdf[tdf["net_pnl"] > 0]
    losses = tdf[tdf["net_pnl"] <= 0]
    wr = len(wins) / len(tdf) * 100.0

    gp = wins["net_pnl"].sum()
    gl = abs(losses["net_pnl"].sum())
    pf = gp / gl if gl > 0 else 999.0

    final_nav = eq_df["nav"].iloc[-1]
    years = (pd.to_datetime(eq_df["date"].iloc[-1]) - pd.to_datetime(eq_df["date"].iloc[0])).days / 365.25
    cagr = ((final_nav / 100_000.0) ** (1.0 / years) - 1.0) * 100.0

    eq_df["hwm"] = eq_df["nav"].cummax()
    eq_df["dd"] = (eq_df["hwm"] - eq_df["nav"]) / eq_df["hwm"]
    max_dd = eq_df["dd"].max() * 100.0

    rets = eq_df["nav"].pct_change().dropna()
    sharpe = (rets.mean() / rets.std()) * np.sqrt(252) if rets.std() > 0 else 0.0

    r_multiples = tdf["net_pnl"] / tdf["initial_risk"]
    scaled_trades = tdf[tdf["scaled_in"]]

    print("=" * 90)
    print(f"FINAL AUDIT: {label}")
    print("=" * 90)
    print(f"Total Completed Trades:  {len(tdf)}")
    print(f"Scaled-In Trades:        {len(scaled_trades)} ({len(scaled_trades)/len(tdf)*100:.1f}%)")
    print(f"Win Rate:                {wr:.2f}% ({len(wins)}W / {len(losses)}L)")
    print(f"Profit Factor (PF):      {pf:.3f}")
    print(f"Total Net R:             {r_multiples.sum():+.2f}R")
    print(f"Mean Expectancy:         {r_multiples.mean():+.3f}R / trade")
    print(f"Final Account NAV ($):   ${final_nav:,.2f}")
    print(f"Compounded CAGR:         {cagr:.2f}%")
    print(f"Max Mark-to-Market DD:   {max_dd:.2f}%")
    print(f"Daily MTM Sharpe Ratio:  {sharpe:.2f}")
    print("=" * 90)

if __name__ == "__main__":
    dfs = load_clean_data()

    # 1. Book A (Canonical 1-Lot Control)
    t_a, eq_a = run_simulation(dfs, mode="A", use_governor=False)
    print_audit(t_a, eq_a, "BOOK A: CANONICAL 1-LOT CONTROL")

    # 2. Book B (Canonical Scale-In: Ungoverned)
    t_b, eq_b = run_simulation(dfs, mode="B", use_governor=False)
    print_audit(t_b, eq_b, "BOOK B: CANONICAL SCALE-IN (UNGOVERNED)")

    # 3. Book B-G3 (Canonical Scale-In + Canonical G3-B5)
    t_bg, eq_bg = run_simulation(dfs, mode="B", use_governor=True)
    print_audit(t_bg, eq_bg, "BOOK B-G3: CANONICAL SCALE-IN + G3-B5 GOVERNOR")
