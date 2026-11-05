import os
from pathlib import Path
from dotenv import load_dotenv
import yfinance as yf
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import LimitOrderRequest, TakeProfitRequest, StopLossRequest, GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus, OrderSide, TimeInForce, OrderClass

load_dotenv("/home/moonrider1968/bots/.env")
API_KEY = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")

tc = TradingClient(API_KEY, API_SECRET, paper=True)

positions = tc.get_all_positions()
print(f"Active positions detected: {len(positions)}")

for p in positions:
    sym = p.symbol
    qty = int(float(p.qty))
    entry = float(p.avg_entry_price)
    cur = float(p.current_price)

    # Calculate 1.5x ATR Risk
    try:
        df = yf.download(sym, period="1mo", interval="1d", progress=False)
        high_low = df['High'] - df['Low']
        atr_val = high_low.tail(14).mean()
        atr = float(atr_val.iloc[0] if hasattr(atr_val, 'iloc') else atr_val)
        risk = round(1.5 * atr, 2)
    except Exception:
        risk = round(entry * 0.03, 2)

    stop_price = round(entry - risk, 2)
    if stop_price >= cur:
        stop_price = round(cur * 0.985, 2)

    tp1 = round(entry + (0.75 * risk), 2)
    tp2 = round(entry + (2.00 * risk), 2)

    q1 = max(1, qty // 2)
    q2 = qty - q1

    # Leg 1: 50% size @ +0.75R
    try:
        req1 = LimitOrderRequest(
            symbol=sym,
            qty=q1,
            side=OrderSide.SELL,
            time_in_force=TimeInForce.GTC,
            limit_price=tp1,
            order_class=OrderClass.OCO,
            stop_loss=StopLossRequest(stop_price=stop_price)
        )
        tc.submit_order(req1)
        print(f"[+] ARMED OCO LEG 1: {sym} ({q1} shs) | TP: ${tp1} (+0.75R) | SL: ${stop_price}")
    except Exception as e:
        print(f"[!] Leg 1 failed for {sym}: {e}")

    # Leg 2: 50% size @ +2.0R (Runner)
    if q2 > 0:
        try:
            req2 = LimitOrderRequest(
                symbol=sym,
                qty=q2,
                side=OrderSide.SELL,
                time_in_force=TimeInForce.GTC,
                limit_price=tp2,
                order_class=OrderClass.OCO,
                stop_loss=StopLossRequest(stop_price=stop_price)
            )
            tc.submit_order(req2)
            print(f"[+] ARMED OCO LEG 2: {sym} ({q2} shs) | TP: ${tp2} (+2.0R) | SL: ${stop_price}")
        except Exception as e:
            print(f"[!] Leg 2 failed for {sym}: {e}")

