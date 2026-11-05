#!/usr/bin/env python3
import os, sys, re, json, datetime, requests
from pathlib import Path
from dotenv import load_dotenv

for env_p in [Path("/home/moonrider1968/bots/.env"), Path("/home/moonrider1968/bots/killer_beast_sniper/.env")]:
    if env_p.exists():
        load_dotenv(env_p)
        break

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN") or os.getenv("TG_BOT_TOKEN") or os.getenv("TELEGRAM_TOKEN")
TG_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID") or os.getenv("TG_CHAT_ID")

LOG_FILES = {
    "Forex (OANDA)" : Path("/home/moonrider1968/bots/oanda_data/oanda_expert.log"),
    "Stocks (H1)"   : Path("/home/moonrider1968/bots/alpaca_data/stock_expert.log"),
    "Crypto (24/7)" : Path("/home/moonrider1968/bots/alpaca_data/crypto_expert.log"),
    "Morning Scalp" : Path("/home/moonrider1968/bots/alpaca_data/orb_morning.log")
}

def send_telegram(text: str):
    if not TG_TOKEN or not TG_CHAT_ID:
        print("⚠️ Telegram token/chat_id missing. Output:\n")
        print(text)
        return True
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    payload = {"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML", "disable_web_page_preview": True}
    try:
        r = requests.post(url, json=payload, timeout=12)
        if r.status_code == 200:
            print("✅ Telegram notification sent.")
            return True
        else:
            print(f"⚠️ Telegram API warning ({r.status_code}): {r.text}")
            print(text)
            return True
    except Exception as e:
        print(f"⚠️ Telegram Exception: {e}")
        print(text)
        return True

def extract_all_trades():
    trades = []
    exit_regex = re.compile(
        r"(?P<date>\d{4}-\d{2}-\d{2})\s+(?P<time>\d{2}:\d{2}:\d{2}).*?🛑\s+(?:ORB\s+|CRYPTO\s+)?EXIT\s+(?P<sym>[A-Z0-9_\/]+)\s+(?P<side>LONG|SHORT)\s+at\s+(?P<px>[\d\.]+)(?:\s+\((?P<reason>[^)]+)\))?.*?PnL:\s+\$(?P<pnl>[+-]?[\d,]+(?:\.\d+)?)"
    )
    for engine, log_p in LOG_FILES.items():
        if not log_p.exists():
            continue
        try:
            for line in log_p.read_text().splitlines():
                m = exit_regex.search(line)
                if m:
                    trades.append({
                        "engine": engine,
                        "date": m.group("date"),
                        "time": f"{m.group('date')} {m.group('time')}",
                        "symbol": m.group("sym"),
                        "side": m.group("side"),
                        "exit_px": float(m.group("px")),
                        "pnl": float(m.group("pnl").replace(",", "")),
                        "reason": m.group("reason") or "SL/Target"
                    })
        except Exception:
            pass
    return trades

def build_daily_reportcard():
    today = datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%d")
    all_trades = extract_all_trades()
    today_trades = [t for t in all_trades if t["date"] == today]

    tot_pnl = sum(t["pnl"] for t in today_trades)
    wins = [t["pnl"] for t in today_trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in today_trades if t["pnl"] <= 0]
    wr = (len(wins) / len(today_trades) * 100.0) if today_trades else 0.0
    pf = (sum(wins) / (abs(sum(losses)) + 1e-9)) if losses else (99.0 if wins else 0.0)

    status_icon = "🟢" if tot_pnl > 0 else ("🔴" if tot_pnl < 0 else "⚪")
    msg = [
        f"<b>{status_icon} MULTI-ASSET DAILY REPORT CARD</b>",
        f"📅 Date: <code>{today} UTC</code>",
        f"💰 Net PnL: <b>${tot_pnl:+,.2f}</b>",
        f"🎯 Win Rate: <b>{wr:.1f}%</b> ({len(wins)}W / {len(losses)}L)",
        f"📊 Profit Factor: <b>{pf:.2f}</b>",
        f"🔢 Total Trades: <b>{len(today_trades)}</b>",
        "━━━━━━━━━━━━━━━━━━"
    ]

    if today_trades:
        msg.append("<b>Recent Closed Executions:</b>")
        for t in today_trades[-8:]:
            icon = "✅" if t["pnl"] > 0 else "❌"
            msg.append(f"{icon} <code>{t['symbol']:<7}</code> {t['side']:<5} | ${t['pnl']:+,.2f} ({t['reason']})")
    else:
        msg.append("<i>No trades closed today. Systems active on standby.</i>")

    return "\n".join(msg)

def build_report7():
    today = datetime.datetime.now(datetime.timezone.utc).date()
    start_date = (today - datetime.timedelta(days=7)).strftime("%Y-%m-%d")
    all_trades = extract_all_trades()
    r7_trades = [t for t in all_trades if t["date"] >= start_date]

    tot_pnl = sum(t["pnl"] for t in r7_trades)
    wins = [t["pnl"] for t in r7_trades if t["pnl"] > 0]
    losses = [t["pnl"] for t in r7_trades if t["pnl"] <= 0]
    wr = (len(wins) / len(r7_trades) * 100.0) if r7_trades else 0.0
    pf = (sum(wins) / (abs(sum(losses)) + 1e-9)) if losses else (99.0 if wins else 0.0)

    status_icon = "🚀" if tot_pnl > 0 else "📉"
    msg = [
        f"<b>{status_icon} 7-DAY PERFORMANCE AUDIT (REPORT7)</b>",
        f"🗓 Period: <code>{start_date}</code> ➔ <code>{today.strftime('%Y-%m-%d')}</code>",
        f"💵 7-Day Net PnL: <b>${tot_pnl:+,.2f}</b>",
        f"🎯 7-Day Win Rate: <b>{wr:.1f}%</b> ({len(wins)}W / {len(losses)}L)",
        f"📈 Profit Factor: <b>{pf:.2f}</b> | Trades: <b>{len(r7_trades)}</b>",
        "━━━━━━━━━━━━━━━━━━"
    ]

    if r7_trades:
        msg.append("<b>Breakdown by Engine:</b>")
        engines = set(t["engine"] for t in r7_trades)
        for eng in sorted(engines):
            eng_trades = [t for t in r7_trades if t["engine"] == eng]
            pnl_sum = sum(t["pnl"] for t in eng_trades)
            eng_icon = "🟢" if pnl_sum >= 0 else "🔴"
            msg.append(f"{eng_icon} <b>{eng}</b>: ${pnl_sum:+,.2f} ({len(eng_trades)} trades)")
    else:
        msg.append("<i>No closed trades recorded in the last 7 days.</i>")

    return "\n".join(msg)

if __name__ == "__main__":
    mode = sys.argv[1].lower() if len(sys.argv) > 1 else "daily"
    if mode in ("report7", "r7", "7day"):
        text = build_report7()
    else:
        text = build_daily_reportcard()
    send_telegram(text)
    sys.exit(0)
