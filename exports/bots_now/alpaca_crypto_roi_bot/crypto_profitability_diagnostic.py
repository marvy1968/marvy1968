import copy
import json
import subprocess
from pathlib import Path

import yaml

ROOT = Path("/home/moonrider1968/bots/alpaca_crypto_roi_bot")
CFG_PATH = ROOT / "config" / "config.yaml"
BACKUP = ROOT / "config" / "config.yaml.profitability_backup"

with CFG_PATH.open() as f:
    original = yaml.safe_load(f)

BACKUP.write_text(CFG_PATH.read_text())

tests = [
    {
        "name": "BASELINE",
        "changes": {}
    },
    {
        "name": "LOWER_COSTS",
        "changes": {
            ("execution", "slippage_bps_backtest"): 6,
            ("execution", "fee_bps_backtest"): 10,
        }
    },
    {
        "name": "ENTRY_Z_1_75",
        "changes": {
            ("signal", "entry_z"): -1.75,
        }
    },
    {
        "name": "ENTRY_Z_1_50",
        "changes": {
            ("signal", "entry_z"): -1.50,
        }
    },
    {
        "name": "RELAX_CORR",
        "changes": {
            ("signal", "min_correlation"): 0.60,
        }
    },
    {
        "name": "RELAX_ADF",
        "changes": {
            ("signal", "max_adf_pvalue"): 0.12,
        }
    },
    {
        "name": "LONGER_HALF_LIFE",
        "changes": {
            ("signal", "max_half_life_bars"): 120,
        }
    },
    {
        "name": "WIDER_RSI",
        "changes": {
            ("signal", "rsi_min"): 25,
            ("signal", "rsi_max"): 65,
        }
    },
]

def apply(cfg, changes):
    x = copy.deepcopy(cfg)
    for keys, value in changes.items():
        d = x
        for k in keys[:-1]:
            d = d[k]
        d[keys[-1]] = value
    return x

def run_backtest(name, cfg):
    CFG_PATH.write_text(yaml.safe_dump(cfg, sort_keys=False))

    proc = subprocess.run(
        [str(ROOT / ".venv/bin/python"), "roi_bot.py", "backtest"],
        cwd=ROOT,
        text=True,
        capture_output=True,
    )

    out = proc.stdout.strip()
    err = proc.stderr.strip()

    result = {
        "name": name,
        "return_code": proc.returncode,
        "stdout": out,
        "stderr": err,
    }

    try:
        start = out.find("{")
        end = out.rfind("}")
        if start >= 0 and end > start:
            data = json.loads(out[start:end+1])
            result.update({
                "roi_pct": data.get("roi_pct"),
                "max_drawdown_pct": data.get("max_drawdown_pct"),
                "closed_trades": data.get("closed_trades"),
                "win_rate_pct": data.get("win_rate_pct"),
                "profit_factor": data.get("profit_factor"),
                "expectancy_dollars": data.get("expectancy_dollars"),
                "ending_equity": data.get("ending_equity"),
                "per_symbol": data.get("per_symbol", {}),
                "per_regime": data.get("per_regime", {}),
            })
    except Exception as e:
        result["parse_error"] = str(e)

    return result

results = []

try:
    for test in tests:
        print("=" * 100)
        print("RUNNING:", test["name"])
        print("=" * 100)

        cfg = apply(original, test["changes"])
        result = run_backtest(test["name"], cfg)
        results.append(result)

        print(
            f"{test['name']:20s} | "
            f"Trades={result.get('closed_trades')} | "
            f"ROI={result.get('roi_pct')} | "
            f"PF={result.get('profit_factor')} | "
            f"WR={result.get('win_rate_pct')} | "
            f"DD={result.get('max_drawdown_pct')} | "
            f"Expectancy={result.get('expectancy_dollars')}"
        )

finally:
    CFG_PATH.write_text(BACKUP.read_text())

out_path = ROOT / "crypto_profitability_diagnostic_results.json"
out_path.write_text(json.dumps(results, indent=2))

print()
print("=" * 100)
print("CRYPTO PROFITABILITY DIAGNOSTIC SUMMARY")
print("=" * 100)

for r in results:
    print(
        f"{r['name']:20s} | "
        f"N={str(r.get('closed_trades')):>4s} | "
        f"ROI={str(r.get('roi_pct')):>10s} | "
        f"PF={str(r.get('profit_factor')):>8s} | "
        f"WR={str(r.get('win_rate_pct')):>8s} | "
        f"DD={str(r.get('max_drawdown_pct')):>8s} | "
        f"Exp=${str(r.get('expectancy_dollars'))}"
    )

print()
print("RESULT FILE:", out_path)
print("ORIGINAL CONFIG RESTORED:", CFG_PATH)
print("=" * 100)
