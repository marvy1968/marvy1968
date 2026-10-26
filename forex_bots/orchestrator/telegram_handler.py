
def get_rpp_squeeze_summary():
    import os, json
    matrix_file = '/home/moonrider1968/orchestrator/logs/rpp_unified_matrix.json'
    if not os.path.exists(matrix_file):
        return "\n\n🎯 <b>RPP Squeeze Watchlist:</b>\n<i>Matrix initializing...</i>"
    try:
        with open(matrix_file, 'r') as f:
            data = json.load(f)
        squeezes = data.get('active_squeezes', [])
        if not squeezes:
            return "\n\n🎯 <b>RPP Squeeze Watchlist:</b>\nNo active compression squeezes."

        msg = "\n\n🎯 <b>RPP Squeeze Watchlist:</b>\n"
        # Sort by highest compression score
        sorted_squeezes = sorted(squeezes, key=lambda x: x.get('compression_score', 0), reverse=True)[:5]
        for s in sorted_squeezes:
            sym = s.get('symbol')
            ac = s.get('asset_class', 'N/A')
            comp = s.get('compression_score', 0)
            p_up = int(s.get('probability_up', 0) * 100)
            p_dn = int(s.get('probability_down', 0) * 100)
            v_long = s.get('virtual_long_trigger')
            v_short = s.get('virtual_short_trigger')

            bias = f"🟢 UP {p_up}%" if p_up >= p_dn else f"🔴 DN {p_dn}%"
            msg += f"• <b>{sym}</b> ({ac}) | Squeeze: {comp:.0f}% | {bias}\n  <i>Triggers:</i> L {v_long} | S {v_short}\n"
        return msg
    except Exception as e:
        return f"\n\n🎯 <b>RPP Squeeze Watchlist:</b> Error loading matrix ({e})"

import os
import sys
import json
import time
import logging
import urllib.request
from datetime import datetime, timedelta, timezone

# Setup Logging
LOG_FILE = '/home/moonrider1968/orchestrator/telegram.log'
os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler(sys.stdout)
    ]
)

# 1. OANDA CREDENTIALS
OANDA_API_KEY = os.getenv("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
OANDA_BASE_URL = "https://api-fxtrade.oanda.com/v3"

# 2. TELEGRAM CREDENTIALS
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "REDACTED_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7922925421")

# 3. ALPACA CREDENTIALS (EXTRACT FROM VELEZ SCRIPT)
STOCK_SCRIPT = '/home/moonrider1968/bots/killer_beast_sniper/velez_stage4_short_daemon.py'
ALPACA_KEY = None
ALPACA_SECRET = None

if os.path.exists(STOCK_SCRIPT):
    with open(STOCK_SCRIPT, 'r') as f:
        for line in f:
            line_str = line.strip()
            if 'API_KEY' in line_str and '=' in line_str and not line_str.startswith('#'):
                parts = line_str.split('=', 1)
                if len(parts) == 2 and not ALPACA_KEY:
                    ALPACA_KEY = parts[1].strip().strip('"\'')
            if ('SECRET_KEY' in line_str or 'SECRET' in line_str) and '=' in line_str and not line_str.startswith('#'):
                parts = line_str.split('=', 1)
                if len(parts) == 2 and not ALPACA_SECRET:
                    ALPACA_SECRET = parts[1].strip().strip('"\'')

import alpaca_trade_api as tradeapi

def send_telegram_msg(chat_id, message):
    if not TELEGRAM_BOT_TOKEN:
        return
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = json.dumps({
        "chat_id": chat_id,
        "text": message,
        "parse_mode": "Markdown"
    }).encode('utf-8')
    headers = {"Content-Type": "application/json"}
    try:
        req = urllib.request.Request(url, data=payload, headers=headers)
        with urllib.request.urlopen(req) as resp:
            pass
    except Exception as e:
        logging.error(f"Failed to send Telegram message: {e}")

def fetch_alpaca_summary():
    if not ALPACA_KEY or not ALPACA_SECRET:
        return None
    try:
        api = tradeapi.REST(key_id=ALPACA_KEY, secret_key=ALPACA_SECRET, base_url='https://paper-api.alpaca.markets', api_version='v2')
        acc = api.get_account()
        positions = api.list_positions()
        
        equity = float(acc.equity)
        buying_power = float(acc.buying_power)
        cash = float(acc.cash)
        
        stock_tickers = ['AAPL', 'NVDA', 'AMD', 'TSLA', 'MSFT', 'COIN', 'RBLX', 'AMZN', 'META', 'GOOGL', 'PLTR', 'BABA', 'NFLX', 'SBUX', 'BA', 'INTC', 'PYPL', 'DIS', 'MARA', 'RIOT']
        
        stock_positions = []
        crypto_positions = []
        unrealized_pnl = 0.0
        
        for p in positions:
            pnl = float(p.unrealized_pl)
            unrealized_pnl += pnl
            if '/' in p.symbol or ('USD' in p.symbol and p.symbol not in stock_tickers):
                crypto_positions.append(p)
            else:
                stock_positions.append(p)
                
        return {
            'equity': equity,
            'cash': cash,
            'buying_power': buying_power,
            'unrealized_pnl': unrealized_pnl,
            'stock_count': len(stock_positions),
            'crypto_count': len(crypto_positions)
        }
    except Exception as e:
        logging.error(f"Error fetching Alpaca summary: {e}")
        return None

