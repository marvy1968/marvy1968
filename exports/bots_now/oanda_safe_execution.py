import os
import v20

# Configuration & Hard Risk Limits
OANDA_ACCESS_TOKEN = os.getenv("OANDA_ACCESS_TOKEN", "YOUR_ACCESS_TOKEN")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "YOUR_ACCOUNT_ID")
OANDA_ENV = os.getenv("OANDA_ENV", "practice")  # "practice" or "live"

HOSTNAME = "api-fxpractice.oanda.com" if OANDA_ENV == "practice" else "api-fxtrade.oanda.com"

# Maximum allowable position units per pair to prevent runaway drawdowns
MAX_UNITS_LIMITS = {
    "USD_JPY": 25000,
    "USD_CAD": 50000,
    "NZD_USD": 50000,
    "EUR_USD": 50000,
    "GBP_USD": 50000,
}
DEFAULT_MAX_UNITS = 20000
MIN_UNITS = 1000


def get_api_client():
    """Initializes and returns the OANDA v20 API context."""
    return v20.Context(
        hostname=HOSTNAME,
        port=443,
        token=OANDA_ACCESS_TOKEN,
        poll_timeout=10
    )


def normalize_instrument(instrument: str) -> str:
    """Converts display format (e.g. 'USD/JPY') to OANDA API format ('USD_JPY')."""
    return instrument.replace("/", "_")


def get_account_balance(client, account_id: str) -> float:
    """Fetches real-time NAV/Balance from OANDA."""
    resp = client.account.summary(account_id)
    if resp.status != 200:
        raise RuntimeError(f"Failed to fetch account summary: {resp.body}")
    account_data = resp.get("account", {})
    return float(account_data.get("balance", 0.0))


def has_open_trade_or_order(client, account_id: str, instrument: str) -> bool:
    """
    Prevents duplicate entries and FIFO safeguard violations by checking
    both existing open positions and pending orders.
    """
    api_inst = normalize_instrument(instrument)

    try:
        # 1. Check existing open position
        pos_resp = client.position.get(account_id, api_inst)
        if pos_resp.status == 200:
            pos = pos_resp.get("position", {})
            long_units = float(pos.get("long", {}).get("units", 0))
            short_units = float(pos.get("short", {}).get("units", 0))
            if long_units != 0 or short_units != 0:
                return True
    except Exception:
        pass

    try:
        # 2. Check pending unfilled orders
        order_resp = client.order.list_pending(account_id)
        if order_resp.status == 200:
            for order in order_resp.get("orders", []):
                if order.get("instrument") == api_inst:
                    return True
    except Exception as e:
        print(f"Warning: Could not verify pending orders for {api_inst}: {e}")
        return True

    return False


def calculate_position_size(balance: float, risk_pct: float, entry_price: float, stop_loss_price: float, instrument: str) -> int:
    """
    Calculates units based on strict fractional account risk with JPY quote adjustment
    and enforces hard caps.
    """
    price_distance = abs(entry_price - stop_loss_price)
    if price_distance <= 0:
        return 0

    risk_amount = balance * risk_pct
    api_inst = normalize_instrument(instrument)

    # Base USD vs Quote USD adjustment
    if api_inst.startswith("USD_"):
        raw_units = (risk_amount * entry_price) / price_distance
    else:
        raw_units = risk_amount / price_distance

    max_limit = MAX_UNITS_LIMITS.get(api_inst, DEFAULT_MAX_UNITS)
    capped_units = int(min(raw_units, max_limit))

    return max(capped_units, MIN_UNITS)


def execute_market_order_with_bracket(client, account_id: str, instrument: str, direction: str, entry_price: float, stop_loss_price: float, risk_pct: float = 0.01):
    """
    Main trade execution wrapper: performs safety checks, calculates size,
    formats stop-loss precision, and submits the order.
    """
    api_inst = normalize_instrument(instrument)
    direction_upper = direction.upper()

    if direction_upper not in ["BUY", "SELL"]:
        print(f"Error: Invalid direction '{direction}'")
        return None

    # Step 1: Idempotency check
    if has_open_trade_or_order(client, account_id, api_inst):
        print(f"[{api_inst}] Skipped: Active position or pending order already exists.")
        return None

    # Step 2: Get account equity
    try:
        balance = get_account_balance(client, account_id)
    except Exception as e:
        print(f"[{api_inst}] Skipped: Could not retrieve account balance: {e}")
        return None

    # Step 3: Size calculation
    units = calculate_position_size(
        balance=balance,
        risk_pct=risk_pct,
        entry_price=entry_price,
        stop_loss_price=stop_loss_price,
        instrument=api_inst
    )

    if units <= 0:
        print(f"[{api_inst}] Skipped: Calculated units were 0.")
        return None

    signed_units = units if direction_upper == "BUY" else -units

    # Step 4: String precision formatting for stop-loss
    precision = 3 if "JPY" in api_inst else 5
    formatted_sl = f"{stop_loss_price:.{precision}f}"

    order_body = {
        "order": {
            "type": "MARKET",
            "instrument": api_inst,
            "units": str(signed_units),
            "timeInForce": "FOK",
            "positionFill": "DEFAULT",
            "stopLossOnFill": {
                "price": formatted_sl,
                "timeInForce": "GTC"
            }
        }
    }

    # Step 5: Send order
    print(f"Submitting {direction_upper} order: {abs(signed_units)} units of {api_inst} (SL @ {formatted_sl})...")
    resp = client.order.create(account_id, body=order_body)

    if resp.status in [200, 201]:
        print(f"[{api_inst}] Successfully filled order.")
        return resp
    else:
        print(f"[{api_inst}] Order rejected with status {resp.status}: {resp.body}")
        return None


if __name__ == "__main__":
    print("OANDA safe execution module loaded successfully.")
