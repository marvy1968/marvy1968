path = "/home/moonrider1968/bots/trade_velez_weinstein_swing.py"
with open(path, "r") as f:
    text = f.read()

# Alpaca API rule: Market entries must be DAY; child legs default to GTC
old_o1 = "order1 = MarketOrderRequest(\n            symbol=sym, qty=q1, side=OrderSide.BUY, time_in_force=TimeInForce.GTC,"
new_o1 = "order1 = MarketOrderRequest(\n            symbol=sym, qty=q1, side=OrderSide.BUY, time_in_force=TimeInForce.DAY,"

old_o2 = "order2 = MarketOrderRequest(\n            symbol=sym, qty=q2, side=OrderSide.BUY, time_in_force=TimeInForce.GTC,"
new_o2 = "order2 = MarketOrderRequest(\n            symbol=sym, qty=q2, side=OrderSide.BUY, time_in_force=TimeInForce.DAY,"

if "time_in_force=TimeInForce.GTC," in text:
    text = text.replace("order_class=OrderClass.BRACKET,\n            take_profit=", "order_class=OrderClass.BRACKET,\n            take_profit=")
    # Replace TIF for MarketOrderRequests
    import re
    text = re.sub(r"(MarketOrderRequest\([\s\S]*?time_in_force=TimeInForce\.)GTC", r"\1DAY", text)
    with open(path, "w") as f:
        f.write(text)
    print("[✓] MarketOrderRequest time_in_force updated to DAY (SDK compliant).")
else:
    print("[*] TIF already set properly.")
