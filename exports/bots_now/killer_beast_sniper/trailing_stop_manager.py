import numpy as np

class ATRTrailingStopManager:
    """
    Manages dynamic ATR trailing stops once positions cross +1.0R profit threshold.
    """
    def __init__(self, atr_multiplier: float = 1.5, activation_r_multiple: float = 1.0):
        self.atr_multiplier = atr_multiplier
        self.activation_r_multiple = activation_r_multiple

    def update_stop_level(self, side: str, entry_price: float, current_price: float, current_stop: float, atr_value: float) -> float:
        """
        Calculates the new trailing stop price if activation threshold is reached.
        """
        if side.upper() == 'BUY' or side.upper() == 'LONG':
            unrealized_r = (current_price - entry_price) / max(0.0001, (entry_price - current_stop))
            if unrealized_r >= self.activation_r_multiple:
                proposed_stop = current_price - (atr_value * self.atr_multiplier)
                # Ratchet upwards only
                return max(current_stop, proposed_stop)
        elif side.upper() == 'SELL' or side.upper() == 'SHORT':
            unrealized_r = (entry_price - current_price) / max(0.0001, (current_stop - entry_price))
            if unrealized_r >= self.activation_r_multiple:
                proposed_stop = current_price + (atr_value * self.atr_multiplier)
                # Ratchet downwards only
                return min(current_stop, proposed_stop)
        return current_stop
