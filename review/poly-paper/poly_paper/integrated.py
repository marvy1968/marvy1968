"""Run the exported runner and pm_fill unchanged, and gate only their fill inserts.

This is the runtime-integrated path. `writers.insert` is a boundary library: it
does not apply stake, edge, daily cap, slug dedupe, or the runner's card
exceptions. Those rules stay in the exported functions. The only new reject is
an identity failure (or a fill that arrives with no captured entry/snapshot).

Known writer connections in the screened tree
----------------------------------------------
- runner.db() -> runner.main inserts into fills, then updates lane/bovada columns.
- runner.db() -> pm_fill.fill inserts into fills. Every other paper caller
  (pm_nba_live, pm_me_track, marv_cbb, live_props.poly_fill) reaches that insert
  and no other one.
- grader.db() updates settlement columns. It is not an insert path. This module
  does not call it and does not open the host database.

No host migration is authorized. A legacy fills file is not opened for writing.
"""
from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import shutil
import sqlite3
import sys
import types
from contextlib import contextmanager
from pathlib import Path

from .identity import assess

_BASE = Path(__file__).resolve().parents[1] / "provenance" / "baseline"
_PAPER = _BASE / "polymarket-paper"
_LIVE = _BASE / "marv-feed" / "live_props" / "live_props.py"
_SUMS = Path(__file__).resolve().parents[1] / "provenance" / "SHA256SUMS"

# pm_nba_live and live_props reach pm_fill.fill. They can pass only when the
# bodies those callers already fetch contain display names, a scheduled start,
# and a Polymarket US event with an explicit league. The November 7
# nba_prop_live rows do not include that US payload. A Gamma 404 is not a
# wrong-game finding and is not an observed success.
ADAPTER_GAPS = {
    "pm_nba_live.main": (
        "Accepts only from already-fetched ESPN display names, a scheduled date, "
        "a player on a team display name, and a Polymarket US event with explicit "
        "league, team names, and startTime. The November 7 rows lack that US "
        "payload. Gamma HTTP 404 is not a wrong-game finding."
    ),
    "live_props.poly_fill": (
        "Same evidence rule. A body that only has abbreviations and a slug date "
        "stays an identity rejection."
    ),
}

WRITER_CONNECTIONS = (
    {
        "module": "runner.py",
        "owner": "runner.db",
        "statement": "insert or ignore into fills",
        "reached_by": ("runner.main",),
    },
    {
        "module": "pm_fill.py",
        "owner": "runner.db",
        "statement": "insert or ignore into fills",
        "reached_by": (
            "pm_fill.fill",
            "pm_nba_live.main",
            "pm_me_track.main",
            "pm_me_track.marv_cbb",
            "live_props.poly_fill",
        ),
    },
    {
        "module": "grader.py",
        "owner": "grader.db",
        "statement": "update fills set status='settled'",
        "reached_by": ("grader.settle_open",),
        "insert": False,
    },
)

_INSERT = "insert"
_LANE_UPDATE = "lane_update"


def _sha_ok():
    expected = {}
    for line in _SUMS.read_text().splitlines():
        if not line.strip():
            continue
        digest, rel = line.split("  ", 1)
        expected[rel] = digest
    needed = [
        "polymarket-paper/runner.py",
        "polymarket-paper/pm_fill.py",
        "polymarket-paper/pm_type_gate.py",
        "polymarket-paper/mapper.py",
        "polymarket-paper/pm_client.py",
        "polymarket-paper/pm_nba_live.py",
        "polymarket-paper/pm_me_track.py",
        "marv-feed/live_props/live_props.py",
    ]
    for rel in needed:
        path = _BASE / rel
        found = hashlib.sha256(path.read_bytes()).hexdigest()
        if found != expected[rel]:
            raise RuntimeError(f"baseline hash changed for {rel}")


