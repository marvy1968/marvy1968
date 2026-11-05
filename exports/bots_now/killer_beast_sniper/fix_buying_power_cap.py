
# ALPACA_PAPER_ONLY_GUARD_V1
import sys as _alpaca_guard_sys
from pathlib import Path as _AlpacaGuardPath
_alpaca_guard_home = _AlpacaGuardPath.home()
if str(_alpaca_guard_home / 'bots') not in _alpaca_guard_sys.path:
    _alpaca_guard_sys.path.insert(0, str(_alpaca_guard_home / 'bots'))
from alpaca_paper_only_guard import enforce_paper_only as _enforce_alpaca_paper_only
_enforce_alpaca_paper_only(__file__)
# END ALPACA_PAPER_ONLY_GUARD_V1

import re

EXECUTOR_PATH = "/home/moonrider1968/bots/killer_beast_sniper/alpaca_paper_executor.py"

with open(EXECUTOR_PATH, "r") as f:
    content = f.read()

new_submit_func = """def submit_alpaca_bracket_order(ticker, strategy, side, current_price):
    equity = get_account_equity()
    risk_pct = get_dynamic_risk_pct(ticker, strategy, side)
    
    stop_price, limit_price, sl_pct, tp_pct = calculate_bracket_params(ticker, strategy, current_price, side)
    risk_dollars = equity * risk_pct
    price_risk = abs(current_price - stop_price)
    
    # Calculate raw share quantity based on Kelly risk
    raw_qty = int(risk_dollars / price_risk) if price_risk > 0 else 1
    
    # Safety Cap: Never exceed 25% of total equity per single scalp trade
    max_notional = equity * 0.25
    max_qty = max(int(max_notional / current_price), 1)
    
    qty = max(min(raw_qty, max_qty), 1)

    order_payload = {
        "symbol": ticker,
        "qty": qty,
        "side": side.lower(),
        "type": "market",
        "time_in_force": "gtc",
        "order_class": "bracket",
        "take_profit": {"limit_price": str(limit_price)},
        "stop_loss": {"stop_price": str(stop_price)}
    }

    try:
        url = f"{ALPACA_PAPER_URL}/v2/orders"
        r = requests.post(url, headers=HEADERS, json=order_payload, timeout=5)
        if r.status_code in [200, 201]:
            data = r.json()
            log_event(f"✅ [DYNAMIC KELLY EXECUTION] {ticker} | {side.upper()} {qty} shares (~${qty*current_price:,.2f} Notional) @ ~${current_price:.2f} | "
                      f"SL: ${stop_price:.2f} | TP: ${limit_price:.2f} | "
                      f"Order ID: {data.get('id')}")
            return True
        else:
            log_event(f"❌ [ALPACA REJECTED] {ticker}: {r.status_code} - {r.text}")
    except Exception as e:
        log_event(f"Execution Exception for {ticker}: {e}")
    return False"""

content = re.sub(r'def submit_alpaca_bracket_order\(.*', new_submit_func, content, flags=re.DOTALL)

with open(EXECUTOR_PATH, "w") as f:
    f.write(content)

print("✅ Added buying power & notional position caps to submit_alpaca_bracket_order.")
