#!/usr/bin/env python3
"""
Central Alpaca fail-closed paper-trading guard.

A patched order-capable Alpaca program may proceed only when:

    ALPACA_PAPER=true

and

    APCA_API_BASE_URL=https://paper-api.alpaca.markets

No credentials are read or printed here.
"""

from __future__ import annotations

import os
import sys


PAPER_ENDPOINT = "https://paper-api.alpaca.markets"


def _truthy(value: str | None) -> bool:
    return str(value or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
        "paper",
    }


def enforce_paper_only(program: str = "Alpaca program") -> None:
    paper = os.getenv("ALPACA_PAPER", "")
    endpoint = (
        os.getenv("APCA_API_BASE_URL")
        or os.getenv("ALPACA_BASE_URL")
        or ""
    ).strip().rstrip("/")

    errors = []

    if not _truthy(paper):
        errors.append(
            "ALPACA_PAPER must explicitly equal true"
        )

    if endpoint != PAPER_ENDPOINT:
        errors.append(
            "APCA_API_BASE_URL must equal "
            + PAPER_ENDPOINT
        )

    if errors:
        sys.stderr.write(
            "\n"
            "============================================================\n"
            " ALPACA SAFETY GUARD: STARTUP BLOCKED\n"
            "============================================================\n"
            f"Program: {program}\n"
            "Reason:\n  - "
            + "\n  - ".join(errors)
            + "\n\n"
            "No Alpaca order request was permitted.\n"
            "============================================================\n"
        )
        raise SystemExit(86)


def paper_endpoint() -> str:
    enforce_paper_only("Alpaca endpoint request")
    return PAPER_ENDPOINT
