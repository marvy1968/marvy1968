import os, time, logging, sqlite3, json
import pandas as pd
from datetime import datetime
from collections import deque
from dotenv import load_dotenv

import oandapyV20
import oandapyV20.endpoints.instruments as instruments
import oandapyV20.endpoints.orders as orders
import oandapyV20.endpoints.positions as positions
from oandapyV20.endpoints.pricing import PricingInfo
from oandapyV20.endpoints.accounts import AccountDetails

load_dotenv("oanda_beast.env")

# ====================== v12.0 MACRO-MICRO HYBRID BEAST ======================
ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
ACCESS_TOKEN = os.getenv("OANDA_ACCESS_TOKEN")
ENV = os.getenv("OANDA_ENV", "practice")

RISK_PER_TRADE = 0.05          # 5% Aggressive Risk
MAX_ACTIVE_TRADES = 5
ATR_SL = 1.8
ATR_INITIAL_TRAIL = 1.5
STAIRCASE_TIERS = [0.30, 0.30, 0.40] # Market, Limit -0.45ATR, Limit -0.90ATR
TRAIL_TIGHTEN_STEPS = {0.8: 1.35, 1.5: 1.10, 2.5: 0.85, 3.5: 0.55}

WATCHLIST = ["EUR_USD", "GBP_USD", "USD_JPY", "USD_CAD", "AUD_USD", "NZD_USD", "EUR_JPY", "GBP_JPY"]
DB = "microbeast_datamine.db"

client = oandapyV20.API(access_token=ACCESS_TOKEN, environment=ENV)
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s',
                    handlers=[logging.FileHandler("oanda_velez.log", mode='a'), logging.StreamHandler()])
log = logging.getLogger(__name__)

