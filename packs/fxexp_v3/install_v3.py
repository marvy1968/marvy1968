#!/usr/bin/env python3
"""fx-experts update 2026-11-17 (owner: "winners to 3% of equity, losers to .05%"): per-system risk and a ceiling on total risk, on top of v2.
  sudo python3 /opt/marv-bot/state/fxexp_v3/install_v3.py          install (backup, copy, set config, run tests, roll back on failure)
  sudo python3 /opt/marv-bot/state/fxexp_v3/install_v3.py --undo   restore every backed-up file
What changes (edit WINNERS / LOSERS / CAP just below before running if you want different lists or numbers):
  * config "risk_by_system": WINNERS at 3.0% of NAV per trade (lien-fader), LOSERS at 0.05% (faith-turtle, bollinger-squeeze,
    crabel-idnr4, tharp-random, raschke-soup, lien-zeros, ponsi-trend, dalton-value). williams-vbo and brooks-range-fade
    keep "risk_pct". The 10-year backtest's worst drawdown for Fader was 41.7R: at 3% a repeat is about -70% of equity.
  * config "max_open_risk_pct" = 9: a new entry is deferred while open trades + resting entry orders + the new trade would
    risk more than 9% of NAV (three full 3% trades). Set it to 0 (or delete it) to remove the ceiling.
  * also sets v2's "entries_off" and "news_pause" if they are not there yet (an existing value is left alone).
  * fxexp/live.py: risk_for(sid) and open_risk_pct(); the sizing step uses them. Includes everything v2 added.
Installs only if the bot's files are a version these changes were built on (original, spread-fixed, v2 or the earlier v3); otherwise stops and changes nothing."""
import hashlib, json, os, shutil, subprocess, sys
from pathlib import Path
HERE = Path(__file__).resolve().parent
DST = Path("/opt/fx-experts")
OK = {"fxexp/live.py": {"bb7908b44a4823675e61cca26198685686dc7ec2213f9fd725472a599f3c4fa6", "c7818857dd566224766746a5ddd70fa17c74a6c299cd5dcbfe671ce4d2f5a0df", "234e64b97c9be0309abc14182ae53426d044159f6a408cba060122619bf52c4c", "57b4fa487d013e73935e17355098903086ac8e48211f9a9f75a3c67a1be1b8f1"}, "tests/test_live.py": {"2d313435791f862a808483c1240f95aa64ca97d8f15555a3a58d3f2d01fb91cd", "2f13c4247123de150d5a426c24e013090a464871bd339efd666d7cb85de964ba", "0dadcdce1c8e00e5eaaf99f9a3d379a04edd89ba3181b03b1ffcc0463de20772", "60533d8d040bcaab6f91d2b8f067a11b242f0a9c8aa0124ceb5bf1ecd2bd9bc9"}, "tests/conftest.py": {"de928dc2e8617bcc3b48d804201cf9dd5dcf6d32c5889d5552387316d4274a92", "3dd91082f855f92bde1c235a4b3944087e7d642e658480d479c0b6528ffd9ed2"}}
COPY = {"fxexp/live.py": "live.py", "fxexp/news.py": "news.py", "tests/test_live.py": "test_live.py", "tests/conftest.py": "conftest.py"}
CONF = "config/config.json"
OFF = ["faith-turtle", "tharp-random", "bollinger-squeeze", "crabel-idnr4"]
WINNERS = ["lien-fader"]                                    # 3% of NAV per trade
LOSERS = ["faith-turtle", "bollinger-squeeze", "crabel-idnr4", "tharp-random", "raschke-soup", "lien-zeros", "ponsi-trend", "dalton-value"]  # 0.05%
WIN_PCT, LOSE_PCT, CAP = 3.0, 0.05, 9.0
RISK = {**{k: WIN_PCT for k in WINNERS}, **{k: LOSE_PCT for k in LOSERS}}
BAK = ".bak-20261117"
def sha(p): return hashlib.sha256(p.read_bytes()).hexdigest()
def failing():
    """Set of failing test ids (empty = all pass). Compared before and after, so a test that was already failing
    on this machine (e.g. one that hard-codes the config's risk_pct) does not block or undo the install."""
    r = subprocess.run([str(DST / "venv/bin/python"), "-m", "pytest", "-q", "-rf", "tests"], cwd=DST, capture_output=True, text=True)
    out = (r.stdout or "") + (r.stderr or "")
    ids = {l.split(" ", 2)[1] for l in out.splitlines() if l.startswith("FAILED ")}
    if r.returncode and not ids: ids = {"<pytest did not run cleanly>"}
    print(out[-500:]); return ids
def tests(base=frozenset()):
    new = failing() - set(base)
    if new: print("NEW test failures:", sorted(new))
    return not new
def restore():
    for rel in list(COPY) + [CONF]:
        b, f = DST / (rel + BAK), DST / rel
        if b.exists(): shutil.copy(b, f); print("restored", rel)
        elif rel == "fxexp/news.py" and f.exists(): f.unlink(); print("removed", rel)
if "--undo" in sys.argv:
    restore(); failing(); sys.exit(0)
same = []
for rel, ok in OK.items():
    f = DST / rel
    if not f.exists(): sys.exit(f"STOP: {f} not found; nothing changed")
    same.append(sha(f) == sha(HERE / COPY[rel]))
    if not same[-1] and sha(f) not in ok: sys.exit(f"STOP: {rel} differs from the versions this update was built on; nothing changed")
if all(same): sys.exit("already installed; nothing changed")
cfg = json.loads((DST / CONF).read_text())
missing = [s for s in set(OFF) | set(RISK) if s not in cfg.get("systems", [])]
if missing: sys.exit(f"STOP: config has no {missing}; nothing changed")
print("baseline test run (before any change) ...")
BASE = failing()
st = (DST / "fxexp/live.py").stat()
for rel in list(COPY) + [CONF]:
    if (DST / rel).exists(): shutil.copy(DST / rel, DST / (rel + BAK))       # backups
for rel, src in COPY.items():
    shutil.copy(HERE / src, DST / rel)                                       # existing files keep their owner/permissions
    os.chown(DST / rel, st.st_uid, st.st_gid)
cfg.setdefault("entries_off", OFF); cfg.setdefault("news_pause", True); cfg["risk_by_system"] = {**cfg.get("risk_by_system", {}), **RISK}; cfg["max_open_risk_pct"] = CAP
tmp = DST / (CONF + ".tmp"); tmp.write_text(json.dumps(cfg, indent=2) + "\n"); os.replace(tmp, DST / CONF)
cs = (DST / (CONF + BAK)).stat(); os.chown(DST / CONF, cs.st_uid, cs.st_gid); os.chmod(DST / CONF, cs.st_mode & 0o777)
print("copied and config set; running the bot's tests ...")
if not tests(BASE):
    restore(); sys.exit("tests FAILED: backups restored, bot unchanged")
print(f"INSTALLED: {WINNERS} risk {WIN_PCT}% of NAV per trade, {LOSERS} risk {LOSE_PCT}%; total open+resting risk capped at {CAP}% of NAV; v2 (losers off, news pause, spread fix) included.")
print("The 5-minute timer picks it up on its next run; no restart needed. Undo: add --undo")
