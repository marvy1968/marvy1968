#!/usr/bin/env python3
"""Show or set ONLY the "no_new_entries" key in the fx-experts bot's config (owner 2026-10-28, after none of its strategies passed the tests).

  fxexp_entries.py status | stop | resume

stop   -> "no_new_entries": true   (the bot opens nothing new; open trades keep their OANDA stops, daily trailing and our profit lock)
resume -> "no_new_entries": false
Every other key and the file's owner / mode are kept; the previous file is saved next to it as config.json.bak-<epoch>. Nothing else is touched."""
import json
import os
import shutil
import sys
import time

CFG = os.environ.get("FXEXP_CONFIG", "/opt/fx-experts/config/config.json")


def main(argv=None, path=CFG) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if len(argv) != 1 or argv[0] not in ("status", "stop", "resume"):
        print("usage: fxexp_entries.py status|stop|resume")
        return 2
    with open(path) as f:
        cfg = json.load(f)
    before = cfg.get("no_new_entries")
    print(f"no_new_entries before: {before}")
    if argv[0] != "status":
        want = argv[0] == "stop"
        if before is not want:
            st = os.stat(path)
            shutil.copy2(path, f"{path}.bak-{int(time.time())}")
            cfg["no_new_entries"] = want
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
            print(f"no_new_entries after: {json.load(f).get('no_new_entries')}")
    print(f"systems listed: {len(cfg.get('systems', []))}, installed: {len(cfg.get('installed', []))}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
