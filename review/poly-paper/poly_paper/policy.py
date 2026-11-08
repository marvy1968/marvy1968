"""Reviewed PAPER authorization precedence.

Observed VM behavior that this keeps when the reviewed policy file is valid:
  * PM_TRADE_ALL defaults on and supersedes the narrow type_gate trade list.
  * trade_all does not beat force_track or the NBA moneyline hold.
  * trade_all still beats pin_track and never_trade, matching pm_type_gate.allowed.
  * pm_fill lanes also require their PM_FILL_<LANE> switch and trade_all.

Divergences, fail-closed on purpose:
  * A missing or malformed policy or type gate authorizes nothing.
  * The reviewed file must keep NCAAB eligible, must not ban NCAAB, must not
    install a Nov 17 switch, and must not release the NBA moneyline hold.
  * NBA moneyline is held from the sport and market as well as the type tag, so
    a me_type label cannot relabel it.
  * force_track is also applied to the sport/market type, so a me_type label
    cannot hide prop:nfl:pre.
  * Identity is not decided here. Callers must still reject an allow when
    identity is not unique. trade_all cannot skip that step.
"""
from __future__ import annotations

import json
from pathlib import Path

_MK = {
    "h2h": "ml", "ml": "ml", "moneyline": "ml", "total": "total", "spread": "spread", "ats": "spread",
    "team_total": "team_total", "tt": "team_total", "prop": "prop",
}
_SPORTS = {"nfl", "cfb", "nba", "nhl", "mlb", "wnba", "soccer", "ncaab", "ncaaw", "cbb", "euroleague", "mls", "epl"}
_OFF = {"off", "0", "false", "no"}
_ML_MARKETS = {"h2h", "ml", "moneyline"}

_REQUIRED = {
    "policy_id",
    "paper_only",
    "preprod_enabled",
    "live_orders",
    "trade_all_default",
    "identity_required",
    "trade_all_cannot_bypass",
    "ncaab_paper_eligible",
    "ncaab_track_only_ban",
    "nov17_auto_switch",
    "nba_ml_hold",
}
_CANNOT_BYPASS = {"identity", "force_track", "nba_hold", "unknown_or_malformed_policy", "hard_kill"}
_GATE_KEYS = {"enabled", "trade", "never_trade", "pin_track", "force_track"}


def classify(key=None, me_type=None, market=None, sport=None):
    """Same tag rules as baseline pm_type_gate.classify."""
    if me_type and me_type not in ("marv",):
        return str(me_type)
    k = str(key or "").lower()
    parts = k.split(":")
    sp = str(sport or "").lower() or None
    if k.startswith(("pmprop:", "prop:")) or str(market or "").lower() == "prop":
        return f"prop:{sp or 'nfl'}"
    if k.startswith("live:"):
        return "legacy_prop"
    if k.startswith("master:") and len(parts) >= 3:
        return f"master_{_MK.get(parts[1], parts[1])}:{parts[2]}"
    if parts and parts[0] in _MK and len(parts) >= 2:
        return f"{_MK[parts[0]]}:{parts[1]}"
    if parts and parts[0] in _SPORTS and len(parts) >= 3 and parts[2] in _MK:
        return f"{_MK[parts[2]]}:{parts[0]}"
    mk = _MK.get(str(market or "").lower(), str(market or "other").lower())
    return f"{mk}:{sp or 'unknown'}"


def _string_list(value, name, errors):
    if not isinstance(value, list) or not all(isinstance(item, str) for item in value):
        errors.append(f"{name}_malformed")
        return []
    return value


def load_json(path: Path):
    try:
        return json.loads(path.read_text()), None
    except FileNotFoundError:
        return None, "missing"
    except (OSError, json.JSONDecodeError, UnicodeError):
        return None, "malformed"


def validate_effective(document):
    errors = []
    if not isinstance(document, dict):
        return ["policy_malformed"]
    extra = set(document) - _REQUIRED
    missing = _REQUIRED - set(document)
    if extra or missing:
        errors.append("policy_malformed")
        return errors
    if not isinstance(document.get("policy_id"), str) or not document["policy_id"].strip():
        errors.append("policy_malformed")
    if document.get("paper_only") is not True:
        errors.append("paper_mode_violation")
    if document.get("preprod_enabled") is not False or document.get("live_orders") is not False:
        errors.append("paper_mode_violation")
    if document.get("trade_all_default") not in ("on", "off"):
        errors.append("policy_malformed")
    if document.get("identity_required") is not True:
        errors.append("unapproved_policy_conflict")
    bypass = document.get("trade_all_cannot_bypass")
    if not isinstance(bypass, list) or set(bypass) != _CANNOT_BYPASS:
        errors.append("unapproved_policy_conflict")
    if document.get("ncaab_paper_eligible") is not True or document.get("ncaab_track_only_ban") is not False:
        errors.append("unapproved_policy_conflict")
    if document.get("nov17_auto_switch") is not False:
        errors.append("unapproved_policy_conflict")
    if document.get("nba_ml_hold") is not True:
        errors.append("unapproved_policy_conflict")
    return errors


