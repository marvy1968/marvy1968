from __future__ import annotations

import argparse
import json
from pathlib import Path

from expert_library.library import (
    ExpertLibrary,
)
from expert_library.report import (
    build_report,
)
from expert_library.research.registry import (
    write_queue,
)


def show_expert(
    library: ExpertLibrary,
    expert_id: str,
) -> None:
    if not expert_id:
        raise SystemExit(
            "--expert is required "
            "for the show command"
        )

    profile = library.get(
        expert_id
    )

    print(
        json.dumps(
            profile.to_dict(),
            indent=2,
        )
    )


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "OANDA Master Alliance "
            "Expert Library"
        )
    )

    parser.add_argument(
        "command",
        choices=[
            "validate",
            "list",
            "show",
            "export",
            "report",
            "queue",
            "coverage",
        ],
    )

    parser.add_argument(
        "--expert",
        default="",
    )

    parser.add_argument(
        "--output",
        default=(
            "reports/"
            "expert_library"
        ),
    )

    arguments = parser.parse_args()

    library = ExpertLibrary()

    output = Path(
        arguments.output
    )

    if arguments.command == "validate":
        errors = library.validate()

        if errors:
            print(
                "Expert library "
                "validation failed."
            )

            for error in errors:
                print(
                    f"- {error}"
                )

            raise SystemExit(1)

        print(
            "Expert library "
            "validation passed."
        )

    elif arguments.command == "list":
        print()
        print(
            "OANDA EXPERT LIBRARY"
        )
        print("=" * 88)

        for profile in library.all():
            print(
                f"{profile.expert_id:12} "
                f"{profile.display_name:34} "
                f"sources="
                f"{len(profile.sources):2} "
                f"rules="
                f"{len(profile.doctrine):2} "
                f"strategies="
                f"{len(profile.strategies):2}"
            )

    elif arguments.command == "show":
        show_expert(
            library,
            arguments.expert,
        )

    elif arguments.command == "export":
        destination = library.export(
            output
        )

        print(
            destination
        )

    elif arguments.command == "report":
        destination = build_report(
            output
            / "expert_library_report.md"
        )

        print(
            destination
        )

    elif arguments.command == "queue":
        destination = write_queue(
            output
            / "research_queue.csv"
        )

        print(
            destination
        )

    elif arguments.command == "coverage":
        print(
            json.dumps(
                library.coverage(),
                indent=2,
            )
        )


if __name__ == "__main__":
    main()
