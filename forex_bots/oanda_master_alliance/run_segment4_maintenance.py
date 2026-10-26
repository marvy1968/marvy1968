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


from segment4.maintenance_manager import (
    MaintenanceManager,
)


def main() -> None:
    result = MaintenanceManager().run()

    print(
        json.dumps(
            result,
            indent=2,
        )
    )

    if not result[
        "successful"
    ]:
        raise SystemExit(
            1
        )


if __name__ == "__main__":
    main()
