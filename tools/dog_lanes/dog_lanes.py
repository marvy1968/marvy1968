"""Underdog-lane test: why each dog lane loses, and whether any fix survives games it was not chosen on.

Runs on the VM against an export of the lanes' bets (paper / demo only; nothing here places, sizes or enables bets).

INPUT: one CSV or JSONL, one row per bet attempt (duplicates allowed; the clean re-grade removes them):
  required  lane, venue (kalshi|polymarket), sport, game, side, start (ISO), price (0-1 paid per contract), result (1 win,
            0 loss, blank/void = push or no action)
  model     model_p   the model's win probability for `side` at bet time
  optional  placed (ISO, bet time; default start), stake ($; default 1 contract), market_p (vig-free market prob of
            `side`; default price), is_home (1/0), spread (side's spread, + for dogs), sharp_p (sharp books' vig-free
            prob of `side`), open_p (market prob of `side` at open, for line movement before the bet), close_p (at start,
            for closing value), fav_price (price of the other side, for the favourite comparison)

STEPS
  1. clean re-grade   one bet per (lane, game, side): the first placement; fee per contract = rate x p x (1 - p)
                      (Kalshi 0.07, Polymarket 0.095, --fee-kalshi / --fee-poly); profit in dollars and ROI
  2. why it loses     calibration: model_p vs win rate vs price (longshot bias = dogs win less than priced / modelled);
                      breakdowns by price band, edge band, home/away, spread band, sport, hours to start, line move
  3. fixes            chosen on the OLDER half of games (by start time), judged on the NEWER half:
                      shrink model toward market (w), recalibrate (Platt fit on older results), min edge, price floor / cap,
                      home dogs only, small-spread only, sharp books agree, line moved toward the dog, bet the favourite
  4. verdict          a lane is "repaired" only if its chosen fix makes money on the newer half with 30+ bets and a
                      game-bootstrap 90% range whose low end is above -5%; otherwise it stays OFF.
                      Ranked by dollars, then ROI, never by win rate. Under 30 bets = too thin.

usage:
  python tools/dog_lanes/dog_lanes.py run BETS.csv OUT_DIR [--fee-kalshi 0.07] [--fee-poly 0.095] [--split 0.5]
  python tools/dog_lanes/dog_lanes.py inspect            # schemas + 3 sample rows of the VM's paper stores (read-only)
"""

from __future__ import annotations

import itertools
import json
import math
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pandas as pd

MIN_BETS = 30
FEES = {"kalshi": 0.07, "polymarket": 0.095}


# ---------------------------------------------------------------- load + clean re-grade


