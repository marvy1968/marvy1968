#!/usr/bin/env python3
"""Show or set ONLY the "no_new_entries" key in the fx-experts bot's config (owner 2026-10-28, after none of its strategies passed the tests).

  fxexp_entries.py status | stop | resume | risk <pct>

stop   -> "no_new_entries": true   (the bot opens nothing new; open trades keep their OANDA stops, daily trailing and our profit lock)
resume -> "no_new_entries": false
risk   -> "risk_pct": <pct> (per-trade risk in % of NAV; owner 2026-11-02 "switch on with smaller equity"). Only 0.05 .. 0.25 (never above
          the bot's own 0.25 default).
Every other key and the file's owner / mode are kept; the previous file is saved next to it as config.json.bak-<epoch>. Nothing else is touched."""
import json
import os
import shutil
import sys
import time

CFG = os.environ.get("FXEXP_CONFIG", "/opt/fx-experts/config/config.json")


def _pct(x):
    try:
        v = round(float(x), 4)
    except ValueError:
        return None
    return v if 0.05 <= v <= 0.25 else None


def main(argv=None, path=CFG) -> int:
    argv = sys.argv[1:] if argv is None else argv
    ok = (len(argv) == 1 and argv[0] in ("status", "stop", "resume")) or (len(argv) == 2 and argv[0] == "risk" and _pct(argv[1]) is not None)
    if not ok:
        print("usage: fxexp_entries.py status|stop|resume|risk <0.05..0.25>")
        return 2
    with open(path) as f:
        cfg = json.load(f)
    key, want = ("risk_pct", _pct(argv[1])) if argv[0] == "risk" else ("no_new_entries", argv[0] == "stop")
    before = cfg.get("no_new_entries")
    print(f"no_new_entries before: {before}")
    print(f"risk_pct before: {cfg.get('risk_pct')}")
    if argv[0] != "status":
        if cfg.get(key) != want:
            st = os.stat(path)
            shutil.copy2(path, f"{path}.bak-{int(time.time())}")
            cfg[key] = want
            tmp = path + ".tmp"
            with open(tmp, "w") as f:
                json.dump(cfg, f, indent=2)
            os.chmod(tmp, st.st_mode & 0o7777)
            try:
                os.chown(tmp, st.st_uid, st.st_gid)
            except PermissionError:
                pass
            os.replace(tmp, path)
        with open(path) as f:
            after = json.load(f)
            print(f"no_new_entries after: {after.get('no_new_entries')}")
            print(f"risk_pct after: {after.get('risk_pct')}")
    print(f"systems listed: {len(cfg.get('systems', []))}, installed: {len(cfg.get('installed', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
