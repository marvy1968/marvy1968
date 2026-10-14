"""python -m fxbot download | run-all [--families ...] | spreads   (practice data only; no order placement exists in this package)"""

import argparse
import json
from datetime import date
from pathlib import Path

from .data import MAJORS, load, token
from .research import FAMILIES, walk_forward

STATE = Path("state/fx")


def _env():
    import os
    p = Path("/opt/marv-bot/.env")
    if p.exists():
        for line in p.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.split(" #")[0].strip().strip('"').strip("'"))


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="fxbot")
    ap.add_argument("cmd", choices=["download", "run-all", "spreads"])
    ap.add_argument("--families", nargs="*", default=list(FAMILIES))
    ap.add_argument("--cache", default=str(STATE / "cache"))
    a = ap.parse_args(argv)
    _env()
    if not token():
        print("WAITING FOR KEYS: add OANDA_API_TOKEN=<practice token> to /opt/marv-bot/.env (practice account only)")
        return 2
    start, end = date(2015, 1, 1), date(2022, 12, 31)
    grans = sorted({FAMILIES[f][0] for f in a.families})
    data = {g: {i: load(i, g, start, end, Path(a.cache)) for i in MAJORS} for g in grans}
    print("data ready: " + ", ".join(f"{g} {sum(len(d) for d in data[g].values())} bars" for g in grans), flush=True)
    if a.cmd == "download":
        return 0
    if a.cmd == "spreads":
        for g in grans:
            for i, d in data[g].items():
                sp = (d["ac"] - d["bc"]) / (0.01 if "JPY" in i else 0.0001)
                print(f"{g} {i}: median spread {sp.median():.2f} pips, 90th pct {sp.quantile(0.9):.2f}")
        return 0
    out = STATE / "reports"
    out.mkdir(parents=True, exist_ok=True)
    lines = ["# fxbot walk-forward (train 2016-2020, validate 2021-2022, bid/ask fills, financing 0.01%/night)\n"]
    for fam in a.families:
        try:
            res = walk_forward(fam, data[FAMILIES[fam][0]])
        except Exception as exc:
            res = {"family": fam, "verdict": f"ERROR {exc!r}", "passes": False}
        (out / f"{fam}.json").write_text(json.dumps(res, indent=1, default=str))
        v, t = res.get("validate_metrics") or {}, res.get("train_metrics") or {}
        lines.append(f"## {fam}: {res['verdict']}\n- chosen {res.get('chosen')}\n"
                     f"- train: {t.get('trades')}t PF {t.get('profit_factor') or 0:.2f} total {t.get('total_return') or 0:+.1%} "
                     f"maxDD {t.get('max_drawdown') or 0:.1%} avg/day {t.get('avg_daily_pct') or 0:+.3f}%\n"
                     f"- validate: {v.get('trades')}t win {v.get('win_rate') or 0:.0%} PF {v.get('profit_factor') or 0:.2f} total {v.get('total_return') or 0:+.1%} "
                     f"CAGR {v.get('cagr') or 0:+.1%} maxDD(closed trades) {v.get('max_drawdown') or 0:.1%} avg/day {v.get('avg_daily_pct') or 0:+.3f}% "
                     f"financing ${v.get('financing_paid') or 0:,.0f}\n"
                     f"- spread x2 total {(res.get('spread_x2') or {}).get('total_return') or 0:+.1%} | placebo PF {res.get('placebo_pf_mean') or 0:.2f}, "
                     f"real beats {res.get('beats_placebo_share') or 0:.0%} | PASS {res.get('passes')}\n")
        if not res.get("chosen") and res.get("grid"):          # a family that failed on train: show how its settings did
            for g in sorted(res["grid"], key=lambda g: -(g.get("total_return") or -9))[:4]:
                lines.append(f"  - train {g['params']}: {g.get('trades')}t PF {g.get('profit_factor') or 0:.2f} total {g.get('total_return') or 0:+.1%} "
                             f"maxDD(closed) {g.get('max_drawdown') or 0:.1%}")
        print("\n".join(lines[-5:]), flush=True)
    (out / "summary.md").write_text("\n".join(lines))
    print("ALL DONE")
    return 0
