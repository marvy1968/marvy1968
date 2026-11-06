#!/usr/bin/env python3
"""VM link: run allowlisted Marv jobs that Claude pushes to the `vm-link` branch, push the output back.

Claude can't reach the VM, but both can reach GitHub. Claude commits a job file `jobs/<name>.job` (one
command line) to the vm-link branch; this runner (systemd timer, every minute) runs it and commits
`results/<name>.txt`. Only the commands below are accepted, nothing goes through a shell, and every
secret value from /opt/marv-bot/.env is blanked out of the output before it's pushed.

Accepted job lines:
  tests                         run the unit tests
  update                        pull the bot branch, reinstall deps, restart services (deploy/install.sh)
  status                        service status, timers, bot version, disk
  logs <unit> [lines]           journalctl for a marv-* unit (default 200 lines)
  show <path>                   print a file under /opt/marv-bot/state (not .env), up to 200 KB
  telegram cards/<name>.txt [mirror]  send that card (committed by Claude) to the owner's Telegram chat
  find-odds-bot                 list other bots on the VM (paths, services, cron), no code
  read-bot-reports /abs/folder  tails of that bot's logs + heads of reports/csv (secrets scrubbed)
  pack <name>                   copy packs/<name>/*.md|py|txt|csv|json (committed by Claude) into /opt/marv-bot/state/<name>/
  export-bot <name> /abs/folder redacted copy of that bot's code into exports/<name>/ (committed to the vm-link branch)
  fx-experts-entries status|stop|resume|risk <0.05..0.25>  show / set only no_new_entries or risk_pct in the fx-experts bot's config
  gemini [question words]       send consult/gemini_brief.md to Gemini (GEMINI_API_KEY in .env), print its reply
  find-trading-bot              same, for Alpaca / broker bots (paths only)
  muse-env [/abs/folder]        owner 2026-11-06: write <muse1 folder>/.env with ONLY the ALPACA_* lines of Marv's .env (chmod 600,
                                owned like the folder; values never printed). No folder given: the one directory named muse1
  patch-report-card [undo]      apply packs/report_card/tg_report.patch to the owner's bots/tg_report.py (dry run, backup) / restore
  where-text <words>            the owner's bot files (py/sh/js under /home/moonrider1968) that contain those words: paths only
  alpaca download|backtest ...  python -m alpacabot (market-data download, walk-forward backtests; no order placement)
  read-odds-bot /abs/folder     that bot's text files with secrets stripped (deploy/find_odds_bot.sh)
  marv <command> [args...]      python -m marv ... as the marvbot user; `run`/`props` need --dry-run,
                                --send / --watch / serve / live are refused (the timers do those)
"""

import os
import re
import subprocess
import sys
import time
from pathlib import Path

BOT = Path("/opt/marv-bot")
LINK = Path("/opt/marv-link")
BRANCH = os.environ.get("VM_LINK_BRANCH", "vm-link")
KEY = "/root/.ssh/marv_link"
GIT_ENV = {**os.environ, "GIT_SSH_COMMAND": f"ssh -i {KEY} -o IdentitiesOnly=yes -o StrictHostKeyChecking=accept-new"}
MARV_OK = {"run", "stats-backtest", "h2h-backtest", "props", "props-backtest", "ingame", "edges", "situational",
           "check", "report", "sports", "probe-ewl", "test-telegram", "get-chat-id", "gaps", "live-probe", "injuries", "live-props-probe", "grade-prop", "mirror-status", "talent-dump"}
MARV_FLAGS_REFUSED = {"--send", "--watch"}
UNITS = {"marv-bot", "marv-bot-late", "marv-bridge", "marv-live", "marv-edges", "marv-vmlink", "marv-health", "marv-alpaca-paper", "marv-swing-scan", "marv-swing-open"}
TOKEN = re.compile(r"^[A-Za-z0-9_.,:=/@+%-]+$")
MAX_OUT = 200_000
JOB_TIMEOUT = int(os.environ.get("VM_LINK_TIMEOUT", 45 * 60))  # per job; a "# timeout 7200" line in the job overrides


