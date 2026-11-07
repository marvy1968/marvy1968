"""Calibration path for Muse Prop1 probabilities.

A probability is calibrated only by a fit on settled win/loss rows whose games
all start strictly before every held-out game, with no shared game. This module
refuses overlapping, future, or unlabeled rows and does not write an artifact.

No such settled held-out set is shipped with this engine. Runtime picks stay
`probability_status = uncalibrated`. The wider interval below is a conservative
uncertainty band. It is not a probability cap and it is not an empirical
calibration.

Remaining requirement: grade a shared snapshot log, split by scheduled start,
and only then pass the artifact to the runner with --calibration. Until that
file exists and passes these checks, do not describe the model probabilities
as calibrated.
"""
from __future__ import annotations

import json
from pathlib import Path

from identity import game_key, parse_time


class LeakageError(ValueError):
    """Train and held-out rows are not time-separated."""


def _start(row: dict):
    return parse_time(row.get("scheduled_start") or row.get("commence_time"))


def _game(row: dict) -> str | None:
    explicit = row.get("game_key")
    if explicit:
        return str(explicit)
    return game_key(row.get("sport"), row.get("home") or row.get("home_team"),
                    row.get("away") or row.get("away_team"),
                    row.get("scheduled_start") or row.get("commence_time"))


def _settled_binary(row: dict) -> tuple[float, float] | None:
    """(probability, 0/1 outcome) for a settled win or loss. None otherwise.

    Pending, void, and push are not wins and are not losses. They are excluded
    rather than filled in.
    """
    status = str(row.get("result") or row.get("status") or "").lower()
    if status in ("pending", "void", "push", ""):
        return None
    if status not in ("win", "loss", "final"):
        return None
    if status == "final":
        return None
    p = row.get("model_p", row.get("p"))
    if p is None:
        return None
    try:
        p = float(p)
    except (TypeError, ValueError):
        return None
    if not 0.0 <= p <= 1.0:
        return None
    return p, 1.0 if status == "win" else 0.0


def assert_time_separated(train: list[dict], test: list[dict]) -> None:
    """Require one cutoff: every train game starts before every test game.

    Also rejects a game key present on both sides, a row with no start, and a
    test row whose start is earlier than a train row.
    """
    if not train or not test:
        raise LeakageError("train and held-out both need at least one row")
    train_games: dict[str, object] = {}
    test_games: dict[str, object] = {}
    for label, rows, bucket in (("train", train, train_games), ("test", test, test_games)):
        for row in rows:
            key = _game(row)
            start = _start(row)
            if key is None or start is None:
                raise LeakageError(f"{label} row is missing sport, teams, or scheduled start")
            previous = bucket.get(key)
            if previous is not None and previous != start:
                raise LeakageError(f"{label} game {key} has two scheduled starts")
            bucket[key] = start
    shared = set(train_games) & set(test_games)
    if shared:
        raise LeakageError(f"shared games in train and held-out: {sorted(shared)[:5]}")
    train_max = max(train_games.values())
    test_min = min(test_games.values())
    if train_max >= test_min:
        raise LeakageError("train scheduled start is not strictly before every held-out start")


def _bins(pairs: list[tuple[float, float]], edges: list[float]) -> list[dict]:
    out = []
    for lo, hi in zip(edges, edges[1:]):
        chosen = [(p, y) for p, y in pairs if lo <= p < hi or (hi == edges[-1] and p == hi)]
        n = len(chosen)
        out.append({
            "lo": lo,
            "hi": hi,
            "n": n,
            "mean_p": (sum(p for p, _ in chosen) / n) if n else None,
            "hit_rate": (sum(y for _, y in chosen) / n) if n else None,
        })
    return out


def brier(pairs: list[tuple[float, float]]) -> float | None:
    if not pairs:
        return None
    return sum((p - y) ** 2 for p, y in pairs) / len(pairs)


DEFAULT_EDGES = [0.0, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 1.0000001]


