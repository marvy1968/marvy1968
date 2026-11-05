import os, re, json, glob
from datetime import datetime

BASE_DIR = "/home/moonrider1968/bots/killer_beast_sniper"
LOG_DIR = os.path.join(BASE_DIR, "logs")
STATE_FILE = os.path.join(BASE_DIR, "learning_matrix_state.json")

def parse_logs():
    stats = {}
    total_equities_signals = 0
    total_forex_signals = 0
    total_crypto_signals = 0

    def init_sym(s, asset_type):
        if s not in stats:
            stats[s] = {
                "asset_type": asset_type,
                "wins": 0,
                "losses": 0,
                "win_rate": 0.0,
                "consecutive_wins": 0,
                "consecutive_failures": 0,
                "total_trades": 0,
                "pnl_r": 0.0
            }

    # 1. Parse Equities Logs
    eq_logs = glob.glob(os.path.join(LOG_DIR, "*scalper*.log"))
    for lfile in eq_logs:
        if not os.path.exists(lfile) or "oanda" in os.path.basename(lfile):
            continue
        try:
            with open(lfile, "r", errors="ignore") as f:
                for line in f:
                    if any(k in line for k in ["SIGNAL", "ENTERED", "DRY RUN ORDER", "PAPER ORDER"]):
                        total_equities_signals += 1

                    m_win = re.search(r'🟢\s*\[([A-Z0-9_\-\.]+)\].*TARGET HIT.*?\+([\d\.]+)R', line)
                    if m_win:
                        sym = m_win.group(1).upper()
                        r_gain = float(m_win.group(2))
                        init_sym(sym, "Equities")
                        stats[sym]["wins"] += 1
                        stats[sym]["total_trades"] += 1
                        stats[sym]["pnl_r"] += r_gain
                        stats[sym]["consecutive_wins"] += 1
                        stats[sym]["consecutive_failures"] = 0

                    m_loss = re.search(r'🔴\s*\[([A-Z0-9_\-\.]+)\].*STOP HIT.*?(-?[\d\.]+)R', line)
                    if m_loss:
                        sym = m_loss.group(1).upper()
                        r_loss = float(m_loss.group(2))
                        init_sym(sym, "Equities")
                        stats[sym]["losses"] += 1
                        stats[sym]["total_trades"] += 1
                        stats[sym]["pnl_r"] += r_loss
                        stats[sym]["consecutive_failures"] += 1
                        stats[sym]["consecutive_wins"] = 0
        except Exception:
            pass

    # 2. Parse OANDA Forex Logs
    oanda_logs = glob.glob(os.path.join(LOG_DIR, "*oanda*.log"))
    for lfile in oanda_logs:
        if not os.path.exists(lfile):
            continue
        try:
            with open(lfile, "r", errors="ignore") as f:
                for line in f:
                    if any(k in line for k in ["SIGNAL", "ENTERED", "LIVE ORDER"]):
                        total_forex_signals += 1

                    m_win = re.search(r'🟢\s*\[([A-Z0-9_]+)\].*TARGET HIT.*?\+([\d\.]+)R', line)
                    if m_win:
                        sym = m_win.group(1).upper()
                        r_gain = float(m_win.group(2))
                        init_sym(sym, "Forex")
                        stats[sym]["wins"] += 1
                        stats[sym]["total_trades"] += 1
                        stats[sym]["pnl_r"] += r_gain
                        stats[sym]["consecutive_wins"] += 1
                        stats[sym]["consecutive_failures"] = 0

                    m_loss = re.search(r'🔴\s*\[([A-Z0-9_]+)\].*STOP HIT.*?-([\d\.]+)R', line)
                    if m_loss:
                        sym = m_loss.group(1).upper()
                        r_loss = -float(m_loss.group(2))
                        init_sym(sym, "Forex")
                        stats[sym]["losses"] += 1
                        stats[sym]["total_trades"] += 1
                        stats[sym]["pnl_r"] += r_loss
                        stats[sym]["consecutive_failures"] += 1
                        stats[sym]["consecutive_wins"] = 0
        except Exception:
            pass

    # Calculate Win Rates
    for sym, d in stats.items():
        decided = d["wins"] + d["losses"]
        d["win_rate"] = round(d["wins"] / decided, 2) if decided > 0 else 0.0
        d["pnl_r"] = round(d["pnl_r"], 2)

    with open(STATE_FILE, "w") as sf:
        json.dump(stats, sf, indent=2)

    return stats, total_equities_signals, total_forex_signals, total_crypto_signals

def get_report_card_text():
    stats, eq_sig, fx_sig, cr_sig = parse_logs()
    
    lines = [
        "📊 <b>KILLER BEAST SNIPER — SYSTEM REPORT CARD</b>",
        "======================================",
        "🧠 <b>LEARNING MATRIX STATUS:</b> Active & Tracking\n"
    ]
    
    eq_items = {k: v for k, v in stats.items() if v["asset_type"] == "Equities"}
    fx_items = {k: v for k, v in stats.items() if v["asset_type"] == "Forex"}

    if eq_items:
        lines.append("🏛️ <b>EQUITIES RUNTIME:</b>")
        for sym, d in sorted(eq_items.items()):
            flag = "❄️ FROZEN" if d["consecutive_failures"] >= 3 else ("🔥 HOT" if d["consecutive_wins"] >= 2 else "⚡ ACTIVE")
            lines.append(f"   • <b>{sym}</b> [{flag}]: {d['wins']}W / {d['losses']}L | WR: {d['win_rate']*100:.0f}% | Streak: W{d['consecutive_wins']}/L{d['consecutive_failures']} | PnL: {d['pnl_r']:+.2f}R")

    if fx_items:
        lines.append("\n🌍 <b>OANDA FOREX RUNTIME:</b>")
        for sym, d in sorted(fx_items.items()):
            flag = "❄️ FROZEN" if d["consecutive_failures"] >= 3 else ("🔥 HOT" if d["consecutive_wins"] >= 2 else "⚡ ACTIVE")
            lines.append(f"   • <b>{sym}</b> [{flag}]: {d['wins']}W / {d['losses']}L | WR: {d['win_rate']*100:.0f}% | Streak: W{d['consecutive_wins']}/L{d['consecutive_failures']} | PnL: {d['pnl_r']:+.2f}R")

    lines.extend([
        "--------------------------------------",
        f"📈 <b>Recorded Signals:</b> {eq_sig + fx_sig + cr_sig}",
        f"   • Equities : {eq_sig}",
        f"   • Forex    : {fx_sig}",
        f"   • Crypto   : {cr_sig}",
        "======================================"
    ])
    
    return "\n".join(lines)

if __name__ == "__main__":
    print(get_report_card_text())
