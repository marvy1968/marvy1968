#!/usr/bin/env python3
from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
from pathlib import Path
from typing import Any

BASE_DIR = Path("/home/moonrider1968/bots/oanda_fifo_practice/MarchEdge")
LOG_DIR = BASE_DIR / "logs"
SERVICE_NAME = "marchedge"


def _run(cmd: str, timeout: int = 15) -> str:
    try:
        out = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True,
            timeout=timeout,
            cwd=str(BASE_DIR),
        )
        txt = (out.stdout or "").strip()
        err = (out.stderr or "").strip()
        return txt if txt else err
    except Exception as e:
        return f"ERROR: {type(e).__name__}: {e}"


def _read_jsonl(path: Path, limit: int = 10) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    try:
        with path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rows.append(json.loads(line))
                except Exception:
                    continue
    except Exception:
        return []
    return rows[-limit:]


def cmd_start() -> str:
    return (
        "MarchEdge Telegram online ✅\n\n"
        "Commands:\n"
        "/status\n"
        "/top\n"
        "/props\n"
        "/middles\n"
        "/bankroll\n"
        "/today\n"
        "/logs\n"
        "/service\n"
        "/restart"
    )


def cmd_status() -> str:
    svc = _run(f"systemctl is-active {SERVICE_NAME}")
    mem = _run("free -h | sed -n '2p'")
    disk = _run(f"df -h {shlex.quote(str(BASE_DIR))} | tail -n 1")
    poll = _run(f"journalctl -u {SERVICE_NAME} -n 80 --no-pager | grep 'POLL SUMMARY' | tail -n 1")
    return (
        f"⚙️ STATUS\n\n"
        f"Service: {svc}\n"
        f"Poll: {poll if poll else 'no recent poll summary'}\n\n"
        f"Memory:\n{mem}\n\n"
        f"Disk:\n{disk}"
    )


def cmd_service() -> str:
    txt = _run(f"systemctl status {SERVICE_NAME} --no-pager -l | tail -n 20")
    return f"🛠 SERVICE\n\n{txt[:3500]}"


def cmd_restart() -> str:
    out = _run(f"sudo systemctl restart {SERVICE_NAME} && systemctl is-active {SERVICE_NAME}", timeout=30)
    return f"♻️ RESTART\n\n{out}"


def _extract_live_alerts_from_journal(limit: int = 200) -> list[str]:
    txt = _run(f"journalctl -u {SERVICE_NAME} -n {limit} --no-pager")
    lines = []
    for line in txt.splitlines():
        if re.search(r"\[[AB]\]\s", line):
            # remove journal prefix up to python[pid]:
            line = re.sub(r"^.*python\[\d+\]:\s*", "", line).strip()
            lines.append(line)
    # dedupe preserve order
    seen = set()
    out = []
    for x in lines:
        if x not in seen:
            seen.add(x)
            out.append(x)
    return out[-5:]


def cmd_top() -> str:
    alerts = _extract_live_alerts_from_journal()
    if not alerts:
        return "🔥 TOP PLAYS\n\nNo recent A/B live alerts found."
    return "🔥 TOP PLAYS\n\n" + "\n".join(f"- {x}" for x in alerts)


def cmd_props() -> str:
    rows = _read_jsonl(LOG_DIR / "team_props_bets.jsonl", limit=5)
    if not rows:
        return "📊 TEAM PROPS\n\nNo recent team prop alerts logged."
    parts = []
    for r in rows[::-1]:
        parts.append(
            f"- {r.get('team')} TT {str(r.get('side', '')).upper()} {r.get('line')} "
            f"| edge={round(float(r.get('edge', 0))*100,2)}% "
            f"| conf={r.get('confidence')}% "
            f"| kelly={round(float(r.get('kelly_quarter', 0))*100,2)}%"
        )
    return "📊 TEAM PROPS\n\n" + "\n".join(parts[:5])


def cmd_middles() -> str:
    txt = _run(f"journalctl -u {SERVICE_NAME} -n 200 --no-pager | grep 'MIDDLE' | tail -n 8")
    if not txt:
        return "🎯 MIDDLES\n\nNo recent middle alerts."
    cleaned = []
    for line in txt.splitlines():
        line = re.sub(r"^.*python\[\d+\]:\s*", "", line).strip()
        cleaned.append(line)
    return "🎯 MIDDLES\n\n" + "\n".join(cleaned[:8])


def cmd_bankroll() -> str:
    main_rows = _read_jsonl(LOG_DIR / "marchedge_results.jsonl", limit=5000)
    prop_rows = _read_jsonl(LOG_DIR / "team_props_results.jsonl", limit=5000)

    units_profit = 0.0
    units_risked = 0.0

    for r in main_rows + prop_rows:
        units_profit += float(r.get("units_profit", 0.0) or 0.0)
        units_risked += float(r.get("units_risked", 0.0) or 0.0)

    roi = (units_profit / units_risked) if units_risked > 0 else 0.0
    return (
        "💰 BANKROLL\n\n"
        f"Units risked: {round(units_risked, 4)}\n"
        f"Units profit: {round(units_profit, 4)}\n"
        f"ROI: {round(roi*100, 2)}%"
    )


def cmd_today() -> str:
    main_rows = _read_jsonl(LOG_DIR / "marchedge_results.jsonl", limit=200)
    prop_rows = _read_jsonl(LOG_DIR / "team_props_results.jsonl", limit=200)
    rows = main_rows + prop_rows
    settled = [r for r in rows if r.get("win") is not None]

    wins = sum(1 for r in settled if r.get("win") is True)
    losses = sum(1 for r in settled if r.get("win") is False)
    units_profit = sum(float(r.get("units_profit", 0.0) or 0.0) for r in settled)
    units_risked = sum(float(r.get("units_risked", 0.0) or 0.0) for r in settled)
    roi = (units_profit / units_risked) if units_risked > 0 else 0.0

    return (
        "📈 TODAY\n\n"
        f"Settled bets: {len(settled)}\n"
        f"Wins: {wins}\n"
        f"Losses: {losses}\n"
        f"Units profit: {round(units_profit, 4)}\n"
        f"ROI: {round(roi*100, 2)}%"
    )


def cmd_logs() -> str:
    txt = _run(f"journalctl -u {SERVICE_NAME} -n 30 --no-pager")
    return f"📜 LOGS\n\n{txt[:3500]}"


def handle_command(text: str) -> str:
    cmd = (text or "").strip().lower()

    if cmd in {"/start", "start"}:
        return cmd_start()
    if cmd in {"/status", "status"}:
        return cmd_status()
    if cmd in {"/top", "top"}:
        return cmd_top()
    if cmd in {"/props", "props"}:
        return cmd_props()
    if cmd in {"/middles", "middles"}:
        return cmd_middles()
    if cmd in {"/bankroll", "bankroll"}:
        return cmd_bankroll()
    if cmd in {"/today", "today"}:
        return cmd_today()
    if cmd in {"/logs", "logs"}:
        return cmd_logs()
    if cmd in {"/service", "service"}:
        return cmd_service()
    if cmd in {"/restart", "restart"}:
        return cmd_restart()

    return (
        "Unknown command.\n\n"
        "Try:\n"
        "/status\n/top\n/props\n/middles\n/bankroll\n/today\n/logs\n/service\n/restart"
    )


if __name__ == "__main__":
    print(handle_command("/status"))
