from __future__ import annotations

import csv
from dataclasses import (
    asdict,
    dataclass,
)
from pathlib import Path


@dataclass
class ResearchQueueItem:
    expert: str
    title: str
    source_type: str
    priority: int
    status: str = "pending"
    reference: str = ""
    notes: str = ""


DEFAULT_QUEUE = [
    ResearchQueueItem(
        "velez",
        (
            "Tools and Tactics for "
            "the Master Day Trader"
        ),
        "book",
        1,
    ),
    ResearchQueueItem(
        "velez",
        "Official video library",
        "video_library",
        2,
    ),
    ResearchQueueItem(
        "raschke",
        "Street Smarts",
        "book",
        1,
    ),
    ResearchQueueItem(
        "raschke",
        "Trading Sardines",
        "book",
        2,
    ),
    ResearchQueueItem(
        "raschke",
        "Long-form interviews",
        "interview_series",
        3,
    ),
    ResearchQueueItem(
        "brooks",
        "Trading Price Action Trends",
        "book",
        1,
    ),
    ResearchQueueItem(
        "brooks",
        (
            "Trading Price Action "
            "Trading Ranges"
        ),
        "book",
        1,
    ),
    ResearchQueueItem(
        "brooks",
        (
            "Trading Price Action "
            "Reversals"
        ),
        "book",
        1,
    ),
    ResearchQueueItem(
        "brooks",
        "Official video course",
        "video_course",
        2,
    ),
    ResearchQueueItem(
        "connors",
        (
            "Short Term Trading "
            "Strategies That Work"
        ),
        "book",
        1,
    ),
    ResearchQueueItem(
        "bollinger",
        (
            "Bollinger on "
            "Bollinger Bands"
        ),
        "book",
        1,
    ),
    ResearchQueueItem(
        "crabel",
        (
            "Day Trading with Short "
            "Term Price Patterns"
        ),
        "book",
        1,
    ),
    ResearchQueueItem(
        "williams",
        (
            "Long-Term Secrets to "
            "Short-Term Trading"
        ),
        "book",
        1,
    ),
]


def write_queue(
    path: str | Path,
) -> Path:
    destination = Path(
        path
    )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    fieldnames = list(
        asdict(
            DEFAULT_QUEUE[0]
        ).keys()
    )

    with destination.open(
        "w",
        newline="",
        encoding="utf-8",
    ) as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=fieldnames,
        )

        writer.writeheader()

        for item in DEFAULT_QUEUE:
            writer.writerow(
                asdict(item)
            )

    return destination
