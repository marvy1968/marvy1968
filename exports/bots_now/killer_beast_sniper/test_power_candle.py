#!/usr/bin/env python3
import numpy as np
import pandas as pd

class PowerCandleBacktester:
    def __init__(self, initial_balance=10000.0, risk_pct=0.01):
        self.initial_balance = initial_balance
        self.risk_pct = risk_pct

    def generate_session_data(self):
        np.random.seed(42)
        periods = 1200
        price = 100.0
        prices, highs, lows, opens, volumes = [], [], [], [], []

        for i in range(periods):
            drift = 0.16 if 150 < i < 450 else -0.22 if 550 < i < 750 else 0.42 if 850 < i < 1100 else -0.01
            price += np.random.normal(0, 0.4) + drift
            prices.append(price)
            opens.append(price + np.random.uniform(-0.15, 0.15))
            highs.append(max(price, opens[-1]) + np.random.uniform(0.01, 0.3))
            lows.append(min(price, opens[-1]) - np.random.uniform(0.01, 0.3))
            
            # Simulate high-volume clusters near breakout points
            vol_mult = 3.0 if (148 < i < 160 or 848 < i < 865) else 1.0
            volumes.append(np.random.randint(100, 1000) * vol_mult)

        return pd.DataFrame({"open": opens, "high": highs, "low": lows, "close": prices, "volume": volumes})

    def calculate_adx(self, df: pd.DataFrame, period: int = 14):
        high, low, close = df["high"], df["low"], df["close"]
        up_move = high - high.shift(1)
        down_move = low.shift(1) - low

        plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
        minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)

        tr = pd.concat([high - low, (high - close.shift(1)).abs(), (low - close.shift(1)).abs()], axis=1).max(axis=1)
        atr = tr.ewm(alpha=1/period, adjust=False).mean()

        plus_di = 100 * (pd.Series(plus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / (atr + 1e-9))
        minus_di = 100 * (pd.Series(minus_dm, index=df.index).ewm(alpha=1/period, adjust=False).mean() / (atr + 1e-9))

        dx = 100 * ((plus_di - minus_di).abs() / ((plus_di + minus_di) + 1e-9))
        adx = dx.ewm(alpha=1/period, adjust=False).mean()

        return adx, plus_di, minus_di

    def run_backtest(self, df: pd.DataFrame, use_confluence: bool = False):
        df = df.copy()
        
        # Technical Indicators
        df["sma20"] = df["close"].rolling(20).mean()
        df["sma200"] = df["close"].rolling(200).mean()
        df["vol_sma20"] = df["volume"].rolling(20).mean()
        
        # 20 SMA Slope (5-bar lookback)
        df["sma20_slope"] = ((df["sma20"] - df["sma20"].shift(5)) / (df["sma20"].shift(5) + 1e-9)) * 100

        # ADX & +DI/-DI
        df["adx"], df["plus_di"], df["minus_di"] = self.calculate_adx(df, period=14)

        # Baseline Trigger: Close > 20 SMA & Volume > 2.0x Average
        baseline_trigger = (df["close"] > df["sma20"]) & (df["volume"] > 2.0 * df["vol_sma20"])

        if use_confluence:
            # 4-Indicator Confluence Filter:
            # 1. Price > 20 SMA > 200 SMA (Macro Stage 2)
            # 2. 20 SMA Slope > +0.10% (Micro trajectory)
            # 3. Volume Surge > 2.0x
            # 4. ADX >= 25.0 and +DI > -DI (Directional Power)
            confluence_filter = (
                (df["close"] > df["sma20"]) & 
                (df["sma20"] > df["sma200"]) & 
                (df["sma20_slope"] > 0.10) & 
                (df["adx"] >= 25.0) & 
                (df["plus_di"] > df["minus_di"])
            )
            df["entry_signal"] = baseline_trigger & confluence_filter
        else:
            df["entry_signal"] = baseline_trigger

        balance = self.initial_balance
        equity_curve = [balance]
        trades = []
        in_position = False
        entry_price = 0.0
        stop_loss = 0.0
        position_size = 0

        for i in range(200, len(df)):
            curr = df.iloc[i]

            if not in_position and curr["entry_signal"]:
                in_position = True
                entry_price = curr["close"]
                stop_loss = curr["low"]
                risk_per_share = max(0.05, entry_price - stop_loss)
                risk_amount = balance * self.risk_pct
                position_size = int(risk_amount / risk_per_share)
                continue

            if in_position:
                target_price = entry_price + (2.0 * (entry_price - stop_loss))

                if curr["low"] <= stop_loss:
                    pnl = position_size * (stop_loss - entry_price)
                    balance += pnl
                    trades.append(pnl)
                    in_position = False
                elif curr["high"] >= target_price:
                    pnl = position_size * (target_price - entry_price)
                    balance += pnl
                    trades.append(pnl)
                    in_position = False

            equity_curve.append(balance)

        return self.compute_metrics(trades, equity_curve)

    def compute_metrics(self, trades, equity_curve):
        if not trades:
            return {
                "final_balance": self.initial_balance,
                "net_pnl": 0.0,
                "total_trades": 0,
                "win_rate_pct": 0.0,
                "profit_factor": 0.0,
                "max_drawdown_pct": 0.0,
                "sharpe_ratio": 0.0
            }

        trades_arr = np.array(trades)
        wins = trades_arr[trades_arr > 0]
        losses = trades_arr[trades_arr < 0]

        total_trades = len(trades)
        win_rate = (len(wins) / total_trades) * 100 if total_trades > 0 else 0.0
        gross_profit = np.sum(wins) if len(wins) > 0 else 0.0
        gross_loss = abs(np.sum(losses)) if len(losses) > 0 else 0.0
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else gross_profit

        eq = np.array(equity_curve)
        peak = np.maximum.accumulate(eq)
        drawdown = (eq - peak) / peak
        max_drawdown = float(np.min(drawdown)) * 100

        returns = np.diff(eq) / eq[:-1]
        sharpe = float((np.mean(returns) / (np.std(returns) + 1e-9)) * np.sqrt(252)) if len(returns) > 1 else 0.0

        return {
            "final_balance": round(float(eq[-1]), 2),
            "net_pnl": round(float(eq[-1] - self.initial_balance), 2),
            "total_trades": total_trades,
            "win_rate_pct": round(win_rate, 2),
            "profit_factor": round(profit_factor, 2),
            "max_drawdown_pct": round(max_drawdown, 2),
            "sharpe_ratio": round(sharpe, 2)
        }

if __name__ == "__main__":
    tester = PowerCandleBacktester(initial_balance=10000.0)
    df = tester.generate_session_data()

    baseline_res = tester.run_backtest(df, use_confluence=False)
    confluence_res = tester.run_backtest(df, use_confluence=True)

    print("=" * 70)
    print("      📊 POWER CANDLE BACKTEST: BASELINE vs. 4-INDICATOR CONFLUENCE      ")
    print("=" * 70)
    print(f"{'METRIC':<22} | {'BASELINE (Raw Vol)':<20} | {'4-INDICATOR CONFLUENCE':<20}")
    print("-" * 70)
    for key in baseline_res.keys():
        m_name = key.replace("_", " ").title()
        v_base = str(baseline_res[key])
        v_conf = str(confluence_res[key])
        print(f"{m_name:<22} | {v_base:<20} | {v_conf:<20}")
    print("=" * 70)
