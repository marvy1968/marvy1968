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
           "check", "report", "sports", "probe-ewl", "test-telegram", "get-chat-id", "gaps"}
MARV_FLAGS_REFUSED = {"--send", "--watch"}
UNITS = {"marv-bot", "marv-bot-late", "marv-bridge", "marv-live", "marv-edges", "marv-vmlink"}
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
    if cmd == "update":  # always the latest installer from GitHub, so a broken local copy can't block updates
        url = "https://raw.githubusercontent.com/marvy1968/marvy1968/claude/analysis-ak180w/deploy/install.sh"
        return ["bash", "-c", f"curl -fsSL {url} | bash"], ""
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
    return None, f"unknown job '{cmd}' (tests, update, status, logs, show, marv)"


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
