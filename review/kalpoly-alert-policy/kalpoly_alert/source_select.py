"""Run the exported gate functions. A gate that cannot be loaded is not a pass.

Env switches use the source default when the variable and the OFF file are
absent. That is not the VM EnvironmentFile. league_allow is fail-closed when
the caller does not supply an explicit fixture. The Marv overlay HTTP call is
not made.
"""
from __future__ import annotations

import ast
import importlib.util
import os
import sqlite3
import sys
from pathlib import Path

from .clock import is_weekend

_ROOT = Path(__file__).resolve().parents[1]
GATES = _ROOT / "provenance" / "gate-source" / "source"
ALERTS = _ROOT / "provenance" / "source" / "home-context" / "aj-bet-bot" / "ajbet" / "alerts.py"
DB_PATH = _ROOT / "provenance" / "source" / "home-context" / "aj-bet-bot" / "ajbet" / "db.py"

_STDLIB = set(sys.stdlib_module_names) | {"__future__"}
_LOADABLE = (
    "oddsmath.py",
    "cfb_track.py",
    "nfl_live_rec_track.py",
    "ncaaf_live_track.py",
    "live_prop_ev50_track.py",
    "me_gates.py",
    "nba_props_tg.py",
    "buy_edge.py",
    "h2h_model.py",
    "gamecast.py",
)
# Present in neither the 14-file export nor the 79-file ajbet set.
BLOCKED_MODULES = (
    "ajbet.markets",
    "ajbet.mybook",
    "ajbet.tgfmt",
    "ajbet.espn",
    "ajbet.injuries",
    "ajbet.models",
    "ajbet.config",
)


