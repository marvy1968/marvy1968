"""Read-only preflight: token valid, environment practice, accounts reachable, prices flowing.
Never prints the token; sends no orders."""
import argparse, json, sys
from .live import load_env
from .oanda import Oanda, OandaError
from .instruments import ALL_PAIRS as PAIRS, spread

def main():
    a = argparse.ArgumentParser(); a.add_argument("--env", required=True); a.add_argument("--config", required=True); a = a.parse_args()
    env, cfg = load_env(a.env), json.load(open(a.config))
    if env.get("OANDA_ENV") != "practice": sys.exit("FAIL: OANDA_ENV must be practice")
    ok = True
    for sid in cfg["systems"]:
        acct = env.get(f"OANDA_ACCOUNT_{sid.upper().replace('-', '_')}") or env.get("OANDA_ACCOUNT_ID", "")
        if not acct.startswith("101-"): print(f"FAIL {sid}: account id is not a practice (101-) account"); ok = False; continue
        try:
            c = Oanda(env["OANDA_TOKEN"], acct, "practice", dry_run=True); s = c.summary()
            print(f"OK   {sid}: acct ...{acct[-7:]} {s['currency']} NAV {float(s['NAV']):,.2f} hedging={s.get('hedgingEnabled')} open={s['openTradeCount']}")
        except OandaError as e:
            print(f"FAIL {sid}: {str(e)[:120]}"); ok = False
    if ok:
        px = c.pricing(PAIRS)
        for p in PAIRS:
            b, a2, t = px[p]; print(f"     {p} spread {(a2 - b) / spread(p) * 1.0:.1f}x typical tradeable={t}")
    sys.exit(0 if ok else 1)

if __name__ == "__main__": main()
