from pathlib import Path

required = [
    "configs",
    "experts",
    "strategies",
    "indicators",
    "portfolio",
    "broker",
    "database",
    "learning",
    "reports",
    "logs",
]

missing = [d for d in required if not Path(d).exists()]

if missing:
    print("FAILED")
    print("Missing:", missing)
else:
    print("SUCCESS")
    print("Bootstrap verified.")
