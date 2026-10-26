#!/usr/bin/env python3
from __future__ import annotations

import importlib
import json
import os
import time
from pathlib import Path
from typing import Any

import aiohttp
from dotenv import load_dotenv

load_dotenv()

ODDS_API_KEY = os.getenv("ODDS_API_KEY", "").strip()
SPORT_KEYS = [s.strip() for s in os.getenv("SPORT_KEYS", "basketball_ncaab").split(",") if s.strip()]
REGIONS = os.getenv("ODDS_REGIONS", "us").strip()
MARKETS = os.getenv("ODDS_MARKETS", "h2h,spreads,totals").strip()
ODDS_FORMAT = os.getenv("ODDS_FORMAT", "american").strip()
DATE_FORMAT = os.getenv("ODDS_DATE_FORMAT", "iso").strip()

RUN_ONCE = os.getenv("RUN_ONCE", "false").lower() == "true"
POLL_SECONDS = int(os.getenv("POLL_SECONDS", "120"))
MAX_GAMES_PER_POLL = int(os.getenv("MAX_GAMES_PER_POLL", "50"))
HTTP_TIMEOUT_SEC = int(os.getenv("HTTP_TIMEOUT_SEC", "25"))
ODDS_CACHE_TTL_SEC = int(os.getenv("ODDS_CACHE_TTL_SEC", "45"))
ENABLE_TERMINAL_ALERTS = os.getenv("ENABLE_TERMINAL_ALERTS", "true").lower() == "true"

STATE_FILE = Path("logs/live_odds_engine_state.json")
SEEN_FILE = Path("logs/live_odds_seen.json")
POLICY_PATH = Path("learned_policy.json")

BASE_MIN_EDGE = float(os.getenv("ENTRY_MIN_GAME_EDGE", "0.09"))
BASE_MIN_CONF = float(os.getenv("LEARN_BASE_MIN_CONF", "0.55"))

_odds_cache: dict[str, tuple[float, list[dict[str, Any]]]] = {}


def safe_import(module_name: str, attr_name: str) -> Any | None:
    try:
        module = importlib.import_module(module_name)
        return getattr(module, attr_name, None)
    except Exception:
        return None


build_game_state = safe_import("game_state_engine", "build_game_state")
detect_edges = safe_import("edge_detector", "detect_edges")
calibrate_edge_output = safe_import("calibration_layer", "calibrate_edge_output")
filter_signals = safe_import("signal_filter", "filter_signals")
process_bet_intelligence = safe_import("bet_intelligence", "process_bet_intelligence")
enforce_production_policy = safe_import("production_policy", "enforce_production_policy")

college_is_aj_edge = safe_import("aj_edge_rules", "is_aj_edge")
college_get_aj_reason = safe_import("aj_edge_rules", "get_aj_reason")

nba_is_aj_edge = safe_import("nba_edge_rules", "is_aj_edge")
nba_get_aj_reason = safe_import("nba_edge_rules", "get_aj_reason")
nba_get_bet_multiplier = safe_import("nba_edge_rules", "get_nba_bet_multiplier")

track_aj_edge = safe_import("aj_edge_tracker", "track_aj_edge")
record_tracked_bet = safe_import("performance_tracker", "record_tracked_bet")


def f(value: Any, default: float = 0.0) -> float:
    try:
        return float(value) if value is not None else default
    except Exception:
        return default


def ensure_parent(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)


