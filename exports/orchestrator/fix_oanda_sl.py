import re

file_path = "/home/moonrider1968/orchestrator/deploy_brain_fleet.py"
with open(file_path, "r") as f:
    code = f.read()

# Unified OANDA v3 Stop Loss payload using PUT on /orders/{orderSpecifier}
new_func = """    @staticmethod
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
        # Fallback payload structure for V3 trade modification endpoint
        alt_url = f"{BASE_URL}/accounts/{ACCOUNT_ID}/trades/{trade_id}/orders"
        alt_body = {
            "stopLoss": {
                "timeInForce": "GTC",
                "price": f"{new_sl:.3f}"
            }
        }
        
        req = urllib.request.Request(
            alt_url,
            data=json.dumps(alt_body).encode('utf-8'),
            headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
            method="PUT"
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return json.loads(resp.read().decode())
        except urllib.error.HTTPError as err:
            if err.code == 400:
                # If existing Stop Loss order exists, update via PUT on /accounts/{ACCOUNT_ID}/orders
                req_post = urllib.request.Request(
                    url,
                    data=json.dumps(order_body).encode('utf-8'),
                    headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json"},
                    method="POST"
                )
                try:
                    with urllib.request.urlopen(req_post) as resp2:
                        return json.loads(resp2.read().decode())
                except Exception as e2:
                    logging.error(f"Stop Loss Modification Error: {e2}")
                    return None
            logging.error(f"Stop Loss Update Error: {err}")
            return None
        except Exception as e:
            logging.error(f"Stop Loss General Error: {e}")
            return None"""

# Replace function block cleanly
pattern = r"    @staticmethod\n    def update_trade_stop_loss\(trade_id, new_sl\):.*?(?=\n    @staticmethod|\nclass|\ndef main)"
updated_code = re.sub(pattern, new_func, code, flags=re.DOTALL)

with open(file_path, "w") as f:
    f.write(updated_code)

print("✅ OANDA SL API Handler successfully updated with dual POST/PUT fallback!")
