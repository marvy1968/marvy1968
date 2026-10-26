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


from segment4.backup_manager import (
    BackupManager,
)


def main() -> None:
    result = BackupManager().run()

    print(
        json.dumps(
            result.to_dict(),
            indent=2,
        )
    )

    if not result.successful:
        raise SystemExit(
            1
        )


if __name__ == "__main__":
    main()
