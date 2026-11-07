"""Replay Muse Prop1 on a marv.prop_snapshot.v1 file. No network, no orders.

The hash, cohort key, and anchor kinds match docs/prop_snapshot_v1.md at
commit 0155dbca20e5143c4d92e7ff2b47726f716e891d. This file does not recompute
elapsed time, does not invent a pregame quote, and does not write a calibration.

Output is marv.prop_shadow.v1. Each run replaces shadow.jsonl in the state
dir. Callers that need history must copy that file into a per-hash archive
before the next replay. orchestrate.py does that.
"""
from __future__ import annotations

import hashlib
import json
import os
from datetime import datetime
from pathlib import Path

import yaml

from calibration import uncalibrated_interval
from cohort import CohortError, cohort_key, norm_person, team_key, utc_stamp
from engine import (
    SPORTS, BasketballPaceInput, PaceInput, american_to_implied, evaluate_pick,
    passes_gates, prob_over_count, prob_over_yards, project_basketball_final,
    project_final, remove_vig,
)
from live_feed import parse_summary
from priors import MARKET_SD, get_prior

SCHEMA_IN = "marv.prop_snapshot.v1"
SCHEMA_OUT = "marv.prop_shadow.v1"
MODEL = "muse-prop1"
MODEL_VERSION = "muse-prop1"
SHARED_INSTALL = Path("/opt/marv-bot")
EXPLICIT_OFF = frozenset({"0", "off", "false", "no"})
ANCHOR_KINDS = frozenset({"quoted_pregame", "model_projection", "inferred"})
_ANCHOR_RANK = {"inferred": 1, "quoted_pregame": 2, "model_projection": 3}
PROP_SPORTS = frozenset({"nfl", "cfb", "nba", "wnba", "ncaab", "ncaaw"})
ENGINE_SPORT = {"nfl": "nfl", "cfb": "ncaaf", "ncaab": "ncaab", "ncaaw": "ncaaw", "nba": "nba"}
COUNT_MARKETS = frozenset({"player_anytime_td", "player_pass_tds", "player_threes"})
QUOTE_MAX_AGE = 180.0
ROOT = Path(__file__).parent
MARKETS = {
    "nfl": ["player_pass_yds", "player_pass_tds", "player_rush_yds",
            "player_reception_yds", "player_receptions", "player_anytime_td"],
    "ncaaf": ["player_pass_yds", "player_pass_tds", "player_rush_yds",
              "player_reception_yds", "player_receptions", "player_anytime_td"],
    "ncaab": ["player_points", "player_rebounds", "player_assists", "player_threes",
              "player_points_rebounds", "player_points_assists",
              "player_rebounds_assists", "player_points_rebounds_assists"],
    "ncaaw": ["player_points", "player_rebounds", "player_assists", "player_threes",
              "player_points_rebounds", "player_points_assists",
              "player_rebounds_assists", "player_points_rebounds_assists"],
    # Same public keys Claude Prop 1 prices for NBA. Not a PropLine key.
    "nba": ["player_points", "player_rebounds", "player_assists", "player_threes"],
}
STAT_KEY = {
    "player_pass_yds": "pass_yds", "player_pass_tds": "pass_tds",
    "player_rush_yds": "rush_yds", "player_reception_yds": "rec_yds",
    "player_receptions": "receptions", "player_points": "points",
    "player_rebounds": "rebounds", "player_assists": "assists", "player_threes": "threes",
    "player_points_rebounds": "pr", "player_points_assists": "pa",
    "player_rebounds_assists": "ra", "player_points_rebounds_assists": "pra",
}


class ReplayError(Exception):
    """The snapshot cannot be replayed. Nothing was written."""


def explicit_send_off() -> bool:
    """True only for an explicit off token. Unset is refused, matching Claude replay."""
    raw = os.environ.get("LIVE_PROPS_SEND")
    if raw is None:
        return False
    return raw.strip().lower() in EXPLICIT_OFF


def _stamp(value, what: str) -> str:
    try:
        return utc_stamp(value, what)
    except CohortError as exc:
        raise ReplayError(str(exc)) from exc


