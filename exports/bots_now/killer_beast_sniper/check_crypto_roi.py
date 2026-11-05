import sqlite3
import os

DB_PATH = "/home/moonrider1968/bots/killer_beast_sniper/trades.db"

def get_crypto_performance():
    if not os.path.exists(DB_PATH):
        print("⚠️ No trade database found at trades.db yet.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    try:
        # Fetch closed crypto trades
        cursor.execute("""
            SELECT symbol, side, qty, entry_price, exit_price, pnl, pnl_pct 
            FROM closed_trades 
            WHERE symbol LIKE '%/USD%' OR symbol LIKE '%USD%'
        """)
        rows = cursor.fetchall()

        if not rows:
            print("ℹ️ No closed crypto trades recorded in the current database session.")
            return

        total_pnl = 0.0
        wins = 0
        losses = 0

        print("=" * 75)
        print("🪙 REALIZED CRYPTO PERFORMANCE & ROI REPORT")
        print("=" * 75)
        print(f"{'Symbol':<10} | {'Side':<5} | {'Qty':>8} | {'Entry':>10} | {'Exit':>10} | {'PnL ($)':>10}")
        print("-" * 75)

        for row in rows:
            symbol, side, qty, entry, exit_p, pnl, pnl_pct = row
            total_pnl += pnl
            if pnl > 0:
                wins += 1
            else:
                losses += 1
            print(f"{symbol:<10} | {side:<5} | {qty:>8.4f} | ${entry:>9.2f} | ${exit_p:>9.2f} | ${pnl:>9.2f}")

        total_trades = wins + losses
        win_rate = (wins / total_trades) * 100 if total_trades > 0 else 0.0

        print("=" * 75)
        print(f"  • Total Crypto Closed Trades : {total_trades}")
        print(f"  • Win Rate                   : {win_rate:.1f}% ({wins}W / {losses}L)")
        print(f"  • Net Realized Crypto PnL   : ${total_pnl:,.2f}")
        print("=" * 75)

    except Exception as e:
        print(f"❌ Error querying trade database: {e}")
    finally:
        conn.close()

if __name__ == "__main__":
    get_crypto_performance()
