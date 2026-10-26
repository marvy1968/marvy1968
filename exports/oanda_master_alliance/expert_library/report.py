from __future__ import annotations

from pathlib import Path

from expert_library.library import (
    ExpertLibrary,
)


def build_report(
    output_path: str | Path,
) -> Path:
    library = ExpertLibrary()
    coverage = library.coverage()
    errors = library.validate()

    lines = [
        (
            "# OANDA Master Alliance "
            "— Expert Library"
        ),
        "",
        "## Coverage",
        "",
        (
            "- Experts: "
            f"**{coverage['experts']}**"
        ),
        (
            "- Sources registered: "
            f"**{coverage['sources_registered']}**"
        ),
        (
            "- Sources fully reviewed: "
            f"**{coverage['sources_reviewed']}**"
        ),
        (
            "- Doctrine rules: "
            f"**{coverage['doctrine_rules']}**"
        ),
        (
            "- Mechanical rules: "
            f"**{coverage['mechanical_rules']}**"
        ),
        (
            "- Strategy specifications: "
            f"**{coverage['strategies']}**"
        ),
        "- Production enabled: **no**",
        "",
        "## Expert profiles",
        "",
    ]

    for profile in library.all():
        lines.extend(
            [
                (
                    "### "
                    f"{profile.display_name}"
                ),
                "",
                (
                    "**Philosophy:** "
                    f"{profile.philosophy}"
                ),
                "",
                (
                    "**Edge hypothesis:** "
                    f"{profile.edge_hypothesis}"
                ),
                "",
                (
                    "- Registered sources: "
                    f"{len(profile.sources)}"
                ),
                (
                    "- Doctrine rules: "
                    f"{len(profile.doctrine)}"
                ),
                (
                    "- Research strategies: "
                    f"{len(profile.strategies)}"
                ),
                "",
                "**Compatible regimes:** "
                + ", ".join(
                    profile.compatible_regimes
                ),
                "",
                "**Incompatible regimes:** "
                + ", ".join(
                    profile.incompatible_regimes
                ),
                "",
            ]
        )

    lines.extend(
        [
            "## Validation",
            "",
        ]
    )

    if errors:
        lines.append(
            "- Library validation failed."
        )

        for error in errors:
            lines.append(
                f"  - {error}"
            )

    else:
        lines.append(
            "- Library validation passed."
        )

    lines.extend(
        [
            "",
            "## Guardrails",
            "",
            (
                "- Every strategy defaults "
                "to research-only."
            ),
            (
                "- A registered source is not "
                "automatically a completed "
                "source review."
            ),
            (
                "- Community claims cannot "
                "become executable rules "
                "without corroboration."
            ),
            (
                "- Forex adaptations require "
                "pair, session and regime "
                "walk-forward testing."
            ),
            (
                "- No expert weight, order "
                "setting or live account "
                "setting is changed."
            ),
            "",
        ]
    )

    destination = Path(
        output_path
    )

    destination.parent.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination.write_text(
        "\n".join(lines),
        encoding="utf-8",
    )

    return destination
