"""`python -m marv stats-backtest`: walk-forward backtest + honest tuning report for one sport.

Thresholds are chosen on the validation seasons only, then reported on the later test seasons,
which the tuning never saw.
"""

import json
import logging
import math
from pathlib import Path

import numpy as np
import pandas as pd

from . import backtest as B

log = logging.getLogger(__name__)


def _roi(x) -> str:
    return "n/a, no odds data" if x is None or (isinstance(x, float) and math.isnan(x)) else f"{x:+.1%}"


def tune_and_report(module, df: pd.DataFrame, val_to: int, weights=(2, 1, 2), target: float = 0.90,
                    min_ml: int = 15, min_ou: int = 100) -> dict:
    d = B.simulate_probs(df[df["h_points"].notna()], module, weights, n=4000)
    val, test = d[d["season"] <= val_to], d[d["season"] > val_to]
    out: dict = {"sport": module.key, "weights": list(weights), "val_seasons": sorted(val["season"].unique().tolist()),
                 "test_seasons": sorted(test["season"].unique().tolist())}
    lines = [f"{module.name}: tuned on {out['val_seasons']}, tested on {out['test_seasons']}"]

    chosen = None
    for t in np.arange(0.60, 0.96, 0.01):
        r = B.score(val, B.PickRules(ml_min_prob=t))
        if r["ml_picks"] >= min_ml and r["ml_acc"] >= target:
            chosen = round(float(t), 2)
            break
    if chosen is None:
        chosen = 0.85
        lines.append(f"No threshold reached {target:.0%} on validation; defaulting to {chosen:.0%}.")
    rv = B.score(val, B.PickRules(ml_min_prob=chosen))
    rt = B.score(test, B.PickRules(ml_min_prob=chosen))
    out["ml"] = {"threshold": chosen, "val": [rv["ml_picks"], rv["ml_acc"]], "test": [rt["ml_picks"], rt["ml_acc"], rt["ml_roi"]]}
    lines.append(f"Moneyline >= {chosen:.0%}: validation {rv['ml_picks']} picks {rv['ml_acc']:.1%} | "
                 f"TEST {rt['ml_picks']} picks {rt['ml_acc']:.1%} (ROI {_roi(rt['ml_roi'])})")

    if d["total"].notna().any():
        best = None
        for e in np.arange(0.0, 0.31, 0.01):
            r = B.score(val, B.PickRules(ou_min_edge=e))
            if r["ou_picks"] >= min_ou and (best is None or r["ou_acc"] > best[0]):
                best = (r["ou_acc"], round(float(e), 2), r)
        if best:
            acc, e, r = best
            rt2 = B.score(test, B.PickRules(ou_min_edge=e))
            out["ou"] = {"edge": e, "val": [r["ou_picks"], acc], "test": [rt2["ou_picks"], rt2["ou_acc"], rt2["ou_roi"]]}
            lines.append(f"Over/under edge >= {e:.0%}: validation {r['ou_picks']} picks {acc:.1%} | "
                         f"TEST {rt2['ou_picks']} picks {rt2['ou_acc']:.1%} (ROI {rt2['ou_roi']:+.1%}; break-even 52.4%)")
    else:
        lines.append("No historical totals in this data source, so over/under accuracy can't be backtested.")

    allr = B.score(test, B.PickRules())
    out["test_all"] = {"games": allr["games"], "ml_acc": allr["ml_acc"], "market_fav": allr["market_fav_acc"]}
    fav = f" (betting favorite won {allr['market_fav_acc']:.1%})" if allr["market_fav_acc"] else ""
    lines.append(f"Every test game: winner picked {allr['ml_acc']:.1%}{fav}")
    tiers = B.accuracy_by_confidence(test, bins=(0.5, 0.6, 0.7, 0.8, 0.85, 0.9, 1.01))
    lines.append("Test accuracy by confidence:\n" + tiers.to_string(index=False))
    out["tiers"] = tiers.to_dict(orient="records")
    out["text"] = "\n".join(lines)
    return out


def run(module, cache: Path, seasons: list[int], val_to: int, out_dir: Path, current: int | None = None,
        resume: bool = False, budget_s: float | None = None) -> dict:
    ckpt = out_dir / f"ckpt_{module.key}_{seasons[0]}-{seasons[-1]}.pkl" if resume else None
    df = B.walk_forward(module, cache, seasons, current=current, ckpt=ckpt, budget_s=budget_s)
    out_dir.mkdir(parents=True, exist_ok=True)
    df.to_pickle(out_dir / f"{module.key}_walkforward.pkl")
    result = tune_and_report(module, df, val_to)
    (out_dir / f"{module.key}_report.json").write_text(json.dumps(result, indent=1, default=float))
    return result
