"""One-shot shared collector for Muse and Claude Prop 1.

One Odds API budget, one ESPN read, one marv.prop_snapshot.v1 file. Both
replays receive that same path. Provider ids are stored as aliases only after
team names and start time match. They are not the match.

No Telegram, no orders, no PropLine, no NCAAW. The process refuses unless
config mode is log_only, telegram_enabled is false, and LIVE_PROPS_SEND is off.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import urlencode

sys.path.insert(0, str(Path(__file__).parent))

import yaml

from cohort import cohort_key, team_key, utc_stamp
from compare_shadow import compare, load_shadow, write_report
from credits import (
    EVENTS_LIST_CREDITS,
    DEFAULT_DAILY_CAP,
    CreditError,
    Ledger,
    event_odds_worst_case,
)
from grade_results import FINAL_SCHEMA, load_final, settlements_for_observation, write_settlement
from live_feed import _clock_to_elapsed
from prop_snapshot import SCHEMA_IN, ReplayError, assert_replay_state, hash_payload, input_hash, load_snapshot, replay_file

ODDS_BASE = "https://api.the-odds-api.com/v4"
ESPN_BASE = "https://site.api.espn.com/apis/site/v2/sports"
PAIR_SKEW_SECONDS = 120
FORBIDDEN = ("install.sh", "vm_link.py", "vm-link.sh", "vm_link")
# Public keys both engines already model. Not PropLine. Not NCAAW.
COVERED = {
    "nfl": {
        "odds_sport": "americanfootball_nfl",
        "espn_path": "football/nfl",
        "markets": ["player_pass_yds", "player_rush_yds", "player_reception_yds", "player_receptions"],
    },
    "nba": {
        "odds_sport": "basketball_nba",
        "espn_path": "basketball/nba",
        "markets": ["player_points", "player_rebounds", "player_assists", "player_threes"],
    },
}


class CollectorError(RuntimeError):
    pass


def assert_tracking_config(cfg: dict, env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    if cfg.get("mode") != "log_only":
        raise CollectorError("config mode must be log_only")
    if cfg.get("telegram_enabled") is not False:
        raise CollectorError("config telegram_enabled must be false")
    if str(cfg.get("live_props_send", "")).strip().lower() != "off":
        raise CollectorError("config live_props_send must be the explicit string off")
    raw = env.get("LIVE_PROPS_SEND")
    if raw is None or raw.strip().lower() not in {"0", "off", "false", "no"}:
        raise CollectorError("LIVE_PROPS_SEND must be explicitly off")
    block = cfg.get("collector") or {}
    sports = list(block.get("sports") or [])
    if not sports or any(sport not in COVERED for sport in sports):
        raise CollectorError("collector sports must be nfl and/or nba only")
    regions = int(block.get("regions", 1))
    if regions != 1:
        raise CollectorError("this collector reserves 1 region; a different region count is refused")
    cap = int(block.get("daily_credit_cap", DEFAULT_DAILY_CAP))
    if cap != DEFAULT_DAILY_CAP and cap > DEFAULT_DAILY_CAP:
        raise CollectorError(f"daily credit cap cannot exceed the shared hard cap of {DEFAULT_DAILY_CAP}")
    return block


def assert_claude_invocation(code_dir: str, python: str) -> tuple[Path, Path]:
    if not code_dir or not python:
        raise CollectorError("claude_code_dir and claude_python must be set; refusing to spend credits")
    code = Path(code_dir)
    py = Path(python)
    blob = f"{code} {py}"
    if any(token in blob for token in FORBIDDEN):
        raise CollectorError("refusing updater or install script")
    if not code.is_dir() or not py.is_file():
        raise CollectorError("claude code directory or python executable is missing")
    return code, py


def _stamp(value, what: str) -> str:
    return utc_stamp(value, what)


def _when(value: str) -> datetime:
    return datetime.fromisoformat(_stamp(value, "timestamp").replace("Z", "+00:00"))


def before_start(observed_at: str, scheduled_start: str) -> bool:
    return _when(observed_at) < _when(scheduled_start)


def espn_games(scoreboard: dict, sport: str) -> list[dict]:
    games = []
    for event in scoreboard.get("events") or []:
        comp = (event.get("competitions") or [{}])[0]
        status = comp.get("status") or {}
        state = ((status.get("type") or {}).get("state") or "")
        home = away = None
        home_score = away_score = None
        for side in comp.get("competitors") or []:
            name = ((side.get("team") or {}).get("displayName") or "").strip()
            if side.get("homeAway") == "home":
                home, home_score = name, side.get("score")
            elif side.get("homeAway") == "away":
                away, away_score = name, side.get("score")
        games.append({
            "sport": sport,
            "espn_event_id": str(event.get("id") or ""),
            "home": home,
            "away": away,
            "scheduled_start": event.get("date"),
            "state": state,
            "status": status,
            "home_score": home_score,
            "away_score": away_score,
        })
    return games


def odds_games(events: list) -> list[dict]:
    return [{
        "odds_event_id": str(event.get("id") or ""),
        "home": event.get("home_team"),
        "away": event.get("away_team"),
        "scheduled_start": event.get("commence_time"),
    } for event in events or []]


def pair_games(espn: list[dict], odds: list[dict], sport: str, skew_seconds: int = PAIR_SKEW_SECONDS) -> list[dict]:
    """Pair on normalized teams and start. Provider ids cannot force a pair."""
    used = set()
    paired = []
    for game in espn:
        try:
            left = _when(game.get("scheduled_start"))
        except Exception:
            paired.append({**game, "ok": False, "reason": "missing_start"})
            continue
        if not team_key(game.get("home") or "") or not team_key(game.get("away") or ""):
            paired.append({**game, "ok": False, "reason": "missing_teams"})
            continue
        hits = []
        for index, event in enumerate(odds):
            if index in used:
                continue
            try:
                right = _when(event.get("scheduled_start"))
            except Exception:
                continue
            if team_key(event.get("home") or "") != team_key(game.get("home") or ""):
                continue
            if team_key(event.get("away") or "") != team_key(game.get("away") or ""):
                continue
            if abs((left - right).total_seconds()) > skew_seconds:
                continue
            hits.append(index)
        if len(hits) != 1:
            paired.append({**game, "ok": False, "reason": "ambiguous_game" if len(hits) > 1 else "unmatched_names"})
            continue
        used.add(hits[0])
        event = odds[hits[0]]
        start = _stamp(event["scheduled_start"], "scheduled_start")
        paired.append({
            **game,
            "ok": True,
            "reason": "",
            "odds_event_id": event["odds_event_id"],
            "scheduled_start": start,
            "cohort_key": cohort_key(sport, game["away"], game["home"], start),
        })
    return paired


def measured_elapsed(sport: str, status: dict) -> float | None:
    period = int(status.get("period") or 0)
    clock = str(status.get("displayClock") or "")
    if period < 1 or re.match(r"^\d+:\d+$", clock) is None:
        return None
    elapsed, _remaining = _clock_to_elapsed(sport, period, clock)
    return float(elapsed)


def period_label(status: dict) -> str | None:
    period = int(status.get("period") or 0)
    clock = str(status.get("displayClock") or "")
    if period < 1 or not clock:
        return None
    return f"Q{period} {clock}"


def quoted_pregame_anchors(raw_odds: dict, markets: list[str]) -> list[dict]:
    """Lines from a response already known to be from before scheduled start."""
    from prop_snapshot import _raw_groups
    rows = []
    for group in _raw_groups(raw_odds):
        if group.get("market") not in markets:
            continue
        if group.get("over") is None or group.get("under") is None:
            continue
        rows.append({
            "player": group["player"],
            "market": group["market"],
            "value": float(group["line"]),
            "kind": "quoted_pregame",
        })
    rows.sort(key=lambda row: (row["player"], row["market"], row["kind"]))
    return rows


def _atomic(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(text)
    os.replace(tmp, path)


def _append_jsonl(path: Path, row: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as fh:
        fh.write(json.dumps(row, sort_keys=True) + "\n")


def _load_json(path: Path, default):
    if not path.exists():
        return default
    return json.loads(path.read_text())


def _safe_odds_error(status: int) -> str:
    if status == 401:
        return "odds auth rejected"
    if status == 429:
        return "odds quota rejected"
    return f"odds http {status}"


def _odds_request(ledger: Ledger, transport, api_key: str, path: str, params: dict,
                  kind: str, worst: int, detail: str):
    """Reserve, call once, reconcile. The key is not put in the exception text."""
    reservation = ledger.reserve(kind, worst, detail)
    query = dict(params)
    query["apiKey"] = api_key
    url = ODDS_BASE + path + "?" + urlencode(query)
    try:
        status, headers, body = transport(url)
    except Exception as exc:
        ledger.reconcile(reservation, None, error="odds transport failed")
        raise CollectorError("odds transport failed") from exc
    if status != 200:
        ledger.reconcile(reservation, headers, error=_safe_odds_error(status))
        raise CollectorError(_safe_odds_error(status))
    state = ledger.reconcile(reservation, headers)
    if state.get("fail_closed"):
        raise CollectorError(state.get("fail_reason") or "credit ledger fail-closed")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectorError("odds response was not json") from exc


def _public_get(transport, url: str):
    try:
        status, _headers, body = transport(url)
    except Exception as exc:
        raise CollectorError("public feed failed") from exc
    if status != 200:
        raise CollectorError(f"public feed http {status}")
    try:
        return json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CollectorError("public feed was not json") from exc


def _replay_env(base: dict) -> dict:
    skipped = {"ODDS_API_KEY", "PROPLINE_API_KEY", "TELEGRAM_BOT_TOKEN", "TELEGRAM_CHAT_ID"}
    env = {key: value for key, value in base.items() if key not in skipped}
    env["LIVE_PROPS_SEND"] = "off"
    return env


def invoke_claude(code_dir: Path, python: Path, snapshot: Path, state_dir: Path, runner, env: dict) -> None:
    cmd = [str(python), "-m", "marv", "live-props", "replay", "--snapshot", str(snapshot), "--state-dir", str(state_dir)]
    if any(token in " ".join(cmd) for token in FORBIDDEN):
        raise CollectorError("refusing updater or install script")
    code = runner(cmd, cwd=str(code_dir), env=_replay_env(env))
    if code != 0:
        raise CollectorError(f"claude replay exited {code}")


def _select(pairs: list[dict], max_games: int) -> list[dict]:
    live = [row for row in pairs if row.get("ok") and row.get("state") == "in"]
    pre = [row for row in pairs if row.get("ok") and row.get("state") == "pre"]
    chosen = []
    for group in (pre, live):
        for row in group:
            if len(chosen) >= max_games:
                return chosen
            chosen.append(row)
    return chosen


def collect_once(cfg: dict, state_dir: Path, archive: Path, transport, runner,
                 now: datetime | None = None, env: dict | None = None) -> dict:
    env = os.environ if env is None else env
    block = assert_tracking_config(cfg, env)
    api_key = env.get("ODDS_API_KEY", "")
    if not str(api_key).strip():
        raise CollectorError("ODDS_API_KEY is not set")
    code_dir, python = assert_claude_invocation(block.get("claude_code_dir") or "", block.get("claude_python") or "")
    state_dir = assert_replay_state(state_dir)
    archive = assert_replay_state(archive)
    state_dir.mkdir(parents=True, exist_ok=True)
    archive.mkdir(parents=True, exist_ok=True)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise CollectorError("clock must be timezone-aware")
    observed_at = now.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    regions = 1
    cap = int(block.get("daily_credit_cap", DEFAULT_DAILY_CAP))
    max_games = int(block.get("max_games_per_run", 4))
    max_calls = int(block.get("max_billable_calls_per_run", 4))
    if max_games < 1 or max_calls < 1:
        raise CollectorError("max games and max billable calls must be positive")
    lock_fh = (state_dir / "collector.lock").open("a+")
    try:
        import fcntl
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError as exc:
        raise CollectorError("collector already running") from exc
    except ImportError as exc:
        raise CollectorError("file locking is unavailable") from exc
    try:
        try:
            return _collect_locked(
                cfg, block, state_dir, archive, transport, runner, now, observed_at,
                api_key, code_dir, python, env, regions, cap, max_games, max_calls)
        except CreditError as exc:
            raise CollectorError(str(exc)) from exc
    finally:
        import fcntl
        fcntl.flock(lock_fh.fileno(), fcntl.LOCK_UN)
        lock_fh.close()


def _collect_locked(cfg, block, state_dir, archive, transport, runner, now, observed_at,
                    api_key, code_dir, python, env, regions, cap, max_games, max_calls) -> dict:
    ledger = Ledger(state_dir / "usage.json", cap=cap, now=now)
    unfinished = ledger.fail_unfinished()
    if unfinished.get("fail_closed"):
        ledger.close()
        raise CreditError(unfinished.get("fail_reason") or "credit ledger is fail-closed")
    anchors = _load_json(state_dir / "anchors.json", {})
    captured = _load_json(state_dir / "captured.json", {})
    abstain_path = state_dir / "abstentions.jsonl"
    observations = []
    calls = 0
    stop = False
    try:
        for sport in block["sports"]:
            if stop:
                break
            spec = COVERED[sport]
            board = _public_get(transport, f"{ESPN_BASE}/{spec['espn_path']}/scoreboard")
            events = _odds_request(
                ledger, transport, api_key, f"/sports/{spec['odds_sport']}/events", {},
                "events", EVENTS_LIST_CREDITS, sport)
            if not isinstance(events, list):
                raise CollectorError("odds events response was not a list")
            pairs = pair_games(espn_games(board, sport), odds_games(events), sport)
            for skipped in pairs:
                if not skipped.get("ok"):
                    _append_jsonl(abstain_path, {
                        "at": observed_at, "sport": sport, "reason": skipped.get("reason"),
                        "home": skipped.get("home"), "away": skipped.get("away"),
                    })
            for game in _select(pairs, max_games):
                if calls >= max_calls or stop:
                    _append_jsonl(abstain_path, {"at": observed_at, "reason": "max_billable_calls", "cohort_key": game["cohort_key"]})
                    stop = True
                    break
                worst = event_odds_worst_case(len(spec["markets"]), regions)
                pregame = game["state"] == "pre" and before_start(observed_at, game["scheduled_start"])
                if pregame and game["cohort_key"] in anchors:
                    continue
                if not pregame and game["state"] != "in":
                    _append_jsonl(abstain_path, {"at": observed_at, "reason": "not_pre_or_live", "cohort_key": game["cohort_key"]})
                    continue
                if not pregame:
                    label = period_label(game["status"])
                    elapsed = measured_elapsed(sport, game["status"])
                    if label is None or elapsed is None:
                        _append_jsonl(abstain_path, {"at": observed_at, "reason": "missing_elapsed", "cohort_key": game["cohort_key"]})
                        continue
                    try:
                        summary = _public_get(
                            transport, f"{ESPN_BASE}/{spec['espn_path']}/summary?event={game['espn_event_id']}")
                    except CollectorError:
                        _append_jsonl(abstain_path, {"at": observed_at, "reason": "missing_boxscore", "cohort_key": game["cohort_key"]})
                        continue
                    box = summary.get("boxscore") if isinstance(summary, dict) else None
                    if not isinstance(box, dict) or not box:
                        _append_jsonl(abstain_path, {"at": observed_at, "reason": "missing_boxscore", "cohort_key": game["cohort_key"]})
                        continue
                slot = game["cohort_key"] + "|" + ("pregame" if pregame else label)
                if captured.get(slot):
                    continue
                captured[slot] = "reserved"
                _atomic(state_dir / "captured.json", json.dumps(captured, sort_keys=True))
                try:
                    raw_odds = _odds_request(
                        ledger, transport, api_key,
                        f"/sports/{spec['odds_sport']}/events/{game['odds_event_id']}/odds",
                        {"markets": ",".join(spec["markets"]), "bookmakers": "bovada", "oddsFormat": "american"},
                        "event_odds", worst, game["cohort_key"])
                except CreditError:
                    _append_jsonl(abstain_path, {"at": observed_at, "reason": "credit_cap", "cohort_key": game["cohort_key"]})
                    stop = True
                    break
                except CollectorError:
                    _append_jsonl(abstain_path, {"at": observed_at, "reason": "odds_failed", "cohort_key": game["cohort_key"]})
                    stop = True
                    break
                calls += 1
                if not isinstance(raw_odds, dict):
                    raise CollectorError("odds props response was not an object")
                if pregame:
                    anchors[game["cohort_key"]] = {
                        "observed_at": observed_at,
                        "scheduled_start": game["scheduled_start"],
                        "anchors": quoted_pregame_anchors(raw_odds, spec["markets"]),
                        "raw_odds": raw_odds,
                    }
                    _atomic(state_dir / "anchors.json", json.dumps(anchors, sort_keys=True))
                    continue
                stored = anchors.get(game["cohort_key"]) or {}
                kept = []
                if stored.get("observed_at") and stored.get("scheduled_start") and before_start(stored["observed_at"], stored["scheduled_start"]):
                    kept = [row for row in stored.get("anchors") or [] if row.get("kind") == "quoted_pregame"]
                else:
                    _append_jsonl(abstain_path, {
                        "at": observed_at, "reason": "no_pregame_anchor", "cohort_key": game["cohort_key"],
                    })
                try:
                    score = {"away": float(game["away_score"]), "home": float(game["home_score"])}
                except (TypeError, ValueError):
                    _append_jsonl(abstain_path, {"at": observed_at, "reason": "missing_score", "cohort_key": game["cohort_key"]})
                    continue
                observation = {
                    "observation_id": f"{sport}-{game['cohort_key']}-{label}",
                    "observed_at": observed_at,
                    "sport": sport,
                    "scheduled_start": game["scheduled_start"],
                    "away": game["away"],
                    "home": game["home"],
                    "aliases": {
                        "espn_event_id": game["espn_event_id"],
                        "odds_api_event_id": game["odds_event_id"],
                    },
                    "elapsed_minutes": elapsed,
                    "period_label": label,
                    "score": score,
                    "boxscore": box,
                    "quoted_odds": raw_odds,
                    "anchors": kept,
                }
                hash_payload(observation, "observation")
                observations.append(observation)
    finally:
        ledger.close()
    if not observations and not any(isinstance(value, dict) and value.get("anchors") for value in anchors.values()):
        return {"observations": 0, "snapshot": None, "live_ready": False,
                "note": "no observation written; this is not live-ready"}
    snapshot_path = None
    if observations:
        doc = {"schema": SCHEMA_IN, "observations": observations}
        text = json.dumps(doc, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
        digest = hashlib.sha256(text.encode("utf-8")).hexdigest()
        snapshot_path = archive / "snapshots" / f"{digest}.json"
        if snapshot_path.exists() and snapshot_path.read_text() != text:
            raise CollectorError("snapshot archive collision")
        if not snapshot_path.exists():
            _atomic(snapshot_path, text)
        for obs in observations:
            payload = hash_payload(obs, "observation")
            key = cohort_key(payload["sport"], payload["away"], payload["home"], payload["scheduled_start"])
            captured[f"{key}|{payload['period_label']}"] = input_hash(payload)
        _atomic(state_dir / "captured.json", json.dumps(captured, sort_keys=True))
        muse_state = archive / "runs" / digest / "muse-state"
        claude_state = archive / "runs" / digest / "claude-state"
        replay_file(snapshot_path, muse_state)
        invoke_claude(code_dir, python, snapshot_path, claude_state, runner, env)
        muse_rows = load_shadow(muse_state / "live_props" / "shadow.jsonl")
        claude_rows = load_shadow(claude_state / "live_props" / "shadow.jsonl")
        report = compare(muse_rows, claude_rows, None, unit=1.0)
        report["live_ready"] = False
        report["note"] = "Tracking comparison only. No calibration was fit. Not an order."
        dest = archive / "by-hash" / digest
        write_report(dest / "report.json", report)
        _atomic(dest / "muse" / "shadow.jsonl", (muse_state / "live_props" / "shadow.jsonl").read_text())
        _atomic(dest / "claude" / "shadow.jsonl", (claude_state / "live_props" / "shadow.jsonl").read_text())
        _atomic(dest / "snapshot.json", text)
    summary = write_daily_summary(archive, state_dir, observed_at[:10])
    return {
        "observations": len(observations),
        "snapshot": str(snapshot_path) if snapshot_path else None,
        "live_ready": False,
        "daily_summary": str(summary),
    }


def write_daily_summary(archive: Path, state_dir: Path, day: str) -> Path:
    usage = {}
    usage_path = state_dir / "usage.json"
    if usage_path.exists():
        try:
            usage = json.loads(usage_path.read_text())
        except json.JSONDecodeError:
            usage = {"fail_closed": True, "fail_reason": "unreadable ledger"}
    abstentions = []
    log = state_dir / "abstentions.jsonl"
    if log.exists():
        for line in log.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            if str(row.get("at") or "").startswith(day):
                abstentions.append(row)
    body = {
        "schema": "muse-prop1.daily-summary.v1",
        "date": day,
        "live_ready": False,
        "calibration_fit": False,
        "hypothetical_unit": 1.0,
        "credits_used": usage.get("used") if usage.get("date") == day else None,
        "credit_cap": usage.get("cap"),
        "fail_closed": bool(usage.get("fail_closed")) if usage.get("date") in (None, day) else False,
        "fail_reason": usage.get("fail_reason") or "",
        "abstentions": len(abstentions),
        "abstention_reasons": sorted({row.get("reason") for row in abstentions if row.get("reason")}),
        "note": "Counts only. No empirical calibration. Not a bet.",
    }
    path = archive / "daily" / day / "summary.json"
    _atomic(path, json.dumps(body, indent=2, sort_keys=True) + "\n")
    return path


def regrade(archive: Path, snapshot: Path, final_path: Path, muse_shadow: Path, claude_shadow: Path) -> Path:
    """Rebuild settlements and the comparison from saved files. No network."""
    archive = assert_replay_state(archive)
    doc = load_snapshot(snapshot)
    final = load_final(final_path)
    results = []
    for obs in doc["observations"]:
        results.extend(settlements_for_observation(obs, final))
    out = archive / "regrade" / "settlement.json"
    write_settlement(out, results, synthetic=bool(final.get("synthetic")))
    report = compare(load_shadow(muse_shadow), load_shadow(claude_shadow),
                     {"results": results}, unit=1.0)
    report["live_ready"] = False
    report["calibration_fit"] = False
    report["regrade"] = True
    dest = archive / "regrade" / "report.json"
    write_report(dest, report)
    return dest


def grade_saved(archive: Path, snapshot: Path, final_fetch, muse_shadow: Path, claude_shadow: Path) -> Path:
    """Fetch each observation's ESPN summary once and save the raw box. No odds call."""
    doc = load_snapshot(snapshot)
    merged = []
    for index, obs in enumerate(doc["observations"]):
        payload = hash_payload(obs, f"observations[{index}]")
        digest = input_hash(payload).removeprefix("sha256:")
        dest = archive / "finals" / f"{digest}.json"
        aliases = obs.get("aliases") or {}
        espn_id = aliases.get("espn_event_id")
        sport = payload["sport"]
        if sport not in COVERED or not espn_id:
            final = {"schema": FINAL_SCHEMA, "state": "in", "boxscore": {}, "synthetic": False}
        elif dest.exists():
            final = load_final(dest)
        else:
            spec = COVERED[sport]
            summary = final_fetch(f"{ESPN_BASE}/{spec['espn_path']}/summary?event={espn_id}")
            header = (summary.get("header") or {})
            comp = ((header.get("competitions") or [{}])[0])
            state = (((comp.get("status") or {}).get("type") or {}).get("state") or "in")
            final = {
                "schema": FINAL_SCHEMA,
                "state": state if state in ("pre", "in", "post") else "in",
                "boxscore": summary.get("boxscore") if isinstance(summary.get("boxscore"), dict) else {},
                "synthetic": False,
            }
            _atomic(dest, json.dumps(final, sort_keys=True))
        merged.extend(settlements_for_observation(obs, final))
    out = archive / "grade" / "settlement.json"
    write_settlement(out, merged)
    report = compare(load_shadow(muse_shadow), load_shadow(claude_shadow), {"results": merged}, unit=1.0)
    report["live_ready"] = False
    report["calibration_fit"] = False
    write_report(archive / "grade" / "report.json", report)
    return archive / "grade" / "report.json"


