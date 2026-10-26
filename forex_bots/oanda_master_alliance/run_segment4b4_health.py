from __future__ import annotations

import json

from segment4b4.campaign_health import (
    CampaignHealthMonitor,
)


def main() -> None:
    result = (
        CampaignHealthMonitor()
        .evaluate()
    )

    print(
        json.dumps(
            result.to_dict(),
            indent=2,
        )
    )

    if not result.healthy:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
