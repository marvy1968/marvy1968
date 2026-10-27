#!/usr/bin/env python3
"""Send-only daily Telegram scorecard for fx-experts (same pattern as /opt/scorecards/scorecard.py:
no polling, --dry-run prints only).  Reads closed/open trades from OANDA practice, grouped by
system via the trade clientExtensions comment."""
import argparse, json, os, sys, urllib.parse, urllib.request
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from .live import load_env
from .oanda import Oanda
ET, CT = ZoneInfo("America/New_York"), ZoneInfo("America/Chicago")

def build(rows, opens, now, systems):
    today = now.astimezone(ET).date(); wk0 = today - timedelta(days=today.weekday())
    out = [f"[PAPER] fx-experts (OANDA practice) — scorecard {now.astimezone(CT):%a %b %d %H:%M} CT"]
    for sid in systems + ["ALL"]:
        ts = [r for r in rows if sid == "ALL" or r["sid"] == sid]
        d = [t for t in ts if t["closed"].astimezone(ET).date() == today]
        wk = [t for t in ts if t["closed"].astimezone(ET).date() >= wk0]
        win = 100.0 * sum(1 for t in ts if t["pl"] > 0) / len(ts) if ts else 0.0
        no = sum(1 for o in opens if (sid == "ALL" and o in systems) or o == sid)
        out.append(f"{sid}: day {sum(t['pl'] for t in d):+,.2f} ({len(d)}) | week {sum(t['pl'] for t in wk):+,.2f} ({len(wk)}) | "
                   f"total {sum(t['pl'] for t in ts):+,.2f} ({len(ts)}, win {win:.0f}%) | open {no}")
    return "\n".join(out)

def send(env, text):
    tok, chat = env.get("TELEGRAM_BOT_TOKEN"), env.get("TELEGRAM_CHAT_ID")
    if not tok or not chat: raise SystemExit("telegram token/chat missing")
    data = urllib.parse.urlencode(dict(chat_id=chat, text=text, disable_web_page_preview="true")).encode()
    r = json.load(urllib.request.urlopen(f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=30))
    if not r.get("ok"): raise SystemExit("telegram send failed")

def main():
    a = argparse.ArgumentParser(); a.add_argument("--env", required=True); a.add_argument("--config", required=True)
    a.add_argument("--dry-run", action="store_true"); a = a.parse_args()
    env = load_env(a.env); cfg = json.load(open(a.config)); now = datetime.now(timezone.utc)
    if env.get("OANDA_ENV", "practice") != "practice": sys.exit("practice only")
    rows, opens, seen = [], [], set()
    for sid in cfg["systems"]:
        acct = env.get(f"OANDA_ACCOUNT_{sid.upper().replace('-', '_')}") or env["OANDA_ACCOUNT_ID"]
        if acct in seen: continue
        seen.add(acct); cli = Oanda(env["OANDA_TOKEN"], acct, "practice", dry_run=True)
        for t in cli.closed_trades(500):
            s = t.get("clientExtensions", {}).get("comment")
            if s in cfg["systems"] and t.get("closeTime"):
                rows.append({"sid": s, "pl": float(t.get("realizedPL", 0)) + float(t.get("financing", 0)),
                             "closed": datetime.fromisoformat(t["closeTime"][:26].rstrip("Z") + "+00:00")})
        opens += [t.get("clientExtensions", {}).get("comment") for t in cli.open_trades()]
    text = build(rows, opens, now, cfg["systems"]); print(text, flush=True)
    if a.dry_run: print("[dry-run] not sent"); return
    send(env, text); print("[sent]")

if __name__ == "__main__": main()
