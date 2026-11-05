"""Shared risk state for all three sleeves (scalp / day / swing).

Paper-first risk rails, in the same spirit as the existing bots:
- max open positions per sleeve group, max new day trades per day,
  daily loss cap (2% by default) — no new trades after it is hit.
- day_pnl is tracked in R (risk units) and in dollars; both are reported.
- No broker calls here; this is pure state, so it is unit-testable.
"""
from datetime import date

class RiskState:
    def __init__(self, equity=100000.0, daily_loss_cap_pct=0.02,
                 max_open_day_scalp=2, max_open_swing=5,
                 max_new_day_per_day=5):
        self.equity = float(equity)
        self.daily_loss_cap_pct = float(daily_loss_cap_pct)
        self.max_open_day_scalp = int(max_open_day_scalp)
        self.max_open_swing = int(max_open_swing)
        self.max_new_day_per_day = int(max_new_day_per_day)
        self.day = date.today()
        self.trades_today = 0
        self.day_pnl_dollars = 0.0
        self.day_pnl_r = 0.0
        self.open_day_scalp = 0
        self.open_swing = 0

    def _rollover(self):
        today = date.today()
        if today != self.day:
            self.day = today
            self.trades_today = 0
            self.day_pnl_dollars = 0.0
            self.day_pnl_r = 0.0

    def daily_loss_cap_dollars(self):
        return self.equity * self.daily_loss_cap_pct

    def cap_hit(self):
        self._rollover()
        return self.day_pnl_dollars <= -self.daily_loss_cap_dollars()

    def can_open(self, sleeve):
        """sleeve: 'scalp' | 'day' | 'swing'. Returns (ok, reason)."""
        self._rollover()
        if self.cap_hit():
            return False, "daily loss cap hit"
        if sleeve == "swing":
            if self.open_swing >= self.max_open_swing:
                return False, f"swing max open {self.max_open_swing} reached"
            return True, "ok"
        # scalp and day share the intraday book
        if self.open_day_scalp >= self.max_open_day_scalp:
            return False, f"day/scalp max open {self.max_open_day_scalp} reached"
        if sleeve == "day" and self.trades_today >= self.max_new_day_per_day:
            return False, f"max new day trades {self.max_new_day_per_day} reached"
        return True, "ok"

    def record_open(self, sleeve):
        self._rollover()
        if sleeve == "swing":
            self.open_swing += 1
        else:
            self.open_day_scalp += 1
        if sleeve == "day":
            self.trades_today += 1

    def record_close(self, sleeve, pnl_dollars, pnl_r):
        self._rollover()
        if sleeve == "swing":
            self.open_swing = max(0, self.open_swing - 1)
        else:
            self.open_day_scalp = max(0, self.open_day_scalp - 1)
        self.day_pnl_dollars += float(pnl_dollars)
        self.day_pnl_r += float(pnl_r)

    def summary(self):
        return {
            "day": str(self.day),
            "equity": self.equity,
            "trades_today": self.trades_today,
            "open_day_scalp": self.open_day_scalp,
            "open_swing": self.open_swing,
            "day_pnl_dollars": round(self.day_pnl_dollars, 2),
            "day_pnl_r": round(self.day_pnl_r, 3),
            "daily_loss_cap_dollars": round(self.daily_loss_cap_dollars(), 2),
            "cap_hit": self.cap_hit(),
        }
