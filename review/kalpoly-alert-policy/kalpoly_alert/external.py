"""A stopped process cannot report itself. No monitor is started here."""


def external_failure_path() -> dict:
    return {
        "status": "blocked",
        "activated": False,
        "proposal": (
            "An outside checker would have to notice that the Kalshi demo timer, "
            "the paper timer, or the alert process stopped. marv-health.sh runs every "
            "30 minutes and does not watch those units. This review does not install that checker."
        ),
    }
