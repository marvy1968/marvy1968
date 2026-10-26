from __future__ import annotations

from typing import Any

from experts.bollinger import (
    BollingerExpert,
)
from experts.brooks import (
    BrooksExpert,
)
from experts.connors import (
    ConnorsExpert,
)
from experts.crabel import (
    CrabelExpert,
)
from experts.raschke import (
    RaschkeExpert,
)
from experts.velez import (
    VelezExpert,
)
from experts.williams import (
    WilliamsExpert,
)


def build_experts(
    config: dict[str, Any],
) -> list:
    return [
        VelezExpert(config),
        RaschkeExpert(config),
        BrooksExpert(config),
        ConnorsExpert(config),
        BollingerExpert(config),
        CrabelExpert(config),
        WilliamsExpert(config),
    ]