def _strip(sql: str) -> str:
    import re
    text = re.sub(r"/\*.*?\*/", " ", sql, flags=re.S)
    text = re.sub(r"--[^\n]*", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def _fill_insert(sql: str) -> bool:
    text = _strip(sql)
    return text.startswith("insert") and "into fills" in text


def _lane_update(sql: str) -> bool:
    text = _strip(sql)
    if not text.startswith("update fills set"):
        return False
    body = text.split(" set ", 1)[1].split(" where ", 1)[0]
    columns = [part.split("=", 1)[0].strip() for part in body.split(",")]
    return set(columns) <= {"lane", "bovada_price", "bovada_line", "bovada_ts"}


class _Cursor:
    def __init__(self, rowcount=0, lastrowid=None):
        self.rowcount = rowcount
        self.lastrowid = lastrowid


class _Guarded:
    """The connection object runner.db() returns to the real writers."""

    def __init__(self, raw, gate):
        self._raw = raw
        self._gate = gate

    def execute(self, sql, params=()):
        if _fill_insert(sql):
            return self._gate.insert(self._raw, sql, params)
        if _strip(sql).startswith("update fills") or _strip(sql).startswith("delete from fills"):
            if _lane_update(sql):
                return self._raw.execute(sql, params)
            raise PermissionError("fill identity columns cannot be rewritten by the writer connection")
        return self._raw.execute(sql, params)

    def commit(self):
        self._raw.commit()

    def close(self):
        self._raw.close()

    def __getattr__(self, name):
        return getattr(self._raw, name)


class _Gate:
    def __init__(self, identity_gate: bool, workspace: Path):
        self.identity_gate = identity_gate
        self.workspace = workspace
        self.captures = []
        self.decisions = []
        self.pending_writer = "runner"
        self.pending_lane = None
        self.failpoint = None
        from .live_identity import LiveEvidence
        self.live = LiveEvidence()

    def remember(self, entry, snapshot, mapped):
        self.captures.append({"entry": dict(entry), "snapshot": snapshot, "mapped": mapped})

    def _match(self, key, slug):
        for item in reversed(self.captures):
            mapped = item.get("mapped") or {}
            entry = item["entry"]
            if key and (entry.get("key") == key or f"me:{entry.get('me_alert_id')}" == key or str(entry.get("key")) == str(key).removeprefix("marvcbb:")):
                return item
            if slug and mapped.get("slug") == slug:
                return item
        return None

    def insert(self, raw, sql, params):
        params = tuple(params)
        # Both writers bind key, pick, slug, side in that order after ts, date.
        key, pick, slug, side = params[2], params[3], params[4], params[5]
        found = self._match(key, slug)
        if not self.identity_gate:
            cur = raw.execute(sql, params)
            self.decisions.append({"accepted": bool(cur.rowcount), "reasons": ["writer_insert"], "fill_id": cur.lastrowid, "public_reason": None})
            return cur
        from .policy import decide_fill
        entry = None if found is None else found["entry"]
        snapshot = None if found is None else found.get("snapshot")
        if entry is None:
            live_hit = self.live.build(key=key, pick=pick, side=side, slug=slug, lane_switch=self.pending_lane)
            if live_hit is not None:
                entry = live_hit["entry"]
                snapshot = live_hit["snapshot"]
        decided = decide_fill(
            entry=entry,
            snapshot=snapshot,
            claimed_slug=slug,
            claimed_side=side,
            claimed_token=None,
            price=params[7],
            writer=self.pending_writer,
            lane_switch=self.pending_lane,
            policy_dir=self.workspace,
            switch_dir=self.workspace / "data",
            env=dict(os.environ),
            extra_dirs=(self.workspace,),
        )
        identity = decided["identity"]
        if found and found.get("mapped") and found["mapped"].get("slug") not in (None, slug):
            if "slug_disagrees" not in decided["reasons"]:
                decided["reasons"].append("slug_disagrees")
            if decided["action"] == "accept":
                decided["action"] = "track_reject"
        if decided["action"] != "accept":
            self._audit(raw, decided, key, pick, slug, side, params, None)
            public = _public_reason(decided)
            self.decisions.append({"accepted": False, "reasons": list(decided["reasons"]), "fill_id": None, "public_reason": public, "token_verified": False})
            return _Cursor(0, None)
        cur = raw.execute(sql, params)
        if self.failpoint == "after_fill":
            raise RuntimeError("injected writer failure")
        if cur.rowcount:
            self._audit(raw, decided, key, pick, slug, side, params, cur.lastrowid)
            self.decisions.append({
                "accepted": True,
                "reasons": list(decided["reasons"]),
                "fill_id": cur.lastrowid,
                "public_reason": None,
                "token_verified": bool(identity.outcome and identity.outcome.get("token_verified")),
            })
        else:
            duplicate = dict(decided, action="duplicate_key", reasons=["duplicate_key"])
            self._audit(raw, duplicate, key, pick, slug, side, params, None)
            self.decisions.append({"accepted": False, "reasons": ["duplicate_key"], "fill_id": None, "public_reason": None, "token_verified": False})
        return cur

    def _audit(self, raw, decided, key, pick, slug, side, params, fill_id):
        raw.execute(
            """create table if not exists fill_audit (
                id integer primary key,
                action text not null,
                reasons_json text not null,
                fill_id integer,
                fill_key text,
                pick text,
                slug text,
                side text,
                outcome_json text,
                participants_json text,
                token_verified integer,
                input_json text,
                mapped_event_id text,
                effective_policy_json text,
                policy_sha256 text
            )"""
        )
        identity = decided.get("identity")
        outcome = None if identity is None or not getattr(identity, "outcome", None) else identity.outcome
        participants = None if identity is None else identity.participants
        effective = decided.get("effective")
        payload = {
            "key": key, "pick": pick, "slug": slug, "side": side,
            "contracts": params[6], "price": params[7], "fee": params[8], "cost": params[9],
            "mapped_event_id": None if identity is None else identity.event_id,
            "mapped_event_slug": None if identity is None else identity.event_slug,
        }
        from .canonical import sha256_obj
        raw.execute(
            """insert into fill_audit(action, reasons_json, fill_id, fill_key, pick, slug, side,
               outcome_json, participants_json, token_verified, input_json, mapped_event_id,
               effective_policy_json, policy_sha256)
               values (?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                decided.get("action") or "track_reject",
                json.dumps(list(decided.get("reasons") or [])),
                fill_id,
                key,
                pick,
                slug,
                side,
                None if outcome is None else json.dumps(outcome),
                None if participants is None else json.dumps(participants),
                1 if outcome and outcome.get("token_verified") else 0,
                json.dumps(payload, default=str),
                None if identity is None else identity.event_id,
                None if effective is None else json.dumps(effective, sort_keys=True),
                None if effective is None else sha256_obj(effective),
            ),
        )


def _public_reason(decided):
    reasons = [code for code in decided.get("reasons") or [] if code not in {"trade_all", "allowlist", "nfl_dog", "cfb_dog"}]
    code = (reasons or decided.get("reasons") or ["denied"])[0]
    prefix = "policy" if decided.get("action") == "fail_closed" or code in {
        "force_track", "nba_hold", "card_rule", "cfb_fav_track", "not_on_allowlist", "pin_track",
        "never_trade", "lane_off", "trade_all_off", "hard_kill", "policy_missing", "policy_malformed",
        "unapproved_policy_conflict", "paper_mode_violation",
    } else "identity"
    return f"{prefix}:{code}"


def assess_missing():
    from .identity import IdentityResult
    return IdentityResult(ok=False, reasons=["identity_context_missing"], notes=["token_not_verified"])


class Session:
    def __init__(self, workspace: Path, identity_gate: bool):
        self.workspace = workspace
        self.gate = _Gate(identity_gate, workspace)
        self.runner = None
        self.pm_fill = None
        self._cleanup = []

    def fills(self):
        if not os.path.exists(self.runner.DB):
            return []
        con = sqlite3.connect(self.runner.DB)
        try:
            con.row_factory = sqlite3.Row
            names = {row[0] for row in con.execute("select name from sqlite_master")}
            if "fills" not in names:
                return []
            return [dict(row) for row in con.execute("select * from fills order by id")]
        finally:
            con.close()

    def audits(self):
        con = sqlite3.connect(self.runner.DB)
        try:
            con.row_factory = sqlite3.Row
            names = {row[0] for row in con.execute("select name from sqlite_master")}
            if "fill_audit" not in names:
                return []
            return [dict(row) for row in con.execute("select * from fill_audit order by id")]
        finally:
            con.close()


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@contextmanager
def writer_session(tmp, *, config=None, type_gate=None, env=None, identity_gate=True, entries=None, calib_mock=None):
    """Load byte-identical baseline writer modules from a temp copy. Restores process state."""
    _sha_ok()
    workspace = Path(tmp) / "polymarket-paper"
    if workspace.exists():
        shutil.rmtree(workspace)
    workspace.mkdir(parents=True)
    for name in ("runner.py", "pm_fill.py", "pm_type_gate.py", "mapper.py", "pm_client.py",
                 "pm_nba_live.py", "pm_me_track.py"):
        shutil.copy(_PAPER / name, workspace / name)
    (workspace / "data").mkdir()
    props = {"built": "2030-11-07T00:00:00+00:00", "entries": [], "stats": {}}
    # Fresh enough that refresh_pm_props does not spawn a process.
    (workspace / "data" / "pm_props.json").write_text(json.dumps(props))
    os.utime(workspace / "data" / "pm_props.json", None)
    cfg = {
        "PAPER_ONLY": True,
        "PREPROD_ENABLED": False,
        "min_edge_cents": 3,
        "max_stake_usd": 50,
        "max_fills_per_run": 5,
        "source": "marv",
        "max_daily_cost_usd": 2000,
        "max_daily_live_props_usd": 1500,
    }
    if config:
        cfg.update(config)
    (workspace / "config.json").write_text(json.dumps(cfg))
    gate_doc = {
        "enabled": True,
        "trade": ["ml:cfb"],
        "never_trade": ["h2h_cfb"],
        "pin_track": ["ml:nfl"],
        "force_track": ["prop:nfl:pre"],
    }
    if type_gate:
        gate_doc.update(type_gate)
    (workspace / "type_gate.json").write_text(json.dumps(gate_doc))
    shutil.copy(Path(__file__).resolve().parent / "effective_policy.json", workspace / "effective_policy.json")

    saved_path = list(sys.path)
    saved_env = os.environ.copy()
    saved_argv = list(sys.argv)
    saved_modules = {name: sys.modules.get(name) for name in (
        "runner", "pm_fill", "pm_type_gate", "mapper", "pm_client", "pick_feed", "bovada_px",
        "pm_nba_live", "pm_me_track", "live_props", "calib_size",
    )}
    popped = object()
    session = Session(workspace, identity_gate)
    try:
        os.environ["CALIB_SIZING"] = os.environ.get("CALIB_SIZING", "on")
        if env:
            os.environ.update({k: str(v) for k, v in env.items()})
        sys.path.insert(0, str(workspace))
        pick = types.ModuleType("pick_feed")
        pick.entries = lambda: list(entries or [])
        sys.modules["pick_feed"] = pick
        bovada = types.ModuleType("bovada_px")
        bovada.lookup = lambda *args, **kwargs: {}
        sys.modules["bovada_px"] = bovada
        runner = _load(workspace / "runner.py", "runner")
        real_db = runner.db
        owned = set()

        def db():
            path = os.path.realpath(runner.DB)
            if path not in owned and os.path.exists(path) and _legacy_writer_file(path):
                raise RuntimeError("refusing to write a legacy ledger; no host migration is authorized")
            con = _Guarded(real_db(), session.gate)
            owned.add(path)
            return con

        runner.db = db
        holder = {"fn": runner.map_entry}

        def map_entry(entry, client):
            mapped = holder["fn"](entry, client)
            snapshot = getattr(client, "snapshot", None)
            session.gate.remember(entry, snapshot if snapshot is not None else {"events": []}, mapped)
            return mapped

        runner.map_entry = map_entry
        import mapper as _mapper
        _mapper.map_entry = map_entry
        session.map_holder = holder
        session.runner = runner
        session.pm_fill = _load(workspace / "pm_fill.py", "pm_fill")
        real_fill = session.pm_fill.fill

        def fill(name, rec, lane, *args, **kwargs):
            session.gate.pending_writer = "pm_fill"
            session.gate.pending_lane = name
            start = len(session.gate.decisions)
            try:
                ok, why = real_fill(name, rec, lane, *args, **kwargs)
            finally:
                session.gate.pending_writer = "runner"
                session.gate.pending_lane = None
            fresh = session.gate.decisions[start:]
            if fresh and not fresh[-1]["accepted"] and why == "dup key" and fresh[-1].get("public_reason"):
                return False, fresh[-1]["public_reason"]
            return ok, why

        session.pm_fill.fill = fill
        session.pick_feed = pick
        if calib_mock is not None:
            mocked = types.ModuleType("calib_size")
            mocked.MOCK = True
            mocked.size = calib_mock
            sys.modules["calib_size"] = mocked
        import subprocess
        real_popen = subprocess.Popen

        def _no_popen(*args, **kwargs):
            raise AssertionError("exported writer attempted to spawn a process")

        subprocess.Popen = _no_popen
        try:
            yield session
        finally:
            subprocess.Popen = real_popen
    finally:
        os.environ.clear()
        os.environ.update(saved_env)
        sys.path[:] = saved_path
        sys.argv[:] = saved_argv
        for name, module in saved_modules.items():
            if module is None:
                sys.modules.pop(name, None)
            else:
                sys.modules[name] = module


def _legacy_writer_file(path: str) -> bool:
    """A fills database that this session did not create and that has no audit table."""
    con = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
    try:
        names = {row[0] for row in con.execute("select name from sqlite_master where type='table'")}
    finally:
        con.close()
    return "fills" in names and "fill_audit" not in names and "review_meta" not in names


def arm_http(session, module):
    """Record ESPN bodies the caller's own get() already returns. Does not fetch."""
    original = module.get

    def wrapped(url):
        payload = original(url)
        session.gate.live.note_http(url, payload)
        return payload

    module.get = wrapped
    return module


def install_client(session, events, bbo):
    client = _Client(events, bbo)

    class _Public:
        def __init__(self):
            pass

        def league_events(self, league):
            events = client.league_events(league)
            for event in events or []:
                session.gate.live.record_pm_event(event)
            return events

        def bbo(self, slug):
            return client.bbo(slug)

        def event(self, slug):
            event = client.event(slug)
            if event:
                session.gate.live.record_pm_event(event)
            return event

    _Public.snapshot = client.snapshot
    session.runner.PMPublic = _Public
    if "pm_client" in sys.modules:
        sys.modules["pm_client"].PMPublic = _Public
    return client


class _Client:
    def __init__(self, events, bbo):
        self.snapshot = {"events": events}
        self._events = events
        self._bbo = bbo or {
            "state": "MARKET_STATE_OPEN",
            "bestAsk": {"value": "0.40"},
            "bestBid": {"value": "0.38"},
        }

    def league_events(self, league):
        return self._events

    def event(self, slug):
        for event in self._events:
            if event.get("slug") == slug:
                return event
        return None

    def bbo(self, slug):
        if isinstance(self._bbo, dict) and slug in self._bbo and isinstance(self._bbo[slug], dict) and "state" in self._bbo[slug]:
            return self._bbo[slug]
        return self._bbo


def run_runner(session, entries, events, bbo=None):
    session.pick_feed.entries = lambda: list(entries)
    install_client(session, events, bbo)
    session.runner.main()


def run_pm_fill(session, name, rec, lane, entry, events, bbo=None, **kwargs):
    """Real pm_fill.fill. Maps with the real mapper first when entry is provided."""
    client = install_client(session, events, bbo)
    if entry is not None:
        session.runner.map_entry(entry, client)
    return session.pm_fill.fill(name, rec, lane, **kwargs)


def run_me_track(session, entries, events, bbo=None):
    install_client(session, events, bbo)
    session.runner.me_entries = lambda: (list(entries), [])
    module = _load(session.workspace / "pm_me_track.py", "pm_me_track")
    module.PMPublic = sys.modules["pm_client"].PMPublic
    module.main()
    return module


def run_marv_cbb(session, entries, events, bbo=None):
    install_client(session, events, bbo)
    board = session.workspace / "data" / "board_cbb.json"
    board.write_text(json.dumps({"entries": entries}))
    os.environ["MARV_CBB_BOARDS"] = str(board)
    module = sys.modules.get("pm_me_track") or _load(session.workspace / "pm_me_track.py", "pm_me_track")
    module.PMPublic = sys.modules["pm_client"].PMPublic
    module.marv_cbb()
    return module


def settle_linked(session, fill_id, settlement):
    """Settle one writer fill from its audit identity. Does not rewrite the game.

    Adds the settlement columns the exported grader already adds, and only on a
    database this session created (it has fill_audit). A legacy file is not settled.
    """
    from .identity import _team_matches
    from .provider import parse_team

    path = session.runner.DB
    if _legacy_writer_file(path):
        raise RuntimeError("refusing to settle a legacy ledger; no history rewrite is authorized")
    con = sqlite3.connect(path)
    con.row_factory = sqlite3.Row
    try:
        cols = {row[1] for row in con.execute("pragma table_info(fills)")}
        for name, typ in (("result", "text"), ("payout", "real"), ("settle_px", "real"), ("settled_ts", "text"), ("settle_src", "text")):
            if name not in cols:
                con.execute(f"alter table fills add column {name} {typ}")
        fill = con.execute("select * from fills where id=?", (fill_id,)).fetchone()
        audit = con.execute(
            "select * from fill_audit where fill_id=? and action='accept' order by id desc",
            (fill_id,),
        ).fetchone()
        if fill is None or audit is None:
            return {"accepted": False, "reasons": ["audit_link_missing"]}
        if fill["status"] != "open":
            return {"accepted": False, "reasons": ["already_settled"]}
        outcome = json.loads(audit["outcome_json"])
        participants = json.loads(audit["participants_json"])
        reasons = []
        if settlement.get("market_slug") != fill["slug"]:
            reasons.append("settlement_event_disagrees")
        supplied = settlement.get("participants")
        teams = [parse_team(item) if isinstance(item, dict) else None for item in supplied or []]
        if not supplied or any(team is None for team in teams):
            reasons.append("settlement_participants_missing")
        else:
            for side in participants:
                if not any(_team_matches(side.get("entry"), team) for team in teams):
                    reasons.append("settlement_participants_disagree")
                    break
        if outcome.get("token_verified"):
            if settlement.get("token_id") != outcome.get("token_id"):
                reasons.append("settlement_token_disagrees")
        elif settlement.get("token_id") not in (None, ""):
            reasons.append("settlement_token_not_verified_at_fill")
        if reasons:
            return {"accepted": False, "reasons": reasons}
        px = settlement.get("long_settlement_px")
        win_px = float(px) if fill["side"] == "long" else 1 - float(px)
        win_px = round(win_px, 4)
        payout = round(fill["contracts"] * win_px, 4)
        pnl = round(payout - fill["cost"], 4)
        result = "win" if win_px >= 0.99 else "loss" if win_px <= 0.01 else "push"
        before = (fill["pick"], fill["slug"], fill["key"], fill["side"], fill["contracts"], fill["price"], fill["fee"], fill["cost"])
        con.execute(
            """update fills set status='settled', result=?, payout=?, pnl=?, settle_px=?, settled_ts=?, settle_src=?
               where id=? and status='open'""",
            (result, payout, pnl, win_px, "2030-11-07T23:00:00-06:00", "synthetic", fill_id),
        )
        after = con.execute("select * from fills where id=?", (fill_id,)).fetchone()
        if (after["pick"], after["slug"], after["key"], after["side"], after["contracts"], after["price"], after["fee"], after["cost"]) != before:
            con.rollback()
            raise RuntimeError("settlement changed the fill identity")
        con.commit()
        return {"accepted": True, "reasons": ["identity_agreed"], "pnl": pnl, "result": result, "token_verified": bool(outcome.get("token_verified"))}
    finally:
        con.close()


def run_live_props_fill(session, rec, game, player, p_side, stats=None):
    """Call the exported live_props.poly_fill, which is the only Polymarket insert in that file."""
    argv = ["live_props.py", "nfl", "poly"]
    sys.argv[:] = argv
    # Load a temp copy so the module's data directory is not the provenance tree.
    # The guarded pm_fill module is already imported, so this still calls that fill.
    dest = session.workspace / "live_props.py"
    if not dest.exists():
        shutil.copy(_LIVE, dest)
    if "live_props" in sys.modules:
        module = sys.modules["live_props"]
    else:
        spec = importlib.util.spec_from_file_location("live_props", dest)
        module = importlib.util.module_from_spec(spec)
        sys.modules["live_props"] = module
        spec.loader.exec_module(module)
    stats = stats if stats is not None else {}
    module.poly_fill(rec, game, player, p_side, stats)
    return stats
