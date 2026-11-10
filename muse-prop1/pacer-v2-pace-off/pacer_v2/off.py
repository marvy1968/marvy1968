"""Kill switch. Presence, not value, turns the process off."""

import os
from pathlib import Path

ENV_VAR = "PACER_V2_OFF"


def switched_off(package_dir: Path | None = None, cwd: Path | None = None) -> bool:
    """True when the tool must exit 0 without reading or writing.

    Triggers:
    - environment variable ``PACER_V2_OFF`` is set (any value, including empty)
    - ``<package>/OFF`` exists (the file shipped next to this code)
    - ``<cwd>/pacer_v2/OFF`` exists (a VM can drop the file beside the package)
    """
    if ENV_VAR in os.environ:
        return True
    package_dir = package_dir or Path(__file__).resolve().parent
    cwd = cwd or Path.cwd()
    if (package_dir / "OFF").is_file():
        return True
    if (cwd / "pacer_v2" / "OFF").is_file():
        return True
    return False
