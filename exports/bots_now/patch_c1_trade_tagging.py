from pathlib import Path

p = Path("/home/moonrider1968/bots/oanda_practice_bot_c1.py")
code = p.read_text()

# Ensure tradeClientExtensions is included in create_market_order
old_order_block = """                "clientExtensions": {
                    "tag": client_tag,
                    "comment": f"C1_{client_tag}"
                }"""

new_order_block = """                "clientExtensions": {
                    "tag": client_tag,
                    "comment": f"C1_{client_tag}"
                },
                "tradeClientExtensions": {
                    "tag": client_tag,
                    "comment": f"C1_{client_tag}"
                }"""

if old_order_block in code:
    code = code.replace(old_order_block, new_order_block)
    p.write_text(code)
    print("Successfully patched tradeClientExtensions for open trade tracking.")
else:
    print("Block already patched or pattern modified.")