def validate_type_gate(document):
    if not isinstance(document, dict):
        return ["policy_malformed"]
    extra = {key for key in document if not str(key).startswith("_")} - _GATE_KEYS
    if extra or not _GATE_KEYS <= set(document):
        return ["policy_malformed"]
    errors = []
    if not isinstance(document.get("enabled"), bool):
        errors.append("policy_malformed")
    for name in ("trade", "never_trade", "pin_track", "force_track"):
        _string_list(document.get(name), name, errors)
    return errors


def load_policy_dir(policy_dir):
    policy_dir = Path(policy_dir)
    effective, eff_err = load_json(policy_dir / "effective_policy.json")
    gate, gate_err = load_json(policy_dir / "type_gate.json")
    errors = []
    if eff_err == "missing" or gate_err == "missing":
        errors.append("policy_missing")
    if eff_err == "malformed" or gate_err == "malformed":
        errors.append("policy_malformed")
    if effective is not None:
        errors.extend(validate_effective(effective))
    if gate is not None:
        errors.extend(validate_type_gate(gate))
    # De-dupe while keeping order.
    ordered = []
    for error in errors:
        if error not in ordered:
            ordered.append(error)
    if ordered:
        return None, None, ordered
    return effective, gate, []


def _flag_on(env, name, default_on: bool) -> bool:
    if name not in env:
        return default_on
    return str(env.get(name)).lower() not in _OFF


def _switch_on(env, switch_dir: Path, name: str, default_on: bool) -> bool:
    if (switch_dir / f"{name}_OFF").exists():
        return False
    return _flag_on(env, name, default_on)


def force_track_match(type_tag, live, gate) -> bool:
    listed = set(gate.get("force_track", []))
    if type_tag in listed:
        return True
    if live is not None and f"{type_tag}:{'live' if live else 'pre'}" in listed:
        return True
    return False


def nba_moneyline(type_tag, entry) -> bool:
    if str(type_tag or "").lower().startswith("ml:nba"):
        return True
    sport = str((entry or {}).get("sport") or "").lower()
    market = str((entry or {}).get("market") or "").lower()
    return sport == "nba" and market in _ML_MARKETS


def evaluate(entry, *, writer, lane_switch, policy_dir, switch_dir, env, live_order=False):
    """Return action allow|track_reject|fail_closed and reason codes. No identity decision."""
    env = {} if env is None else env
    if live_order:
        return "fail_closed", ["live_order_refused"]
    if switch_dir is None:
        return "fail_closed", ["switch_dir_missing"]
    switch_dir = Path(switch_dir)
    if not switch_dir.is_dir():
        return "fail_closed", ["switch_dir_missing"]
    effective, gate, errors = load_policy_dir(policy_dir)
    if errors:
        return "fail_closed", errors
    if (switch_dir / "KILL").exists():
        return "fail_closed", ["hard_kill"]
    trade_all = _switch_on(env, switch_dir, "PM_TRADE_ALL", effective["trade_all_default"] == "on")
    force_on = _switch_on(env, switch_dir, "PM_FORCE_TRACK", True)
    pin_on = _switch_on(env, switch_dir, "PIN_TRACK", True)
    # Baseline pin file is PIN_TRACK_OFF in the repo dir, and env PIN_TRACK=off.
    # _switch_on already maps PIN_TRACK_OFF and PIN_TRACK=off. Default is on, matching
    # the baseline where the env must be explicitly off (empty env does not disable).
    gate_active = _switch_on(env, switch_dir, "TYPE_GATE", True) and bool(gate.get("enabled"))
    # TYPE_GATE default in baseline is "not off", and enabled comes from the file.
    # _switch_on default True plus enabled matches active() except the baseline env
    # check is `TYPE_GATE` in off-list, defaulting to active when unset. Good.
    classified = classify(entry.get("key"), entry.get("me_type"), entry.get("market"), entry.get("sport"))
    derived = classify(entry.get("key"), None, entry.get("market"), entry.get("sport"))
    live = entry.get("live")
    if live is not None and not isinstance(live, bool):
        return "fail_closed", ["request_malformed"]
    reasons = []
    if force_on and (
        force_track_match(classified, live, gate) or force_track_match(derived, live, gate)
    ):
        reasons.append("force_track")
    if effective["nba_ml_hold"] and (nba_moneyline(classified, entry) or nba_moneyline(derived, entry)):
        reasons.append("nba_hold")
    if writer == "pm_fill":
        if not lane_switch or not isinstance(lane_switch, str):
            return "fail_closed", ["lane_switch_missing"]
        lane_on = _switch_on(env, switch_dir, "PM_FILL_" + lane_switch, True)
        if not trade_all or not lane_on:
            reasons.append("lane_off" if not lane_on else "trade_all_off")
    if reasons:
        return "track_reject", reasons
    if trade_all:
        return "allow", ["trade_all"]
    if classified in set(gate.get("never_trade", [])):
        return "track_reject", ["never_trade"]
    if pin_on and classified in set(gate.get("pin_track", [])):
        return "track_reject", ["pin_track"]
    if not gate_active:
        return "allow", ["type_gate_off"]
    if classified in set(gate.get("trade", [])):
        return "allow", ["allowlist"]
    return "track_reject", ["not_on_allowlist"]
