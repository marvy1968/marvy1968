"""Replay Claude Prop 1 on a frozen snapshot. No network, no Telegram, no orders.

The live service still polls ESPN and The Odds API and still charges state/live_props/credits.json.
This module does not. It prices marv.prop_snapshot.v1 observations through the existing scan_game
function and writes marv.prop_shadow.v1 rows to an isolated state directory.

Bounded on purpose: boxscore is the ESPN boxscore object parse_box already reads, and quoted_odds
is The Odds API event payload bovada_lines already reads. A different box schema would be a second
parser and is not implemented here. There is no grader.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from . import live_prop_engine as E

SCHEMA_IN = "marv.prop_snapshot.v1"
SCHEMA_OUT = "marv.prop_shadow.v1"
MODEL_VERSION = "claude-prop1"
SHARED_INSTALL = Path("/opt/marv-bot")
EXPLICIT_OFF = frozenset({"0", "off", "false", "no"})
ANCHOR_KINDS = frozenset({"quoted_pregame", "model_projection", "inferred"})
# Higher rank wins when two anchors name the same player and market.
# model_projection matches the live rule that Marv's projection overrides a captured quote.
_ANCHOR_RANK = {"inferred": 1, "quoted_pregame": 2, "model_projection": 3}


class ReplayError(Exception):
    """The snapshot cannot be replayed. Nothing was written."""


def explicit_send_off() -> bool:
    """True only for an explicit off token. Unset stays on, so it is not enough for replay."""
    raw = os.environ.get("LIVE_PROPS_SEND")
    if raw is None:
        return False
    return raw.strip().lower() in EXPLICIT_OFF


def team_key(name: str) -> str:
    """Stable team key: casefold, strip accents, punctuation to spaces."""
    text = unicodedata.normalize("NFKD", str(name))
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = text.lower().replace("&", " and ")
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return " ".join(text.split())


def utc_stamp(value: str, what: str) -> str:
    """Timezone-aware timestamp as UTC YYYY-MM-DDTHH:MM:SSZ with seconds."""
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ReplayError(f"{what} is not an ISO timestamp") from exc
    if parsed.tzinfo is None:
        raise ReplayError(f"{what} must include a timezone")
    parsed = parsed.astimezone(timezone.utc).replace(microsecond=0)
    return parsed.strftime("%Y-%m-%dT%H:%M:%SZ")


def cohort_key(sport: str, away: str, home: str, scheduled_start: str) -> str:
    """sport|away key|home key|scheduled start. Away then home, both normalized."""
    return "|".join([sport.strip().lower(), team_key(away), team_key(home), scheduled_start])


def canonical_dumps(payload: object) -> str:
    """UTF-8 JSON, object keys sorted, no extra whitespace. Integers stay integers."""
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def input_hash(payload: dict) -> str:
    digest = hashlib.sha256(canonical_dumps(payload).encode("utf-8")).hexdigest()
    return "sha256:" + digest


def assert_replay_state(state_dir: Path) -> Path:
    """Resolved writable root. Refuses the shared /opt/marv-bot tree and its parents."""
    state = Path(state_dir).expanduser().resolve()
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
    if sport not in E.GAME:
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
        "scheduled_start": utc_stamp(_require(obs, "scheduled_start", where), f"{where}.scheduled_start"),
        "observed_at": utc_stamp(_require(obs, "observed_at", where), f"{where}.observed_at"),
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
        key = (E._norm(row["player"]), row["market"])
        rank = _ANCHOR_RANK[row["kind"]]
        prev = chosen.get(key)
        if prev is None or rank >= prev[0]:
            chosen[key] = (rank, row["kind"], row["value"])
    return {key: (kind, value) for key, (_rank, kind, value) in chosen.items()}


def _raw_groups(quoted_odds: dict) -> list[dict]:
    """Two-sided and one-sided Bovada groups. Other books are ignored, matching bovada_lines."""
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


def _jsonable(value):
    if isinstance(value, bool) or value is None or isinstance(value, (str, int)):
        return value
    if isinstance(value, float):
        return round(float(value), 4)
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(v) for v in value]
    try:
        return round(float(value), 4)
    except (TypeError, ValueError):
        return str(value)


def _base(payload: dict, obs: dict, digest: str, aliases: dict[str, str]) -> dict:
    key = cohort_key(payload["sport"], payload["away"], payload["home"], payload["scheduled_start"])
    return {
        "schema": SCHEMA_OUT,
        "mode": "snapshot_replay",
        "model": MODEL_VERSION,
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
    }


def replay_observation(obs: dict, where: str = "observation") -> list[dict]:
    """Price one observation. Does not write, send, or read the network."""
    payload = hash_payload(obs, where)
    aliases = _aliases(obs.get("aliases"), where)
    digest = input_hash(payload)
    observed = datetime.fromisoformat(payload["observed_at"].replace("Z", "+00:00"))
    summary = {"boxscore": payload["boxscore"]}
    table = _anchor_table(payload["anchors"])
    anchor = {key: value for key, (_kind, value) in table.items()}
    priced, _recs = E.scan_game(
        payload["sport"], summary, payload["quoted_odds"], anchor, payload["elapsed_minutes"],
        payload["home"], payload["away"], payload["score"]["home"], payload["score"]["away"], now=observed)
    priced_keys = {(E._norm(row["player"]), row["market"], float(row["line"])) for row in priced}
    box_names = {E._norm(name) for name in E.parse_box(summary)["players"]}
    fresh = {(E._norm(row["player"]), row["market"], float(row["line"]))
             for row in E.bovada_lines(payload["quoted_odds"], now=observed)}
    base = _base(payload, obs, digest, aliases)
    rows = []
    for src in priced:
        kind, _value = table.get((E._norm(src["player"]), src["market"]), ("inferred", src.get("anchor")))
        rows.append({
            **base,
            "abstained": False,
            "abstain_reason": "",
            "anchor_kind": kind,
            "quoted_before_start": kind == "quoted_pregame",
            **_jsonable(src),
        })
    abstentions = []
    for group in _raw_groups(payload["quoted_odds"]):
        key = (E._norm(group["player"]), group["market"], float(group["line"]))
        if key in priced_keys:
            continue
        if "over" not in group or "under" not in group or group.get("over") is None or group.get("under") is None:
            reason = "incomplete_quote"
        elif key not in fresh:
            reason = "stale_quote"
        elif group["market"] not in E.specs(payload["sport"]):
            reason = "market_not_modeled"
        elif key[0] not in box_names:
            reason = "player_not_in_box"
        elif key[:2] not in table:
            reason = "no_anchor"
        else:
            reason = "not_priced"
        kind = table.get(key[:2], (None, None))[0]
        abstentions.append({
            **base,
            "abstained": True,
            "abstain_reason": reason,
            "player": group["player"],
            "market": group["market"],
            "line": group["line"],
            "anchor_kind": kind,
            "quoted_before_start": kind == "quoted_pregame",
        })
    abstentions.sort(key=lambda r: (r["player"], r["market"], r["line"], r["abstain_reason"]))
    rows.sort(key=lambda r: (str(r.get("player")), str(r.get("market")), str(r.get("side")), float(r.get("line") or 0)))
    return rows + abstentions


def load_snapshot(path: Path) -> dict:
    try:
        doc = json.loads(Path(path).read_text())
    except (OSError, json.JSONDecodeError) as exc:
        raise ReplayError(f"cannot read snapshot: {exc}") from exc
    if not isinstance(doc, dict) or doc.get("schema") != SCHEMA_IN:
        raise ReplayError(f"snapshot schema must be {SCHEMA_IN}")
    if not isinstance(doc.get("observations"), list):
        raise ReplayError("snapshot observations must be a list")
    return doc


def replay_rows(doc: dict) -> list[dict]:
    rows = []
    for i, obs in enumerate(doc["observations"]):
        if not isinstance(obs, dict):
            raise ReplayError(f"observations[{i}] must be an object")
        rows.extend(replay_observation(obs, f"observations[{i}]"))
    return rows


def write_shadow(state_dir: Path, rows: list[dict]) -> Path:
    """Replace shadow.jsonl under state_dir. Creates no other file."""
    root = assert_replay_state(state_dir)
    dest_dir = root / "live_props"
    dest_dir.mkdir(parents=True, exist_ok=True)
    dest = dest_dir / "shadow.jsonl"
    text = "".join(canonical_dumps(row) + "\n" for row in rows)
    dest.write_text(text)
    return dest


def replay_file(snapshot: Path, state_dir: Path) -> tuple[int, int, int, Path]:
    """Validate, price, and write. Refuses unless LIVE_PROPS_SEND is explicitly off.

    Returns (observations, priced rows, abstentions, shadow path).
    """
    if not explicit_send_off():
        raise ReplayError("LIVE_PROPS_SEND must be explicitly off (unset defaults to on and is refused)")
    # Resolve the state root before reading the snapshot so a bad directory fails closed.
    assert_replay_state(state_dir)
    doc = load_snapshot(snapshot)
    rows = replay_rows(doc)
    dest = write_shadow(state_dir, rows)
    priced = sum(not row["abstained"] for row in rows)
    return len(doc["observations"]), priced, len(rows) - priced, dest


def replay_cli(args) -> int:
    if not getattr(args, "snapshot", None) or not getattr(args, "state_dir", None):
        print("replay requires --snapshot and --state-dir")
        return 2
    try:
        n_obs, n_priced, n_abs, dest = replay_file(Path(args.snapshot), Path(args.state_dir))
    except ReplayError as exc:
        print(f"replay refused: {exc}")
        return 2
    print(f"replay schema={SCHEMA_OUT} model={MODEL_VERSION} observations={n_obs} "
          f"priced={n_priced} abstained={n_abs} shadow={dest}")
    return 0
