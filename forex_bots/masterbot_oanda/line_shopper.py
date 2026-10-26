#!/usr/bin/env python3
import sys, time
from datetime import datetime
CONFIDENCE_FLOOR = float(sys.argv[1]) if len(sys.argv) > 1 else 70.0
print(f"🛒 Line Shopper started (floor: {CONFIDENCE_FLOOR}%)")
while True:
    print(f"🛒 [LINE SHOPPER] {datetime.now().strftime('%H:%M')} — No targets yet")
    time.sleep(300)