def fit_and_evaluate(train: list[dict], test: list[dict], min_bin: int = 30,
                     edges: list[float] | None = None) -> dict:
    """Fit reliability bins on train and score Brier on the held-out rows only.

    Raises LeakageError and returns nothing when the split is not valid.
    The returned artifact is not applied by the runner unless a caller passes
    it explicitly after this function has accepted the split.
    """
    assert_time_separated(train, test)
    edges = edges or DEFAULT_EDGES
    train_pairs = [pair for row in train if (pair := _settled_binary(row)) is not None]
    test_pairs = [pair for row in test if (pair := _settled_binary(row)) is not None]
    if len(train_pairs) < min_bin:
        raise LeakageError(
            f"settled train n={len(train_pairs)} < {min_bin}; refusing to publish a calibration")
    artifact = {
        "schema": "muse-prop1.calibration.v1",
        "method": "reliability_bins",
        "empirical_calibration": True,
        "applied_by_default": False,
        "note": (
            "Held-out frequencies from time-separated settled results. "
            "This is not a probability cap. Do not use it unless provenance.separated is true "
            "and the rows are real graded props, not a synthetic fixture."
        ),
        "provenance": {
            "separated": True,
            "train_start_max": max(_start(r) for r in train).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "test_start_min": min(_start(r) for r in test).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "train_rows": len(train),
            "test_rows": len(test),
            "train_settled": len(train_pairs),
            "test_settled": len(test_pairs),
            "shared_games": 0,
            "min_bin": min_bin,
        },
        "train_bins": _bins(train_pairs, edges),
        "heldout": {
            "n": len(test_pairs),
            "brier": brier(test_pairs),
            "bins": _bins(test_pairs, edges),
            "mean_p": (sum(p for p, _ in test_pairs) / len(test_pairs)) if test_pairs else None,
            "hit_rate": (sum(y for _, y in test_pairs) / len(test_pairs)) if test_pairs else None,
        },
    }
    return artifact


def load_calibration(path: Path | None) -> dict | None:
    """Load an artifact only when its provenance says the split was separated.

    A missing file, a synthetic file, or a file without that flag returns None.
    Returning None keeps the engine uncalibrated. It does not invent a map.
    """
    if path is None or not Path(path).exists():
        return None
    try:
        data = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError):
        return None
    prov = data.get("provenance") or {}
    if data.get("schema") != "muse-prop1.calibration.v1":
        return None
    if data.get("synthetic") is True:
        return None
    if prov.get("separated") is not True or not prov.get("train_start_max") or not prov.get("test_start_min"):
        return None
    if prov.get("train_start_max") >= prov.get("test_start_min"):
        return None
    if not data.get("empirical_calibration"):
        return None
    return data


def apply_bin(probability: float, artifact: dict | None) -> tuple[float, str]:
    """Return (probability, status). Unknown or thin bins stay uncalibrated.

    This does not clamp the probability to a cap. A bin is used only when that
    bin's train count meets min_bin and the artifact passed load_calibration.
    """
    if artifact is None:
        return probability, "uncalibrated"
    edges_bins = artifact.get("train_bins") or []
    min_bin = int((artifact.get("provenance") or {}).get("min_bin") or 30)
    for band in edges_bins:
        lo, hi = band.get("lo"), band.get("hi")
        if lo is None or hi is None:
            continue
        if lo <= probability < hi and band.get("n", 0) >= min_bin and band.get("hit_rate") is not None:
            return float(band["hit_rate"]), "calibrated_heldout_bin"
    return probability, "uncalibrated"


def uncalibrated_interval(point: float, alternate: float | None = None) -> dict:
    """Conservative band around an uncalibrated point probability.

    `alternate` is the same event probability recomputed with a wider dispersion.
    The band is the span of those two numbers. It is not applied as a cap and
    it is not a fitted reliability diagram.
    """
    point = min(1.0, max(0.0, float(point)))
    other = point if alternate is None else min(1.0, max(0.0, float(alternate)))
    lo, hi = (point, other) if point <= other else (other, point)
    # A one-sided numerical tie still gets a disclosed minimum width so the
    # status is not a false precision of many decimals. Width is uncertainty,
    # not a new point estimate.
    if hi - lo < 1e-6:
        pad = min(0.05, point * 0.25, (1 - point) * 0.25)
        lo = max(0.0, point - pad)
        hi = min(1.0, point + pad)
    return {
        "kind": "conservative_uncalibrated_interval",
        "empirical_calibration": False,
        "probability_status": "uncalibrated",
        "p_low": round(lo, 4),
        "p_high": round(hi, 4),
        "point": round(point, 4),
        "note": (
            "No time-separated settled held-out fit is loaded. "
            "This interval is wider uncertainty, not a probability cap and not a calibration."
        ),
    }


def write_artifact(path: Path, artifact: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(artifact, indent=2, sort_keys=True) + "\n")
