from __future__ import annotations

import tarfile
from pathlib import Path

from segment4.integrity_checker import (
    IntegrityChecker,
)


def test_integrity_checker() -> None:
    result = IntegrityChecker().run()

    assert result[
        "passed"
    ]


def test_tar_archive(
    tmp_path: Path,
) -> None:
    source = (
        tmp_path
        / "source.txt"
    )

    source.write_text(
        "test\n",
        encoding="utf-8",
    )

    archive = (
        tmp_path
        / "archive.tar.gz"
    )

    with tarfile.open(
        archive,
        "w:gz",
    ) as handle:
        handle.add(
            source,
            arcname="source.txt",
        )

    assert archive.exists()

    with tarfile.open(
        archive,
        "r:gz",
    ) as handle:
        assert (
            "source.txt"
            in handle.getnames()
        )
