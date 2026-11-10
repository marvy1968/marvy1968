"""Tolerant JSONL parsers for pacer v1 candidate rows.

Missing fields become None. A bad line is skipped. Nothing here talks to a book.
"""

from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from pathlib import Path

STAT_ALIASES = {
    "pts": "pts", "point": "pts", "points": "pts", "player_points": "pts",
    "reb": "reb", "rebs": "reb", "rebound": "reb", "rebounds": "reb", "player_rebounds": "reb",
    "ast": "ast", "asts": "ast", "assist": "ast", "assists": "ast", "player_assists": "ast",
    "pra": "pra", "pts+reb+ast": "pra", "player_pra": "pra", "points_rebounds_assists": "pra",
    "3pm": "3pm", "3pt": "3pm", "3ptm": "3pm", "threes": "3pm", "three": "3pm",
    "player_threes": "3pm", "player_threes_made": "3pm",
    "py": "py", "pass_yds": "py", "pass_yards": "py", "passing_yards": "py", "player_pass_yds": "py",
    "ry": "ry", "rush_yds": "ry", "rush_yards": "ry", "rushing_yards": "ry", "player_rush_yds": "ry",
    "recy": "recy", "rec_yds": "recy", "rec_yards": "recy", "receiving_yards": "recy",
    "player_reception_yds": "recy", "player_receiving_yds": "recy",
    "rec": "rec", "recs": "rec", "reception": "rec", "receptions": "rec", "player_receptions": "rec",
    "td": "td", "tds": "td", "touchdown": "td", "touchdowns": "td",
    "player_pass_tds": "td", "player_anytime_td": "td",
}

FOOTBALL_STATS = frozenset({"py", "ry", "recy", "rec", "td"})
BASKETBALL_STATS = frozenset({"pts", "reb", "ast", "pra", "3pm"})

_NUM = re.compile(r"[-+]?\d+(?:\.\d+)?")
_WIN = frozenset({"won", "win", "w", "true", "yes", "1"})
_LOSS = frozenset({"lost", "loss", "lose", "l", "false", "no", "0"})
_PUSH = frozenset({"push", "tie", "void", "cancel", "cancelled", "canceled"})


@dataclass
class Row:
    raw: dict
    ts: str | None = None
    ts_epoch: float | None = None
    game_id: str | None = None
    player: str | None = None
    stat: str | None = None
    side: str | None = None
    line: float | None = None
    cur: float | None = None
    minutes_played: float | None = None
    period: int | None = None
    period_label: str | None = None
    clock_remaining_sec: float | None = None
    fouls: float | None = None
    margin: float | None = None
    season_rate: float | None = None
    rate_unit: str | None = None
    p_model_raw: float | None = None
    bid: float | None = None
    ask: float | None = None
    price: float | None = None
    result: bool | None = None
    push: bool = False
    pnl: float | None = None
    b2b: bool | None = None
    opponent: str | None = None
    team: str | None = None
    league: str | None = None
    role: str | None = None
    foul_limit: int | None = None
    regulation_minutes: float | None = None
    p_old_field: float | None = None
    source: str | None = None
    gaps: list[str] = field(default_factory=list)


def read_jsonl(paths: list[str | Path]) -> list[tuple[dict, str]]:
    """Return (object, origin) for every valid line. Bad lines are reported on stderr."""
    import sys
    out: list[tuple[dict, str]] = []
    for path in paths:
        p = Path(path)
        try:
            text = p.read_text()
        except OSError as exc:
            print(f"pacer_v2: cannot read {p}: {exc}", file=sys.stderr)
            continue
        for i, line in enumerate(text.splitlines(), start=1):
            s = line.strip()
            if not s or s.startswith("#"):
                continue
            try:
                obj = json.loads(s)
            except json.JSONDecodeError as exc:
                print(f"pacer_v2: skip bad json {p}:{i}: {exc.msg}", file=sys.stderr)
                continue
            if not isinstance(obj, dict):
                print(f"pacer_v2: skip non-object {p}:{i}", file=sys.stderr)
                continue
            out.append((obj, f"{p}:{i}"))
    return out


def read_rows(paths: list[str | Path]) -> list[Row]:
    return [parse_row(obj, source=origin) for obj, origin in read_jsonl(paths)]


