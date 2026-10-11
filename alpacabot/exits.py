"""Virtual exit manager: the SAME object manages exits in the backtest (per bar) and live (per price tick).

Rules (long only), all in ATR units measured at entry:
  initial stop   entry - trail x ATR
  break-even     once the high-water mark is +be_trigger x ATR above entry, the stop moves to entry + be_offset x ATR
  trailing       the stop never sits below high-water mark - trail x ATR (it only moves up)
  take-profit    regime scaled: ADX >= 35 -> 4.5 ATR, 25-35 -> 3.0 ATR, below 25 -> 1.8 ATR (scaled by tp_scale in research)
Exits are 'virtual': nothing rests at the exchange (no bracket legs to be rejected); the manager watches prices and sends a market close the
moment a level is crossed, so the fill is the level plus adverse slippage, and a gap through a level fills at the open.
Order inside a bar: check the levels set BEFORE this bar, then ratchet with this bar's high (no look-ahead).
State serializes to JSON (atomic write) so a restart does not lose open-trade parameters.
"""

import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


def tp_multiple(adx: float, strong: float = 4.5, standard: float = 3.0, range_: float = 1.8) -> float:
    return strong if adx >= 35 else standard if adx >= 25 else range_


@dataclass
class ExitState:
    entry: float
    atr: float
    stop: float
    tp: float
    hwm: float
    be_trigger: float = 1.0
    be_offset: float = 0.1
    trail: float = 1.5
    be_done: bool = False

    @classmethod
    def open(cls, entry: float, atr: float, adx: float, trail: float = 1.5, tp_scale: float = 1.0, be_trigger: float = 1.0, be_offset: float = 0.1):
        return cls(entry, atr, entry - trail * atr, entry + tp_multiple(adx) * tp_scale * atr, entry, be_trigger, be_offset, trail)

    def check(self, open_: float, high: float, low: float):
        """Did this bar/tick cross a level that was set before it? Returns (reason, raw_price) or None. Stop beats target on a tie."""
        if low <= self.stop:
            return "stop", min(self.stop, open_)
        if high >= self.tp:
            return "target", max(self.tp, open_) if open_ > self.tp else self.tp
        return None

    def ratchet(self, high: float) -> None:
        """Update the high-water mark, the break-even lock and the trailing stop from this bar's high."""
        self.hwm = max(self.hwm, high)
        if not self.be_done and self.hwm - self.entry >= self.be_trigger * self.atr:
            self.stop, self.be_done = max(self.stop, self.entry + self.be_offset * self.atr), True
        self.stop = max(self.stop, self.hwm - self.trail * self.atr)

    def to_json(self) -> dict:
        return asdict(self)

    @classmethod
    def from_json(cls, d: dict):
        return cls(**d)


class StateStore:
    """Persist {symbol: {state, qty, opened}} to disk on every change (write temp file then rename: never half-written)."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load(self) -> dict:
        if not self.path.exists():
            return {}
        try:
            raw = json.loads(self.path.read_text())
        except (json.JSONDecodeError, OSError):
            bad = self.path.with_suffix(".corrupt")
            os.replace(self.path, bad)          # keep the evidence, start clean rather than crash
            return {}
        return {s: {"state": ExitState.from_json(v["state"]), "qty": v["qty"], "opened": v["opened"]} for s, v in raw.items()}

    def save(self, positions: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(json.dumps({s: {"state": p["state"].to_json(), "qty": p["qty"], "opened": p["opened"]} for s, p in positions.items()}, indent=1))
        os.replace(tmp, self.path)
