#!/usr/bin/env python3
from __future__ import annotations

import sys
from performance_tracker import settle_bet

def main() -> None:
    if len(sys.argv) < 4:
        print("Usage: python settle_tracker_bet.py <bet_id> <win|loss|push> <profit_units> [final_home_score] [final_away_score]")
        sys.exit(1)

    bet_id = int(sys.argv[1])
    result = sys.argv[2]
    profit_units = float(sys.argv[3])
    final_home_score = float(sys.argv[4]) if len(sys.argv) > 4 else None
    final_away_score = float(sys.argv[5]) if len(sys.argv) > 5 else None

    settle_bet(
        bet_id=bet_id,
        result=result,
        profit_units=profit_units,
        final_home_score=final_home_score,
        final_away_score=final_away_score,
    )
    print("Bet settled.")

if __name__ == "__main__":
    main()
