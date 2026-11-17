"""Safe OANDA secret setter. Run on the VM as root:  sudo python3 set_oanda_secrets.py [--fxexp-file PATH]

- Asks for the token with no echo (getpass); never prints, logs or stores it anywhere except the env files.
- Checks it against OANDA practice (GET /v3/accounts) and that your account id is on it, before writing anything.
- Updates OANDA_API_TOKEN / OANDA_ACCOUNT_ID in /opt/marv-bot/.env (the Claude runner);
  with --fxexp-file also OANDA_TOKEN / OANDA_ACCOUNT_ID / OANDA_ENV for the fx-experts bot.
- Keeps each file's owner, sets mode 600, and saves a mode-600 backup next to it first (.bak-YYYYmmdd-HHMMSS).
Undo: copy the .bak file back over the original.
"""
import argparse
import getpass
import json
import os
import shutil
import sys
import urllib.request
from datetime import datetime

HOST = "https://api-fxpractice.oanda.com"
MARV_ENV = "/opt/marv-bot/.env"


def set_keys(text, updates):
    """Return env-file text with KEY=value lines replaced (or appended). Other lines are untouched."""
    lines = text.splitlines()
    seen = set()
    out = []
    for ln in lines:
        key = ln.split("=", 1)[0].strip() if "=" in ln and not ln.lstrip().startswith("#") else None
        if key in updates:
            if key not in seen:
                out.append(f"{key}={updates[key]}")
                seen.add(key)
            continue  # drop duplicate lines for the same key
        out.append(ln)
    for key, val in updates.items():
        if key not in seen:
            out.append(f"{key}={val}")
    return "\n".join(out) + "\n"


def oanda_accounts(token, timeout=15):
    req = urllib.request.Request(HOST + "/v3/accounts", headers={"Authorization": f"Bearer {token}", "Accept": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return [a["id"] for a in json.loads(r.read().decode())["accounts"]]


def write_env(path, updates):
    st = os.stat(path) if os.path.exists(path) else None
    old = open(path).read() if st else ""
    if st:
        bak = f"{path}.bak-{datetime.now():%Y%m%d-%H%M%S}"
        shutil.copy2(path, bak)
        os.chmod(bak, 0o600)
    tmp = path + ".tmp"
    with open(tmp, "w") as f:
        f.write(set_keys(old, updates))
    os.chmod(tmp, 0o600)
    if st:
        os.chown(tmp, st.st_uid, st.st_gid)
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fxexp-file", help="env file the fx-experts bot reads (optional)")
    ap.add_argument("--marv-env", default=MARV_ENV)
    a = ap.parse_args()
    token = getpass.getpass("OANDA practice token (input hidden): ").strip()
    acct = input("Primary account id (ends in -001): ").strip()
    if not token or not acct:
        sys.exit("need both a token and an account id; nothing written")
    try:
        ids = oanda_accounts(token)
    except Exception as e:
        sys.exit(f"OANDA rejected the token or could not be reached ({type(e).__name__}); nothing written")
    print(f"OANDA accepted the token; it can see {len(ids)} account(s)")
    if acct not in ids:
        sys.exit("that account id is not on this token; nothing written")
    write_env(a.marv_env, {"OANDA_API_TOKEN": token, "OANDA_ACCOUNT_ID": acct})
    print(f"updated {a.marv_env} (mode 600, backup saved)")
    if a.fxexp_file:
        write_env(a.fxexp_file, {"OANDA_TOKEN": token, "OANDA_ACCOUNT_ID": acct, "OANDA_ENV": "practice"})
        print(f"updated {a.fxexp_file} (mode 600, backup saved)")
    print("done. The token was not printed or logged.")


if __name__ == "__main__":
    main()