def parse_row(obj: dict, source: str | None = None) -> Row:
    gaps: list[str] = []
    ts = _first_str(obj, "ts", "timestamp", "time", "at")
    row = Row(
        raw=obj,
        ts=ts,
        ts_epoch=_parse_ts(ts),
        game_id=_first_str(obj, "game_id", "game", "event_id", "gid"),
        player=_first_str(obj, "player", "name", "athlete"),
        stat=_parse_stat(obj.get("stat", obj.get("market", obj.get("prop")))),
        side=_parse_side(obj.get("side", obj.get("selection", obj.get("bet")))),
        line=_first_float(obj, "line", "point", "total"),
        cur=_first_float(obj, "cur", "current", "so_far", "stat_so_far", "value"),
        minutes_played=_first_float(obj, "minutes_played", "minutes", "min", "mp"),
        fouls=_first_float(obj, "fouls", "pf", "personal_fouls"),
        margin=_first_float(obj, "margin", "score_margin", "team_margin"),
        season_rate=_first_float(obj, "season_rate", "rate", "per_min", "season_per_min"),
        rate_unit=_first_str(obj, "rate_unit"),
        p_model_raw=_first_prob(obj, "p_model_raw", "p_raw", "p_v1", "pacer_p"),
        bid=_first_prob(obj, "bid"),
        ask=_first_prob(obj, "ask"),
        price=_first_prob(obj, "price", "cost", "implied"),
        pnl=_first_float(obj, "pnl", "profit"),
        opponent=_first_str(obj, "opponent", "opp", "defense_team", "opp_team"),
        team=_first_str(obj, "team", "player_team"),
        league=_first_str(obj, "league", "sport", "competition"),
        role=_norm_role(obj.get("role")),
        foul_limit=_first_int(obj, "foul_limit"),
        regulation_minutes=_first_float(obj, "regulation_minutes", "game_minutes"),
        p_old_field=_first_prob(obj, "p_old", "p_model_old", "old_p", "p_baseline", "model_old"),
        source=source,
        gaps=gaps,
    )
    row.period, row.period_label = _parse_period(obj.get("period", obj.get("quarter", obj.get("q"))))
    row.clock_remaining_sec = _parse_clock(obj.get("clock", obj.get("clock_remaining", obj.get("time_remaining"))))
    if obj.get("clock") not in (None, "") and row.clock_remaining_sec is None:
        gaps.append("clock_unparsed")
    row.b2b = _parse_b2b(obj)
    row.result, row.push = _parse_result(obj)
    if row.stat is None and obj.get("stat") not in (None, ""):
        gaps.append("stat_unrecognized")
    if row.side is None and obj.get("side") not in (None, ""):
        gaps.append("side_unrecognized")
    return row


def write_jsonl(path: str | Path, rows: list[dict]) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    payload = "".join(json.dumps(r, sort_keys=True, separators=(",", ":")) + "\n" for r in rows)
    tmp.write_text(payload)
    tmp.replace(path)


def write_json(path: str | Path, obj: dict) -> None:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n")
    tmp.replace(path)


def load_json(path: str | Path) -> dict:
    data = json.loads(Path(path).read_text())
    if not isinstance(data, dict):
        raise ValueError(f"{path} is not a JSON object")
    return data


def _first_str(obj: dict, *keys: str) -> str | None:
    for k in keys:
        if k not in obj or obj[k] is None:
            continue
        s = str(obj[k]).strip()
        if s:
            return s
    return None


def _first_float(obj: dict, *keys: str) -> float | None:
    for k in keys:
        if k not in obj:
            continue
        v = _as_float(obj[k])
        if v is not None:
            return v
    return None


def _first_int(obj: dict, *keys: str) -> int | None:
    v = _first_float(obj, *keys)
    if v is None:
        return None
    return int(v)


def _first_prob(obj: dict, *keys: str) -> float | None:
    for k in keys:
        if k not in obj:
            continue
        v = _as_prob(obj[k])
        if v is not None:
            return v
    return None


def _as_float(v) -> float | None:
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return None
        return x
    s = str(v).strip().replace(",", "")
    if not s:
        return None
    m = _NUM.search(s)
    if not m:
        return None
    try:
        return float(m.group())
    except ValueError:
        return None


