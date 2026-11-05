#!/usr/bin/env python3
import os, sys, requests
from pathlib import Path
from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import GetOrdersRequest
from alpaca.trading.enums import QueryOrderStatus

env_path = Path("/home/moonrider1968/bots/.env")
if env_path.exists():
    load_dotenv(dotenv_path=env_path)

API_KEY = os.getenv("ALPACA_API_KEY") or os.getenv("APCA_API_KEY_ID")
API_SECRET = os.getenv("ALPACA_SECRET_KEY") or os.getenv("APCA_API_SECRET_KEY")
TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID")

if not API_KEY or not API_SECRET or not TG_TOKEN or not TG_CHAT_ID:
    print("[!] Missing credentials.")
    sys.exit(1)

tc = TradingClient(API_KEY, API_SECRET, paper=True)

def send_audit():
    acc = tc.get_account()
    eq, last, cash = float(acc.equity), float(acc.last_equity), float(acc.cash)
    dd = ((eq - last) / last) * 100.0
    pos = tc.get_all_positions()
    ord = tc.get_orders(filter=GetOrdersRequest(status=QueryOrderStatus.OPEN))
    
    lines = [
        "<b>VALEZE2 BOT | AUDIT DISPATCH</b>",
        "-------------------------------------",
        f"Equity: <code>${eq:,.2f}</code>",
        f"Cash: <code>${cash:,.2f}</code>",
        f"Intraday PnL: <code>{dd:+.2f}%</code> (Cap: -1.00%)",
        "-------------------------------------",
        f"<b>POSITIONS ({len(pos)}/10):</b>"
    ]
    for p in pos:
        lines.append(f"- <b>{p.symbol}</b> ({p.qty} sh): <code>${float(p.unrealized_pl):+.2f}</code> (<code>{float(p.unrealized_plpc)*100:+.2f}%</code>)")
    
    lines.append("-------------------------------------")
    lines.append(f"<b>QUEUE ({len(ord)} Orders):</b>")
    for o in ord[:6]:
        lines.append(f"  [{o.side.name}] {o.qty}x <b>{o.symbol}</b> ({o.order_class.name})")
    
    log = Path("/home/moonrider1968/bots/swing_execution.log")
    if log.exists():
        vetoes = [l.strip() for l in log.read_text().splitlines() if "SECTOR" in l or "VETOED" in l]
        if vetoes:
            lines.append("-------------------------------------")
            lines.append("<b>RECENT SECTOR VETOES:</b>")
            for v in vetoes[-3:]:
                lines.append(f"  {v}")
    
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": chr(10).join(lines), "parse_mode": "HTML"}
    r = requests.post(url, json=payload, timeout=8)
    if r.status_code == 200:
        print("[+] Audit report dispatched to Telegram successfully.")
    else:
        print(f"[!] Telegram Error: {r.text}")

if __name__ == "__main__":
    send_audit()