def fetch_oanda_summary(days=1):
    if not OANDA_API_KEY or not OANDA_ACCOUNT_ID:
        return None
    headers = {"Authorization": f"Bearer {OANDA_API_KEY}"}
    summary_url = f"{OANDA_BASE_URL}/accounts/{OANDA_ACCOUNT_ID}/summary"
    try:
        req = urllib.request.Request(summary_url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            acc = json.loads(resp.read().decode())['account']
            balance = float(acc['balance'])
            nav = float(acc['NAV'])
            unrealized_pnl = float(acc['unrealizedPL'])
            open_count = int(acc['openTradeCount'])
            
        # Transactions PnL
        cutoff_time = datetime.now(timezone.utc) - timedelta(days=days)
        realized_pnl = 0.0
        closed_trades = 0
        wins = 0
        
        tx_url = f"{OANDA_BASE_URL}/accounts/{OANDA_ACCOUNT_ID}/transactions/sinceid?id=1"
        req_tx = urllib.request.Request(tx_url, headers=headers)
        with urllib.request.urlopen(req_tx) as resp:
            txs = json.loads(resp.read().decode()).get('transactions', [])
            for tx in txs:
                if tx.get('type') == 'ORDER_FILL':
                    tx_time_str = tx.get('time', '')
                    if tx_time_str:
                        tx_dt = datetime.fromisoformat(tx_time_str.replace('Z', '+00:00'))
                        if tx_dt >= cutoff_time:
                            pl = float(tx.get('realizedPL', 0.0))
                            if pl != 0.0:
                                realized_pnl += pl
                                closed_trades += 1
                                if pl > 0: wins += 1
                                
        win_rate = (wins / closed_trades * 100) if closed_trades > 0 else 0.0
        return {
            'nav': nav,
            'balance': balance,
            'unrealized_pnl': unrealized_pnl,
            'open_count': open_count,
            'realized_pnl': realized_pnl,
            'closed_trades': closed_trades,
            'win_rate': win_rate
        }
    except Exception as e:
        logging.error(f"Error fetching OANDA summary: {e}")
        return None

def generate_multi_day_report(days=1):
    period_title = "24-HOUR" if days == 1 else f"{days}-DAY MULTI-SESSION"
    
    alpaca = fetch_alpaca_summary()
    oanda = fetch_oanda_summary(days=days)
    
    report_lines = [f"📊 *{period_title} MASTER PORTFOLIO REPORT*", "============================"]
    
    # Alpaca Section
    if alpaca:
        report_lines.extend([
            "📈 *ALPACA (STOCKS & CRYPTO)*",
            f"💰 *NAV:* `${alpaca['equity']:,.2f}`",
            f"💵 *Cash:* `${alpaca['cash']:,.2f}`",
            f"📈 *Unrealized P&L:* `${alpaca['unrealized_pnl']:+,.2f}`",
            f"📉 *Stock Shorts Active:* `{alpaca['stock_count']} Positions`",
            f"🪙 *Crypto Longs Active:* `{alpaca['crypto_count']} Positions`",
            "----------------------------"
        ])
    else:
        report_lines.extend(["⚠️ *Alpaca Data:* Offline / Error", "----------------------------"])
        
    # OANDA Section
    if oanda:
        report_lines.extend([
            "💱 *OANDA FOREX FLEET*",
            f"💰 *NAV:* `${oanda['nav']:,.2f}`",
            f"📈 *Unrealized P&L:* `${oanda['unrealized_pnl']:+,.2f}`",
            f"💸 *{days}D Realized P&L:* `${oanda['realized_pnl']:+,.2f}`",
            f"🔒 *Active FX Trades:* `{oanda['open_count']}`",
            f"🎯 *{days}D Closed Trades:* `{oanda['closed_trades']}` (Win: `{oanda['win_rate']:.1f}%`)",
            "============================"
        ])
    else:
        report_lines.extend(["⚠️ *OANDA Data:* Offline / Error", "============================"])
        
    report_lines.append("🤖 *Status:* `All Execution Daemons Active`")
    return "\n".join(report_lines)

def listen_telegram_commands():
    if not TELEGRAM_BOT_TOKEN:
        logging.info("Telegram Bot Token missing.")
        return
        
    last_update_id = 0
    logging.info("📱 Telegram Listener Online. Listening for /report, /report card, /report7...")
    
    while True:
        try:
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getUpdates?offset={last_update_id + 1}&timeout=30"
            req = urllib.request.Request(url)
            with urllib.request.urlopen(req) as resp:
                updates = json.loads(resp.read().decode()).get('result', [])
                for update in updates:
                    last_update_id = update['update_id']
                    message = update.get('message', {})
                    text = message.get('text', '').strip().lower()
                    chat_id = str(message.get('chat', {}).get('id', ''))
                    
                    if not text or not chat_id:
                        continue
                        
                    if text in ['/report', '/status', '/summary', '/report card', 'report']:
                        report_msg = generate_multi_day_report(days=1)
                        send_telegram_msg(chat_id, report_msg)
                    elif text in ['/report7', '/report 7', '/status7', '/week', 'report7']:
                        report_msg = generate_multi_day_report(days=7)
                        send_telegram_msg(chat_id, report_msg)
        except Exception as e:
            time.sleep(5)

if __name__ == "__main__":
    listen_telegram_commands()