def _as_prob(v) -> float | None:
    """Probability in (0, 1], or American odds (<= -100 or >= 100)."""
    if isinstance(v, bool) or v is None:
        return None
    if isinstance(v, str):
        s = v.strip()
        if not s:
            return None
        try:
            x = float(s.replace(",", ""))
        except ValueError:
            return None
    elif isinstance(v, (int, float)):
        x = float(v)
        if math.isnan(x) or math.isinf(x):
            return None
    else:
        return None
    if x <= -100 or x >= 100:
        if x < 0:
            p = (-x) / ((-x) + 100.0)
        else:
            p = 100.0 / (x + 100.0)
        return p
    if 0.0 < x <= 1.0:
        return x
    return None


def _parse_stat(v) -> str | None:
    if v is None:
        return None
    key = str(v).strip().lower().replace(" ", "_").replace("-", "_")
    return STAT_ALIASES.get(key)


def _parse_side(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in {"over", "o", "yes", "over_yes"}:
        return "over"
    if s in {"under", "u", "no", "under_yes"}:
        return "under"
    return None


def _parse_period(v) -> tuple[int | None, str | None]:
    if v is None or v == "":
        return None, None
    if isinstance(v, bool):
        return None, None
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        n = int(v)
        return (n if n > 0 else None), str(n)
    s = str(v).strip().lower()
    if s in {"ot", "overtime"} or s.startswith("ot"):
        return None, "ot"
    m = re.search(r"\d+", s)
    if not m:
        return None, s
    n = int(m.group())
    return (n if n > 0 else None), s


def _parse_clock(v) -> float | None:
    """Seconds remaining in the current period.

    Numeric values under 20 are minutes (6.5 -> 390). Numeric values of 20 or more
    are already seconds (a full quarter is 720 or 900; 12.0 would be ambiguous and
    is treated as minutes). Strings use m:ss or h:mm:ss, ignoring a leading 'Q4'.
    """
    if v is None or isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        x = float(v)
        if math.isnan(x) or x < 0:
            return None
        if x >= 20:
            return x
        return x * 60.0
    s = str(v).strip().lower()
    if not s:
        return None
    # Drop a leading quarter / half token so "Q4 2:00" is 2:00, not 4:02:00.
    s = re.sub(r"\b(?:q|quarter|period|half|h|ot)\s*\d+\b", " ", s)
    if ":" in s:
        parts = re.findall(r"\d+", s)
        if s.count(":") >= 2 and len(parts) >= 3:
            # h:mm:ss. A single colon with a fractional second stays m:ss.
            h, m, sec = (int(parts[-3]), int(parts[-2]), int(parts[-1]))
            return h * 3600 + m * 60 + sec
        if len(parts) >= 2:
            return int(parts[0]) * 60 + int(parts[1])
    m = _NUM.search(s)
    if not m:
        return None
    return _parse_clock(float(m.group()))


def _parse_b2b(obj: dict) -> bool | None:
    for k in ("b2b", "back_to_back", "is_b2b", "back2back"):
        if k not in obj or obj[k] is None:
            continue
        v = obj[k]
        if isinstance(v, bool):
            return v
        if isinstance(v, (int, float)):
            return bool(v)
        s = str(v).strip().lower()
        if s in {"1", "true", "yes", "y"}:
            return True
        if s in {"0", "false", "no", "n"}:
            return False
    return None


def _parse_result(obj: dict) -> tuple[bool | None, bool]:
    for k in ("result", "outcome", "won", "win"):
        if k not in obj or obj[k] is None:
            continue
        v = obj[k]
        if isinstance(v, bool):
            return v, False
        if isinstance(v, (int, float)) and not isinstance(v, bool) and v in (0, 1):
            return bool(int(v)), False
        if isinstance(v, str):
            s = v.strip().lower()
            if s in _PUSH:
                return None, True
            if s in _WIN:
                return True, False
            if s in _LOSS:
                return False, False
        return None, False
    return None, False


def _parse_ts(ts: str | None) -> float | None:
    if ts is None:
        return None
    s = ts.strip()
    if not s:
        return None
    if re.fullmatch(r"-?\d+(\.\d+)?", s):
        x = float(s)
        if x > 1e12:  # epoch milliseconds
            x /= 1000.0
        return x
    from datetime import datetime
    text = s.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    if dt.tzinfo is None:
        from datetime import timezone
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.timestamp()


def _norm_role(v) -> str | None:
    if v is None:
        return None
    s = str(v).strip().lower()
    if s in {"backup", "reserve", "bench"}:
        return "backup"
    if s in {"starter", "star", "primary"}:
        return "starter"
    return s or None