def git(*args, check=True):
    return subprocess.run(["git", "-C", str(LINK), *args], env=GIT_ENV, capture_output=True, text=True, check=check)


def secrets() -> list[str]:
    vals = []
    env = BOT / ".env"
    if env.exists():
        for line in env.read_text(errors="ignore").splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                v = line.split("=", 1)[1].strip().strip("'\"")
                if len(v) >= 6:
                    vals.append(v)
    return sorted(vals, key=len, reverse=True)


def redact(text: str) -> str:
    for v in secrets():
        text = text.replace(v, "***")
    return text


def parse(line: str) -> tuple[list[str] | None, str]:
    """Job line -> (argv, error). argv runs without a shell."""
    words = line.split()
    if not words:
        return None, "empty job"
    bad = [w for w in words if not TOKEN.match(w)]
    if bad:
        return None, f"refused characters in: {' '.join(bad)}"
    cmd, args = words[0], words[1:]
    py = str(BOT / ".venv/bin/python")
    as_bot = ["runuser", "-u", "marvbot", "--"]
    if cmd == "tests":
        return as_bot + [py, "-m", "unittest", "discover", "-s", "tests"], ""
    if cmd == "update":  # the installer fetches over SSH with the deploy key, so it works with a private repo
        return ["bash", f"{BOT}/deploy/install.sh"], ""
    if cmd == "telegram":  # send a card committed to the vm-link branch (cards/<name>.txt) to the owner's chat
        if not args or len(args) > 2 or not re.fullmatch(r"cards/[A-Za-z0-9_.-]+\.txt", args[0]) \
                or (len(args) == 2 and args[1] != "mirror"):
            return None, "telegram needs cards/<name>.txt [mirror]"
        return as_bot + [py, "-m", "marv", "notify", "--mono", *(["--mirror"] if len(args) == 2 else []),
                         "--file", str(LINK / args[0])], ""
    if cmd == "find-odds-bot":  # where the owner's other bots live and how they run (paths only, no code)
        return ["bash", str(BOT / "deploy/find_odds_bot.sh")], ""
    if cmd == "alpaca":  # backtests / data download for alpacabot (never live trading: that has no job here)
        if not args or args[0] not in ("download", "backtest", "run-all", "check", "paper-report", "coverage", "history", "symbol-check", "positions", "close-others", "swing", "paper-eod", "paper-pause", "paper-flatten", "paper-trades", "paper-telegram", "equity-vs-spy"):
            return None, "alpaca needs download, backtest, run-all, check or paper-report"
        return as_bot + [py, "-m", "alpacabot", *args], ""
    if cmd == "fx-experts-entries":  # owner 2026-10-28: show / set ONLY no_new_entries in /opt/fx-experts/config/config.json (deploy/fxexp_entries.py)
        if not ((len(args) == 1 and args[0] in ("status", "stop", "resume")) or
                (len(args) == 2 and args[0] == "risk" and re.fullmatch(r"0\.\d{1,4}", args[1]))):
            return None, "fx-experts-entries needs status, stop, resume or risk <0.05..0.25>"
        return ["python3", str(BOT / "deploy/fxexp_entries.py"), *args], ""
    if cmd == "patch-report-card":  # owner 2026-11-04 ("yes fix the report card"): the patch Claude committed to packs/report_card/ applied to
        # the owner's /home/moonrider1968/bots/tg_report.py ONLY if it applies cleanly (dry run first), with a dated backup; "undo" restores it
        f, pf = "/home/moonrider1968/bots/tg_report.py", f"{LINK}/packs/report_card/tg_report.patch"
        if args == ["undo"]:
            script = (f"set -e; b=$(ls -1t {f}.bak-* | head -1); cp -p \"$b\" {f}; echo restored from $b")
        elif not args:
            script = (f"set -e; test -f {f}; test -f {pf}; patch --dry-run -N {f} < {pf}; cp -p {f} {f}.bak-$(date +%Y%m%d%H%M); "
                      f"patch -N {f} < {pf}; chown --reference={f}.bak-* {f} 2>/dev/null || true; "
                      f"python3 -m py_compile {f} && echo compiles; "
                      f"sudo -u moonrider1968 test -r {BOT}/state/alpaca_velez/trades.jsonl && echo 'owner user can read the Marv ledgers' "
                      f"|| echo 'WARNING: owner user cannot read {BOT}/state ledgers'")
        else:
            return None, "patch-report-card takes no argument, or undo"
        return ["bash", "-c", script], ""
    if cmd == "pack":  # owner 2026-11-02: copy files Claude committed to packs/<name>/ on the vm-link branch into /opt/marv-bot/state/<name>/
        if len(args) != 1 or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", args[0]):
            return None, "pack needs <name> (files committed under packs/<name>/)"
        src, dst = f"{LINK}/packs/{args[0]}", f"{BOT}/state/{args[0]}"
        script = (f"set -e; test -d {src}; mkdir -p {dst}; "
                  f"find {src} -maxdepth 1 -type f \\( -name '*.md' -o -name '*.py' -o -name '*.txt' -o -name '*.csv' -o -name '*.json' \\) "
                  f"-size -2M -exec cp {{}} {dst}/ \\; ; chown -R marvbot:marvbot {dst} 2>/dev/null || true; ls -la {dst}")
        return ["bash", "-c", script], ""
    if cmd == "export-bot":  # redacted copy of a bot folder into exports/<name>/ on the vm-link branch (no 200 KB output limit)
        if len(args) != 2 or not re.fullmatch(r"[A-Za-z0-9_-]{1,40}", args[0]) or not args[1].startswith("/") or ".." in args[1]:
            return None, "export-bot needs <name> /abs/folder"
        dest = f"{LINK}/exports/{args[0]}"
        script = (f"set -e; export HOME=/root; bash {BOT}/deploy/find_odds_bot.sh {args[1]} >/dev/null; d=$(mktemp -d); "
                  f"tar -xzf /root/odds-bot-redacted.tar.gz -C $d; rm -rf {dest}; mkdir -p {dest}; cd $d/odds-bot; "
                  "find . -type f \\( -name '*.py' -o -name '*.json' -o -name '*.txt' -o -name '*.md' -o -name '*.sh' -o -name '*.service' "
                  f"-o -name '*.toml' -o -name '*.yaml' -o -name '*.yml' -o -name '*.cfg' \\) -size -300k -not -path '*/archive/*' "
                  f"-not -name '*.log' | while read f; do mkdir -p \"{dest}/$(dirname \"$f\")\"; cp \"$f\" \"{dest}/$f\"; done; rm -rf $d; "
                  f"if grep -rIlE '[0-9a-f]{{32}}-[0-9a-f]{{32}}|[0-9]{{3}}-[0-9]{{3}}-[0-9]{{6,9}}-[0-9]{{3}}|[0-9]{{8,10}}:[A-Za-z0-9_-]{{30,}}' {dest}; "
                  f"then echo 'SECRET-LIKE TEXT LEFT: export removed'; rm -rf {dest}; exit 1; fi; "
                  f"echo exported $(find {dest} -type f | wc -l) files, $(du -sk {dest} | cut -f1) KB")
        return ["bash", "-c", script], ""
    if cmd == "gemini":  # consult Gemini with consult/gemini_brief.md (+ optional words); needs GEMINI_API_KEY in .env
        return as_bot + [py, str(BOT / "tools/gemini_consult.py"), *args], ""
    if cmd == "alpaca-paper-status":  # the paper-trading service and its last log lines
        return ["bash", "-c", "systemctl --no-pager status marv-alpaca-paper | head -15; journalctl -u marv-alpaca-paper -n 30 --no-pager"], ""
    if cmd == "read-bot-reports":  # logs/reports/csv heads of a bot folder, secrets scrubbed (find_odds_bot.sh --reports)
        if len(args) != 1 or not args[0].startswith("/") or ".." in args[0]:
            return None, "read-bot-reports needs one absolute folder path"
        return ["bash", str(BOT / "deploy/find_odds_bot.sh"), "--reports", args[0]], ""
    if cmd == "where-text":  # owner 2026-11-04: which bot script sends a given message (file paths only, never the code)
        words = " ".join(args).strip()
        if not words or len(words) > 80 or not all(ch.isalnum() or ch in " -_|:." for ch in words):
            return None, "where-text needs 1-80 characters of plain text (letters, digits, space - _ | : .)"
        return ["grep", "-rIlnF", "--include=*.py", "--include=*.sh", "--include=*.js", "-e", words, "/home/moonrider1968"], ""
    if cmd == "muse-env":  # owner 2026-11-06 ("Add env file to muse1", "Muse1 is on my vm"): Muse's bot gets the Alpaca keys only
        if len(args) > 1 or (args and (not args[0].startswith("/") or ".." in args[0])):
            return None, "muse-env takes no argument or one absolute folder path"
        find = (f"d={args[0]}; " if args else
                "m=$(find /home /opt /srv /root -maxdepth 4 -type d -iname muse1 2>/dev/null); n=$(printf '%s' \"$m\" | grep -c . || true); "
                "if [ \"$n\" != 1 ]; then echo \"found $n folders named muse1:\"; printf '%s\\n' \"$m\"; "
                "echo 'run: muse-env /abs/folder'; exit 3; fi; d=$m; ")
        script = (f"set -e; {find}test -d \"$d\" || {{ echo \"no such folder: $d\"; exit 2; }}; "
                  f"f=\"$d/.env\"; test -e \"$f\" && cp -p \"$f\" \"$f.bak-$(date +%Y%m%d%H%M)\" && echo \"backed up the old $f\"; "
                  f"umask 077; grep -E '^ALPACA_[A-Z_]*=' {BOT}/.env > \"$f.new\"; "
                  f"test -s \"$f.new\" || {{ rm -f \"$f.new\"; echo 'no ALPACA_ lines in the Marv .env'; exit 4; }}; "
                  f"if test -e \"$f\"; then grep -vE '^ALPACA_[A-Z_]*=' \"$f\" >> \"$f.new\" || true; echo 'kept the other lines of the old .env'; fi; "
                  f"mv \"$f.new\" \"$f\"; chmod 600 \"$f\"; chown --reference=\"$d\" \"$f\"; "
                  f"echo \"wrote $f (mode 600, owner $(stat -c %U \"$f\")) with these names (values not shown):\"; cut -d= -f1 \"$f\"")
        return ["bash", "-c", script], ""
    if cmd == "find-trading-bot":  # where an Alpaca / broker bot lives on the VM (paths only, no code)
        return ["bash", str(BOT / "deploy/find_odds_bot.sh"), "--trading"], ""
    if cmd == "read-odds-bot":  # the bot's code with secrets stripped (find_odds_bot.sh redaction), as text
        if len(args) != 1 or not args[0].startswith("/") or ".." in args[0]:
            return None, "read-odds-bot needs one absolute folder path (from find-odds-bot)"
        script = (f"set -e; export HOME=/root; bash {BOT}/deploy/find_odds_bot.sh {args[0]} >/dev/null; d=$(mktemp -d); "
                  "tar -xzf /root/odds-bot-redacted.tar.gz -C $d; cd $d/odds-bot; "
                  "find . -type f \\( -name '*.py' -o -name '*.json' -o -name '*.txt' -o -name '*.md' -o -name '*.sh' "
                  "-o -name '*.service' -o -name '*.toml' -o -name '*.yaml' -o -name '*.yml' -o -name '*.cfg' \\) "
                  "-size -300k | sort | while read f; do echo; echo \"===== $f\"; cat \"$f\"; done; rm -rf $d")
        return ["bash", "-c", script], ""
    if cmd == "status":
        return ["bash", "-c", "systemctl --no-pager status 'marv-*' | head -80; systemctl list-timers 'marv-*' --no-pager;"
                f" git -c safe.directory={BOT} -C {BOT} log -3 --oneline; df -h /"], ""
    if cmd == "logs":
        if not args or args[0] not in UNITS:
            return None, f"logs needs one of: {', '.join(sorted(UNITS))}"
        n = args[1] if len(args) > 1 and args[1].isdigit() else "200"
        return ["journalctl", "-u", args[0], "-n", n, "--no-pager"], ""
    if cmd == "show":
        if len(args) != 1:
            return None, "show needs one path under state/"
        path = (BOT / "state" / args[0]).resolve()
        if not str(path).startswith(str(BOT / "state")) or path.name.startswith(".env"):
            return None, "show only reads files under /opt/marv-bot/state"
        return ["head", "-c", str(MAX_OUT), str(path)], ""
    if cmd == "marv":
        if not args or args[0] not in MARV_OK:
            return None, f"marv command must be one of: {', '.join(sorted(MARV_OK))}"
        if MARV_FLAGS_REFUSED & set(args):
            return None, "--send / --watch are refused (the services do those)"
        if args[0] in ("run", "props") and "--dry-run" not in args and "--grade" not in args:
            return None, f"marv {args[0]} needs --dry-run (the timers send the real cards)"
        return as_bot + [py, "-m", "marv", *args], ""
    return None, f"unknown job '{cmd}' (tests, update, status, logs, show, find-odds-bot, read-odds-bot, marv)"


