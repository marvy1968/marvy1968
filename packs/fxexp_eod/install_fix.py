#!/usr/bin/env python3
"""Install / undo the fx-experts end-of-day spread fix (2026-11-10).
  sudo python3 /opt/marv-bot/state/fxexp_eod/install_fix.py          install (backup, copy, run tests, roll back on failure)
  sudo python3 /opt/marv-bot/state/fxexp_eod/install_fix.py --undo   restore the backups
Only installs if the bot's current files are exactly the version the fix was built on."""
import hashlib, shutil, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
DST = Path("/opt/fx-experts")
FILES = {"fxexp/live.py": ("live.py", "c7818857dd566224766746a5ddd70fa17c74a6c299cd5dcbfe671ce4d2f5a0df"), "tests/test_live.py": ("test_live.py", "2f13c4247123de150d5a426c24e013090a464871bd339efd666d7cb85de964ba")}
BAK = ".bak-20261110"
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def tests():
    r = subprocess.run([str(DST / "venv/bin/python"), "-m", "pytest", "-q", "tests"], cwd=DST, capture_output=True, text=True)
    print(r.stdout[-600:] or r.stderr[-600:]); return r.returncode == 0
def restore():
    for rel in FILES:
        b = DST / (rel + BAK)
        if b.exists(): shutil.copy(b, DST / rel); print("restored", rel)
if "--undo" in sys.argv:
    restore(); sys.exit(0 if tests() else 1)
for rel, (src, old) in FILES.items():
    cur = DST / rel
    if not cur.exists(): sys.exit(f"STOP: {cur} not found; nothing changed")
    h = sha(cur)
    if h == sha(HERE / src): sys.exit(f"already installed ({rel}); nothing changed")
    if h != old: sys.exit(f"STOP: {rel} differs from the version the fix was built on; nothing changed")
for rel, (src, _) in FILES.items():
    shutil.copy(DST / rel, DST / (rel + BAK))          # backup
    shutil.copy(HERE / src, DST / rel)                 # copy onto the existing file: owner/permissions kept
print("copied; running the bot's tests ...")
if not tests():
    restore(); sys.exit("tests FAILED: backups restored, bot unchanged")
print("INSTALLED. The 5-minute timer picks it up on its next run; no restart needed. Undo: add --undo")
