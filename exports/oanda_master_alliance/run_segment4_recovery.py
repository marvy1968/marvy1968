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


from segment4.recovery import (
    RecoveryManager,
)


def main() -> None:
    report = RecoveryManager().run()

    print(
        json.dumps(
            report.to_dict(),
            indent=2,
        )
    )

    if not report.recovery_passed:
        raise SystemExit(
            1
        )


if __name__ == "__main__":
    main()
