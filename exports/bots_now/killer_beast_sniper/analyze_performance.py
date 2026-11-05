
# ALPACA_PAPER_ONLY_GUARD_V1
import sys as _alpaca_guard_sys
from pathlib import Path as _AlpacaGuardPath
_alpaca_guard_home = _AlpacaGuardPath.home()
if str(_alpaca_guard_home / 'bots') not in _alpaca_guard_sys.path:
    _alpaca_guard_sys.path.insert(0, str(_alpaca_guard_home / 'bots'))
from alpaca_paper_only_guard import enforce_paper_only as _enforce_alpaca_paper_only
_enforce_alpaca_paper_only(__file__)
# END ALPACA_PAPER_ONLY_GUARD_V1

import sys
import re
import requests
from collections import defaultdict

sys.path.append("/home/moonrider1968/bots/killer_beast_sniper")
from alpaca_paper_executor import HEADERS, ALPACA_PAPER_URL, LOG_FILE

def fetch_alpaca_closed_orders():
    url = f"{ALPACA_PAPER_URL}/v2/orders?status=closed&limit=500"
    try:
        r = requests.get(url, headers=HEADERS, timeout=10)
        if r.status_code == 200:
            return r.json()
        else:
            print(f"⚠️ Failed to fetch closed orders: {r.status_code}")
    except Exception as e:
        print(f"⚠️ Exception fetching orders: {e}")
    return []

def calculate_realized_pnl(closed_orders):
    # Filter for filled orders that have price data
    filled_orders = [o for o in closed_orders if o.get("status") == "filled" and o.get("filled_avg_price")]
    
    # Sort chronologically by execution / submission time
    filled_orders.sort(key=lambda x: x.get("filled_at") or x.get("submitted_at") or "")

    ticker_pnl = defaultdict(lambda: {"pnl": 0.0, "wins": 0, "losses": 0, "breakeven": 0, "matched_pairs": 0})
    inventory = defaultdict(list)

    total_realized_pnl = 0.0
    total_wins = 0
    total_losses = 0

    for o in filled_orders:
        ticker = o.get("symbol")
        side = o.get("side")
        qty = float(o.get("filled_qty", 0) or 0)
        price = float(o.get("filled_avg_price", 0) or 0)

        if qty <= 0 or price <= 0:
            continue

        inv = inventory[ticker]

        # Match current fill against opposite side inventory (FIFO)
        while qty > 0 and inv and inv[0]["side"] != side:
            match_lot = inv[0]
            matched_qty = min(qty, match_lot["qty"])

            # Calculate realized PnL
            if match_lot["side"] == "buy" and side == "sell":
                pnl = (price - match_lot["price"]) * matched_qty
            elif match_lot["side"] == "sell" and side == "buy":
                pnl = (match_lot["price"] - price) * matched_qty
            else:
                pnl = 0.0

            ticker_pnl[ticker]["pnl"] += pnl
            ticker_pnl[ticker]["matched_pairs"] += 1
            total_realized_pnl += pnl

            if pnl > 0.0001:
                ticker_pnl[ticker]["wins"] += 1
                total_wins += 1
            elif pnl < -0.0001:
                ticker_pnl[ticker]["losses"] += 1
                total_losses += 1
            else:
                ticker_pnl[ticker]["breakeven"] += 1

            qty -= matched_qty
            match_lot["qty"] -= matched_qty

            if match_lot["qty"] <= 0.000001:
                inv.pop(0)

        # Store any remaining unmatched shares
        if qty > 0.000001:
            inv.append({"price": price, "qty": qty, "side": side})

    return total_realized_pnl, total_wins, total_losses, ticker_pnl

def parse_execution_logs():
    stats = defaultdict(lambda: {"executions": 0, "total_notional": 0.0})
    try:
        with open(LOG_FILE, "r") as f:
            for line in f:
                match = re.search(r'✅ \[DYNAMIC KELLY EXECUTION\] (\w+) \| (\w+) (\d+) shares \(~\$([\d,]+\.\d+) Notional\)', line)
                if match:
                    ticker, side, qty, notional = match.groups()
                    notional_val = float(notional.replace(",", ""))
                    stats[ticker]["executions"] += 1
                    stats[ticker]["total_notional"] += notional_val
    except Exception as e:
        print(f"⚠️ Could not read execution log file: {e}")
    return stats

def main():
    print("=" * 90)
    print("📈 PERFORMANCE & REALIZED PnL ANALYTICS ENGINE")
    print("=" * 90)

    # 1. Fetch Alpaca Account Summary
    r_acc = requests.get(f"{ALPACA_PAPER_URL}/v2/account", headers=HEADERS)
    if r_acc.status_code == 200:
        acc = r_acc.json()
        eq = float(acc.get("equity", 0))
        bp = float(acc.get("buying_power", 0))
        total_pnl = float(acc.get("portfolio_value", 0)) - 100000.0
        print(f"💰 Equity: ${eq:,.2f} | Buying Power: ${bp:,.2f} | Total Account PnL: ${total_pnl:,.2f}")
        print("-" * 90)

    # 2. Analyze Closed Orders & Calculate Realized Gains
    closed_orders = fetch_alpaca_closed_orders()
    total_realized, total_wins, total_losses, ticker_pnl = calculate_realized_pnl(closed_orders)

    decided_trades = total_wins + total_losses
    win_rate = (total_wins / decided_trades * 100.0) if decided_trades > 0 else 0.0

    print(f"📊 REALIZED CLOSED TRADE METRICS ({len(closed_orders)} Orders Processed):")
    print(f"  • Realized Closed PnL : ${total_realized:,.2f}")
    print(f"  • Closed Trade Win Rate: {win_rate:.1f}% ({total_wins} Wins / {total_losses} Losses / {decided_trades} Total Matched Pairs)")
    print("-" * 90)

    if ticker_pnl:
        print(f"{'Ticker':<8} | {'Matched Pairs':>13} | {'Wins':>6} | {'Losses':>6} | {'Win Rate':>9} | {'Realized PnL ($)':>18}")
        print("-" * 90)
        for ticker, data in sorted(ticker_pnl.items(), key=lambda x: x[1]["pnl"], reverse=True):
            t_wins = data["wins"]
            t_losses = data["losses"]
            t_decided = t_wins + t_losses
            t_win_rate = (t_wins / t_decided * 100.0) if t_decided > 0 else 0.0
            pnl_val = data["pnl"]
            sign = "+" if pnl_val > 0 else ""
            print(f"{ticker:<8} | {data['matched_pairs']:>13} | {t_wins:>6} | {t_losses:>6} | {t_win_rate:>8.1f}% | {sign}${pnl_val:>16,.2f}")
    else:
        print("No completed matched trade pairs found in history yet.")

    # 3. Analyze Execution Telemetry Logs
    log_stats = parse_execution_logs()
    print("\n" + "=" * 90)
    print("🧪 SHADOW EXECUTION TELEMETRY LOG ANALYSIS")
    print("=" * 90)
    if log_stats:
        print(f"{'Ticker':<8} | {'Executions':>12} | {'Total Notional Deployed':>25}")
        print("-" * 90)
        for ticker, data in sorted(log_stats.items()):
            print(f"{ticker:<8} | {data['executions']:>12} | ${data['total_notional']:>24,.2f}")
    else:
        print("No dynamic Kelly execution log lines recorded yet.")
    print("=" * 90)

if __name__ == "__main__":
    main()