def load(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.suffix == ".jsonl":
        return load_frame(pd.DataFrame([json.loads(line) for line in p.open() if line.strip()]))
    return load_frame(pd.read_csv(p))


def load_frame(d: pd.DataFrame) -> pd.DataFrame:
    d = d.copy()
    need = {"lane", "venue", "sport", "game", "side", "start", "price", "result", "model_p"}
    missing = need - set(d.columns)
    if missing:
        raise SystemExit(f"missing columns: {sorted(missing)}")
    d["start"] = pd.to_datetime(d["start"], utc=True, errors="coerce")
    d["placed"] = pd.to_datetime(d["placed"], utc=True, errors="coerce") if "placed" in d else d["start"]
    d["placed"] = d["placed"].fillna(d["start"])
    for c in ("price", "model_p", "market_p", "stake", "spread", "sharp_p", "open_p", "close_p", "fav_price", "is_home"):
        if c in d:
            d[c] = pd.to_numeric(d[c], errors="coerce")
    d["market_p"] = d["market_p"].fillna(d["price"]) if "market_p" in d else d["price"]
    d["result"] = pd.to_numeric(d["result"], errors="coerce")
    d["venue"] = d["venue"].astype(str).str.lower().map(lambda v: "polymarket" if v.startswith("poly") else v)
    return d


def clean(d: pd.DataFrame, fees: dict) -> pd.DataFrame:
    """One bet per (lane, game, side): the first placement. Voids / pushes are dropped from P&L."""
    n0 = len(d)
    d = d.sort_values("placed").drop_duplicates(["lane", "game", "side"], keep="first").copy()
    d = d[d["result"].isin([0, 1]) & d["price"].between(0.01, 0.99) & d["model_p"].between(0.001, 0.999)]
    d["fee_rate"] = d["venue"].map(fees).fillna(0.07)
    d["contracts"] = (d["stake"] / d["price"]).fillna(1.0) if "stake" in d else 1.0
    d["edge"] = d["model_p"] - d["market_p"]
    d.attrs["dupes_removed"] = n0 - len(d)
    return d.reset_index(drop=True)


def pnl(d: pd.DataFrame, side_col: str = "price", win_col: str = "result") -> pd.DataFrame:
    """Dollar profit per row at its price + fee, per contract count."""
    p = d[side_col].to_numpy(float)
    fee = d["fee_rate"].to_numpy(float) * p * (1 - p)
    cost = (p + fee) * d["contracts"].to_numpy(float)
    win = d[win_col].to_numpy(float)
    return pd.DataFrame({"cost": cost, "pnl": win * d["contracts"].to_numpy(float) - cost}, index=d.index)


def summary(d: pd.DataFrame, game_boot: bool = True, side_col: str = "price", win_col: str = "result") -> dict:
    if d.empty:
        return {"n": 0, "pnl": 0.0, "roi": None, "win": None, "thin": True}
    x = pnl(d, side_col, win_col)
    out = {"n": int(len(d)), "pnl": round(float(x["pnl"].sum()), 2), "staked": round(float(x["cost"].sum()), 2),
           "roi": float(x["pnl"].sum() / x["cost"].sum()), "win": float(d[win_col].mean()),
           "avg_price": float(d[side_col].mean()), "thin": len(d) < MIN_BETS}
    if game_boot and len(d) >= 5:
        g = x.assign(game=d["game"].to_numpy()).groupby("game")[["pnl", "cost"]].sum()
        rng = np.random.default_rng(0)
        rois = []
        for _ in range(1000):
            s = g.iloc[rng.integers(0, len(g), len(g))].sum()
            rois.append(s["pnl"] / s["cost"] if s["cost"] else 0.0)
        out["lo"], out["hi"] = float(np.percentile(rois, 5)), float(np.percentile(rois, 95))
    return out


# ---------------------------------------------------------------- why it loses


def calibration(d: pd.DataFrame) -> list[dict]:
    bins = [0, 0.15, 0.25, 0.35, 0.45, 0.55, 1]
    out = []
    for lo, hi in zip(bins[:-1], bins[1:]):
        m = d[(d["price"] >= lo) & (d["price"] < hi)]
        if len(m):
            out.append({"price_band": f"{lo:.2f}-{hi:.2f}", "n": int(len(m)), "price": float(m["price"].mean()),
                        "model_p": float(m["model_p"].mean()), "won": float(m["result"].mean()),
                        "model_minus_won": float(m["model_p"].mean() - m["result"].mean()),
                        "price_minus_won": float(m["price"].mean() - m["result"].mean())})
    return out


def breakdowns(d: pd.DataFrame) -> dict:
    hrs = (d["start"] - d["placed"]).dt.total_seconds() / 3600
    cuts = {
        "price band": pd.cut(d["price"], [0, 0.2, 0.3, 0.4, 0.5, 1]),
        "edge band": pd.cut(d["edge"], [-1, 0, 0.03, 0.06, 0.10, 1]),
        "sport": d["sport"],
    }
    if "is_home" in d and d["is_home"].notna().any():
        cuts["home/away"] = d["is_home"].map({1: "home", 0: "away"})
    if "spread" in d and d["spread"].notna().any():
        cuts["spread band"] = pd.cut(d["spread"].abs(), [0, 3, 7, 10, 100])
    if hrs.notna().any():
        cuts["hours to start"] = pd.cut(hrs, [-1, 1, 6, 24, 72, 1e6])
    if "open_p" in d and d["open_p"].notna().any():
        mv = d["price"] - d["open_p"]
        cuts["line move before bet"] = np.where(mv > 0.005, "toward dog", np.where(mv < -0.005, "away from dog", "flat"))
    if "close_p" in d and d["close_p"].notna().any():
        clv = d["close_p"] - d["price"]
        cuts["closing value"] = np.where(clv > 0.005, "beat close", np.where(clv < -0.005, "worse than close", "even"))
    out = {}
    for name, key in cuts.items():
        rows = []
        for val, g in d.groupby(pd.Series(key, index=d.index).astype(str), sort=True):
            if val in ("nan", "None"):
                continue
            s = summary(g, game_boot=False)
            rows.append({"bucket": val, **{k: s[k] for k in ("n", "pnl", "roi", "win", "thin")}})
        out[name] = rows
    return out


# ---------------------------------------------------------------- fixes (chosen on older, judged on newer)


def platt(older: pd.DataFrame):
    from sklearn.linear_model import LogisticRegression
    x = np.log(older["model_p"] / (1 - older["model_p"])).to_numpy().reshape(-1, 1)
    if older["result"].nunique() < 2:
        return None
    return LogisticRegression(C=1.0).fit(x, older["result"])


def candidates(d: pd.DataFrame) -> list[tuple[str, dict]]:
    c = [("as is", {})]
    for w in (0.0, 0.25, 0.5, 0.75):
        for e in (0.0, 0.02, 0.04, 0.06, 0.08):
            c.append((f"shrink w={w} edge>={e}", {"shrink": w, "min_edge": e}))
    for e in (0.0, 0.02, 0.04, 0.06):
        c.append((f"recalibrated edge>={e}", {"recal": True, "min_edge": e}))
    for e in (0.03, 0.06, 0.10, 0.15):
        c.append((f"min edge {e}", {"min_edge": e}))
    for lo, hi in itertools.product((0.0, 0.2, 0.3), (0.45, 0.4, 0.35)):
        c.append((f"price {lo}-{hi}", {"floor": lo, "cap": hi}))
    if "is_home" in d and d["is_home"].notna().any():
        c.append(("home dogs only", {"home": True}))
    if "spread" in d and d["spread"].notna().any():
        for s in (3, 5, 7):
            c.append((f"spread <= {s}", {"max_spread": s}))
    if "sharp_p" in d and d["sharp_p"].notna().any():
        c.append(("sharp books agree", {"sharp": True}))
    if "open_p" in d and d["open_p"].notna().any():
        c.append(("line moved toward dog", {"move": "toward"}))
        c.append(("line moved away from dog", {"move": "away"}))
    if "fav_price" in d and d["fav_price"].notna().any():
        c.append(("bet the favourite instead", {"fav": True}))
    return c


def apply(d: pd.DataFrame, rule: dict, model=None) -> pd.DataFrame:
    x = d.copy()
    p = x["model_p"]
    if rule.get("recal") and model is not None:
        p = pd.Series(model.predict_proba(np.log(p / (1 - p)).to_numpy().reshape(-1, 1))[:, 1], index=x.index)
    if "shrink" in rule:
        p = rule["shrink"] * p + (1 - rule["shrink"]) * x["market_p"]
    x["p_used"] = p
    fee = x["fee_rate"] * x["price"] * (1 - x["price"])
    x["edge_after_fee"] = p - x["price"] - fee
    keep = pd.Series(True, index=x.index)
    if "min_edge" in rule:
        keep &= x["edge_after_fee"] >= rule["min_edge"]
    if "floor" in rule:
        keep &= x["price"].between(rule["floor"], rule["cap"])
    if rule.get("home"):
        keep &= x["is_home"] == 1
    if "max_spread" in rule:
        keep &= x["spread"].abs() <= rule["max_spread"]
    if rule.get("sharp"):
        keep &= x["sharp_p"] >= x["price"]
    if rule.get("move") == "toward":
        keep &= x["price"] - x["open_p"] > 0.005
    if rule.get("move") == "away":
        keep &= x["price"] - x["open_p"] < -0.005
    x = x[keep]
    if rule.get("fav"):  # same games, the other side at its own price
        x = x[x["fav_price"].between(0.01, 0.99)].copy()
        x["price"], x["result"] = x["fav_price"], 1 - x["result"]
    return x


def evaluate_lane(d: pd.DataFrame, split: float) -> dict:
    games = d.groupby("game")["start"].min().sort_values()
    cut = games.iloc[int(len(games) * split)] if len(games) > 1 else games.iloc[0]
    older, newer = d[d["start"] < cut], d[d["start"] >= cut]
    model = platt(older) if len(older) >= MIN_BETS else None
    rows = []
    for name, rule in candidates(d):
        if rule.get("recal") and model is None:
            continue
        o, n = apply(older, rule, model), apply(newer, rule, model)
        so, sn = summary(o, game_boot=False), summary(n)
        rows.append({"fix": name, "rule": rule, "older": so, "newer": sn})
    eligible = [r for r in rows if r["older"]["n"] >= MIN_BETS]
    eligible.sort(key=lambda r: (r["older"]["pnl"], r["older"]["roi"] or -9), reverse=True)  # dollars, then ROI
    chosen = eligible[0] if eligible else None
    verdict = "OFF (too thin to choose a fix)"
    if chosen:
        n = chosen["newer"]
        if n["n"] >= MIN_BETS and n["pnl"] > 0 and n.get("lo", -1) > -0.05:
            verdict = f"REPAIRED on newer games by '{chosen['fix']}' (paper only)"
        elif n["n"] < MIN_BETS:
            verdict = f"OFF ('{chosen['fix']}' has only {n['n']} newer bets: too thin)"
        else:
            verdict = f"OFF ('{chosen['fix']}' did not hold on newer games)"
    return {"split_date": str(cut), "older_bets": int(len(older)), "newer_bets": int(len(newer)),
            "fixes": rows, "chosen": chosen and chosen["fix"], "verdict": verdict}


# ---------------------------------------------------------------- report


def fmt(s: dict) -> str:
    if not s or not s.get("n"):
        return "0 bets"
    roi = f"{s['roi']:+.1%}" if s["roi"] is not None else "-"
    rng = f" [{s['lo']:+.1%} .. {s['hi']:+.1%}]" if "lo" in s else ""
    return f"{s['n']} bets, ${s['pnl']:+.2f}, ROI {roi}{rng}" + (" (thin)" if s.get("thin") else "")


def run(path: str, out_dir: str, fee_kalshi: float = 0.07, fee_poly: float = 0.095, split: float = 0.5) -> dict:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    raw = load(path)
    d = clean(raw, {"kalshi": fee_kalshi, "polymarket": fee_poly})
    rep = {"rows_in": int(len(raw)), "duplicates_removed": int(d.attrs["dupes_removed"]), "lanes": {}}
    L = [f"# Underdog lanes: analysis and repair", "",
         f"{len(raw):,} rows in; {d.attrs['dupes_removed']:,} duplicates / voids / bad rows removed; "
         f"fees: Kalshi {fee_kalshi} x p(1-p), Polymarket {fee_poly} x p(1-p). Fixes chosen on older games, judged on newer.",
         f"Under {MIN_BETS} bets = too thin. Ranked by dollars, then ROI.", ""]
    for lane, g in d.groupby("lane"):
        lane_rep = {"clean": summary(g), "calibration": calibration(g), "breakdowns": breakdowns(g),
                    **evaluate_lane(g, split)}
        rep["lanes"][lane] = lane_rep
        L += [f"## {lane}", "", f"Clean re-grade: {fmt(lane_rep['clean'])}", "",
              "Why it loses (by price band: price, model, actual win rate):", "",
              "| price band | n | price | model | won | model - won |", "|---|---|---|---|---|---|"]
        L += [f"| {c['price_band']} | {c['n']} | {c['price']:.3f} | {c['model_p']:.3f} | {c['won']:.3f} | "
              f"{c['model_minus_won']:+.3f} |" for c in lane_rep["calibration"]]
        for name, rows in lane_rep["breakdowns"].items():
            L += ["", f"By {name}: " + "; ".join(
                f"{r['bucket']} {r['n']} bets ${r['pnl']:+.2f}" + (f" ({r['roi']:+.1%})" if r['roi'] is not None else "")
                + (" thin" if r["thin"] else "") for r in rows)]
        L += ["", f"Split: older games before {lane_rep['split_date']} ({lane_rep['older_bets']} bets) / newer "
                  f"({lane_rep['newer_bets']} bets)", "",
              "| fix | older (chosen on) | newer (judged on) |", "|---|---|---|"]
        top = sorted(lane_rep["fixes"], key=lambda r: (r["older"]["pnl"], r["older"]["roi"] or -9), reverse=True)[:12]
        L += [f"| {r['fix']} | {fmt(r['older'])} | {fmt(r['newer'])} |" for r in top]
        L += ["", f"**Verdict: {lane_rep['verdict']}**", ""]
    (out / "dog_lanes.json").write_text(json.dumps(rep, indent=1, default=str))
    (out / "dog_lanes.md").write_text("\n".join(L) + "\n")
    print("\n".join(L))
    return rep


# ---------------------------------------------------------------- inspect (read-only)


STORES = {"polymarket paper": "/opt/polymarket-paper/data/pm_paper.db", "marv paper": "/opt/marv-bot/state/marv_paper.db",
          "marv clv": "/opt/marv-bot/state/marv_clv.db"}
JSONL = {"kalshi demo": "/opt/kalshi-demo/data/demo_log.jsonl", "master decisions": "/opt/marv-bot/state/master/decisions.jsonl"}


def inspect() -> None:
    """Prints table names, columns and 3 sample rows of each paper store so an export can be mapped. Read-only."""
    for name, path in STORES.items():
        print(f"\n== {name}: {path}")
        try:
            c = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            for (t,) in c.execute("SELECT name FROM sqlite_master WHERE type='table'"):
                cols = [r[1] for r in c.execute(f"PRAGMA table_info({t})")]
                n = c.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
                print(f"  table {t} ({n} rows): {cols}")
                for row in c.execute(f"SELECT * FROM {t} LIMIT 3"):
                    print("    ", row)
        except Exception as exc:
            print("  unavailable:", exc)
    for name, path in JSONL.items():
        print(f"\n== {name}: {path}")
        try:
            with open(path) as fh:
                for i, line in enumerate(fh):
                    if i >= 3:
                        break
                    print("    ", line.strip()[:400])
        except Exception as exc:
            print("  unavailable:", exc)


if __name__ == "__main__":
    if len(sys.argv) >= 2 and sys.argv[1] == "inspect":
        inspect()
    elif len(sys.argv) >= 4 and sys.argv[1] == "run":
        kw, args = {}, sys.argv[4:]
        for flag, key in (("--fee-kalshi", "fee_kalshi"), ("--fee-poly", "fee_poly"), ("--split", "split")):
            if flag in args:
                kw[key] = float(args[args.index(flag) + 1])
        run(sys.argv[2], sys.argv[3], **kw)
    else:
        print(__doc__)
