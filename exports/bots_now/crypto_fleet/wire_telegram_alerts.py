#!/usr/bin/env python3
"""
TELEGRAM ALERT INTEGRATOR FOR FLEETS
------------------------------------
Modifies execute functions in workers and brains to dispatch alerts via telegram_notifier.py
"""

import sys
sys.path.append("/home/moonrider1968/bots/crypto_fleet")
from telegram_notifier import send_telegram_alert

send_telegram_alert("🚀 *System Alert:* Crypto & Stock Fleet Telegram Hooks Active and System Online!")
print("✅ Telegram notification pipeline active.")