def load_json(path: Path, default: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        pass
    return default


def save_json(path: Path, payload: Any) -> None:
    try:
        ensure_parent(path)
        path.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    except Exception as e:
        print(f"[live_odds_engine][WARN] save_json failed for {path}: {e}")


def load_learned_policy() -> dict[str, Any]:
    try:
        if POLICY_PATH.exists():
            data = json.loads(POLICY_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                return data
    except Exception as e:
        print(f"[policy] load failed: {e}")
    return {"buckets": {}}


def get_bucket_key(sport_key: str, market: str) -> str:
    return f"{str(sport_key or '').lower()}|{str(market or '').lower()}"


def get_policy_for(sport_key: str, market: str) -> dict[str, Any]:
    policy = load_learned_policy()
    buckets = policy.get("buckets", {})
    if not isinstance(buckets, dict):
        return {}
    return buckets.get(get_bucket_key(sport_key, market), {}) or {}


def normalize_any(obj: Any) -> dict[str, Any]:
    if isinstance(obj, dict):
        return dict(obj)

    if obj is None:
        return {}

    if isinstance(obj, list):
        return {"alerts": [x for x in obj if isinstance(x, dict)]}

    out: dict[str, Any] = {}

    if hasattr(obj, "__dict__"):
        try:
            for k, v in vars(obj).items():
                if not k.startswith("_"):
                    out[k] = v
        except Exception:
            pass

    for name in (
        "home_team",
        "away_team",
        "home_score",
        "away_score",
        "minutes_left",
        "seconds_left",
        "mode",
        "half",
        "phase_label",
        "completed",
        "status",
        "status_detail",
        "event_id",
        "sport_key",
        "period",
        "leader",
        "score_diff",
        "pace",
        "pressure",
    ):
        if hasattr(obj, name):
            try:
                out[name] = getattr(obj, name)
            except Exception:
                pass

    return out


def extract_alerts(obj: Any) -> list[dict[str, Any]]:
    if isinstance(obj, list):
        return [x for x in obj if isinstance(x, dict)]
    if isinstance(obj, dict):
        for key in ("alerts_to_send", "alerts", "signals", "edges", "bets", "final_signals"):
            value = obj.get(key)
            if isinstance(value, list):
                return [x for x in value if isinstance(x, dict)]
    return []


def safe_apply_module(func: Any, alerts: list[dict[str, Any]], payload: dict[str, Any], label: str) -> list[dict[str, Any]]:
    if not func or not alerts:
        return alerts

    try:
        raw = func(alerts, payload)
        maybe = extract_alerts(raw)
        return maybe if maybe else alerts
    except TypeError:
        try:
            raw = func(alerts)
            maybe = extract_alerts(raw)
            return maybe if maybe else alerts
        except Exception as e:
            print(f"[live_odds_engine][WARN] {label} failed: {e}")
            return alerts
    except Exception as e:
        print(f"[live_odds_engine][WARN] {label} failed: {e}")
        return alerts


def get_is_aj_edge(sig: dict[str, Any]) -> bool:
    sport_key = str(sig.get("sport_key", "") or "").lower()
    try:
        if "nba" in sport_key and nba_is_aj_edge:
            return bool(nba_is_aj_edge(sig))
        if college_is_aj_edge:
            return bool(college_is_aj_edge(sig))
    except Exception:
        return False
    return False


def get_aj_reason_for_sig(sig: dict[str, Any]) -> str:
    sport_key = str(sig.get("sport_key", "") or "").lower()
    try:
        if "nba" in sport_key and nba_get_aj_reason:
            return str(nba_get_aj_reason(sig) or "")
        if college_get_aj_reason:
            return str(college_get_aj_reason(sig) or "")
    except Exception:
        return ""
    return ""


def load_engine_state() -> dict[str, Any]:
    return load_json(STATE_FILE, {"cycles": 0, "status": "idle", "last_alerts": []})


def save_engine_state(state: dict[str, Any]) -> None:
    save_json(STATE_FILE, state)


def update_seen_state(engine_state: dict[str, Any], game_key: str, scoreboard: dict[str, Any] | None) -> None:
    seen = load_json(SEEN_FILE, {})
    try:
        seen[game_key] = {
            "ts": time.time(),
            "scoreboard_found": bool(scoreboard),
            "status": scoreboard.get("status_detail", "") if isinstance(scoreboard, dict) else "",
        }
        save_json(SEEN_FILE, seen)
    except Exception:
        pass


def choose_bookmaker_lines(odds_event: dict[str, Any]) -> dict[str, Any]:
    home_team = odds_event.get("home_team", "")
    away_team = odds_event.get("away_team", "")

    market_total = 150.5
    home_spread_line = -3.5
    home_ml_price = -110
    away_ml_price = -110

    bookmakers = odds_event.get("bookmakers", []) or []
    for book in bookmakers:
        markets = book.get("markets", []) or []
        for market in markets:
            key = str(market.get("key", "")).lower()
            outcomes = market.get("outcomes", []) or []

            if key == "totals":
                for outcome in outcomes:
                    point = outcome.get("point")
                    if point is not None:
                        market_total = f(point, market_total)
                        break

            elif key == "spreads":
                for outcome in outcomes:
                    name = outcome.get("name", "")
                    point = outcome.get("point")
                    if point is None:
                        continue
                    if str(name).strip().lower() == str(home_team).strip().lower():
                        home_spread_line = f(point, home_spread_line)
                        break

            elif key == "h2h":
                for outcome in outcomes:
                    name = outcome.get("name", "")
                    price = outcome.get("price")
                    if price is None:
                        continue
                    if str(name).strip().lower() == str(home_team).strip().lower():
                        home_ml_price = f(price, home_ml_price)
                    elif str(name).strip().lower() == str(away_team).strip().lower():
                        away_ml_price = f(price, away_ml_price)

    return {
        "market_total": market_total,
        "home_spread_line": home_spread_line,
        "home_ml_price": home_ml_price,
        "away_ml_price": away_ml_price,
    }


def build_edge_payload(odds_event: dict[str, Any], matched_game: dict[str, Any]) -> dict[str, Any]:
    lines = choose_bookmaker_lines(odds_event)

    home_score = f(matched_game.get("home_score", 0), 0.0)
    away_score = f(matched_game.get("away_score", 0), 0.0)
    score_diff = abs(home_score - away_score)
    leader = matched_game.get("home_team") if home_score >= away_score else matched_game.get("away_team")

    return {
        "game": f"{matched_game.get('away_team', '')} @ {matched_game.get('home_team', '')}",
        "sport_key": matched_game.get("sport_key", odds_event.get("sport_key", "")),
        "home_team": matched_game.get("home_team", odds_event.get("home_team", "")),
        "away_team": matched_game.get("away_team", odds_event.get("away_team", "")),
        "home_score": home_score,
        "away_score": away_score,
        "minutes_left": matched_game.get("minutes_left", 0),
        "seconds_left": matched_game.get("seconds_left", 0),
        "period": matched_game.get("period", 0),
        "mode": matched_game.get("mode", "2h"),
        "half": matched_game.get("half", ""),
        "phase_label": matched_game.get("phase_label", ""),
        "status_detail": matched_game.get("status_detail", ""),
        "completed": matched_game.get("completed", False),
        "event_id": matched_game.get("event_id", odds_event.get("id", "")),
        "market_total": lines["market_total"],
        "home_spread_line": lines["home_spread_line"],
        "home_ml_price": lines["home_ml_price"],
        "away_ml_price": lines["away_ml_price"],
        "leader": leader,
        "score_diff": score_diff,
        "matched_game": matched_game,
        "odds_event": odds_event,
    }


def sanitize_edge_payload(payload: dict[str, Any], edge: dict[str, Any]) -> dict[str, Any]:
    sport_key = str(payload.get("sport_key", "") or "").lower()

    market_total = float(payload.get("market_total", 150.5) or 150.5)
    home_spread_line = float(payload.get("home_spread_line", -3.5) or -3.5)
    minutes_left = float(payload.get("minutes_left", 0) or 0)
    score_diff = float(payload.get("score_diff", 0) or 0)
    status_detail = str(payload.get("status_detail", "") or "").lower()

    if "halftime" in status_detail:
        minutes_left = 12.0 if "nba" in sport_key else 20.0

    model_total = float(edge.get("model_total", market_total) or market_total)
    model_margin = float(edge.get("model_margin", abs(home_spread_line)) or abs(home_spread_line))

    if "nba" in sport_key:
        max_total_delta = 18.0
        max_margin_delta = 12.0
        base_edge_cap = 0.20
    elif "wnba" in sport_key:
        max_total_delta = 14.0
        max_margin_delta = 10.0
        base_edge_cap = 0.18
    else:
        max_total_delta = 16.0
        max_margin_delta = 10.0
        base_edge_cap = 0.18

    model_total = max(market_total - max_total_delta, min(market_total + max_total_delta, model_total))

    spread_abs = abs(home_spread_line)
    model_margin = max(max(0.0, spread_abs - max_margin_delta), min(spread_abs + max_margin_delta, abs(model_margin)))

    edge["model_total"] = round(model_total, 2)
    edge["model_margin"] = round(model_margin, 2)

    projected_home = round((model_total + model_margin) / 2.0, 2)
    projected_away = round((model_total - model_margin) / 2.0, 2)
    edge["projected_home"] = projected_home
    edge["projected_away"] = projected_away

    dynamic_cap = base_edge_cap
    if minutes_left <= 6:
        dynamic_cap -= 0.03
    if score_diff >= 15:
        dynamic_cap -= 0.03
    if score_diff >= 25:
        dynamic_cap -= 0.03
    dynamic_cap = max(0.08, dynamic_cap)

    for key in (
        "home_spread_edge",
        "away_spread_edge",
        "over_edge",
        "under_edge",
        "home_ml_edge",
        "away_ml_edge",
        "edge",
    ):
        if key in edge:
            try:
                val = float(edge.get(key, 0.0) or 0.0)
                edge[key] = round(max(0.0, min(dynamic_cap, val)), 4)
            except Exception:
                edge[key] = 0.0

    edge["dynamic_edge_cap"] = round(dynamic_cap, 4)
    return edge


async def _fetch_one_sport_odds(session: aiohttp.ClientSession, sport_key: str) -> list[dict[str, Any]]:
    if not ODDS_API_KEY:
        return []

    cache_key = f"{sport_key}|{REGIONS}|{MARKETS}|{ODDS_FORMAT}|{DATE_FORMAT}"
    now = time.time()
    cached = _odds_cache.get(cache_key)
    if cached and (now - cached[0]) <= ODDS_CACHE_TTL_SEC:
        return cached[1]

    url = f"https://api.the-odds-api.com/v4/sports/{sport_key}/odds"
    params = {
        "apiKey": ODDS_API_KEY,
        "regions": REGIONS,
        "markets": MARKETS,
        "oddsFormat": ODDS_FORMAT,
        "dateFormat": DATE_FORMAT,
    }

    try:
        async with session.get(url, params=params) as resp:
            if resp.status != 200:
                print(f"[odds] {sport_key} status={resp.status}")
                return []
            data = await resp.json()
            if isinstance(data, list):
                for row in data:
                    if isinstance(row, dict):
                        row.setdefault("sport_key", sport_key)
                _odds_cache[cache_key] = (now, data)
                return data
    except Exception as e:
        print(f"[odds] fetch failed for {sport_key}: {e}")

    return []


async def fetch_live_odds() -> list[dict[str, Any]]:
    timeout = aiohttp.ClientTimeout(total=HTTP_TIMEOUT_SEC)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        all_rows: list[dict[str, Any]] = []
        for sport_key in SPORT_KEYS:
            rows = await _fetch_one_sport_odds(session, sport_key)
            all_rows.extend(rows)
        return all_rows


def run_signal_pipeline(payload: dict[str, Any]):
    matched_game = payload.get("matched_game")

    game_state_obj = {}
    if build_game_state:
        try:
            game_state_obj = build_game_state(matched_game) if matched_game else {}
        except Exception as e:
            print(f"[live_odds_engine][WARN] build_game_state failed: {e}")
            game_state_obj = matched_game if isinstance(matched_game, dict) else {}

    game_state = normalize_any(game_state_obj)
    if "score_diff" not in game_state:
        try:
            game_state["score_diff"] = abs(
                f(game_state.get("home_score", 0.0), 0.0) - f(game_state.get("away_score", 0.0), 0.0)
            )
        except Exception:
            game_state["score_diff"] = 0

    alerts: list[dict[str, Any]] = []

    edge: dict[str, Any] = {
        "game": payload.get("game", ""),
        "sport_key": payload.get("sport_key", ""),
        "market_total": payload.get("market_total", 150.5),
        "home_spread_line": payload.get("home_spread_line", -3.5),
        "period": payload.get("period", game_state.get("period", 0)),
        "minutes_left": payload.get("minutes_left", game_state.get("minutes_left", 0)),
        "seconds_left": payload.get("seconds_left", game_state.get("seconds_left", 0)),
        "score_diff": payload.get("score_diff", game_state.get("score_diff", 0)),
        "home_team": payload.get("home_team", ""),
        "away_team": payload.get("away_team", ""),
        "home_score": payload.get("home_score", game_state.get("home_score", 0)),
        "away_score": payload.get("away_score", game_state.get("away_score", 0)),
    }

    raw_detection: Any = {}
    if detect_edges:
        try:
            raw_detection = detect_edges(payload)
            alerts = extract_alerts(raw_detection)
            if isinstance(raw_detection, dict):
                edge.update({k: v for k, v in raw_detection.items() if not isinstance(v, (list, dict))})
        except TypeError:
            try:
                raw_detection = detect_edges(game_state)
                alerts = extract_alerts(raw_detection)
                if isinstance(raw_detection, dict):
                    edge.update({k: v for k, v in raw_detection.items() if not isinstance(v, (list, dict))})
            except Exception as e:
                print(f"[live_odds_engine][WARN] detect_edges failed: {e}")
        except Exception as e:
            print(f"[live_odds_engine][WARN] detect_edges failed: {e}")

    edge = sanitize_edge_payload(payload, edge)

    alerts = safe_apply_module(calibrate_edge_output, alerts, payload, "calibrate_edge_output")
    alerts = safe_apply_module(filter_signals, alerts, payload, "filter_signals")
    alerts = safe_apply_module(process_bet_intelligence, alerts, payload, "process_bet_intelligence")
    alerts = safe_apply_module(enforce_production_policy, alerts, payload, "enforce_production_policy")

    if not alerts and isinstance(raw_detection, dict):
        fallback_alerts: list[dict[str, Any]] = []

        def add_alert(market: str, selection: str, edge_key: str, line_value: Any = None, confidence: float = 0.65):
            edge_val = edge.get(edge_key, raw_detection.get(edge_key, 0))
            try:
                edge_num = float(edge_val or 0)
            except Exception:
                edge_num = 0.0

            minutes_left = float(payload.get("minutes_left", game_state.get("minutes_left", 0)) or 0)
            score_diff = float(payload.get("score_diff", game_state.get("score_diff", 0)) or 0)
            status_detail = str(payload.get("status_detail", "") or "").lower()
            suspicious_any = bool(raw_detection.get("suspicious_any", False))

            if "halftime" in status_detail and "nba" in str(payload.get("sport_key", "")).lower():
                minutes_left = 12.0

            if suspicious_any and edge_num < 0.18:
                return
            if minutes_left <= 4 and edge_num < 0.12:
                return
            if score_diff >= 20 and market in {"moneyline", "spread"} and edge_num < 0.14:
                return
            if score_diff >= 25 and market == "total" and edge_num < 0.14:
                return
            if edge_num <= 0:
                return

            fallback_alerts.append({
                "game": payload.get("game", ""),
                "sport_key": payload.get("sport_key", ""),
                "market": market,
                "selection": selection,
                "edge": round(edge_num, 4),
                "confidence": confidence,
                "period": payload.get("period", game_state.get("period", 0)),
                "minutes_left": payload.get("minutes_left", game_state.get("minutes_left", 0)),
                "seconds_left": payload.get("seconds_left", game_state.get("seconds_left", 0)),
                "score_diff": payload.get("score_diff", game_state.get("score_diff", 0)),
                "home_team": payload.get("home_team", ""),
                "away_team": payload.get("away_team", ""),
                "home_score": payload.get("home_score", game_state.get("home_score", 0)),
                "away_score": payload.get("away_score", game_state.get("away_score", 0)),
                "suspicious": suspicious_any,
                "side": selection,
                "team": selection if market != "total" else "",
                "line": line_value,
            })

        add_alert("spread", payload.get("home_team", ""), "home_spread_edge", payload.get("home_spread_line"))
        add_alert("spread", payload.get("away_team", ""), "away_spread_edge", payload.get("home_spread_line"))
        add_alert("moneyline", payload.get("home_team", ""), "home_ml_edge", None)
        add_alert("moneyline", payload.get("away_team", ""), "away_ml_edge", None)
        add_alert("total", "over", "over_edge", payload.get("market_total"))
        add_alert("total", "under", "under_edge", payload.get("market_total"))

        fallback_alerts.sort(key=lambda x: float(x.get("edge", 0)), reverse=True)
        alerts = fallback_alerts[:3]

    normalized: list[dict[str, Any]] = []
    for sig in alerts:
        row = dict(sig)

        market = str(row.get("market", "moneyline")).lower()
        selection = row.get("selection", "")
        line_value = row.get("line")

        if market == "total" and (line_value is None or line_value == ""):
            line_value = payload.get("market_total")
        elif market == "spread" and (line_value is None or line_value == ""):
            line_value = payload.get("home_spread_line")

        row.setdefault("game", payload.get("game", ""))
        row.setdefault("selection", payload.get("home_team", ""))
        row.setdefault("edge", row.get("edge", 0.0))
        row.setdefault("confidence", row.get("confidence", 0.0))
        row.setdefault("sport_key", payload.get("sport_key", ""))
        row.setdefault("period", payload.get("period", game_state.get("period", 0)))

        row["minutes_left"] = payload.get("minutes_left", game_state.get("minutes_left", 0))
        row["seconds_left"] = payload.get("seconds_left", game_state.get("seconds_left", 0))
        row["score_diff"] = payload.get("score_diff", game_state.get("score_diff", 0))

        row.setdefault("side", selection)
        row.setdefault("team", "" if market == "total" else selection)
        row["line"] = line_value

        policy_row = get_policy_for(row.get("sport_key", ""), row.get("market", ""))
        row["recommended_min_edge"] = policy_row.get("recommended_min_edge", BASE_MIN_EDGE)
        row["recommended_min_confidence"] = policy_row.get("recommended_min_confidence", BASE_MIN_CONF)
        row["allow_adjust"] = policy_row.get("allow_adjust", False)

        sport_key_l = str(row.get("sport_key", "")).lower()
        market_l = str(row.get("market", "")).lower()
        edge_val = float(row.get("edge", 0.0) or 0.0)

        if "basketball_nba" in sport_key_l:
            if market_l == "total" and edge_val < 0.10:
                continue
            if market_l == "moneyline" and edge_val < 0.11:
                continue
            if market_l == "spread" and edge_val < 0.11:
                continue

        if row.get("suspicious") and float(row.get("edge", 0.0) or 0.0) < 0.18:
            continue

        if "recommended_bankroll_pct" not in row:
            if get_is_aj_edge(row):
                base_bet = 0.02
                multiplier = 1.0
                if "nba" in str(row.get("sport_key", "")).lower() and nba_get_bet_multiplier:
                    try:
                        multiplier = float(nba_get_bet_multiplier(row))
                    except Exception:
                        multiplier = 1.0
                row["recommended_bankroll_pct"] = round(base_bet * multiplier, 4)
            else:
                if edge_val >= 0.15:
                    row["recommended_bankroll_pct"] = 0.01
                elif edge_val >= 0.10:
                    row["recommended_bankroll_pct"] = 0.005
                else:
                    row["recommended_bankroll_pct"] = 0.0

        normalized.append(row)

    if normalized:
        edge.update(dict(normalized[0]))

    intel = {
        "signal_count": len(normalized),
        "status": "ok" if normalized else "no_signals",
        "alerts_to_send": normalized,
        "game_state": game_state,
    }

    return edge, intel


def is_truly_live(scoreboard: dict[str, Any] | None) -> bool:
    if not isinstance(scoreboard, dict):
        return False
    if scoreboard.get("completed"):
        return False

    status = str(scoreboard.get("status", "")).lower()
    status_detail = str(scoreboard.get("status_detail", "")).lower()
    period = scoreboard.get("period", 0)

    if "final" in status or "final" in status_detail:
        return False
    if "halftime" in status or "halftime" in status_detail:
        return True
    if period in (0, None):
        return False

    return True


def print_header(*args, **kwargs):
    e = args[0] if len(args) > 0 else 0
    b = args[1] if len(args) > 1 else 0
    print(f"=== LIVE ENGINE ===\n events={e} | scoreboard_games={b}")


def print_game_block(*args, **kwargs):
    home = args[0] if len(args) > 0 else ""
    away = args[1] if len(args) > 1 else ""
    print(f"\n--- {away} @ {home} ---")


def print_game_state_block(*args, **kwargs):
    gs = args[0] if args else {}
    print("STATE:", gs)


def print_edge_output(*args, **kwargs):
    e = args[0] if args else {}
    print("EDGE:", e)


def print_intelligence_output(*args, **kwargs):
    i = args[0] if args else {}
    if isinstance(i, dict):
        print("INTEL:", {"signal_count": i.get("signal_count", 0), "status": i.get("status", "")})
    else:
        print("INTEL:", i)


def print_terminal_alerts(alerts: list[dict[str, Any]]):
    if not ENABLE_TERMINAL_ALERTS:
        return

    for sig in alerts:
        high_value = get_is_aj_edge(sig)

        if high_value:
            sport_key = str(sig.get("sport_key", "") or "").lower()
            base_bet = 2.0
            multiplier = 1.0

            if "nba" in sport_key and nba_get_bet_multiplier:
                try:
                    multiplier = float(nba_get_bet_multiplier(sig))
                except Exception:
                    multiplier = 1.0

            final_bet = round(base_bet * multiplier, 2)

            print("\n🔥 [AJ EDGE ALERT] 🔥")
            print(f"GAME: {sig.get('game')}")
            print(f"MARKET: {sig.get('market')}")
            print(f"SELECTION: {sig.get('selection')}")
            print(f"EDGE: {sig.get('edge')} | CONF: {sig.get('confidence')}")
            print(f"BET SIZE: {final_bet}% bankroll")

            reason = get_aj_reason_for_sig(sig)
            if reason:
                print(f"REASON: {reason}")

            if multiplier < 1.0:
                print("⚠️ NBA garbage-time reduction applied")

            if record_tracked_bet:
                try:
                    tracked = dict(sig)
                    tracked["recommended_bankroll_pct"] = final_bet / 100.0
                    track_tag = tracked.get("sport_key", "unknown")
                    record_tracked_bet(tracked, tag=track_tag)
                except Exception:
                    pass

            if track_aj_edge:
                try:
                    tracked = dict(sig)
                    tracked["recommended_bankroll_pct"] = final_bet / 100.0
                    track_aj_edge(tracked)
                except Exception:
                    pass
        else:
            print(f"Checked: {sig.get('game')} - No significant edge.")
