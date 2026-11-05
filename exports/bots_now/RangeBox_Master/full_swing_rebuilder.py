#!/usr/bin/env python3
import os

file_path = "/home/moonrider1968/alpaca_swing_velez_weinstein.py"
if os.path.exists(file_path):
    with open(file_path, "r") as f:
        code = f.read()

    # Define the exact text signature to target for replacement from line 127 down
    target_block = """            if in_stage2_transition and is_elephant_bar:
                stop_loss = last_candle['low']
                entry_price = last_candle['close']
                risk_per_share = entry_price - stop_loss

                if risk_per_share <= 0: return

                risk_cash = self.equity * SwingConfig.RISK_PER_TRADE_PCT
                qty = int(risk_cash // risk_per_share)

                if qty > 0:
                    log.info(f"🚀 [STAGE 2 SWING ENTRY] {symbol} | Elephant Bar Ignition | Price: {entry_price} | Qty: {qty} | SL Target: {stop_loss}")
                    from alpaca.trading.requests import MarketOrderRequest
                    from alpaca.trading.enums import OrderSide, TimeInForce
                    order = MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.GTC)
                    self.trade_client.submit_order(order)"""

    # We replace it with a structurally sound block featuring immaculate 12-space and 16-space tabs
    upgraded_block = """            # ─── EXTENDED CONTINGENCY: VELEZ 20-EMA PULLBACK CHASER ───
            is_established_stage2 = last_candle['close'] > last_candle['ema_velez'] and last_candle['ema_velez'] > last_candle['ma_stage']
            is_pullback_touch = last_candle['low'] <= last_candle['ema_velez'] and last_candle['close'] >= (last_candle['ema_velez'] * 0.995)
            
            trigger_entry = False
            reason = ""
            stop_loss = 0.0
            entry_price = last_candle['close']
            
            if in_stage2_transition and is_elephant_bar:
                trigger_entry = True
                reason = "STAGE_2_INITIAL_BREAKOUT"
                stop_loss = last_candle['low']
            elif is_established_stage2 and is_pullback_touch:
                trigger_entry = True
                reason = "STAGE_2_20-EMA_PULLBACK_CHASE"
                stop_loss = last_candle['ema_velez'] - (last_candle['candle_range'] * 0.5)

            if trigger_entry:
                risk_per_share = entry_price - stop_loss
                if risk_per_share <= 0: return

                risk_cash = self.equity * SwingConfig.RISK_PER_TRADE_PCT
                qty = int(risk_cash // risk_per_share)

                if qty > 0:
                    log.info(f"🚀 [{reason}] MATCHED | {symbol} | Price: {entry_price:.2f} | Qty: {qty} | SL: {stop_loss:.2f}")
                    from alpaca.trading.requests import MarketOrderRequest
                    from alpaca.trading.enums import OrderSide, TimeInForce
                    order = MarketOrderRequest(symbol=symbol, qty=qty, side=OrderSide.BUY, time_in_force=TimeInForce.GTC)
                    self.trade_client.submit_order(order)"""

    if target_block in code:
        code = code.replace(target_block, upgraded_block)
        with open(file_path, "w") as f:
            f.write(code)
        print("✅ Swing core rebuilt with perfect structural indentation syntax.")
    else:
        # Fallback if text was partially modified by previous patch attempts
        print("⚠️ Direct text match not found. Restoring clean blueprint copy first...")
        os.system("cp /home/moonrider1968/alpaca_swing_velez_weinstein.py.bak /home/moonrider1968/alpaca_swing_velez_weinstein.py 2>/dev/null")
        # Let's read it fresh and force it down
        with open(file_path, "r") as f:
            code = f.read()
        code = code.replace(target_block, upgraded_block)
        with open(file_path, "w") as f:
            f.write(code)
        print("✅ Swing core rebuilt from base blueprint backup.")
