import os

file_name = "telegram_sender.py"
with open(file_name, "r", encoding="utf-8") as f:
    content = f.read()

# 1. Update Team Prop Layout
old_team = """    return (
        f"📊 <b>TEAM PROP [{strength}]</b>\\n"
        f"{game}\\n"
        f"{team} team total {side} {line}\\n"
        f"edge={edge} | conf={conf} | ev={ev} | bankroll={bankroll}{proj_text}{suspicious_text}"
    )"""

new_team = """    return (
        f"🔥 <b>TEAM PROP [{strength}]</b> | 📈 <b>EDGE: {edge}</b>\\n"
        f"⚔️ <b>{game}</b>\\n"
        f"🎯 <b>PLAY: {team} Total {side} {line}</b>\\n\\n"
        f"💰 <b>THE MATH</b>\\n"
        f"EV: {ev} | Conf: {conf}\\n"
        f"💵 <b>Stake: {bankroll} Bankroll</b>{proj_text}{suspicious_text}"
    )"""

# 2. Update Generic/Fallback Layout
old_generic = """    return (
        f"📌 <b>ALERT [{strength}]</b>\\n"
        f"{game}\\n"
        f"{market} | {selection}\\n"
        f"edge={edge} | conf={conf} | ev={ev} | bankroll={bankroll}"
    )"""

new_generic = """    return (
        f"📌 <b>ALERT [{strength}]</b> | 📈 <b>EDGE: {edge}</b>\\n"
        f"⚔️ <b>{game}</b>\\n"
        f"🎯 <b>PLAY: {market} | {selection}</b>\\n\\n"
        f"💰 <b>THE MATH</b>\\n"
        f"EV: {ev} | Conf: {conf}\\n"
        f"💵 <b>Stake: {bankroll} Bankroll</b>"
    )"""

if old_team in content:
    content = content.replace(old_team, new_team)
    print("✅ Team Prop layout updated.")

if old_generic in content:
    content = content.replace(old_generic, new_generic)
    print("✅ Generic layout updated.")

with open(file_name, "w", encoding="utf-8") as f:
    f.write(content)