def _real_transport(url: str):
    import urllib.error
    import urllib.request
    request = urllib.request.Request(url, method="GET")
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            headers = {key.lower(): value for key, value in response.headers.items()}
            return response.status, headers, response.read()
    except urllib.error.HTTPError as exc:
        headers = {key.lower(): value for key, value in (exc.headers.items() if exc.headers else [])}
        return exc.code, headers, exc.read()


def _subprocess_runner(cmd, cwd, env):
    import subprocess
    completed = subprocess.run(cmd, cwd=cwd, env=env, check=False)
    return completed.returncode


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="collector")
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--regrade", action="store_true")
    ap.add_argument("--daily-summary", action="store_true")
    ap.add_argument("--grade", action="store_true", help="ESPN finals only; no Odds API call")
    ap.add_argument("--config", default=str(Path(__file__).parent / "config.yaml"))
    ap.add_argument("--state-dir", required=True)
    ap.add_argument("--archive", required=True)
    ap.add_argument("--snapshot", default=None)
    ap.add_argument("--final", default=None)
    ap.add_argument("--muse-shadow", default=None)
    ap.add_argument("--claude-shadow", default=None)
    ap.add_argument("--date", default=None)
    args = ap.parse_args(argv)
    cfg = yaml.safe_load(Path(args.config).read_text())
    try:
        assert_tracking_config(cfg)
        if args.regrade:
            if not args.snapshot or not args.final or not args.muse_shadow or not args.claude_shadow:
                print("regrade requires --snapshot --final --muse-shadow --claude-shadow")
                return 2
            path = regrade(Path(args.archive), Path(args.snapshot), Path(args.final),
                           Path(args.muse_shadow), Path(args.claude_shadow))
            print(f"regrade report={path} live_ready=false")
            return 0
        if args.daily_summary:
            day = args.date or datetime.now(timezone.utc).strftime("%Y-%m-%d")
            path = write_daily_summary(assert_replay_state(Path(args.archive)), assert_replay_state(Path(args.state_dir)), day)
            print(f"daily summary={path} live_ready=false")
            return 0
        if args.grade:
            if not args.snapshot or not args.muse_shadow or not args.claude_shadow:
                print("grade requires --snapshot --muse-shadow --claude-shadow")
                return 2
            def fetch(url):
                status, _headers, body = _real_transport(url)
                if status != 200:
                    raise CollectorError(f"public feed http {status}")
                return json.loads(body.decode("utf-8"))
            path = grade_saved(assert_replay_state(Path(args.archive)), Path(args.snapshot), fetch,
                               Path(args.muse_shadow), Path(args.claude_shadow))
            print(f"grade report={path} live_ready=false")
            return 0
        if not args.once:
            print("pass --once, --regrade, --grade, or --daily-summary")
            return 2
        result = collect_once(cfg, Path(args.state_dir), Path(args.archive), _real_transport, _subprocess_runner)
    except (CollectorError, CreditError, ReplayError, ValueError) as exc:
        print(f"collector refused: {exc}")
        return 2
    print(f"collector observations={result['observations']} live_ready=false snapshot={result.get('snapshot')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