def push(message: str) -> None:
    git("add", "-A")
    if git("diff", "--cached", "--quiet", check=False).returncode == 0:
        return
    git("-c", "user.name=marv-vm", "-c", "user.email=marv-vm@localhost", "commit", "-q", "-m", message)
    for attempt in range(4):
        if git("push", "-q", "origin", f"HEAD:{BRANCH}", check=False).returncode == 0:
            return
        git("pull", "-q", "--rebase", "origin", BRANCH, check=False)
        time.sleep(2 ** attempt)
    raise RuntimeError("push failed")


def main() -> int:
    git("fetch", "-q", "origin", BRANCH)
    git("reset", "-q", "--hard", f"origin/{BRANCH}")
    (LINK / "results").mkdir(exist_ok=True)
    for job in sorted((LINK / "jobs").glob("*.job")):
        out = LINK / "results" / f"{job.stem}.txt"
        if out.exists():
            continue
        text = job.read_text().splitlines()
        line = next((l.strip() for l in text if l.strip() and not l.startswith("#")), "")
        limit = next((int(l.split()[2]) for l in text if l.startswith("# timeout ") and l.split()[2].isdigit()), JOB_TIMEOUT)
        argv, err = parse(line)
        started = time.strftime("%Y-%m-%d %H:%M:%S %Z")
        if argv is None:
            out.write_text(f"$ {line}\nREFUSED: {err}\n")
            push(f"vm-link: refused {job.stem}")
            continue
        out.write_text(f"$ {line}\nstarted {started}\nRUNNING...\n")
        push(f"vm-link: running {job.stem}")
        t0 = time.time()
        try:
            proc = subprocess.run(argv, cwd=str(BOT), capture_output=True, text=True, timeout=limit)
            body, code = proc.stdout + ("\n--- stderr ---\n" + proc.stderr if proc.stderr.strip() else ""), proc.returncode
        except subprocess.TimeoutExpired as exc:
            body, code = f"{exc.stdout or ''}\nTIMED OUT after {limit}s", "timeout"
        body = redact(body)
        if len(body) > MAX_OUT:
            body = "...(trimmed, last 200 KB)...\n" + body[-MAX_OUT:]
        out.write_text(f"$ {line}\nstarted {started}, took {time.time() - t0:.0f}s, exit {code}\n\n{body}")
        push(f"vm-link: done {job.stem} (exit {code})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
