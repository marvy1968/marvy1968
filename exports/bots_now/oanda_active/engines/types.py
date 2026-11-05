from __future__ import annotations
from dataclasses import dataclass
from typing import Optional, Dict, Any, List

@dataclass
class EngineContext:
    # market
    instrument: str
    time: str  # tick time string from feed (or local iso)
    price: float
    bid: Optional[float] = None
    ask: Optional[float] = None
    spr_pips: Optional[float] = None
    pip_size: float = 0.0001

    # indicators / structure
    adx: Optional[float] = None
    atr: Optional[float] = None
    regime: str = "range"  # "range" or "trend"
    donH: Optional[float] = None
    donL: Optional[float] = None
    buf: float = 0.0

    # triggers / gates (optional)
    lt: Optional[float] = None   # long trigger (example: donH + buf)
    st: Optional[float] = None   # short trigger (example: donL - buf)
    buy_gate: Optional[float] = None
    sell_gate: Optional[float] = None
    bias: str = "neutral"  # "long" / "short" / "neutral"

    # risk / state
    loss_streak: int = 0
    equity: Optional[float] = None

    # misc debugging
    extra: Dict[str, Any] = None

@dataclass
class EngineSignal:
    name: str
    # "long" / "short" / None
    direction: Optional[str]
    confidence: float  # 0..1
    block: bool = False
    reason: str = ""
    # can carry tuning suggestions (e.g., confirm distance multiplier)
    payload: Dict[str, Any] = None


def clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))
