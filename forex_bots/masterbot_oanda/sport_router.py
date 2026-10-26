#!/usr/bin/env python3
from __future__ import annotations

import os

def get_enabled_sports() -> list[str]:
    sports = []
    main = os.getenv("SPORT_KEY", "basketball_ncaab").strip()
    if main:
        sports.append(main)

    enable_wncaab = os.getenv("ENABLE_WNCAAB", "false").lower() == "true"
    wncaab = os.getenv("WNCAAB_SPORT_KEY", "basketball_wncaab").strip()

    if enable_wncaab and wncaab and wncaab not in sports:
        sports.append(wncaab)

    return sports


if __name__ == "__main__":
    print(get_enabled_sports())
