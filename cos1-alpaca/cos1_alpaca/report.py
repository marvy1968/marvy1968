"""Daily report file. Written to disk only."""

from __future__ import annotations

from datetime import date
from pathlib import Path


def write_daily_report(
    directory: Path,
    day: date,
    *,
    win_rate: float,
    pnl: float,
    trades: int,
    errors: int,
    extra: str = "",
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"daily-{day.isoformat()}.txt"
    lines = [
        f"date={day.isoformat()}",
        f"win_rate={win_rate:.4f}",
        f"pnl={pnl:.2f}",
        f"trades={trades}",
        f"errors={errors}",
    ]
    if extra:
        lines.append(extra.rstrip())
    path.write_text("\n".join(lines) + "\n")
    return path
