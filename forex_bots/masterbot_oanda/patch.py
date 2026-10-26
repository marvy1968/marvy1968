import os

file_name = "telegram_sender.py"
with open(file_name, "r", encoding="utf-8") as f:
    content = f.read()

old_code = """    diff_text = ""
    if sys_val is not None and mkt_val is not None:
        try:
            diff_text = f"\\n📊 model={round(float(sys_val), 2)} | market={round(float(mkt_val), 2)}"
        except (ValueError, TypeError):
            pass

    return (
        f"🔥 <b>EDGE [{strength}]</b>\\n"
        f"{sport_tag} | <b>{game}</b>\\n"
        f"<b>{market}</b> | {team}{line_text}{diff_text}\\n"
        f"edge={edge} | conf={conf} | ev={ev} | bankroll={bankroll}{suspicious_text}"
    )"""

new_code = """    diff_text = ""
    if sys_val is not None and mkt_val is not None:
        try:
            diff_text = (
                f"\\n\\n📊 <b>THE LINE</b>\\n"
                f"🏛️ Market: {round(float(mkt_val), 2)}\\n"
                f"🤖 Bot Fair: {round(float(sys_val), 2)}"
            )
        except (ValueError, TypeError):
            pass

    return (
        f"🔥 <b>GRADE [{strength}]</b> | 📈 <b>EDGE: {edge}</b>\\n"
        f"{sport_tag} | <b>{game}</b>\\n"
        f"🎯 <b>PLAY: {market} | {team}{line_text}</b>{diff_text}\\n\\n"
        f"💰 <b>THE MATH</b>\\n"
        f"EV: {ev} | Conf: {conf}\\n"
        f"💵 <b>Stake: {bankroll} Bankroll</b>{suspicious_text}"
    )"""

if old_code in content:
    content = content.replace(old_code, new_code)
    with open(file_name, "w", encoding="utf-8") as f:
        f.write(content)
    print("✅ SUCCESS: Alert format upgraded to the Pro Layout!")
else:
    print("⚠️ FAILED: Could not find the exact code block. It might already be changed.")

