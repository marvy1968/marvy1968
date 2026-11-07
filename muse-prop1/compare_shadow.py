"""Compare Muse and Claude marv.prop_shadow.v1 logs. No network, no orders.

Join on input_hash (the same frozen observation) and then on player, market,
and line. A shared cohort key with a different hash is the same game at a
different capture, not a common input.

Priced rows (abstained false) are snapshot forecasts. Rows with recommended
true are the picked cohort. Those are reported separately.

Paper ROI uses a disclosed hypothetical unit and only rows that recorded an
American price. It is not a fill. This report does not test significance:
props from one observation are not independent.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from cohort import norm_person, utc_stamp
from engine import american_to_decimal

SCHEMA = "marv.prop_shadow.v1"
HYPOTHETICAL_UNIT = 1.0
DISCLOSURE = (
    "Paper ROI uses one disclosed hypothetical unit on every settled win or loss "
    "that recorded an American price. It is not a stake that was offered, filled, "
    "or sent. Push, void, pending, and rows without a price are excluded from ROI. "
    "Props that share an observation are not independent observations. "
    "This report does not compute a significance test."
)


def _reject_path(path: Path) -> None:
    text = str(path)
    if "muse_nfl_shadow" in text or "marv-feed" in text:
        raise ValueError(f"{path} is the older AJ experiment, not a Prop1 shadow log")


def load_shadow(path: Path, max_rows: int = 100000) -> list[dict]:
    _reject_path(path)
    rows = []
    with Path(path).open() as fh:
        for line in fh:
            if not line.strip():
                continue
            if len(rows) >= max_rows:
                raise ValueError(f"{path} exceeds {max_rows} rows")
            row = json.loads(line)
            if row.get("schema") != SCHEMA:
                raise ValueError(f"{path} is not {SCHEMA}")
            rows.append(row)
    return rows


def _prop_key(row: dict, with_side: bool) -> tuple:
    key = (norm_person(row.get("player") or ""), str(row.get("market")), round(float(row["line"]), 3))
    if with_side:
        return key + (str(row.get("side") or "").lower(),)
    return key


def _stamp(value) -> str | None:
    if not value:
        return None
    try:
        return utc_stamp(value, "timestamp")
    except Exception:
        return None


def grade_row(row: dict, settlement: dict | None) -> str:
    """pending, void, push, win, or loss. Missing settlement stays pending."""
    if row.get("abstained") or not row.get("recommended"):
        return "not_a_pick"
    if not settlement:
        return "pending"
    status = str(settlement.get("status") or "").lower()
    if status in ("", "pending"):
        return "pending"
    if status == "void":
        return "void"
    if status == "push":
        return "push"
    final = settlement.get("final")
    if status != "final" or final is None:
        return "pending"
    line = float(row["line"])
    final = float(final)
    if final == line:
        return "push"
    over = final > line
    won = over if str(row.get("side", "")).lower() == "over" else not over
    return "win" if won else "loss"


def _settlement_index(doc: dict | None) -> dict[tuple, dict]:
    out = {}
    if not doc:
        return out
    for row in doc.get("results") or []:
        if row.get("synthetic") is True or doc.get("synthetic") is True:
            row = {**row, "_synthetic_settlement": True}
        start = _stamp(row.get("scheduled_start"))
        key = (
            str(row.get("sport") or "").lower(),
            norm_person(row.get("away") or ""),
            norm_person(row.get("home") or ""),
            start,
            norm_person(row.get("player") or ""),
            str(row.get("market")),
        )
        # Cohort uses team_key, not person norm. Index by cohort when present.
        if row.get("cohort_key"):
            out[("cohort", row["cohort_key"], norm_person(row.get("player") or ""), str(row["market"]))] = row
        out[("names",) + key] = row
    return out


def _find_settlement(row: dict, index: dict) -> dict | None:
    direct = index.get(("cohort", row.get("cohort_key"), norm_person(row.get("player") or ""), str(row.get("market"))))
    return direct


def _profit(price, result: str, unit: float) -> float | None:
    if result not in ("win", "loss"):
        return None
    if price is None:
        return None
    try:
        price = int(price)
    except (TypeError, ValueError):
        return None
    if result == "loss":
        return -unit
    return unit * (american_to_decimal(price) - 1)


def _metrics(rows: list[dict], index: dict, unit: float) -> dict:
    decided = []
    counts = {"win": 0, "loss": 0, "push": 0, "void": 0, "pending": 0}
    brier_terms = []
    profit = 0.0
    staked = 0
    missing_odds = 0
    for row in rows:
        result = grade_row(row, _find_settlement(row, index))
        if result == "not_a_pick":
            continue
        counts[result] = counts.get(result, 0) + 1
        if result in ("win", "loss"):
            decided.append(row)
            p = row.get("p")
            if p is not None:
                y = 1.0 if result == "win" else 0.0
                brier_terms.append((float(p) - y) ** 2)
            pnl = _profit(row.get("price"), result, unit)
            if pnl is None:
                missing_odds += 1
            else:
                profit += pnl
                staked += 1
    n = counts["win"] + counts["loss"]
    return {
        "n_rows": len(rows),
        "wins": counts["win"],
        "losses": counts["loss"],
        "pushes": counts["push"],
        "voids": counts["void"],
        "pending": counts["pending"],
        "hit_rate": (counts["win"] / n) if n else None,
        "brier": (sum(brier_terms) / len(brier_terms)) if brier_terms else None,
        "brier_n": len(brier_terms),
        "reliability_bins": _bins(rows, index),
        "paper_roi": {
            "hypothetical_fixed_unit_stake": unit,
            "disclosure": DISCLOSURE,
            "n_with_recorded_price": staked,
            "n_excluded_missing_price": missing_odds,
            "profit_units": round(profit, 4) if staked else None,
            "roi": (profit / (staked * unit)) if staked else None,
            "real_fills": False,
        },
    }


def _bins(rows: list[dict], index: dict) -> list[dict]:
    edges = [0.0, 0.5, 0.55, 0.6, 0.65, 0.7, 0.75, 0.8, 0.85, 0.9, 1.0000001]
    bands = []
    for lo, hi in zip(edges, edges[1:]):
        chosen = []
        for row in rows:
            if row.get("abstained") or not row.get("recommended") or row.get("p") is None:
                continue
            result = grade_row(row, _find_settlement(row, index))
            if result not in ("win", "loss"):
                continue
            p = float(row["p"])
            if lo <= p < hi or (hi == edges[-1] and p == hi):
                chosen.append((p, 1.0 if result == "win" else 0.0))
        n = len(chosen)
        bands.append({
            "lo": lo, "hi": min(hi, 1.0), "n": n,
            "mean_p": (sum(p for p, _ in chosen) / n) if n else None,
            "hit_rate": (sum(y for _, y in chosen) / n) if n else None,
        })
    return bands


def _split(rows: list[dict], index: dict, unit: float, field: str) -> dict:
    groups: dict[str, list] = {}
    for row in rows:
        groups.setdefault(str(row.get(field) or "unknown"), []).append(row)
    return {key: _metrics(value, index, unit) for key, value in sorted(groups.items())}


def _range(rows: list[dict], field: str) -> dict:
    stamps = [s for s in (_stamp(row.get(field)) for row in rows) if s]
    if not stamps:
        return {"min": None, "max": None}
    return {"min": min(stamps), "max": max(stamps)}


def compare(muse: list[dict], claude: list[dict], settlement: dict | None = None,
            unit: float = HYPOTHETICAL_UNIT) -> dict:
    if unit <= 0:
        raise ValueError("hypothetical unit must be positive")
    index = _settlement_index(settlement)
    muse_by = {}
    claude_by = {}
    for row in muse:
        muse_by.setdefault(row.get("input_hash"), []).append(row)
    for row in claude:
        claude_by.setdefault(row.get("input_hash"), []).append(row)
    common_hashes = sorted(set(muse_by) & set(claude_by))
    muse_only = sorted(set(muse_by) - set(claude_by))
    claude_only = sorted(set(claude_by) - set(muse_by))

    def flat(table, hashes):
        out = []
        for digest in hashes:
            out.extend(table[digest])
        return out

    common_muse = flat(muse_by, common_hashes)
    common_claude = flat(claude_by, common_hashes)
    forecast_pairs = []
    recommended_pairs = []
    disagreements = []
    for digest in common_hashes:
        left = {}
        right = {}
        for row in muse_by[digest]:
            if not row.get("abstained"):
                left[_prop_key(row, False)] = row
        for row in claude_by[digest]:
            if not row.get("abstained"):
                right[_prop_key(row, False)] = row
        for key in sorted(set(left) & set(right)):
            forecast_pairs.append({"muse": left[key], "claude": right[key], "input_hash": digest})
            if left[key].get("recommended") and right[key].get("recommended"):
                if str(left[key].get("side", "")).lower() == str(right[key].get("side", "")).lower():
                    recommended_pairs.append({"muse": left[key], "claude": right[key], "input_hash": digest})
                else:
                    disagreements.append({"input_hash": digest, "prop": key,
                                          "muse_side": left[key].get("side"), "claude_side": right[key].get("side")})
    muse_cohorts = {row.get("cohort_key") for row in muse}
    claude_cohorts = {row.get("cohort_key") for row in claude}
    same_game_other_capture = []
    for row in muse:
        if row.get("input_hash") in common_hashes:
            continue
        if row.get("cohort_key") in claude_cohorts:
            same_game_other_capture.append(row.get("input_hash"))
    alias_only = []
    muse_alias = {}
    for row in muse:
        for value in (row.get("aliases") or {}).values():
            muse_alias.setdefault(value, row.get("cohort_key"))
    for row in claude:
        if row.get("cohort_key") in muse_cohorts:
            continue
        for value in (row.get("aliases") or {}).values():
            other = muse_alias.get(value)
            if other and other != row.get("cohort_key") and row.get("scheduled_start"):
                alias_only.append({"alias": value, "muse_cohort": other, "claude_cohort": row.get("cohort_key"),
                                   "scheduled_start": row.get("scheduled_start")})
                break
    recommended_muse = [pair["muse"] for pair in recommended_pairs]
    recommended_claude = [pair["claude"] for pair in recommended_pairs]
    n_common_rec = len(recommended_pairs)
    report = {
        "schema": "muse-prop1.compare.v1",
        "comparable": bool(common_hashes),
        "reason": None if common_hashes else "no common input_hash; these logs did not price the same frozen observations",
        "independent_observations": False,
        "significance_test": None,
        "sparse_sample": n_common_rec < 30,
        "sparse_note": (
            "Common recommended props are fewer than 30, or the join is empty. "
            "Do not read a hit-rate gap as an edge."
        ),
        "hypothetical_unit": unit,
        "disclosure": DISCLOSURE,
        "date_range": {
            "scheduled_start": _range(common_muse + common_claude, "scheduled_start"),
            "observed_at": _range(common_muse + common_claude, "observed_at"),
        },
        "coverage": {
            "muse_rows": len(muse),
            "claude_rows": len(claude),
            "muse_priced": sum(not r.get("abstained") for r in muse),
            "claude_priced": sum(not r.get("abstained") for r in claude),
            "muse_recommended": sum(bool(r.get("recommended")) for r in muse),
            "claude_recommended": sum(bool(r.get("recommended")) for r in claude),
            "muse_abstained": sum(bool(r.get("abstained")) for r in muse),
            "claude_abstained": sum(bool(r.get("abstained")) for r in claude),
            "common_input_hashes": len(common_hashes),
            "muse_only_input_hashes": len(muse_only),
            "claude_only_input_hashes": len(claude_only),
            "common_forecast_props": len(forecast_pairs),
            "common_recommended_props": n_common_rec,
            "side_disagreements_on_common_forecasts": len(disagreements),
            "same_cohort_different_input_hash": len(set(same_game_other_capture)),
            "alias_join_without_same_cohort": alias_only,
            "muse_only_hashes": muse_only,
            "claude_only_hashes": claude_only,
        },
        "cohorts": {
            "common_input_forecasts": {
                "note": "Both engines priced this player, market, and line from the same input_hash. This is not the recommended cohort.",
                "n": len(forecast_pairs),
            },
            "common_input_recommended": {
                "note": "Both engines marked the same side recommended on the same input_hash.",
                "muse": _metrics(recommended_muse, index, unit),
                "claude": _metrics(recommended_claude, index, unit),
            },
            "engine_recommended_on_common_inputs": {
                "note": "Everything each engine recommended on shared observations, including props the other engine did not pick.",
                "muse": _metrics([r for r in common_muse if r.get("recommended")], index, unit),
                "claude": _metrics([r for r in common_claude if r.get("recommended")], index, unit),
            },
        },
        "splits": {
            "by_sport": {
                "muse": _split([r for r in common_muse if r.get("recommended")], index, unit, "sport"),
                "claude": _split([r for r in common_claude if r.get("recommended")], index, unit, "sport"),
            },
            "by_market": {
                "muse": _split([r for r in common_muse if r.get("recommended")], index, unit, "market"),
                "claude": _split([r for r in common_claude if r.get("recommended")], index, unit, "market"),
            },
            "by_model_version": {
                "muse": _split([r for r in common_muse if r.get("recommended")], index, unit, "model_version"),
                "claude": _split([r for r in common_claude if r.get("recommended")], index, unit, "model_version"),
            },
        },
    }
    return report


def atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def write_report(path: Path, report: dict) -> None:
    atomic_write(path, json.dumps(report, indent=2, sort_keys=True) + "\n")


def main(argv: list[str] | None = None) -> int:
    import argparse
    ap = argparse.ArgumentParser(prog="compare_shadow")
    ap.add_argument("--muse", required=True)
    ap.add_argument("--claude", required=True)
    ap.add_argument("--settlement", default=None)
    ap.add_argument("--out", required=True)
    ap.add_argument("--hypothetical-unit", type=float, default=HYPOTHETICAL_UNIT)
    args = ap.parse_args(argv)
    try:
        muse = load_shadow(Path(args.muse))
        claude = load_shadow(Path(args.claude))
        settlement = json.loads(Path(args.settlement).read_text()) if args.settlement else None
        report = compare(muse, claude, settlement, args.hypothetical_unit)
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        print(f"compare refused: {exc}")
        return 2
    out = Path(args.out)
    write_report(out / "report.json", report)
    atomic_write(out / "muse_normalized.jsonl", "".join(json.dumps(r, sort_keys=True) + "\n" for r in muse))
    atomic_write(out / "claude_normalized.jsonl", "".join(json.dumps(r, sort_keys=True) + "\n" for r in claude))
    print(f"compare comparable={report['comparable']} common_hashes={report['coverage']['common_input_hashes']} "
          f"common_recommended={report['coverage']['common_recommended_props']} out={out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
