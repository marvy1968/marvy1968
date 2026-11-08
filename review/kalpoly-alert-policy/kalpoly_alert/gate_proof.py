"""Gate proof for Notifier.send.

A caller boolean is not proof. A file that is not in the immutable VM export is
not proof either, and this module does not import it. Missing proof is not a
rejection and is not eligibility.
"""
from __future__ import annotations

import json
from pathlib import Path

AJ = "/home/moonrider1968/aj-bet-bot/ajbet"

# Bounded to send() and the returns that skip send() at the two call sites.
# Not the odds, ESPN, staking, or model stack.
_GATES = (
    ("oddsmath.py", "ajbet.oddsmath", "alerts.py", 14, "from .oddsmath import fmt_american", "import_time"),
    ("strategies.py", "ajbet.strategies", "alerts.py", 15, "from .strategies import Signal", "import_time"),
    ("cfb_track.py", "ajbet.cfb_track", "alerts.py", 170, "_cft.blocked_alert", "send_gate_source_swallows_import_error"),
    ("nfl_live_rec_track.py", "ajbet.nfl_live_rec_track", "alerts.py", 178, "_nlr.blocked_alert", "send_gate_source_swallows_import_error"),
    ("ncaaf_live_track.py", "ajbet.ncaaf_live_track", "alerts.py", 185, "_nlt.blocked_alert", "send_gate_source_swallows_import_error"),
    ("live_prop_ev50_track.py", "ajbet.live_prop_ev50_track", "alerts.py", 193, "_lpe.blocked_alert", "send_gate_source_swallows_import_error"),
    ("me_gates.py", "ajbet.me_gates", "alerts.py", 200, "_mg.nosend_alert", "send_gate_source_swallows_import_error"),
    ("nba_props_tg.py", "ajbet.nba_props_tg", "alerts.py", 273, "nba_props_tg.enabled", "send_gate"),
    ("nba_props_tg.py", "ajbet.nba_props_tg", "alerts.py", 277, "nba_props_tg.gamelines_blocked", "send_gate"),
    ("enrich.py", "ajbet.enrich", "alerts.py", 213, "_enrich.enrich", "send_text_source_swallows_import_error"),
    ("h2h_model.py", "ajbet.h2h_model", "alerts.py", 218, "_h2h.alert_append", "send_text_source_swallows_import_error"),
    ("buy_edge.py", "ajbet.buy_edge", "alerts.py", 223, "_be.append", "send_text_source_swallows_import_error"),
    ("friendly.py", "ajbet.friendly", "alerts.py", 237, "from .friendly import plain", "dry_run_and_post"),
    ("friendly.py", "ajbet.friendly", "alerts.py", 568, "from .friendly import tg_safe", "post"),
    ("live_guard.py", "ajbet.live_guard", "scanner.py", 572, "live_guard.stale_check", "returns_before_send"),
    ("live_guard.py", "ajbet.live_guard", "scanner.py", 583, "live_guard.check", "returns_before_send"),
    ("gamecast.py", "ajbet.gamecast", "scanner.py", 614, "gamecast.hold_check", "returns_before_send"),
    ("gamecast.py", "ajbet.gamecast", "value_scan.py", 1543, "gamecast.hold_check", "returns_before_send"),
)

_PROVENANCE = {
    "alerts.py": "source/home-context/aj-bet-bot/ajbet/alerts.py",
    "scanner.py": "source/home-context/aj-bet-bot/ajbet/scanner.py",
    "value_scan.py": "source/home-context/aj-bet-bot/ajbet/value_scan.py",
}


def required_gates() -> list[dict]:
    rows = []
    for filename, module, caller, line, call, effect in _GATES:
        rows.append({
            "absolute_path": f"{AJ}/{filename}",
            "module": module,
            "needed_by_file": f"{AJ}/{caller}",
            "needed_by_line": line,
            "call": call,
            "effect": effect,
        })
    return rows


def export_request() -> dict:
    return {
        "kind": "READ_ONLY_SOURCE_EXPORT_REQUEST",
        "vm_access": False,
        "include": "nonsecret Python source only",
        "do_not_include": ["configs", "env", "credentials", "databases", "raw logs", "recipient ids"],
        "reason": "Notifier.send cannot be executed. Import errors inside send() are swallowed by the exported source, so a missing gate is not a recorded rejection and must not become feed eligibility.",
        "files": required_gates(),
    }


def line_text(provenance: Path, caller: str, line: int) -> str:
    path = provenance / _PROVENANCE[caller]
    return path.read_text().splitlines()[line - 1]


def prove_gates(directory: Path | None) -> dict:
    """Do not import files in `directory`. Unhashed bytes are not VM proof."""
    absent = []
    present_untrusted = []
    for row in required_gates():
        name = Path(row["absolute_path"]).name
        if directory is not None and (directory / name).is_file():
            present_untrusted.append(row["module"])
        else:
            absent.append(row["module"])
    return {
        "executed": False,
        "eligible": False,
        "status": "missing_dependency",
        "absent_modules": sorted(set(absent)),
        "present_untrusted_modules": sorted(set(present_untrusted)),
    }


def write_request(path: Path) -> None:
    path.write_text(json.dumps(export_request(), indent=2) + "\n")