def module_level_imports(path: Path) -> list[str]:
    tree = ast.parse(path.read_text())
    found = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            found.extend(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append(node.module.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.level:
            found.append("." + ".".join(alias.name for alias in node.names))
    return found


def import_is_local_only(path: Path) -> bool:
    return all(name in _STDLIB or name.startswith(".") for name in module_level_imports(path))


def load_local(filename: str):
    path = GATES / filename
    if not import_is_local_only(path):
        raise ImportError(f"{filename} imports a module this review does not have")
    spec = importlib.util.spec_from_file_location(f"kalpoly_gate_{path.stem}", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _methods(nba_module):
    import types
    pkg = types.ModuleType("ajbet_review")
    pkg.__path__ = []
    sys.modules["ajbet_review"] = pkg
    nba_module.__package__ = "ajbet_review"
    sys.modules["ajbet_review.nba_props_tg"] = nba_module
    tree = ast.parse(ALERTS.read_text())
    wanted = {"_marv_veto", "_double_dip", "_dd_side", "_dd_moved", "_league_gate"}
    body = []
    for node in tree.body:
        if not isinstance(node, ast.ClassDef) or node.name != "Notifier":
            continue
        for item in node.body:
            if isinstance(item, ast.FunctionDef) and item.name in wanted:
                item.decorator_list = []
                body.append(item)
    module = ast.Module(body=body, type_ignores=[])
    ast.fix_missing_locations(module)
    namespace = {"os": os, "json": __import__("json"), "re": __import__("re"),
                 "log": __import__("logging").getLogger("kalpoly_alert.source"),
                 "__package__": "ajbet_review", "__name__": "ajbet_review.alerts"}
    exec(compile(module, str(ALERTS), "exec"), namespace)
    return namespace


class _Self:
    _VETO_EXEMPT = ("props", "team_total_over")
    _marv_verdict = ""
    _dd_note = ""
    _league_allow = None
    _league_label = {}
    _outbox_db = None


def _db():
    spec = importlib.util.spec_from_file_location("kalpoly_alert_db", DB_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module.DB(":memory:")


def decide(row: dict, *, now, dry_run: bool = False, league_allow="ABSENT",
           marv_verdict: str = "", prior_sent: dict | None = None, live: bool = False,
           situation=None) -> dict:
    """Same gate result on Monday and the weekend. Nothing here writes sent=1."""
    blocked = list(BLOCKED_MODULES)
    if live:
        blocked.append("ajbet.live_guard")
    modules = {name: load_local(name) for name in (
        "cfb_track.py", "nfl_live_rec_track.py", "ncaaf_live_track.py",
        "live_prop_ev50_track.py", "me_gates.py", "nba_props_tg.py", "h2h_model.py",
    )}
    db = _db()
    details = row.get("details_json") or "{}"
    db.conn.execute(
        "INSERT INTO alerts(ts, dedupe_key, event_id, league, strategy, market, selection, line, price, "
        "fair_p, edge, message, details_json, sent) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,0)",
        (row.get("ts") or "2026-11-09T18:00:00+00:00", row.get("dedupe_key") or "fixture",
         row.get("event_id") or "ev", row.get("league"), row.get("strategy"), row.get("market"),
         row.get("selection"), row.get("line"), row.get("price"), row.get("fair_p"), row.get("edge"),
         row.get("message") or "", details),
    )
    db.conn.commit()
    alert_id = db.conn.execute("SELECT last_insert_rowid()").fetchone()[0]
    if prior_sent:
        db.conn.execute(
            "INSERT INTO alerts(ts, dedupe_key, event_id, league, strategy, market, selection, line, price, "
            "message, details_json, sent) VALUES (?,?,?,?,?,?,?,?,?,?,?,1)",
            ("2026-11-09T17:00:00+00:00", "prior", prior_sent.get("event_id") or row.get("event_id"),
             prior_sent.get("league") or row.get("league"), prior_sent.get("strategy") or row.get("strategy"),
             prior_sent.get("market") or row.get("market"), prior_sent.get("selection") or row.get("selection"),
             prior_sent.get("line"), prior_sent.get("price"), prior_sent.get("message") or "",
             prior_sent.get("details_json") or "{}"),
        )
        db.conn.commit()
    executed = {}
    holder = _Self()
    holder._outbox_db = db
    holder._marv_verdict = marv_verdict
    methods = _methods(modules["nba_props_tg.py"])
    holder._dd_side = methods["_dd_side"]
    holder._dd_moved = methods["_dd_moved"]
    for key, func in (
        ("cfb_track", modules["cfb_track.py"].blocked_alert),
        ("nfl_live_rec_track", modules["nfl_live_rec_track.py"].blocked_alert),
        ("ncaaf_live_track", modules["ncaaf_live_track.py"].blocked_alert),
        ("live_prop_ev50_track", modules["live_prop_ev50_track.py"].blocked_alert),
        ("me_gates", modules["me_gates.py"].nosend_alert),
    ):
        executed[key] = func(db, alert_id)
        if executed[key]:
            return _out("rejected", executed, blocked, now, reason=key)
    if league_allow == "ABSENT":
        return _out("missing_dependency", executed, blocked + ["telegram.league_allow"], now,
                    reason="league_allow_unknown")
    holder._league_allow = league_allow
    gate = methods["_league_gate"](holder, alert_id)
    executed["league_gate"] = gate
    if gate is False:
        return _out("rejected", executed, blocked, now, reason="league_allow")
    text = modules["h2h_model.py"].alert_append(db, alert_id, row.get("message") or "")
    executed["h2h_model"] = "display_only"
    if methods["_marv_veto"](holder, alert_id, text):
        executed["marv_veto"] = True
        return _out("rejected", executed, blocked, now, reason="vetoed")
    executed["marv_veto"] = False
    if methods["_double_dip"](holder, alert_id):
        executed["double_dip"] = True
        return _out("rejected", executed, blocked, now, reason="double_dip")
    executed["double_dip"] = False
    if dry_run:
        executed["dry_run"] = True
        return _out("rejected", executed, blocked, now, reason="dry_run")
    if situation is not None:
        gamecast = load_local("gamecast.py")
        gamecast.configure(clock=lambda: 1_700_000_000.0)
        action, why = gamecast.hold_check(db, situation["game"], "fixture", side=situation.get("side"),
                                           sit=situation["sit"])
        executed["hold_check"] = action
        if action == "hold":
            return _out("rejected", executed, blocked, now, reason="held", detail=why)
    # send() still calls the Marv overlay over HTTP before the veto can see a live verdict.
    return _out("missing_dependency", executed, blocked + ["marv_overlay_http"], now,
                reason="overlay_not_executed")


def hold_result(game, sit, *, side: str | None = None) -> tuple[str, str]:
    """The real hold_check. Passing sit avoids the ESPN summary fetch."""
    gamecast = load_local("gamecast.py")
    gamecast.configure(clock=lambda: 1_700_000_000.0)
    db = _db()
    return gamecast.hold_check(db, game, "fixture", side=side, sit=sit)


def _out(status, executed, blocked, now, reason, detail=None) -> dict:
    return {
        "status": status,
        "eligible": False,
        "sent": False,
        "reason": reason,
        "detail": detail,
        "weekend": is_weekend(now),
        "executed": executed,
        "blocked_dependencies": blocked,
        "config_source": "explicit_fixture_not_runtime_policy",
    }
