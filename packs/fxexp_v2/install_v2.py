#!/usr/bin/env python3
"""fx-experts update 2026-11-11 (owner: "turn off the losers and add news pause"), plus the end-of-day spread fix.
  sudo python3 /opt/marv-bot/state/fxexp_v2/install_v2.py          install (backup, copy, set config, run tests, roll back on failure)
  sudo python3 /opt/marv-bot/state/fxexp_v2/install_v2.py --undo   restore every backed-up file
What changes:
  * config: "entries_off" = faith-turtle, tharp-random, bollinger-squeeze, crabel-idnr4 (no NEW trades; their open trades
    keep their broker stops and normal management, Crabel still closes at 16:50 New York); "news_pause" = true.
  * fxexp/live.py: per-system entry switch, news pause, end-of-day spread fix.   * fxexp/news.py: new (calendar + rule).
Installs only if the bot's files are a version these changes were built on; otherwise stops and changes nothing."""
import hashlib, json, os, shutil, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
DST = Path("/opt/fx-experts")
OK = {"fxexp/live.py": {"c7818857dd566224766746a5ddd70fa17c74a6c299cd5dcbfe671ce4d2f5a0df", "234e64b97c9be0309abc14182ae53426d044159f6a408cba060122619bf52c4c"}, "tests/test_live.py": {"2f13c4247123de150d5a426c24e013090a464871bd339efd666d7cb85de964ba", "0dadcdce1c8e00e5eaaf99f9a3d379a04edd89ba3181b03b1ffcc0463de20772"}, "tests/conftest.py": {"de928dc2e8617bcc3b48d804201cf9dd5dcf6d32c5889d5552387316d4274a92"}}
COPY = {"fxexp/live.py": "live.py", "fxexp/news.py": "news.py", "tests/test_live.py": "test_live.py", "tests/conftest.py": "conftest.py"}
CONF = "config/config.json"
OFF = ["faith-turtle", "tharp-random", "bollinger-squeeze", "crabel-idnr4"]
BAK = ".bak-20261111"
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def tests():
    r = subprocess.run([str(DST / "venv/bin/python"), "-m", "pytest", "-q", "tests"], cwd=DST, capture_output=True, text=True)
    print((r.stdout or r.stderr)[-700:]); return r.returncode == 0
def restore():
    for rel in list(COPY) + [CONF]:
        b, f = DST / (rel + BAK), DST / rel
        if b.exists(): shutil.copy(b, f); print("restored", rel)
        elif rel == "fxexp/news.py" and f.exists(): f.unlink(); print("removed", rel)
if "--undo" in sys.argv:
    restore(); sys.exit(0 if tests() else 1)
for rel, ok in OK.items():
    f = DST / rel
    if not f.exists(): sys.exit(f"STOP: {f} not found; nothing changed")
    if sha(f) == sha(HERE / COPY[rel]): sys.exit("already installed; nothing changed")
    if sha(f) not in ok: sys.exit(f"STOP: {rel} differs from the versions this update was built on; nothing changed")
cfg = json.loads((DST / CONF).read_text())
missing = [s for s in OFF if s not in cfg.get("systems", [])]
if missing: sys.exit(f"STOP: config has no {missing}; nothing changed")
st = (DST / "fxexp/live.py").stat()
for rel in list(COPY) + [CONF]:
    if (DST / rel).exists(): shutil.copy(DST / rel, DST / (rel + BAK))       # backups
for rel, src in COPY.items():
    shutil.copy(HERE / src, DST / rel)                                       # existing files keep their owner/permissions
    os.chown(DST / rel, st.st_uid, st.st_gid)
cfg["entries_off"] = OFF; cfg["news_pause"] = True
tmp = DST / (CONF + ".tmp"); tmp.write_text(json.dumps(cfg, indent=2) + "\n"); os.replace(tmp, DST / CONF)
cs = (DST / (CONF + BAK)).stat(); os.chown(DST / CONF, cs.st_uid, cs.st_gid); os.chmod(DST / CONF, cs.st_mode & 0o777)
print("copied and config set; running the bot's tests ...")
if not tests():
    restore(); sys.exit("tests FAILED: backups restored, bot unchanged")
print("INSTALLED: Turtle, Tharp random, Bollinger and Crabel take no new trades; news pause on; spread fix on.")
print("The 5-minute timer picks it up on its next run; no restart needed. Undo: add --undo")
