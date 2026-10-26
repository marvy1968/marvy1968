from expert_library.library import (
    ExpertLibrary,
)


def test_library_validates() -> None:
    library = ExpertLibrary()

    assert library.validate() == []


def test_expected_experts_exist() -> None:
    library = ExpertLibrary()

    expected = {
        "velez",
        "raschke",
        "brooks",
        "connors",
        "bollinger",
        "crabel",
        "williams",
    }

    assert expected.issubset(
        set(
            library.names()
        )
    )


def test_strategies_are_research_only() -> None:
    library = ExpertLibrary()

    for profile in library.all():
        for strategy in profile.strategies:
            assert (
                strategy.status
                == "research_only"
            )


def test_rules_have_sources() -> None:
    library = ExpertLibrary()

    for profile in library.all():
        for doctrine in profile.doctrine:
            assert doctrine.source_ids
