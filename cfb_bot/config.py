"""Settings loaded from environment variables (and an optional .env file)."""

import os
from dataclasses import dataclass
from pathlib import Path


def load_dotenv(path: Path) -> None:
    """Minimal .env loader so the bot has no extra dependencies."""
    if not path.exists():
        return
    for line in path.read_text().splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.split(" #")[0].strip().strip('"').strip("'"))


def _f(name: str, default: float) -> float:
    return float(os.environ.get(name) or default)


@dataclass
class Settings:
    cfbd_api_key: str = ""
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    simulations: int = 20000
    spread_edge: float = 0.04
    big_spread_edge: float = 0.08
    huge_spread_edge: float = 0.10
    total_edge: float = 0.05
    ml_edge: float = 0.05
    trap_move: float = 2.5
    garbage_margin: float = 21.0
    max_games: int = 30

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(Path(os.environ.get("CFB_BOT_ENV", Path(__file__).resolve().parent.parent / ".env")))
        return cls(
            cfbd_api_key=os.environ.get("CFBD_API_KEY", ""),
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            simulations=int(_f("SIMULATIONS", 20000)),
            spread_edge=_f("SPREAD_EDGE", 0.04),
            big_spread_edge=_f("BIG_SPREAD_EDGE", 0.08),
            huge_spread_edge=_f("HUGE_SPREAD_EDGE", 0.10),
            total_edge=_f("TOTAL_EDGE", 0.05),
            ml_edge=_f("ML_EDGE", 0.05),
            trap_move=_f("TRAP_MOVE", 2.5),
            garbage_margin=_f("GARBAGE_MARGIN", 21),
            max_games=int(_f("MAX_GAMES", 30)),
        )
