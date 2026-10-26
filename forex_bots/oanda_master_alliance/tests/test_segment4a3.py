from __future__ import annotations

from segment4.alert_manager import (
    AlertManager,
)
from segment4.deployment_common import (
    safety_configuration,
)
from segment4.integrity_checker import (
    IntegrityChecker,
)


def test_safety_configuration() -> None:
    result = safety_configuration()

    assert result[
        "passed"
    ]


def test_integrity() -> None:
    result = IntegrityChecker().run()

    assert result[
        "passed"
    ]


def test_alert_manager_loads() -> None:
    manager = AlertManager()

    assert manager is not None
