"""Actual gate functions from the 14-file export. Not live selection parity."""
import socket
import threading
from datetime import datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from kalpoly_alert.source_select import BLOCKED_MODULES, decide, hold_result, load_local
from kalpoly_alert.summary import publish_summary
from kalpoly_alert.delivery import Receipt

CT = ZoneInfo("America/Chicago")
MON = datetime(2026, 11, 9, 15, 0, tzinfo=CT)
SAT = datetime(2026, 11, 7, 15, 0, tzinfo=CT)


def _block_network(monkeypatch):
    def refused(*args, **kwargs):
        raise AssertionError("network is blocked")
    monkeypatch.setattr(socket, "socket", refused)


def test_blocked_dependencies_were_checked_against_both_bundles():
    assert "ajbet.markets" in BLOCKED_MODULES
    assert "ajbet.espn" in BLOCKED_MODULES
    assert "ajbet.config" in BLOCKED_MODULES
    gamecast = load_local("gamecast.py")
    assert gamecast.hold_check.__module__


def test_real_gates_reject_the_same_set_on_monday_and_saturday(monkeypatch):
    _block_network(monkeypatch)
    prop = {"league": "ncaaf", "strategy": "value", "market": "player_points", "selection": "Over",
            "event_id": "cfb1", "dedupe_key": "cfb", "edge": 0.1, "price": -110}
    swing = {"league": "nba", "strategy": "total_swing", "market": "totals", "selection": "Over",
             "event_id": "sw1", "dedupe_key": "sw"}
    dip = {"league": "nfl", "strategy": "value", "market": "h2h", "selection": "Home Team",
           "event_id": "g1", "dedupe_key": "dip", "price": -120, "message": "Home Team"}
    prior = dict(dip, dedupe_key="prior")
    dry = {"league": "nba", "strategy": "value", "market": "player_points", "selection": "Over",
           "event_id": "dry1", "dedupe_key": "dry", "edge": 0.1, "price": -110}
    unknown = dict(dry, dedupe_key="unknown", event_id="u1")
    cases = []
    for when in (MON, SAT):
        day = []
        day.append(decide(prop, now=when, league_allow={})["reason"])
        day.append(decide(swing, now=when, league_allow={})["reason"])
        day.append(decide(dip, now=when, league_allow={}, prior_sent=prior, marv_verdict="")["reason"])
        veto = decide(dict(dip, dedupe_key="veto", event_id="v1"), now=when, league_allow={}, marv_verdict="not")
        day.append(veto["reason"])
        assert veto["eligible"] is False and veto["sent"] is False
        day.append(decide(dry, now=when, league_allow={}, dry_run=True)["reason"])
        missing = decide(unknown, now=when)
        day.append(missing["reason"])
        assert missing["status"] == "missing_dependency" and missing["eligible"] is False
        cases.append(day)
    assert cases[0] == cases[1]
    assert cases[0] == ["cfb_track", "me_gates", "double_dip", "vetoed", "dry_run", "league_allow_unknown"]


def test_hold_check_holds_a_red_zone_drive_without_fetching(monkeypatch):
    _block_network(monkeypatch)
    gamecast = load_local("gamecast.py")
    game = SimpleNamespace(
        state="in", league="nfl", event_id="401", name="Away at Home", detail="3rd",
        away=SimpleNamespace(abbr="AWY", score=0), home=SimpleNamespace(abbr="HOM", score=0),
        team=lambda side: SimpleNamespace(abbr="HOM" if side == "home" else "AWY"),
    )
    sit = gamecast.Situation(league="nfl", poss_side="home", ytg=8, red_zone=True, drive_start="own 20")
    action, why = hold_result(game, sit, side="away")
    assert action == "hold" and why
    released, _ = hold_result(
        game, gamecast.Situation(league="nfl", poss_side="away", ytg=80, red_zone=False), side="away")
    assert released == "send"


def test_two_local_claims_post_once_and_an_ambiguous_attempt_does_not_repeat(tmp_path):
    path = tmp_path / "summary.db"
    import sqlite3
    started = threading.Event()
    release = threading.Event()
    calls = []

    def slow(text):
        calls.append("post")
        started.set()
        assert release.wait(2)
        return Receipt("http_200")

    results = {}

    def run(name, transport):
        con = sqlite3.connect(path, isolation_level=None, timeout=5)
        results[name] = publish_summary(
            con, now=datetime(2026, 11, 6, 21, 0, tzinfo=CT), poly_rows=[], kalshi_rows=[], transport=transport)

    left = threading.Thread(target=run, args=("a", slow))
    left.start()
    assert started.wait(2)
    right = threading.Thread(target=run, args=("b", lambda text: calls.append("other") or Receipt("http_200")))
    right.start()
    right.join(2)
    release.set()
    left.join(2)
    assert calls == ["post"]
    assert results["a"]["delivery_proven"] is True and results["a"]["exactly_once_telegram"] is False
    assert results["b"]["posted"] is False
    ambiguous = sqlite3.connect(path, isolation_level=None)
    ambiguous.execute("UPDATE summary_receipt SET state='ambiguous', receipt=NULL")
    again = publish_summary(ambiguous, now=datetime(2026, 11, 6, 21, 10, tzinfo=CT), poly_rows=[], kalshi_rows=[],
                            transport=lambda text: (_ for _ in ()).throw(AssertionError("retried")))
    assert again["posted"] is False and again["status"] == "ambiguous" and again["delivery_proven"] is False
