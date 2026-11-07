"""Telegram alerts via existing MarvsT_bot. Reads token/chat from env only.
Never prints token. One message per pick, rate-limited by caller.
"""
from __future__ import annotations
import os
import requests

def send_telegram(text: str) -> bool:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    chat_id = os.environ.get("TELEGRAM_CHAT_ID")
    if not token or not chat_id:
        raise RuntimeError("TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID not set in env")
    r = requests.post(f"https://api.telegram.org/bot{token}/sendMessage",
                      json={"chat_id": chat_id, "text": text}, timeout=10)
    r.raise_for_status()
    return True

SPORT_EMOJI = {"nfl": "🏈", "ncaaf": "🏈", "ncaab": "🏀", "ncaaw": "🏀"}
SPORT_LABEL = {"nfl": "NFL", "ncaaf": "NCAAF Top 30", "ncaab": "NCAAB", "ncaaw": "NCAAW"}

def format_pick(pick: dict) -> str:
    emoji = SPORT_EMOJI.get(pick.get("sport", "nfl"), "🏈")
    label = SPORT_LABEL.get(pick.get("sport", "nfl"), pick.get("sport", "").upper())
    return (
        f"{emoji} LIVE PROP — {label} | Muse Prop1\n"
        f"{pick['player']} — {pick['side'].title()} {pick['line']} {pick['market_label']} @ {pick['odds']} (Bovada)\n"
        f"Model: {pick['model_p']:.0%} | Fair (no-vig): {pick['fair_p']:.0%} | Edge +{pick['edge_pp']:.0%} | EV +{pick['ev']:.1%}\n"
        f"Pace: {pick['pace_note']}\n"
        f"Stake: {pick['stake_units']}u — check line now, expires ~90s\n"
        f"Log ID: {pick['log_id']}"
    )