class MacroMicroBeast:
    def __init__(self):
        self.active_trades = {}
        self.virtual_trails = {}
        self.history_m5 = {p: deque(maxlen=200) for p in WATCHLIST}
        self.daily_stages = {p: "Unknown" for p in WATCHLIST}
        self.last_macro_check = 0
        self.conn = sqlite3.connect(DB, check_same_thread=False)
        self.conn.execute("CREATE TABLE IF NOT EXISTS trades (pair TEXT, side TEXT, entry REAL, exit REAL, pnl REAL, reason TEXT, ts TEXT)")

    def get_candles(self, instrument, granularity, count):
        try:
            r = instruments.InstrumentsCandles(instrument=instrument, params={"granularity": granularity, "count": count})
            resp = client.request(r)
            return pd.DataFrame([{"c": float(c['mid']['c']), "h": float(c['mid']['h']), "l": float(c['mid']['l']), "v": int(c['volume'])} for c in resp['candles'] if c['complete']])
        except: return pd.DataFrame()

    def update_macro_stages(self):
        now = time.time()
        if now - self.last_macro_check < 3600: return # Check once per hour
        log.info("🔍 Updating Weinstein Daily Stages...")
        for pair in WATCHLIST:
            df = self.get_candles(pair, "D", 160)
            if df.empty or len(df) < 150: continue
            close, sma30, sma150 = df['c'].iloc[-1], df['c'].rolling(30).mean().iloc[-1], df['c'].rolling(150).mean().iloc[-1]
            if close > sma30 and sma30 > sma150: self.daily_stages[pair] = "Stage2"
            elif close < sma30 and sma30 < sma150: self.daily_stages[pair] = "Stage4"
            else: self.daily_stages[pair] = "Consolidating"
        self.last_macro_check = now

    def get_m5_stats(self, pair):
        df = self.get_candles(pair, "M5", 50)
        if df.empty: return None
        price = df['c'].iloc[-1]
        atr = (df['h'] - df['l']).rolling(14).mean().iloc[-1]
        # Velez Elephant Bar detection
        is_elephant = (df['h'].iloc[-1] - df['l'].iloc[-1]) > (atr * 1.7)
        return {"price": price, "atr": atr, "is_elephant": is_elephant, "sma20": df['c'].rolling(20).mean().iloc[-1]}

    def execute_hybrid_staircase(self, pair, side, stats):
        if len(self.active_trades) >= MAX_ACTIVE_TRADES: return
        try:
            bal = float(client.request(AccountDetails(ACCOUNT_ID))['account']['balance'])
            raw_units = int((bal * RISK_PER_TRADE) / (stats["atr"] * ATR_SL) / 10) * 10
            for i, alloc in enumerate(STAIRCASE_TIERS):
                tier_units = int(raw_units * alloc) * (1 if side == "buy" else -1)
                offset = i * 0.45 * stats["atr"]
                entry_p = stats["price"] - offset if side == "buy" else stats["price"] + offset
                order = {"order": {"units": str(tier_units), "instrument": pair, "type": "MARKET" if i==0 else "LIMIT", "price": str(round(entry_p, 3 if "JPY" in pair else 5)) if i>0 else None, "timeInForce": "FOK" if i==0 else "GTC"}}
                client.request(orders.OrderCreate(ACCOUNT_ID, data=order))
            self.active_trades[pair] = {"side": side, "entry": stats["price"], "atr": stats["atr"]}
            self.virtual_trails[pair] = stats["price"] - (stats["atr"] * ATR_INITIAL_TRAIL) if side == "buy" else stats["price"] + (stats["atr"] * ATR_INITIAL_TRAIL)
            log.info(f"🪜 HYBRID {side.upper()} {pair} fired in {self.daily_stages[pair]}")
        except Exception as e: log.error(f"Entry Error: {e}")

    def run(self):
        log.info("🧠 v12.0 MACRO-MICRO BEAST LIVE")
        while True:
            try:
                self.update_macro_stages()
                r = PricingInfo(ACCOUNT_ID, params={"instruments": ",".join(WATCHLIST)})
                for p_data in client.request(r).get('prices', []):
                    pair, price = p_data['instrument'], (float(p_data['closeoutBid']) + float(p_data['closeoutAsk'])) / 2
                    if pair in self.active_trades:
                        # Ghost Squeeze
                        t = self.active_trades[pair]
                        p_atr = (price - t["entry"]) / t["atr"] if t["side"] == "buy" else (t["entry"] - price) / t["atr"]
                        tighten = next((v for k, v in sorted(TRAIL_TIGHTEN_STEPS.items(), reverse=True) if p_atr >= k), 1.0)
                        if t["side"] == "buy":
                            self.virtual_trails[pair] = max(self.virtual_trails[pair], price - (t["atr"] * ATR_INITIAL_TRAIL * tighten))
                            if price <= self.virtual_trails[pair]: self.close_bot_position(pair, price, "GHOST_TRAIL")
                        else:
                            self.virtual_trails[pair] = min(self.virtual_trails[pair], price + (t["atr"] * ATR_INITIAL_TRAIL * tighten))
                            if price >= self.virtual_trails[pair]: self.close_bot_position(pair, price, "GHOST_TRAIL")
                    else:
                        stats = self.get_m5_stats(pair)
                        if stats and stats["is_elephant"]:
                            if self.daily_stages[pair] == "Stage2" and price > stats["sma20"]: self.execute_hybrid_staircase(pair, "buy", stats)
                            elif self.daily_stages[pair] == "Stage4" and price < stats["sma20"]: self.execute_hybrid_staircase(pair, "sell", stats)
                time.sleep(15)
            except: time.sleep(10)

    def close_bot_position(self, pair, price, reason):
        try:
            client.request(positions.PositionClose(ACCOUNT_ID, instrument=pair, data={"longUnits": "ALL", "shortUnits": "ALL"}))
            side, entry = self.active_trades[pair]["side"], self.active_trades[pair]["entry"]
            pnl = round((price - entry) * (100 if "JPY" in pair else 10000) * (1 if side == "buy" else -1), 1)
            self.conn.execute("INSERT INTO trades VALUES (?,?,?,?,?,?,?)", (pair, side, entry, price, pnl, reason, datetime.now().strftime("%H:%M:%S")))
            self.conn.commit()
            self.active_trades.pop(pair); self.virtual_trails.pop(pair)
            log.info(f"🔴 CLOSED {pair} | {pnl} pips")
        except: pass

if __name__ == "__main__":
    MacroMicroBeast().run()