def canonical_dumps(payload: object) -> str:
    """UTF-8 JSON, object keys sorted, no extra whitespace. Integers stay integers."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def input_hash(payload: dict) -> str:
    digest = hashlib.sha256(canonical_dumps(payload).encode("utf-8")).hexdigest()
    return "sha256:" + digest


def assert_replay_state(state_dir: Path) -> Path:
    """Resolved writable root. Refuses the shared /opt/marv-bot tree and its parents."""
    state = Path(state_dir).expanduser().resolve()
    text = str(state)
    if "muse_nfl_shadow" in text or "marv-feed" in text:
        raise ReplayError(
            f"state dir {state} is the older AJ muse_nfl_shadow experiment, not Muse Prop1")
    shared = SHARED_INSTALL.resolve()
    if state == shared or shared in state.parents or state in shared.parents:
        raise ReplayError(f"state dir {state} overlaps the shared install {shared}")
    return state


def _aliases(raw, where: str) -> dict[str, str]:
    if raw is None:
        return {}
    if not isinstance(raw, dict):
        raise ReplayError(f"{where}.aliases must be an object of strings")
    out = {}
    for key, value in raw.items():
        if not isinstance(key, str) or not isinstance(value, str):
            raise ReplayError(f"{where}.aliases values must be strings")
        out[key] = value
    return out


def _anchors(raw, where: str) -> list[dict]:
    if not isinstance(raw, list):
        raise ReplayError(f"{where}.anchors must be a list")
    rows = []
    for i, item in enumerate(raw):
        if not isinstance(item, dict):
            raise ReplayError(f"{where}.anchors[{i}] must be an object")
        kind = item.get("kind")
        if kind not in ANCHOR_KINDS:
            raise ReplayError(
                f"{where}.anchors[{i}].kind must be quoted_pregame, model_projection, or inferred "
                "(the word pregame alone is refused)")
        if "player" not in item or "market" not in item or "value" not in item:
            raise ReplayError(f"{where}.anchors[{i}] needs player, market, and value")
        try:
            value = float(item["value"])
        except (TypeError, ValueError) as exc:
            raise ReplayError(f"{where}.anchors[{i}].value must be a number") from exc
        rows.append({"player": str(item["player"]), "market": str(item["market"]), "value": value, "kind": kind})
    rows.sort(key=lambda r: (r["player"], r["market"], r["kind"]))
    return rows


def _require(obs: dict, key: str, where: str):
    if key not in obs:
        raise ReplayError(f"{where} missing {key}")
    return obs[key]


def hash_payload(obs: dict, where: str) -> dict:
    """The object both sides hash. Aliases and observation_id are join keys, not model inputs."""
    sport = str(_require(obs, "sport", where)).strip().lower()
    if sport not in PROP_SPORTS:
        raise ReplayError(f"{where}.sport is not a prop sport")
    away = str(_require(obs, "away", where)).strip()
    home = str(_require(obs, "home", where)).strip()
    if not isinstance(obs.get("boxscore"), dict) or not isinstance(obs.get("quoted_odds"), dict):
        raise ReplayError(f"{where} needs boxscore and quoted_odds objects")
    try:
        elapsed = float(_require(obs, "elapsed_minutes", where))
        home_score = float(obs["score"]["home"])
        away_score = float(obs["score"]["away"])
    except (TypeError, ValueError, KeyError) as exc:
        raise ReplayError(f"{where} needs elapsed_minutes and score.home / score.away numbers") from exc
    return {
        "schema": SCHEMA_IN,
        "sport": sport,
        "away": away,
        "home": home,
        "away_key": team_key(away),
        "home_key": team_key(home),
        "scheduled_start": _stamp(_require(obs, "scheduled_start", where), f"{where}.scheduled_start"),
        "observed_at": _stamp(_require(obs, "observed_at", where), f"{where}.observed_at"),
        "elapsed_minutes": elapsed,
        "score": {"away": away_score, "home": home_score},
        "period_label": str(_require(obs, "period_label", where)),
        "boxscore": obs["boxscore"],
        "quoted_odds": obs["quoted_odds"],
        "anchors": _anchors(obs.get("anchors", []), where),
    }


def _anchor_table(anchors: list[dict]) -> dict[tuple[str, str], tuple[str, float]]:
    chosen: dict[tuple[str, str], tuple[int, str, float]] = {}
    for row in anchors:
        key = (norm_person(row["player"]), row["market"])
        rank = _ANCHOR_RANK[row["kind"]]
        prev = chosen.get(key)
        if prev is None or rank >= prev[0]:
            chosen[key] = (rank, row["kind"], row["value"])
    return {key: (kind, value) for key, (_rank, kind, value) in chosen.items()}


def _raw_groups(quoted_odds: dict) -> list[dict]:
    """Two-sided and one-sided Bovada groups. Other books are ignored."""
    out = []
    for book in quoted_odds.get("bookmakers") or []:
        if book.get("key") != "bovada":
            continue
        for market in book.get("markets") or []:
            grouped: dict[tuple, dict] = {}
            for outcome in market.get("outcomes") or []:
                if outcome.get("name") not in ("Over", "Under"):
                    continue
                if not outcome.get("description") or outcome.get("point") is None:
                    continue
                key = (outcome["description"], float(outcome["point"]))
                group = grouped.setdefault(key, {
                    "player": outcome["description"], "line": float(outcome["point"]),
                    "market": market.get("key"), "quoted_at": market.get("last_update"),
                })
                group[outcome["name"].lower()] = outcome.get("price")
            out.extend(grouped.values())
    return out


def _base(payload: dict, obs: dict, digest: str, aliases: dict[str, str]) -> dict:
    key = cohort_key(payload["sport"], payload["away"], payload["home"], payload["scheduled_start"])
    return {
        "schema": SCHEMA_OUT,
        "mode": "snapshot_replay",
        "model": MODEL,
        "model_version": MODEL_VERSION,
        "cohort_key": key,
        "observation_id": "" if obs.get("observation_id") is None else str(obs.get("observation_id")),
        "input_hash": digest,
        "sport": payload["sport"],
        "scheduled_start": payload["scheduled_start"],
        "observed_at": payload["observed_at"],
        "home": payload["home"],
        "away": payload["away"],
        "aliases": aliases,
        "game_id": key,
        "period": payload["period_label"],
        "elapsed_minutes": payload["elapsed_minutes"],
        "probability_status": "uncalibrated",
        "empirical_calibration": False,
    }


def _round_row(row: dict) -> dict:
    out = {}
    for key, value in row.items():
        if isinstance(value, float):
            out[key] = round(value, 4)
        elif isinstance(value, dict):
            out[key] = _round_row(value)
        else:
            out[key] = value
    return out


def load_config(path: Path | None = None) -> dict:
    return yaml.safe_load((path or (ROOT / "config.yaml")).read_text())


def _lookup_team_number(player_team: str | None, by_team: dict) -> float | None:
    if not by_team:
        return None
    if player_team in by_team:
        return float(by_team[player_team])
    want = team_key(player_team or "")
    for name, value in by_team.items():
        if team_key(name) == want:
            return float(value)
    return None


def _stat(player: dict, market: str) -> float:
    if market == "player_anytime_td":
        return float(player.get("rush_tds") or 0) + float(player.get("rec_tds") or 0)
    return float(player.get(STAT_KEY.get(market, ""), 0) or 0)


def _side_probability(market: str, proj: float, sd: float, line: float, side: str, scale: float = 1.0) -> float:
    if market in COUNT_MARKETS:
        over = prob_over_count(max(0.0, proj) * scale, line)
    else:
        over = prob_over_yards(proj, sd * scale, line)
    return over if side == "Over" else 1.0 - over


def _quote_stale(observed_at: str, quoted_at) -> bool:
    if not quoted_at:
        return False
    try:
        observed = datetime.fromisoformat(observed_at.replace("Z", "+00:00"))
        quoted = datetime.fromisoformat(str(quoted_at).replace("Z", "+00:00"))
    except ValueError:
        return True
    if quoted.tzinfo is None or observed.tzinfo is None:
        return True
    return (observed - quoted).total_seconds() > QUOTE_MAX_AGE


def _project(sport: str, market: str, player: dict, anchor_value: float, anchor_kind: str,
             elapsed: float, score_home: float, score_away: float, cfg: dict,
             priors_yaml: dict) -> tuple[dict | None, str]:
    """Project from the snapshot's elapsed and the box score's own pace.

    Returns (details, "") or (None, abstain_reason). Does not fill pace or minutes.
    """
    engine = ENGINE_SPORT[sport]
    scfg = cfg.get(engine, {}) or {}
    total = SPORTS[engine]["total_min"]
    sd = float(MARKET_SD.get(market, 5.0))
    opportunities = None
    possession = sport == "nba"
    if possession:
        # Claude GAME nba league possessions. Shrink target only, not observed pace.
        opportunities = float(scfg.get("league_possessions", 99))
    elif engine in ("nfl", "ncaaf"):
        opportunities = float(scfg.get("season_plays_per_min", 1.05)) * total
    minutes = None
    entry_minutes = None
    # A player file may supply minutes or a rate. It is not a generic table.
    if priors_yaml:
        block = None
        for name, body in priors_yaml.items():
            if norm_person(name) == norm_person(player.get("name", "")) and isinstance(body, dict):
                block = body.get(market) or {}
                break
        if isinstance(block, dict) and block.get("minutes") is not None:
            entry_minutes = float(block["minutes"])
    observed_line = anchor_value if anchor_kind == "quoted_pregame" else None
    prior = get_prior(engine, player.get("name", ""), market, observed_line, priors_yaml,
                      opportunities=opportunities, player_minutes=entry_minutes)
    if anchor_kind != "quoted_pregame":
        # The number is this player's anchor, but it is not a captured pregame quote.
        if prior["rate"] is None or prior["mean_source"] != "player_prior_file":
            if engine in ("nfl", "ncaaf") and opportunities:
                prior = dict(prior)
                prior["mean"] = float(anchor_value)
                prior["rate"] = float(anchor_value) / opportunities
                prior["rate_source"] = f"{anchor_kind}_over_league_plays"
                prior["mean_source"] = anchor_kind
                prior["reason"] = None
            elif entry_minutes:
                prior = dict(prior)
                prior["mean"] = float(anchor_value)
                prior["rate"] = float(anchor_value) / entry_minutes
                prior["rate_source"] = f"{anchor_kind}_over_player_minutes"
                prior["mean_source"] = anchor_kind
                prior["pregame_minutes"] = entry_minutes
                prior["reason"] = None
            else:
                return None, "missing_player_rate" if engine in ("nfl", "ncaaf") else "missing_player_minutes"
        else:
            prior = dict(prior)
            prior["mean_source"] = "player_prior_file"
    if prior["mean"] is None or prior["rate"] is None:
        return None, prior["reason"] or "missing_player_rate"
    current = _stat(player, market)
    if engine in ("nfl", "ncaaf") or possession:
        if possession:
            plays = player.get("team_possessions")
            pace_source = "observed_team_possessions"
            season_ppm = opportunities / total if total else 0.0
        else:
            plays = player.get("team_plays")
            pace_source = "observed_team_plays"
            season_ppm = scfg.get("season_plays_per_min", 1.05)
        if plays is None or float(plays) <= 0:
            return None, "missing_real_pace"
        proj, live_sd = project_final(PaceInput(
            current=current, elapsed_min=elapsed, total_min=total,
            pregame_mean=prior["mean"], pregame_sd=sd,
            pregame_per_play=prior["rate"], plays_so_far=int(float(plays)),
            season_plays_per_min=season_ppm,
            sd_inflation=scfg.get("sd_inflation", SPORTS[engine]["sd_inflation"])))
        return {
            "proj": proj, "sd": live_sd, "current": current, "rate": prior["rate"],
            "rate_source": prior["rate_source"], "mean_source": prior["mean_source"],
            "pace_source": pace_source, "pace_value": float(plays),
        }, ""
    poss = player.get("team_possessions")
    if poss is None or float(poss) <= 0:
        return None, "missing_real_pace"
    if not prior.get("pregame_minutes"):
        return None, "missing_player_minutes"
    lead = score_home - score_away
    proj, live_sd = project_basketball_final(BasketballPaceInput(
        current=current, minutes_played=float(player.get("minutes") or 0),
        elapsed_min=elapsed, total_min=total,
        pregame_mean=prior["mean"], pregame_sd=sd,
        pregame_per_min=prior["rate"], pregame_minutes=prior["pregame_minutes"],
        fouls=int(player.get("fouls") or 0), lead=lead,
        season_poss_per_min=scfg.get("season_poss_per_min", 1.75),
        poss_so_far=int(float(poss)),
        sd_inflation=scfg.get("sd_inflation", SPORTS[engine]["sd_inflation"])))
    if float(player.get("minutes") or 0) <= 0:
        return None, "no_minutes"
    return {
        "proj": proj, "sd": live_sd, "current": current, "rate": prior["rate"],
        "rate_source": prior["rate_source"], "mean_source": prior["mean_source"],
        "pace_source": "observed_team_possessions", "pace_value": float(poss),
    }, ""


def _box_players(sport: str, boxscore: dict) -> dict[str, dict]:
    engine = ENGINE_SPORT.get(sport)
    if engine is None:
        return {}
    parsed = parse_summary({"boxscore": boxscore, "header": {"competitions": [{}]}}, engine)
    players = {}
    for name, row in (parsed.get("players") or {}).items():
        row = dict(row)
        row["name"] = name
        row["team_plays"] = _lookup_team_number(row.get("team"), parsed.get("team_plays_by_team") or {})
        row["team_possessions"] = _lookup_team_number(row.get("team"), parsed.get("team_poss_by_team") or {})
        players[norm_person(name)] = row
    return players


def _abstain(base: dict, group: dict, reason: str, kind) -> dict:
    return {
        **base,
        "abstained": True,
        "abstain_reason": reason,
        "recommended": False,
        "player": group["player"],
        "market": group["market"],
        "line": group["line"],
        "anchor_kind": kind,
        "quoted_before_start": kind == "quoted_pregame",
    }


def replay_observation(obs: dict, cfg: dict | None = None, priors_yaml: dict | None = None,
                       where: str = "observation") -> list[dict]:
    """Price one observation. Does not write, send, or read the network."""
    cfg = cfg if cfg is not None else load_config()
    priors_yaml = priors_yaml if priors_yaml is not None else {}
    payload = hash_payload(obs, where)
    aliases = _aliases(obs.get("aliases"), where)
    digest = input_hash(payload)
    table = _anchor_table(payload["anchors"])
    players = _box_players(payload["sport"], payload["boxscore"])
    base = _base(payload, obs, digest, aliases)
    engine = ENGINE_SPORT.get(payload["sport"])
    modeled = set(MARKETS.get(engine, []))
    rows = []
    abstentions = []
    elapsed = payload["elapsed_minutes"]
    for group in _raw_groups(payload["quoted_odds"]):
        who = norm_person(group["player"])
        market = group["market"]
        kind_value = table.get((who, market))
        kind = kind_value[0] if kind_value else None
        if "over" not in group or "under" not in group or group.get("over") is None or group.get("under") is None:
            abstentions.append(_abstain(base, group, "incomplete_quote", kind))
            continue
        if _quote_stale(payload["observed_at"], group.get("quoted_at")):
            abstentions.append(_abstain(base, group, "stale_quote", kind))
            continue
        if engine is None:
            abstentions.append(_abstain(base, group, "sport_not_modeled", kind))
            continue
        if market not in modeled:
            abstentions.append(_abstain(base, group, "market_not_modeled", kind))
            continue
        player = players.get(who)
        if player is None:
            abstentions.append(_abstain(base, group, "player_not_in_box", kind))
            continue
        if kind_value is None:
            abstentions.append(_abstain(base, group, "no_anchor", kind))
            continue
        details, why = _project(
            payload["sport"], market, player, kind_value[1], kind, elapsed,
            payload["score"]["home"], payload["score"]["away"], cfg, priors_yaml)
        if details is None:
            abstentions.append(_abstain(base, group, why, kind))
            continue
        try:
            over_price = int(group["over"])
            under_price = int(group["under"])
        except (TypeError, ValueError):
            abstentions.append(_abstain(base, group, "incomplete_quote", kind))
            continue
        fair_over, fair_under = remove_vig(american_to_implied(over_price), american_to_implied(under_price))
        best = None
        for side, price, fair in (("Over", over_price, fair_over), ("Under", under_price, fair_under)):
            model_p = _side_probability(market, details["proj"], details["sd"], group["line"], side)
            ev = evaluate_pick(model_p, price, side.lower())["ev"]
            ok, gate_why = passes_gates(model_p, fair, ev,
                                        min_edge_pp=cfg.get("min_edge_pp", 0.07),
                                        min_ev=cfg.get("min_ev", 0.05))
            row = {
                **base,
                "abstained": False,
                "abstain_reason": "",
                "player": group["player"],
                "market": market,
                "line": group["line"],
                "side": side,
                "price": price,
                "p": model_p,
                "edge": model_p - fair,
                "ev": ev,
                "fair_p": fair,
                "proj": details["proj"],
                "so_far": details["current"],
                "anchor": kind_value[1],
                "anchor_kind": kind,
                "quoted_before_start": kind == "quoted_pregame",
                "recommended": False,
                "reason": gate_why,
                "gate_pass": ok,
                "rate": details["rate"],
                "rate_source": details["rate_source"],
                "mean_source": details["mean_source"],
                "pace_source": details["pace_source"],
                "pace_value": details["pace_value"],
                "uncertainty": uncalibrated_interval(
                    model_p, _side_probability(market, details["proj"], details["sd"], group["line"], side, 1.5)),
            }
            if best is None or row["edge"] > best["edge"]:
                best = row
        scfg = cfg.get(engine, {}) or {}
        min_elapsed = scfg.get("min_elapsed_minutes", SPORTS[engine]["min_elapsed"])
        total = SPORTS[engine]["total_min"]
        if elapsed < min_elapsed:
            best["gate_pass"] = False
            best["reason"] = "too_early"
        elif total - elapsed <= cfg.get("no_pick_final_minutes", 2):
            best["gate_pass"] = False
            best["reason"] = "too_late"
        elif engine == "ncaaf" and elapsed > 30 and abs(payload["score"]["home"] - payload["score"]["away"]) >= scfg.get("blowout_lead_skip", 28):
            best["gate_pass"] = False
            best["reason"] = "blowout"
        elif engine in ("ncaab", "ncaaw") and int(player.get("fouls") or 0) >= 4:
            best["gate_pass"] = False
            best["reason"] = "foul_trouble_4"
        rows.append(best)
    rows.sort(key=lambda r: (str(r.get("player")), str(r.get("market")), str(r.get("side")), float(r.get("line") or 0)))
    abstentions.sort(key=lambda r: (r["player"], r["market"], r["line"], r["abstain_reason"]))
    return rows + abstentions


def recommendation_id(row: dict) -> str:
    return "|".join([
        row["cohort_key"], norm_person(row["player"]), row["market"],
        str(row.get("side", "")).lower(), f"{float(row['line']):.3f}",
    ])


def apply_recommendation_cap(rows: list[dict], already: set[str] | None = None, cap: int = 3) -> list[dict]:
    """Mark at most `cap` new recommendations per cohort. Same id is not recommended twice.

    `already` holds recommendation ids from other snapshots. Replaying this same
    output does not consume extra slots: ids already chosen inside this call are
    the ones written recommended.
    """
    already = set(already or ())
    by_cohort: dict[str, list[dict]] = {}
    for row in rows:
        if row.get("abstained") or not row.get("gate_pass"):
            row["recommended"] = False
            continue
        by_cohort.setdefault(row["cohort_key"], []).append(row)
    for cohort, group in by_cohort.items():
        group.sort(key=lambda r: (-float(r["edge"]), r["player"], r["market"], float(r["line"]), r["side"]))
        used = {rid for rid in already if rid.split("|", 1)[0] == cohort or rid.startswith(cohort + "|")}
        # recommendation ids start with the cohort key, which itself contains pipes.
        used = {rid for rid in already if rid.startswith(cohort + "|")}
        slots = max(0, cap - len(used))
        for row in group:
            rid = recommendation_id(row)
            if rid in used:
                row["recommended"] = False
                row["reason"] = "duplicate"
                continue
            if slots <= 0:
                row["recommended"] = False
                row["reason"] = "max_picks_per_game"
                continue
            row["recommended"] = True
            row["reason"] = row.get("reason") or ""
            used.add(rid)
            slots -= 1
    return rows


def _public_row(row: dict) -> dict:
    hidden = {"gate_pass"}
    return _round_row({key: value for key, value in row.items() if key not in hidden})


def load_snapshot(path: Path) -> dict:
    target = Path(path)
    if "muse_nfl_shadow" in str(target) or "marv-feed" in str(target):
        raise ReplayError("refusing muse_nfl_shadow input; that cron is not Muse Prop1")
    try:
        doc = json.loads(target.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ReplayError(f"cannot read snapshot: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA_IN:
        raise ReplayError(f"snapshot schema must be {SCHEMA_IN}")
    if not isinstance(doc.get("observations"), list):
        raise ReplayError("snapshot observations must be a list")
    return doc


def replay_rows(doc: dict, cfg: dict | None = None, priors_yaml: dict | None = None,
                already: set[str] | None = None, cap: int = 3,
                max_observations: int = 500) -> list[dict]:
    observations = doc["observations"]
    if len(observations) > max_observations:
        raise ReplayError(f"{len(observations)} observations exceeds the cap of {max_observations}")
    priced: list[dict] = []
    abstained: list[dict] = []
    # Stable order is file order. Cap counts earlier observations first.
    running = set(already or ())
    for i, obs in enumerate(observations):
        if not isinstance(obs, dict):
            raise ReplayError(f"observations[{i}] must be an object")
        produced = replay_observation(obs, cfg, priors_yaml, f"observations[{i}]")
        produced = apply_recommendation_cap(produced, running, cap)
        for row in produced:
            if row.get("recommended"):
                running.add(recommendation_id(row))
            if row.get("abstained"):
                abstained.append(row)
            else:
                priced.append(row)
    priced.sort(key=lambda r: (r["input_hash"], str(r.get("player")), str(r.get("market")), str(r.get("side")), float(r.get("line") or 0)))
    abstained.sort(key=lambda r: (r["input_hash"], r["player"], r["market"], r["line"], r["abstain_reason"]))
    return [_public_row(row) for row in priced + abstained]


def write_shadow(state_dir: Path, rows: list[dict]) -> Path:
    """Replace shadow.jsonl under state_dir. Creates no other ledger."""
    root = assert_replay_state(state_dir)
    dest_dir = root / "live_props"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "shadow.jsonl"
    text = "".join(canonical_dumps(row) + "\n" for row in rows)
    dest.write_text(text)
    return dest


def replay_file(snapshot: Path, state_dir: Path, cfg: dict | None = None,
                priors_yaml: dict | None = None, already: set[str] | None = None,
                cap: int = 3) -> tuple[int, int, int, Path]:
    """Validate, price, and replace the shadow file. Requires LIVE_PROPS_SEND off.

    Returns (observations, priced rows, abstentions, shadow path).
    """
    if not explicit_send_off():
        raise ReplayError("LIVE_PROPS_SEND must be explicitly off (unset is refused)")
    assert_replay_state(state_dir)
    doc = load_snapshot(snapshot)
    rows = replay_rows(doc, cfg, priors_yaml, already, cap)
    dest = write_shadow(state_dir, rows)
    priced = sum(not row["abstained"] for row in rows)
    return len(doc["observations"]), priced, len(rows) - priced, dest


def load_priors(path: Path | None = None) -> dict:
    target = path or (ROOT / "priors.yaml")
    if not target.exists():
        return {}
    return (yaml.safe_load(target.read_text()) or {}).get("priors", {}) or {}
