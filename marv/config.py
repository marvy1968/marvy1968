"""Settings from environment variables (and an optional .env file)."""

import os
from dataclasses import dataclass, field, replace
from pathlib import Path

from .sports import DEFAULT_SPORTS, SPORTS, VetoParams

ROOT = Path(__file__).resolve().parent.parent


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


@dataclass
class Settings:
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""
    cfbd_api_key: str = ""
    odds_api_key: str = ""
    odds_book: str = "bovada"  # Odds API bookmaker key whose prices the cards use
    simulations: int = 20000
    state_dir: str = str(ROOT / "state")
    sports: list[str] = field(default_factory=lambda: list(DEFAULT_SPORTS))
    paper_mode: bool = True
    stats_model: bool = True  # NFL/college/NBA/WNBA/MLB use the box-score experts
    pdf_sports: list[str] = field(default_factory=lambda: ["nfl", "cfb"])  # attach the weekly chart PDF
    send_empty_cards: bool = False  # False: a card is only sent when it has a new qualified play

    @classmethod
    def from_env(cls) -> "Settings":
        load_dotenv(Path(os.environ.get("MARV_ENV", ROOT / ".env")))
        enabled = os.environ.get("SPORTS", "")
        return cls(
            telegram_bot_token=os.environ.get("TELEGRAM_BOT_TOKEN", ""),
            telegram_chat_id=os.environ.get("TELEGRAM_CHAT_ID", ""),
            cfbd_api_key=os.environ.get("CFBD_API_KEY", ""),
            odds_api_key=os.environ.get("ODDS_API_KEY", ""),
            # The Odds API calls the book "bovada"; older .env files said "bovado".
            odds_book=os.environ.get("ODDS_BOOK", "bovada").strip().lower().replace("bovado", "bovada"),
            simulations=int(os.environ.get("SIMULATIONS") or 20000),
            state_dir=os.environ.get("STATE_DIR") or str(ROOT / "state"),
            sports=[x.strip().lower() for x in enabled.split(",") if x.strip()] or list(DEFAULT_SPORTS),
            paper_mode=os.environ.get("PAPER_MODE", "true").lower() not in ("0", "false", "no", "off"),
            stats_model=os.environ.get("STATS_MODEL", "true").lower() not in ("0", "false", "no", "off"),
            pdf_sports=[x.strip().lower() for x in os.environ.get("REPORT_PDF_SPORTS", "nfl,cfb").split(",") if x.strip()],
            send_empty_cards=os.environ.get("SEND_EMPTY_CARDS", "false").lower() in ("1", "true", "yes", "on"),
        )

    @staticmethod
    def model_weight(sport_key: str) -> float:
        raw = os.environ.get(f"{sport_key.upper()}_MODEL_WEIGHT") or os.environ.get("MODEL_WEIGHT")
        return float(raw) if raw else SPORTS[sport_key].model_weight

    @staticmethod
    def veto_for(sport_key: str) -> VetoParams:
        """Sport veto thresholds with env overrides, e.g. NBA_SPREAD_EDGE=0.05, MLB_MIN_GAMES=15."""
        base = SPORTS[sport_key].veto
        changes = {}
        for name in ("spread_edge", "total_edge", "ml_edge", "trap_spread", "trap_total",
                     "spread_gap", "total_gap", "ml_prob_gap", "min_games", "ml_min", "ml_max"):
            raw = os.environ.get(f"{sport_key.upper()}_{name.upper()}")
            if raw:
                changes[name] = int(raw) if name == "min_games" else float(raw)
        return replace(base, **changes)
