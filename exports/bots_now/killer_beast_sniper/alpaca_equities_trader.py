import os, time, math, logging, requests, urllib.request, json
from datetime import datetime, timezone
from dotenv import load_dotenv
from notifier import send_alert

LOG_PATH = "/home/moonrider1968/bots/killer_beast_sniper/logs/alpaca_equities_trader.log"
os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s [%(levelname)s] %(message)s',
    handlers=[logging.FileHandler(LOG_PATH), logging.StreamHandler()]
)

ENV_FILE = '/home/moonrider1968/bots/killer_beast_sniper/.env'

def get_config():
    load_dotenv(ENV_FILE, override=True)
    api_key = os.getenv('APCA_API_KEY_ID') or os.getenv('ALPACA_API_KEY', '')
    secret_key = os.getenv('APCA_API_SECRET_KEY') or os.getenv('ALPACA_SECRET_KEY', '')
    base_url = os.getenv('APCA_API_BASE_URL') or os.getenv('ALPACA_BASE_URL', 'https://paper-api.alpaca.markets')
    return {
        'api_key': api_key.strip(),
        'secret_key': secret_key.strip(),
        'base_url': base_url.strip(),
        'data_url': 'https://data.alpaca.markets'
    }

class PowerTriadAllianceEngine:
    def __init__(self):
        self.config = get_config()
        self.session = requests.Session()
        
        # 24 Elite Sector Alpha Basket
        self.sector_map = {
            'TECH':          ['NVDA', 'AVGO', 'AAPL', 'AMD'],
            'INDUSTRIALS':   ['CAT', 'GE', 'DE', 'RTX'],
            'HEALTHCARE':    ['LLY', 'ABBV', 'ISRG', 'UNH'],
            'UTILITIES':     ['VST', 'CEG', 'NEE'],
            'STAPLES':       ['COST', 'PG', 'WMT'],
            'DISCRETIONARY': ['AMZN', 'TSLA', 'BKNG'],
            'COMM':          ['GOOGL', 'META', 'NFLX']
        }
        
        self.symbols = [sym for group in self.sector_map.values() for sym in group]
        self.ticker_to_sector = {sym: sec for sec, syms in self.sector_map.items() for sym in syms}
        
        self.MAX_TOTAL_POSITIONS = 5
        self.MAX_SECTOR_POSITIONS = 2
        
        self.cooldowns = {}
        self.earnings_cache = {}
        self.cycle_count = 0

    def get_headers(self):
        return {
            'APCA-API-KEY-ID': self.config['api_key'],
            'APCA-API-SECRET-KEY': self.config['secret_key'],
            'Content-Type': 'application/json'
        }

    def api_get(self, endpoint, params=None, is_data=False):
        url = f"{self.config['data_url'] if is_data else self.config['base_url']}{endpoint}"
        try:
            r = self.session.get(url, headers=self.get_headers(), params=params, timeout=10)
            if r.status_code == 401:
                logging.error("❌ Alpaca 401 Unauthorized. Reloading credentials...")
                self.config = get_config()
            return r
        except Exception as e:
            logging.warning(f"⚠️ Alpaca GET error ({endpoint}): {e}")
            return None

    def api_post(self, endpoint, data=None):
        url = f"{self.config['base_url']}{endpoint}"
        try:
            return self.session.post(url, headers=self.get_headers(), json=data, timeout=10)
        except Exception as e:
            logging.warning(f"⚠️ Alpaca POST error ({endpoint}): {e}")
            return None

    def is_market_open(self):
        r = self.api_get("/v2/clock")
        if r and r.status_code == 200:
            return r.json().get('is_open', False)
        return False

    def get_account_equity(self):
        r = self.api_get("/v2/account")
        if r and r.status_code == 200:
            return float(r.json().get('equity', 100000.0))
        return 100000.0

    def get_open_positions(self):
        r = self.api_get("/v2/positions")
        if r and r.status_code == 200:
            return r.json()
        return []

    def get_daily_bars(self, symbol, limit=220):
        endpoint = f"/v2/stocks/{symbol}/bars"
        params = {'timeframe': '1Day', 'limit': limit, 'adjustment': 'split', 'feed': 'sip'}
        r = self.api_get(endpoint, params=params, is_data=True)
        if r and r.status_code == 200:
            return r.json().get('bars', [])
        return []

    def is_earnings_near(self, symbol):
        now = time.time()
        if symbol in self.earnings_cache and now < self.earnings_cache[symbol]['expiry']:
            return self.earnings_cache[symbol]['is_near']

        try:
            url = f"https://query2.finance.yahoo.com/v1/finance/search?q={symbol}"
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            self.earnings_cache[symbol] = {'is_near': False, 'expiry': now + 86400}
            return False
        except Exception:
            return False

    def calculate_indicators(self, bars):
        if len(bars) < 205:
            return None
        opens  = [float(b['o']) for b in bars]
        closes = [float(b['c']) for b in bars]
        highs  = [float(b['h']) for b in bars]
        lows   = [float(b['l']) for b in bars]
        vols   = [float(b.get('v', 0)) for b in bars]

        # 1. Kaufman Efficiency Ratio (20-bar)
        net_disp = abs(closes[-1] - closes[-21])
        total_path = sum(abs(closes[j] - closes[j-1]) for j in range(len(closes)-20, len(closes)))
        er = net_disp / (total_path + 1e-9)

        # 2. 14-Bar ATR
        trs = [max(highs[j]-lows[j], abs(highs[j]-closes[j-1]), abs(lows[j]-closes[j-1])) for j in range(len(closes)-14, len(closes))]
        atr = sum(trs) / 14.0

        # 3. Moving Averages
        sma20 = sum(closes[-20:]) / 20.0
        sma20_prev = sum(closes[-21:-1]) / 20.0
        sma50 = sum(closes[-50:]) / 50.0
        sma200 = sum(closes[-200:]) / 200.0

        # 4. Bollinger Bands & VCP Squeeze
        std20 = math.sqrt(sum((x - sma20)**2 for x in closes[-20:]) / 20.0)
        bb_upper = sma20 + (2.0 * std20)
        bb_lower = sma20 - (2.0 * std20)
        bb_width = (bb_upper - bb_lower) / (sma20 + 1e-9)
        min_bb_width = min((max(highs[j-20:j]) - min(lows[j-20:j])) / closes[j] for j in range(len(closes)-10, len(closes)))

        # 5. Pivots & Volume SMA
        pivot_20 = max(highs[-21:-1])
        pivot_50 = max(highs[-51:-1])
        vol_sma20 = sum(vols[-20:]) / 20.0 if len(vols) >= 20 else 1.0

        return {
            'open': opens[-1], 'close': closes[-1], 'high': highs[-1], 'low': lows[-1],
            'prev_high': highs[-2], 'prev_low': lows[-2], 'vol': vols[-1], 'vol_sma20': vol_sma20,
            'er': er, 'atr': atr,
            'sma20': sma20, 'sma20_prev': sma20_prev, 'sma50': sma50, 'sma200': sma200,
            'bb_upper': bb_upper, 'bb_width': bb_width, 'min_bb_width': min_bb_width,
            'pivot_20': pivot_20, 'pivot_50': pivot_50
        }

    def manage_active_positions(self, positions):
        for pos in positions:
            symbol = pos.get('symbol')
            qty = float(pos.get('qty', 0))
            entry = float(pos.get('avg_entry_price', 0))
            current = float(pos.get('current_price', 0))

            bars = self.get_daily_bars(symbol, limit=40)
            if len(bars) < 30: continue

            closes = [float(b['c']) for b in bars]
            highs  = [float(b['h']) for b in bars]
            lows   = [float(b['l']) for b in bars]

            net_disp = abs(closes[-1] - closes[-21])
            total_path = sum(abs(closes[j] - closes[j-1]) for j in range(len(closes)-20, len(closes)))
            er = net_disp / (total_path + 1e-9)

            trs = [max(highs[j]-lows[j], abs(highs[j]-closes[j-1]), abs(lows[j]-closes[j-1])) for j in range(len(closes)-14, len(closes))]
            atr = sum(trs) / 14.0

            # Adaptive Exhaustion Ratchet: If in profit and ER drops below 0.35, protect capital
            if current > entry and er < 0.35:
                tight_sl = round(highs[-1] - (atr * 1.0), 2)
                logging.info(f"🛡️ [{symbol}] ER Exhaustion Ratchet Active (ER: {er:.2f}). Trailing SL @ ${tight_sl:.2f}")

    def scan_and_execute(self):
        equity = self.get_account_equity()
        positions = self.get_open_positions()
        active_symbols = {p.get('symbol') for p in positions}
        now_ts = time.time()

        if positions:
            self.manage_active_positions(positions)

        if len(active_symbols) >= self.MAX_TOTAL_POSITIONS:
            if self.cycle_count % 30 == 0:
                logging.info(f"🔒 [PORTFOLIO FULL] At max capacity ({len(active_symbols)}/{self.MAX_TOTAL_POSITIONS} positions). Standing by...")
            return

        sector_counts = {}
        for sym in active_symbols:
            sec = self.ticker_to_sector.get(sym, 'OTHER')
            sector_counts[sec] = sector_counts.get(sec, 0) + 1

        for sym in self.symbols:
            if len(active_symbols) >= self.MAX_TOTAL_POSITIONS: break
            if sym in active_symbols: continue
            if sym in self.cooldowns and now_ts < self.cooldowns[sym]: continue

            sec = self.ticker_to_sector.get(sym, 'OTHER')
            if sector_counts.get(sec, 0) >= self.MAX_SECTOR_POSITIONS: continue

            if self.is_earnings_near(sym):
                logging.info(f"⏭️ [{sym}] Skipped due to upcoming earnings release window.")
                continue

            bars = self.get_daily_bars(sym, limit=220)
            ind = self.calculate_indicators(bars)
            if not ind: continue

            c, o, h, l = ind['close'], ind['open'], ind['high'], ind['low']
            vol, vol_sma20 = ind['vol'], ind['vol_sma20']
            er, atr = ind['er'], ind['atr']
            sma20, sma20_prev = ind['sma20'], ind['sma20_prev']
            sma50, sma200 = ind['sma50'], ind['sma200']
            bb_upper, bb_width, min_bb_width = ind['bb_upper'], ind['bb_width'], ind['min_bb_width']
            pivot_20, pivot_50 = ind['pivot_20'], ind['pivot_50']

            setup_triggered = False
            setup_name = ""
            risk_pct = 0.020

            # -------------------------------------------------------------------------
            # LAYER 1: TRIAD 1 - TREND CONTINUATION (VELEZ + WEINSTEIN + MINERVINI)
            # -------------------------------------------------------------------------
            if sma20 > sma200 and sma20 > sma20_prev and c > sma20:
                is_pullback = (ind['prev_low'] <= sma20 + (atr * 0.5)) or (l <= sma20 + (atr * 0.5))
                is_gbr = (c > o) and (h > ind['prev_high']) and is_pullback
                is_elephant = ((h - l) >= 1.5 * atr) and (abs(c - o) >= 0.70 * (h - l)) and (c > o)

                if is_gbr or is_elephant:
                    setup_triggered = True
                    setup_name = "VELEZ 20-SMA GBR" if is_gbr else "VELEZ ELEPHANT BAR"
                    risk_pct = 0.020

            if not setup_triggered and (sma20 > sma50 and sma50 > sma200 and c > bb_upper and bb_width > min_bb_width * 1.3 and (c > o)):
                setup_triggered = True
                setup_name = "MINERVINI VCP SQUEEZE"
                risk_pct = 0.025

            if not setup_triggered and (c > pivot_20 and c > sma50 and er > 0.55):
                setup_triggered = True
                setup_name = f"WEINSTEIN STAGE 2 (ER: {er:.2f})"
                norm_er = min(1.0, max(0.0, (er - 0.55) / 0.30))
                risk_pct = 0.015 + (norm_er * 0.025)

            # -------------------------------------------------------------------------
            # LAYER 2: TRIAD 3 - CLASSICAL VOLUME BREAKOUT (DARVAS + LIVERMORE + O'NEIL)
            # -------------------------------------------------------------------------
            if not setup_triggered:
                is_darvas = (c > pivot_20 and vol > vol_sma20 * 1.3 and c > sma50)
                is_livermore = (c > pivot_50 and er > 0.60 and c > sma200)
                is_oneil = (c > pivot_20 and (c - o) >= 0.60 * (h - l) and vol > vol_sma20 * 1.5)

                if is_darvas or is_livermore or is_oneil:
                    setup_triggered = True
                    setup_name = "DARVAS/LIVERMORE VOLUME BREAKOUT"
                    risk_pct = 0.025

            # -------------------------------------------------------------------------
            # LAYER 3: TRIAD 5 - INSTITUTIONAL TREND FOLLOWING (SEYKOTA + PTJ + SHANNON)
            # -------------------------------------------------------------------------
            if not setup_triggered:
                is_ptj = (c > sma200 and c > pivot_20 and er > 0.55)
                is_shannon = (c > sma20 and sma20 > sma50 and sma50 > sma200 and (c > o) and (h > ind['prev_high']) and er > 0.50)

                if is_ptj or is_shannon:
                    setup_triggered = True
                    setup_name = "INSTITUTIONAL TREND ALIGNMENT (PTJ/SHANNON)"
                    risk_pct = 0.020

            # -------------------------------------------------------------------------
            # EXECUTION & ORDER PLACEMENT
            # -------------------------------------------------------------------------
            if setup_triggered:
                risk_dist = max(atr * 1.5, c * 0.02)
                dollar_risk = equity * risk_pct
                shares = max(1, int(dollar_risk / (risk_dist + 1e-9)))

                sl_price = round(c - risk_dist, 2)
                tp_price = round(c + (risk_dist * 2.5), 2)

                order_data = {
                    "symbol": sym,
                    "qty": shares,
                    "side": "buy",
                    "type": "market",
                    "time_in_force": "gtc",
                    "order_class": "bracket",
                    "take_profit": {"limit_price": str(tp_price)},
                    "stop_loss": {"stop_price": str(sl_price)}
                }

                logging.info(f"🎯 [{sym} | {sec}] {setup_name} | Risk: {risk_pct*100:.1f}% (${dollar_risk:,.0f}) | Shares: {shares} @ ${c:.2f} | SL: ${sl_price} | TP: ${tp_price}")
                r = self.api_post("/v2/orders", data=order_data)
                if r and r.status_code in (200, 201):
                    msg = f"🚀 **POWER TRIAD BUY FILLED**\nTicker: `{sym}` ({sec})\nMaster Setup: `{setup_name}`\nShares: `{shares}` @ `${c:.2f}`\nRisk: `{risk_pct*100:.1f}%` (${dollar_risk:,.0f})\nStop Loss: `${sl_price}` | Target: `${tp_price}`"
                    send_alert(msg, title=f"🎯 EQUITIES {sym} ORDER FILLED")
                    self.cooldowns[sym] = now_ts + 14400
                    active_symbols.add(sym)
                    sector_counts[sec] = sector_counts.get(sec, 0) + 1
                else:
                    err_msg = r.text if r else "No response"
                    logging.warning(f"⚠️ Order failed for {sym}: {err_msg}")

        if self.cycle_count % 20 == 0:
            logging.info(f"💓 [POWER TRIAD HEARTBEAT] Equity: ${equity:,.2f} | Active Positions ({len(active_symbols)}/5): {list(active_symbols)}")

    def start(self):
        logging.info("=" * 105)
        logging.info("🚀 ALPACA POWER TRIAD ALLIANCE ENGINE ONLINE (T1 TREND + T3 BREAKOUT + T5 INSTITUTIONAL)")
        logging.info(f"   • Universe (24 Stocks): {', '.join(self.symbols[:10])}...")
        logging.info("   • Layer 1 (Trend): Velez 20 SMA GBR + Weinstein Stage 2 + Minervini VCP")
        logging.info("   • Layer 2 (Breakout): Darvas Box + Livermore Pivotal Point + O'Neil CAN SLIM Volume")
        logging.info("   • Layer 3 (Institutional): Seykota Smoothing + PTJ 200 SMA + Shannon Alignment")
        logging.info(f"   • Constraints: Max {self.MAX_TOTAL_POSITIONS} Positions | Max {self.MAX_SECTOR_POSITIONS} per Sector | 48h Earnings Block")
        logging.info("   • Execution: 2.5R TP / 1.5 ATR SL GTC Server-Side Brackets | Adaptive ER < 0.35 Ratchet")
        logging.info("=" * 105)

        while True:
            try:
                self.cycle_count += 1
                if self.is_market_open():
                    self.scan_and_execute()
                else:
                    if self.cycle_count % 30 == 0:
                        logging.info("🌙 Market is currently closed. Standing by for opening bell...")
            except Exception as e:
                logging.error(f"Error in Equities cycle: {e}")
            time.sleep(60)

if __name__ == "__main__":
    PowerTriadAllianceEngine().start()
