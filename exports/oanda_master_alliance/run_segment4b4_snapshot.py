from __future__ import annotations

import json

from segment4b4.progress_snapshot import (
    CampaignProgressRecorder,
)


def main() -> None:
    snapshot = (
        CampaignProgressRecorder()
        .create()
    )

    print(
        json.dumps(
            snapshot.to_dict(),
            indent=2,
        )
    )

    print()
    print(
        "Campaign progress snapshot recorded."
    )
    print(
        "Automatic promotion: false"
    )
    print(
        "Automatic execution: false"
    )
    print(
        "Orders submitted: 0"
    )


if __name__ == "__main__":
    main()
