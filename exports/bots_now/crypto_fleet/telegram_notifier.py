#!/usr/bin/env python3
"""
TELEGRAM WEBHOOK NOTIFIER MODULE (HARDCODED USER CONFIG)
-------------------------------------------------------
Routes Stock & Crypto trade alerts directly to Marv (@Marvi1968).
"""

import requests
import logging

TELEGRAM_BOT_TOKEN = "REDACTED"
TELEGRAM_CHAT_ID = "REDACTED"

def send_telegram_alert(message: str) -> bool:
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = {
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }
    try:
        response = requests.post(url, json=payload, timeout=5)
        return response.status_code == 200
    except Exception as e:
        logging.error(f"Failed to send Telegram alert: {e}")
        return False

def notify_trade_execution(fleet_type: str, symbol: str, side: str, tier: int, notional: float, entry: float, stop: float, tp: float):
    emoji = "🚨" if side.upper() == "SHORT" else "🚀"
    msg = (
        f"{emoji} *{fleet_type.upper()} FLEET EXECUTION*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"• *Symbol:* `{symbol}` ({side.upper()})\n"
        f"• *Tier:* `Tier {tier}`\n"
        f"• *Allocation:* `${notional:,.2f}`\n"
        f"• *Entry Price:* `${entry:.4f}`\n"
        f"• *Stop Loss:* `${stop:.4f}`\n"
        f"• *Take Profit:* `${tp:.4f}`"
    )
    send_telegram_alert(msg)

def notify_circuit_breaker(fleet_type: str, nav: float, session_roi: float, floor_pct: float):
    msg = (
        f"⚠️ *{fleet_type.upper()} CIRCUIT BREAKER TRIGGERED*\n"
        f"━━━━━━━━━━━━━━━━━━━\n"
        f"• *Current NAV:* `${nav:,.2f}`\n"
        f"• *Session ROI:* `{session_roi:+.2f}%`\n"
        f"• *Trailing Floor:* `{floor_pct:.2f}%`\n"
        f"⛔ *Action:* Sizing multiplier set to 0.0x. All new entries halted!"
    )
    send_telegram_alert(msg)

if __name__ == "__main__":
    print("Sending live test alert to Chat ID 7922925421...")
    success = send_telegram_alert("🤖 *Master Trade AI Bot (@MarvsT_bot) Linked to Marv!*")
    if success:
        print("✅ Live test alert sent directly to your Telegram!")
    else:
        print("⚠️ Direct delivery failed. Verify that you have clicked START on @MarvsT_bot in Telegram.")
