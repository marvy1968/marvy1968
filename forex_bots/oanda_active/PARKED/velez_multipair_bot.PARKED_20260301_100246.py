import os
import time
import requests
import pandas as pd
import pandas_ta as ta
from dotenv import load_dotenv
from datetime import datetime, timezone

# --- INITIALIZATION ---
load_dotenv()

API_KEY = os.getenv("OANDA_API_KEY")
ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID")
ENV = (os.getenv("OANDA_ENVIRONMENT", "practice") or "practice").lower().strip()

if not API_KEY or not ACCOUNT_ID:
    raise SystemExit("❌ Missing OANDA_API_KEY or OANDA_ACCOUNT_ID in .env")

URL = "https://api-fxpractice.oanda.com" if ENV == "practice" else "https://api-fxtrade.oanda.com"

# --- POPULAR PAIRS (THE MAJORS) ---
POPULAR_PAIRS = ["EUR_USD", "GBP_USD", "USD_JPY", "AUD_USD", "USD_CAD", "USD_CHF"]

class MultiPairVelezSniper:
    def __init__(self):
        self.headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json",
        }

        self.risk_pct = float(os.getenv("RISK_PER_TRADE", "0.02"))
        self.daily_limit = float(os.getenv("DAILY_LOSS_LIMIT", "250.0"))

        # Connection & Balance Check
        self.session_start_nav = self.get_nav()
        print(f"[{datetime.now()}] ONLINE: OANDA {ENV.upper()} | NAV={self.session_start_nav:.2f} | Pairs: {', '.join(POPULAR_PAIRS)}")

        # Trade Tracking: { 'EUR_USD': { 'side': 'LONG', 'entry': 1.1, 'sl': 1.09, 'time': ts }, ... }
        self.active_trades = {}

    def _req_json(self, method, url, **kwargs):
        """Centralized request so API errors don’t silently kill logic."""
        r = requests.request(method, url, headers=self.headers, timeout=20, **kwargs)
        try:
            j = r.json()
        except Exception:
            j = {"_raw": r.text}

        if r.status_code >= 400:
            raise RuntimeError(f"HTTP {r.status_code} | {j}")
        return j

    def get_nav(self):
        try:
            j = self._req_json("GET", f"{URL}/v3/accounts/{ACCOUNT_ID}/summary")
            return float(j["account"]["nav"])
        except Exception as e:
            print(f"API Connection Error (get_nav): {e}")
            return 0.0

    def get_market_data(self, symbol):
        """Fetch 2-minute candles."""
        params = {"count": 220, "granularity": "M2", "price": "M"}  # 220 ensures ema200 has enough data
        try:
            j = self._req_json("GET", f"{URL}/v3/instruments/{symbol}/candles", params=params)
        except Exception as e:
            print(f"[{datetime.now()}] Market data error {symbol}: {e}")
            return None

        candles = j.get("candles")
        if not candles:
            return None

        rows = []
        for c in candles:
            # Skip incomplete candles (common on short granularities)
            if not c.get("complete", True):
                continue
            mid = c.get("mid") or {}
            if not mid:
                continue
            rows.append({
                "o": float(mid["o"]),
                "h": float(mid["h"]),
                "l": float(mid["l"]),
                "c": float(mid["c"]),
                "v": int(c.get("volume", 0)),
            })

        if len(rows) < 210:  # need enough for ema200 + adx
            return None

        return pd.DataFrame(rows)

    def get_signal(self, df, symbol):
        """Velez Elephant Bar + Narrow State + Volume Filter."""
        df["ema20"] = ta.ema(df["c"], length=20)
        df["ema200"] = ta.ema(df["c"], length=200)

        adx_df = ta.adx(df["h"], df["l"], df["c"], length=14)
        if adx_df is None or "ADX_14" not in adx_df:
            return None, None
        df["adx"] = adx_df["ADX_14"]

        last = df.iloc[-1]
        body = abs(last["c"] - last["o"])
        avg_body = (df["c"] - df["o"]).abs().tail(20).mean()
        avg_vol = df["v"].tail(20).mean()

        # Institutional Logic
        if last["ema200"] == 0 or pd.isna(last["ema200"]):
            return None, None

        is_narrow = abs(last["ema20"] - last["ema200"]) / last["ema200"] < 0.012
        is_elephant = body > (avg_body * 2.1)
        is_high_vol = last["v"] > (avg_vol * 1.8)
        is_trending = last["adx"] > 25

        if is_narrow and is_elephant and is_high_vol and is_trending:
            if last["c"] > last["ema20"] > last["ema200"]:
                return "LONG", float(last["l"])
            if last["c"] < last["ema20"] < last["ema200"]:
                return "SHORT", float(last["h"])
        return None, None

    def execute_entry(self, symbol, side, stop_loss):
        nav = self.get_nav()
        if nav <= 0:
            return

        # Daily loss limit
        if (self.session_start_nav - nav) >= self.daily_limit:
            print(f"[{datetime.now()}] DAILY LIMIT HIT: start={self.session_start_nav:.2f} now={nav:.2f} limit={self.daily_limit:.2f}")
            return

        df = self.get_market_data(symbol)
        if df is None:
            return
        entry = float(df.iloc[-1]["c"])

        dist = abs(entry - float(stop_loss))
        if dist <= 0:
            return

        # Dynamic Risk Sizing
        units = int((nav * self.risk_pct) / dist)

        # JPY scaling (keep your original behavior)
        if "JPY" in symbol:
            units = int(units / 100)

        if units <= 0:
            return

        units = units if side == "LONG" else -units

        order = {
            "order": {
                "type": "MARKET",
                "instrument": symbol,
                "units": str(units),
                "timeInForce": "FOK",
            }
        }

        try:
            self._req_json("POST", f"{URL}/v3/accounts/{ACCOUNT_ID}/orders", json=order)
        except Exception as e:
            print(f"[{datetime.now()}] ORDER FAIL {symbol} {side}: {e}")
            return

        self.active_trades[symbol] = {
            "side": side,
            "entry": entry,
            "sl": float(stop_loss),
            "time": datetime.now(timezone.utc),
        }
        print(f"[{datetime.now()}] + TRADE OPEN: {symbol} {side} @ {entry} (Units: {abs(units)})")

    def close_fifo(self, symbol, side):
        """FIFO Safe Position Close."""
        data = {"longUnits": "ALL"} if side == "LONG" else {"shortUnits": "ALL"}
        try:
            self._req_json("PUT", f"{URL}/v3/accounts/{ACCOUNT_ID}/positions/{symbol}/close", json=data)
        except Exception as e:
            print(f"[{datetime.now()}] CLOSE FAIL {symbol}: {e}")
            return

        if symbol in self.active_trades:
            del self.active_trades[symbol]
        print(f"[{datetime.now()}] - TRADE CLOSED: {symbol} (FIFO Offset)")

    def manage_trades(self):
        """Handle Virtual Stops and Time-Stops across all pairs."""
        for symbol in list(self.active_trades.keys()):
            df = self.get_market_data(symbol)
            if df is None or len(df) < 3:
                continue

            last = df.iloc[-1]
            prev = df.iloc[-2]
            trade = self.active_trades[symbol]

            age = (datetime.now(timezone.utc) - trade["time"]).seconds / 60.0

            # 1) Time-Stop (3-Bar/6-Min Rule) — same logic, but fixed precedence
            if age > 6:
                if trade["side"] == "LONG":
                    if float(last["c"]) < float(trade["entry"]):
                        self.close_fifo(symbol, "LONG")
                        continue
                else:
                    if float(last["c"]) > float(trade["entry"]):
                        self.close_fifo(symbol, "SHORT")
                        continue

            # 2) Virtual Bar-by-Bar Trailing Stop
            if trade["side"] == "LONG":
                trade["sl"] = max(float(trade["sl"]), float(prev["l"]))
                if float(last["c"]) < float(trade["sl"]):
                    self.close_fifo(symbol, "LONG")
            else:
                trade["sl"] = min(float(trade["sl"]), float(prev["h"]))
                if float(last["c"]) > float(trade["sl"]):
                    self.close_fifo(symbol, "SHORT")

    def run(self):
        while True:
            try:
                self.manage_trades()

                for symbol in POPULAR_PAIRS:
                    if symbol not in self.active_trades:
                        df = self.get_market_data(symbol)
                        if df is not None:
                            sig, sl = self.get_signal(df, symbol)
                            if sig:
                                self.execute_entry(symbol, sig, sl)

                time.sleep(20)
            except Exception as e:
                print(f"[{datetime.now()}] Loop Error: {e}")
                time.sleep(30)

if __name__ == "__main__":
    MultiPairVelezSniper().run()
