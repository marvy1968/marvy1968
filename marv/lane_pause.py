"""One pause predicate for actionable card paths.

Paused lanes stay out of bet-card selections, daily straight lists, and parlay pools.
They are kept as separate tracking rows. Which lanes are paused is unchanged:
nhl moneyline, nhl total, mlb total, cfb total, unless the existing switches say otherwise.

Switches (read on every call):
  MARV_LANE_PAUSE=off            or a file named MARV_LANE_PAUSE_OFF under the feed root
  MARV_LANE_PAUSE_LANES=...      comma list of sport:market
  MARV_LANE_PAUSE_OFF_<sport>_<market> file under the feed root turns one lane back on
  MARV_FEED_ROOT                 feed directory (default /opt/marv-feed). Tests only.

master_extreme legs are not paused. This module does not change prices, edges, or stakes.
"""
import json
import os
from pathlib import Path

CONTRACT = 1
KIND = "paused_track"
DEFAULT = "nhl:ml,nhl:total,mlb:total,cfb:total"
_OFFS = ("off", "0", "false", "no")
_TRACK = []


def feed_root() -> Path:
    return Path(os.environ.get("MARV_FEED_ROOT", "/opt/marv-feed"))


def on() -> bool:
    if os.environ.get("MARV_LANE_PAUSE", "on").strip().lower() in _OFFS:
        return False
    return not (feed_root() / "MARV_LANE_PAUSE_OFF").exists()


def lanes():
    if not on():
        return set()
    out = set()
    root = feed_root()
    for raw in os.environ.get("MARV_LANE_PAUSE_LANES", DEFAULT).split(","):
        token = raw.strip().lower()
        if ":" not in token:
            continue
        sport, market = token.split(":", 1)
        if not (root / f"MARV_LANE_PAUSE_OFF_{sport}_{market}").exists():
            out.add((sport, market))
    return out


def _pair(item):
    """(sport, market) or None when the item is not a pausable leg."""
    if isinstance(item, dict):
        if item.get("master_extreme"):
            return None
        sport, market = item.get("sport", ""), item.get("market", "")
    else:
        if getattr(item, "master_extreme", False):
            return None
        sport, market = getattr(item, "sport", ""), getattr(item, "market", "")
    return (str(sport).lower(), str(market).lower())


def paused(item, lane_set=None) -> bool:
    pair = _pair(item)
    if pair is None:
        return False
    return pair in (lanes() if lane_set is None else lane_set)


def partition(items, source="select"):
    """Actionable items in their original order, plus tracking notes for paused ones."""
    chosen = lanes()
    keep, tracked = [], []
    for item in items:
        if paused(item, chosen):
            tracked.append(item)
        else:
            keep.append(item)
    for item in tracked:
        _TRACK.append((source, item))
    return keep


def drain_tracking():
    out = list(_TRACK)
    _TRACK.clear()
    return out


def _rid(leg: dict) -> str:
    return f"{leg['game']}:{leg['market']}:{leg['side']}:{leg['line']}"


def split_card(card):
    """Move paused legs out of ml/ou/spread/parlays into track_paused. Idempotent.

    Turning the switch off puts those legs back on the card at view time.
    """
    if not isinstance(card, dict) or "ml" not in card:
        return card
    chosen = lanes()
    card = dict(card)
    parked = list(card.get("track_paused") or [])
    back = [dict(item) for item in parked if not paused(item, chosen)]
    for item in back:
        item.pop("lane_paused", None)
        key = "ou" if item.get("market") == "total" else ("spread" if item.get("market") == "spread" else "ml")
        card.setdefault(key, []).append(item)
    parked = [item for item in parked if paused(item, chosen)]
    have = {(item.get("sport"), item.get("game"), item.get("market"), item.get("side")) for item in parked}
    for key in ("ml", "ou", "spread", "track_ml_fav"):
        rows = card.get(key) or []
        for item in rows:
            ident = (item.get("sport"), item.get("game"), item.get("market"), item.get("side"))
            if paused(item, chosen) and ident not in have:
                parked.append(dict(item, lane_paused=True, tracking_only=True, track_gate="lane_pause"))
                have.add(ident)
        if key in card:
            card[key] = [item for item in rows if not paused(item, chosen)]
    card["track_paused"] = parked
    if isinstance(card.get("parlays"), dict):
        kept = {}
        for sport, rows in card["parlays"].items():
            rows = [parlay for parlay in (rows or []) if not any(paused(leg, chosen) for leg in parlay.get("legs") or [])]
            if rows:
                kept[sport] = rows
        card["parlays"] = kept
    if "sports_with_legs" in card:
        live = {item.get("sport") for key in ("ml", "ou", "spread") for item in card.get(key) or []}
        live |= set(card.get("parlays") or {})
        card["sports_with_legs"] = [sport for sport in card["sports_with_legs"] if sport in live]
    return card


def append_daily_tracking(book, have, card) -> int:
    """Append paused_track rows. Existing rows are left as they are. Not straight-record rows."""
    added = 0
    for leg in (card or {}).get("track_paused") or []:
        rid = _rid(leg)
        ident = (card["date"], KIND, leg["sport"], rid)
        if ident in have:
            continue
        book.append({
            "date": card["date"], "kind": KIND, "sport": leg["sport"], "id": rid, "price": leg["price"],
            "units": 0.5, "track_only": True, "track_gate": "lane_pause",
            "track_note": "lane paused 2026-11-01 (Marvin): off card, paper-tracked",
            "legs": [{key: leg.get(key) for key in (
                "sport", "game", "home", "away", "start", "market", "side", "line", "price", "tier", "league", "skey",
            )} | {"result": None}],
            "result": None, "profit": None,
        })
        have.add(ident)
        added += 1
    return added


def _jsonable(item):
    if isinstance(item, dict):
        return {key: item.get(key) for key in (
            "sport", "game", "home", "away", "start", "market", "side", "line", "price", "tier", "units", "why",
        )}
    return {key: getattr(item, key, None) for key in (
        "sport", "game", "home", "away", "start", "market", "side", "line", "price", "tier", "units", "why",
    )}


def persist_tracking(state, card_date: str) -> int:
    """Append-only bet-card tracking rows. Does not write the actionable betcard log."""
    rows = drain_tracking()
    if not rows:
        return 0
    path = Path(state) / "betcard_lane_pause.jsonl"
    path.parent.mkdir(parents=True, exist_ok=True)
    seen = set()
    if path.exists():
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            try:
                old = json.loads(line)
            except json.JSONDecodeError:
                continue
            seen.add(old.get("idempotency_key"))
    added = 0
    with path.open("a") as handle:
        for source, item in rows:
            body = _jsonable(item)
            key = "|".join(str(body.get(part)) for part in ("sport", "game", "market", "side", "line", "start"))
            token = f"{card_date}|{key}"
            if token in seen:
                continue
            record = {
                "card_date": card_date, "source": source, "track_only": True, "track_gate": "lane_pause",
                "idempotency_key": token, "leg": body,
            }
            handle.write(json.dumps(record, sort_keys=True) + "\n")
            seen.add(token)
            added += 1
    return added
