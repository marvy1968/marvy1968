import re

file_path = "/home/moonrider1968/orchestrator/deploy_brain_fleet.py"
with open(file_path, "r") as f:
    code = f.read()

# Replace update_trade_stop_loss method with corrected OANDA V3 endpoint format
old_func = """    @staticmethod
    def update_trade_stop_loss(trade_id, new_sl):
        url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/orders"
        order_body = {
            "order": {
                "timeInForce": "GTC",
                "type": "STOP_LOSS",
                "tradeID": str(trade_id),
                "price": f"{new_sl:.3f}"
            }
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(order_body).encode('utf-8'),
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            method="POST"
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            logging.error(f"Stop Loss Update Error: {e}")
            return None"""

new_func = """    @staticmethod
    def update_trade_stop_loss(trade_id, new_sl):
        url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/trades/{trade_id}/orders"
        order_body = {
            "stopLoss": {
                "timeInForce": "GTC",
                "price": f"{new_sl:.3f}"
            }
        }
        req = urllib.request.Request(
            url,
            data=json.dumps(order_body).encode('utf-8'),
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            method="PUT"
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode())
        except Exception as e:
            logging.error(f"Stop Loss Update Error: {e}")
            return None"""

if "accounts/{ACCOUNT_ID}/trades/{trade_id}/orders" not in code:
    code = code.replace(old_func, new_func)
    with open(file_path, "w") as f:
        f.write(code)
    print("✅ Stop Loss update payload successfully patched!")
