#!/usr/bin/env python3
import subprocess
import os
import time
import json
import urllib.request
import urllib.parse

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "REDACTED_TELEGRAM_TOKEN")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "7922925421")

SERVICES = [
    "rpp-scanner.service",
    "oanda-execution.service",
    "oanda-trailing.service",
    ]

CACHE_FILE = "/tmp/fleet_alert_cache.json"
COOLDOWN_SEC = 900  # 15 minutes between alerts per service

def load_cache():
    if os.path.exists(CACHE_FILE):
        try:
            with open(CACHE_FILE, 'r') as f:
                return json.load(f)
        except Exception:
            return {}
    return {}

def save_cache(cache):
    try:
        with open(CACHE_FILE, 'w') as f:
            json.dump(cache, f)
    except Exception:
        pass

def send_telegram(message):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    payload = urllib.parse.urlencode({
        "chat_id": TELEGRAM_CHAT_ID,
        "text": message,
        "parse_mode": "Markdown"
    }).encode('utf-8')
    
    try:
        req = urllib.request.Request(url, data=payload)
        with urllib.request.urlopen(req, timeout=5) as resp:
            pass
    except Exception as e:
        print(f"Error sending Telegram alert: {e}", flush=True)

def check_services():
    now = time.time()
    cache = load_cache()
    
    for svc in SERVICES:
        cmd = ["systemctl", "is-active", svc]
        res = subprocess.run(cmd, capture_output=True, text=True)
        status = res.stdout.strip()
        
        if status != "active":
            last_alert = cache.get(svc, 0)
            if now - last_alert > COOLDOWN_SEC:
                msg = f"⚠️ *FLEET ALERT*: Service `{svc}` is DOWN!\n*Status:* `{status}`\n*Host:* `instance-stockbot`"
                print(f"[{time.strftime('%Y-%m-%d %H:%M:%S')}] {msg}", flush=True)
                send_telegram(msg)
                cache[svc] = now
        else:
            # Clear cache when service recovers
            if svc in cache:
                del cache[svc]
                
    save_cache(cache)

if __name__ == "__main__":
    check_services()
