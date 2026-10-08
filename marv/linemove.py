"""Line movement note for cards: how the spread and total moved from the open to now."""

from .models import Game


def _fmt(x: float) -> str:
    return f"{x:+g}" if x else "PK"


def note(game: Game, min_move: float = 0.5) -> str:
    o = game.odds
    if not o:
        return ""
    parts = []
    if o.spread is not None and o.spread_open is not None and abs(o.spread - o.spread_open) >= min_move:
        toward = game.home if o.spread < o.spread_open else game.away
        parts.append(f"spread {game.home} {_fmt(o.spread_open)} → {_fmt(o.spread)} (toward {toward})")
    if o.total is not None and o.total_open is not None and abs(o.total - o.total_open) >= min_move:
        parts.append(f"total {o.total_open:g} → {o.total:g} ({o.total - o.total_open:+g})")
    return "line move: " + ", ".join(parts) if parts else ""
