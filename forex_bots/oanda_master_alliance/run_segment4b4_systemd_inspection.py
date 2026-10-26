from __future__ import annotations

import json

from segment4b4.systemd_deployment import (
    SystemdDeploymentManager,
)


def main() -> None:
    manager = SystemdDeploymentManager()

    result = manager.inspect()

    print(
        json.dumps(
            result.to_dict(),
            indent=2,
        )
    )

    print()
    print(
        "Systemd deployment inspection complete."
    )
    print(
        "Automatic promotion: False"
    )
    print(
        "Automatic execution: False"
    )
    print(
        "Practice-order submission: False"
    )
    print(
        "Live trading: Blocked"
    )
    print(
        "Orders submitted: 0"
    )


if __name__ == "__main__":
    main()
