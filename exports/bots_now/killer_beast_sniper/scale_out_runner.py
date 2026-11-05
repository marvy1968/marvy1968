import math
import logging
from alpaca.trading.requests import ClosePositionRequest, MarketOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce

def execute_70_30_scale_out(trade_client, sym: str, pos_data: dict, current_price: float, atr: float) -> dict:
    """
    Liquidates 70% of position at +0.85R, moves SL to Break-Even for the 30% runner.
    """
    total_qty = int(pos_data.get("qty", 1))
    side = pos_data.get("side", "long").lower()
    entry_price = float(pos_data.get("entry_price", current_price))
    risk_per_share = float(pos_data.get("risk_per_share", 0.05))

    if total_qty <= 1:
        logging.info(f"⚡ [FULL CLOSE] {sym} quantity ({total_qty}) too small to split. Closing 100%.")
        try:
            trade_client.close_position(symbol_or_asset_id=sym)
        except Exception as e:
            logging.error(f"Error closing single unit position {sym}: {e}")
        return {"status": "CLOSED", "remaining_qty": 0}

    t1_qty = max(1, math.floor(total_qty * 0.70))
    runner_qty = total_qty - t1_qty

    logging.info(
        f"🎯 [T1 HIT] {sym} @ {current_price:.2f} (+0.85R) | "
        f"Liquidating {t1_qty}/{total_qty} units (70%) | "
        f"Retaining {runner_qty} units as 1.85 ATR trailing runner"
    )

    try:
        close_req = ClosePositionRequest(qty=str(t1_qty))
        trade_client.close_position(symbol_or_asset_id=sym, close_options=close_req)
        logging.info(f"✅ [ALPACA PARTIAL FILL] Liquidated {t1_qty} shares of {sym}")
    except Exception as e:
        logging.warning(f"⚠️ ClosePositionRequest fallback to market order: {e}")
        try:
            opposing_side = OrderSide.SELL if side == "long" else OrderSide.BUY
            market_order = MarketOrderRequest(
                symbol=sym,
                qty=t1_qty,
                side=opposing_side,
                time_in_force=TimeInForce.DAY
            )
            trade_client.submit_order(market_order)
        except Exception as err:
            logging.error(f"Fallback market order failed for {sym}: {err}")

    return {
        "sym": sym,
        "side": side,
        "qty": runner_qty,
        "entry_price": entry_price,
        "risk_per_share": risk_per_share,
        "phase": "RUNNER_TRAILING",
        "stop_loss": entry_price,
        "trailing_atr_mult": 1.85,
        "peak_price": current_price,
        "realized_t1_pnl": ((current_price - entry_price) * t1_qty if side == "long" else (entry_price - current_price) * t1_qty)
    }

def update_trailing_runner(trade_client, sym: str, runner: dict, current_price: float, current_atr: float):
    side = runner.get("side", "long")
    sl = float(runner.get("stop_loss", runner.get("entry_price", current_price)))
    peak = float(runner.get("peak_price", current_price))
    trail_dist = current_atr * runner.get("trailing_atr_mult", 1.85)

    if side == "long":
        if current_price > peak:
            runner["peak_price"] = current_price
            new_sl = max(runner["entry_price"], current_price - trail_dist)
            if new_sl > sl:
                runner["stop_loss"] = new_sl
                logging.info(f"📈 [TRAIL RATCHET] {sym} Long SL -> ${new_sl:.4f}")
        elif current_price <= sl:
            logging.info(f"🛑 [RUNNER STOPPED] {sym} Long triggered SL @ ${current_price:.4f}")
            try:
                trade_client.close_position(symbol_or_asset_id=sym)
            except Exception as e:
                logging.error(f"Failed to close stopped runner {sym}: {e}")
            return None

    elif side == "short":
        if current_price < peak:
            runner["peak_price"] = current_price
            new_sl = min(runner["entry_price"], current_price + trail_dist)
            if new_sl < sl:
                runner["stop_loss"] = new_sl
                logging.info(f"📉 [TRAIL RATCHET] {sym} Short SL -> ${new_sl:.4f}")
        elif current_price >= sl:
            logging.info(f"🛑 [RUNNER STOPPED] {sym} Short triggered SL @ ${current_price:.4f}")
            try:
                trade_client.close_position(symbol_or_asset_id=sym)
            except Exception as e:
                logging.error(f"Failed to close stopped runner {sym}: {e}")
            return None

    return runner
