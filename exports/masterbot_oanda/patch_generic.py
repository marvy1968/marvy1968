import os

file_name = "telegram_sender.py"
with open(file_name, "r", encoding="utf-8") as f:
    content = f.read()

old_code = """    return (
        f"📌 <b>ALERT [{strength}]</b>\n"
        f"{game}\n"
        f"{market} | {selection}\n"
        f"edge={edge} | conf={conf} | ev={ev} | bankroll={bankroll}"
    )"""

new_code = """    return (
        f"📌 <b>ALERT [{strength}]</b> | 📈 <b>EDGE: {edge}</b>\n"
        f"⚔️ <b>{game}</b>\n"
        f"🎯 <b>PLAY: {market} | {selection}</b>\n\n"
        f"💰 <b>THE MATH</b>\n"
        f"EV: {ev} | Conf: {conf}\n"
        f"💵 <b>Stake: {bankroll} Bankroll</b>"
    )"""

if old_code in content:
    content = content.replace(old_code, new_code)
    with open(file_name, "w", encoding="utf-8") as f:
        f.write(content)
    print("✅ SUCCESS: The ghost alert has been upgraded to the Pro Layout!")
else:
    print("⚠️ FAILED: Could not find the generic block.")
