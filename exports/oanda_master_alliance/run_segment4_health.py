from __future__ import annotations

import json
import sys
from pathlib import Path


ROOT = (
    Path(__file__)
    .resolve()
    .parent
)

if str(ROOT) not in sys.path:
    sys.path.insert(
        0,
        str(ROOT),
    )


from segment4.health_monitor import (
    HealthMonitor,
)


def main() -> None:
    report = HealthMonitor().run()

    print(
        json.dumps(
            report.to_dict(),
            indent=2,
        )
    )

    if not report.healthy:
        raise SystemExit(
            1
        )


if __name__ == "__main__":
    main()
