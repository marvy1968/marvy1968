"""Leakage checks for the calibration path. Synthetic rows only.

No artifact in this repo is an empirical calibration. Replay does not load one.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from calibration import LeakageError, apply_bin, fit_and_evaluate, load_calibration


def _row(start, game, result, p=0.7):
    home, away = game
    return {
        "sport": "nfl", "home": home, "away": away, "scheduled_start": start,
        "result": result, "model_p": p, "player": "A", "market": "player_pass_yds",
    }


def test_overlapping_times_are_refused():
    train = [_row("2026-11-01T18:00:00Z", ("Home A", "Away A"), "win")]
    test = [_row("2026-11-01T18:00:00Z", ("Home B", "Away B"), "loss")]
    try:
        fit_and_evaluate(train, test, min_bin=1)
    except LeakageError:
        return
    raise AssertionError("same-time split must be refused")


def test_shared_game_is_refused():
    train = [_row("2026-11-01T18:00:00Z", ("Dallas Cowboys", "New York Giants"), "win")]
    test = [_row("2026-11-08T18:00:00Z", ("Dallas Cowboys", "New York Giants"), "loss")]
    # Different starts but the cohort key includes the start, so this is NOT the same game key.
    # A repeated game is the same teams AND the same start.
    train2 = [_row("2026-11-01T18:00:00Z", ("Dallas Cowboys", "New York Giants"), "win")]
    test2 = [_row("2026-11-01T18:00:00Z", ("Dallas Cowboys", "New York Giants"), "loss")]
    try:
        fit_and_evaluate(train2, test2, min_bin=1)
    except LeakageError:
        pass
    else:
        raise AssertionError("shared game must be refused")
    # Future train relative to held-out is refused even with distinct games.
    try:
        fit_and_evaluate(
            [_row("2026-11-09T18:00:00Z", ("Home A", "Away A"), "win")],
            [_row("2026-11-02T18:00:00Z", ("Home B", "Away B"), "loss")],
            min_bin=1)
    except LeakageError:
        return
    raise AssertionError("train after held-out must be refused")


def test_pending_is_not_a_win_and_no_file_is_not_a_calibration():
    train = []
    test = []
    for i, result in enumerate(["win"] * 10 + ["loss"] * 10):
        train.append(_row(f"2026-10-{i+1:02d}T18:00:00Z", (f"Home {i}", f"Away {i}"), result, 0.8))
    train.append(_row("2026-10-21T18:00:00Z", ("Home P", "Away P"), "pending", 0.99))
    train.append(_row("2026-10-22T18:00:00Z", ("Home V", "Away V"), "void", 0.99))
    for i, result in enumerate(["win", "loss"]):
        test.append(_row(f"2026-11-0{i+1}T18:00:00Z", (f"Late {i}", f"Road {i}"), result, 0.8))
    artifact = fit_and_evaluate(train, test, min_bin=5)
    assert artifact["provenance"]["separated"] is True
    assert artifact["heldout"]["n"] == 2
    # Pending and void are not in the settled train count.
    assert artifact["provenance"]["train_settled"] == 20
    assert load_calibration(Path("/tmp/does-not-exist-muse-calib.json")) is None
    assert apply_bin(0.8, None) == (0.8, "uncalibrated")


if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print(f"PASS {name}")
    print("all calibration tests passed")
