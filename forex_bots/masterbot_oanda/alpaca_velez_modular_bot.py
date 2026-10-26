import os, time, logging, sqlite3, pytz
from datetime import datetime, timedelta
from dotenv import load_dotenv

from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import CryptoHistoricalDataClient
from alpaca.data.requests import CryptoBarsRequest
from alpaca.data.timeframe import TimeFrame

load_dotenv("alpaca.env")

API_KEY = os.getenv("ALPACA_API_KEY", "").strip()
SECRET_KEY = os.getenv("ALPACA_API_SECRET", "").strip()
PAPER = True

# --- VIRTUAL CAPITAL HACK ---
VIRTUAL_CAPITAL = 2000.00  # The bot ignores the $100k Alpaca balance
RISK_PER_TRADE = 0.01      # Risks exactly 1% of the Virtual Capital ($20)

CRYPTO_WATCHLIST = ["BTC/USD", "ETH/USD"]

api = TradingClient(API_KEY, SECRET_KEY, paper=PAPER)
crypto_data = CryptoHistoricalDataClient(API_KEY, SECRET_KEY)

logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s',
                    handlers=[logging.FileHandler("alpaca_dual.log", mode='a'), logging.StreamHandler()])
log = logging.getLogger(__name__)

class AlpacaGlobalBeast:
    def __init__(self):
        self.db = sqlite3.connect("alpaca_dual_datamine.db")
        self.db.execute("CREATE TABLE IF NOT EXISTS trades (symbol TEXT, system TEXT, stage TEXT, price REAL, qty REAL, ts REAL)")
        self.active_trades = {}

    def run(self):
        log.info(f"🧠 v10.4 GLOBAL BEAST | Virtual Capital Engaged: ${VIRTUAL_CAPITAL:,.2f}")
        while True:
            try:
                for symbol in CRYPTO_WATCHLIST:
                    # Fetch M5 Data
                    req = CryptoBarsRequest(symbol_or_symbols=[symbol], timeframe=TimeFrame.Minute, start=datetime.now()-timedelta(minutes=100))
                    bars = crypto_data.get_crypto_bars(req)[symbol]
                    prices = [b.close for b in bars]
                    
                    if len(prices) < 20: continue
                    
                    curr_p = prices[-1]
                    m5_ema20 = sum(prices[-20:]) / 20
                    
                    # Velez Elephant Bar Trigger
                    if curr_p > m5_ema20 and prices[-2] <= m5_ema20 and symbol not in self.active_trades:
                        
                        # Use Virtual Capital for Position Sizing
                        risk_amt = VIRTUAL_CAPITAL * RISK_PER_TRADE
                        qty = round(risk_amt / curr_p, 4)
                        
                        # Execute the Paper Trade
                        order = MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.GTC)
                        api.submit_order(order)
                        
                        # Log to the SQLite Datamine
                        self.db.execute("INSERT INTO trades (symbol, system, stage, price, qty, ts) VALUES (?, ?, ?, ?, ?, ?)", 
                                        (symbol, 'CRYPTO_SCALP', 'EARLY_IGNITION', curr_p, qty, time.time()))
                        self.db.commit()
                        
                        self.active_trades[symbol] = curr_p
                        log.info(f"⚡ [EXECUTION] {symbol} Velez Trigger! Logged to DB. Price: ${curr_p:,.2f} | Risking: ${risk_amt:,.2f}")
                    else:
                        log.info(f"🪙 Crypto Scan: {symbol} @ ${curr_p:,.2f} - Waiting for setup.")
                
                time.sleep(300)
            except Exception as e:
                log.error(f"Loop Error: {e}")
                time.sleep(60)

if __name__ == "__main__":
    AlpacaGlobalBeast().run()
