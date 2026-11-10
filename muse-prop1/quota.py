"""Muse Prop1 — PropLine quota tracker.
Counts PropLine API calls per day and per month in state/propline_usage.json
and refuses (fail-closed) once a configured budget is hit, so the runner can
never burn the free tier by polling too fast. Grok review 2026-11-09: the old
polling rate would exhaust PropLine's monthly calls; the budgets below are the
binding guardrail. No network — pure file bookkeeping, fully unit-testable.

Budgets are configured in config.yaml (propline_daily_budget /
propline_monthly_budget). The tracker is deliberately dumb: it counts calls,
rolls the day/month over on the calendar, and says yes/no. The caller
(fetch_propline_props) is what enforces the refusal.
"""
from __future__ import annotations
import json
from datetime import datetime, timezone
from pathlib import Path


class QuotaExceeded(RuntimeError):
    """Raised when a PropLine call would exceed the daily or monthly budget."""


class QuotaTracker:
    def __init__(self, state_dir: Path, daily_budget: int = 500,
                 monthly_budget: int = 1000):
        self.state_dir = Path(state_dir)
        self.daily_budget = int(daily_budget)
        self.monthly_budget = int(monthly_budget)
        self.path = self.state_dir / "propline_usage.json"
        self._data = self._load()

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text())
            if isinstance(d, dict) and "day" in d and "month" in d:
                return d
        except Exception:
            pass
        return {"day": None, "day_count": 0, "month": None, "month_count": 0}

    def _save(self) -> None:
        self.state_dir.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self._data))

    def _today(self, now: datetime | None = None) -> tuple[str, str]:
        now = now or datetime.now(timezone.utc)
        return now.strftime("%Y-%m-%d"), now.strftime("%Y-%m")

    def usage(self, now: datetime | None = None) -> dict:
        """Current counts for today / this month (rolls over lazily)."""
        day, month = self._today(now)
        if self._data.get("day") != day:
            return {"day": day, "day_count": 0, "month": month,
                    "month_count": self._data.get("month_count", 0)
                    if self._data.get("month") == month else 0}
        if self._data.get("month") != month:
            return {"day": day, "day_count": self._data.get("day_count", 0),
                    "month": month, "month_count": 0}
        return {"day": self._data["day"], "day_count": self._data["day_count"],
                "month": self._data["month"], "month_count": self._data["month_count"]}

    def reserve(self, n: int = 1, now: datetime | None = None) -> dict:
        """Reserve n calls. Returns the new usage. Raises QuotaExceeded if
        the reservation would exceed either budget. The reservation is
        persisted before the call is made, so a crash cannot double-spend."""
        day, month = self._today(now)
        if self._data.get("day") != day:
            self._data["day"], self._data["day_count"] = day, 0
        if self._data.get("month") != month:
            self._data["month"], self._data["month_count"] = month, 0
        if self._data["day_count"] + n > self.daily_budget:
            raise QuotaExceeded(
                f"PropLine daily budget {self.daily_budget} reached "
                f"({self._data['day_count']} used on {day})")
        if self._data["month_count"] + n > self.monthly_budget:
            raise QuotaExceeded(
                f"PropLine monthly budget {self.monthly_budget} reached "
                f"({self._data['month_count']} used in {month})")
        self._data["day_count"] += n
        self._data["month_count"] += n
        self._save()
        return {"day": day, "day_count": self._data["day_count"],
                "month": month, "month_count": self._data["month_count"]}
