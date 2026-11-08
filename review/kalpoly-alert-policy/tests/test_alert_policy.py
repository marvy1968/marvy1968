"""Offline policy tests. No Telegram, no network, no timers."""
import importlib
import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PROV = ROOT / "provenance"

from kalpoly_alert.betcard import deliver_card
from kalpoly_alert.delivery import Receipt, backing_ids, commit_retry_receipt, deliver_ordinary
from kalpoly_alert.export_feed import ALERT_SQL, export_alerts
from kalpoly_alert.loss_notice import evaluate, read_policy
from kalpoly_alert.summary import render
from kalpoly_alert.urgent import notify

CT = ZoneInfo("America/Chicago")
SAT = datetime(2026, 11, 7, 15, 0, tzinfo=CT)
MON = datetime(2026, 11, 9, 15, 0, tzinfo=CT)
MON_2059 = datetime(2026, 11, 9, 20, 59, tzinfo=CT)
MON_2100 = datetime(2026, 11, 9, 21, 0, tzinfo=CT)
MON_2130 = datetime(2026, 11, 9, 21, 30, tzinfo=CT)
SUN_DST = datetime(2026, 11, 1, 21, 0, tzinfo=CT)
MON_DST = datetime(2026, 3, 9, 21, 0, tzinfo=CT)


def db():
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.executescript(Path(PROV / "source/home-context/aj-bet-bot/ajbet/db.py").read_text().split('SCHEMA = """')[1].split('"""')[0])
    con.execute("ALTER TABLE paper_bets ADD COLUMN player TEXT")
    con.execute("ALTER TABLE paper_bets ADD COLUMN commence TEXT")
    con.execute("INSERT INTO alerts(ts, dedupe_key, event_id, league, strategy, market, selection, fair_p, edge, message, details_json) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                ("2026-11-09T20:00:00+00:00", "k1", "ev1", "nba", "value", "player_points", "Over", 0.6, 0.05, "Will Example Player\nmore", "{}"))
    con.execute("INSERT INTO paper_bets(alert_id, player, commence) VALUES (1, 'Example Player', '2026-11-09T23:00:00Z')")
    return con


def test_source_hashes_and_call_sites():
    sums = (ROOT / "provenance" / "SHA256SUMS").read_text().splitlines()
    archive = sums[0].split()[0]
    assert archive == "0081aefa3b8ac67cc51dada790e7839afdaf8f54f7858c35146e190e8a40e9b0"
    for line in sums[1:]:
        digest, name = line.split(None, 1)
        assert __import__("hashlib").sha256((PROV / name).read_bytes()).hexdigest() == digest
    scanner = (PROV / "source/home-context/aj-bet-bot/ajbet/scanner.py").read_text()
    value = (PROV / "source/home-context/aj-bet-bot/ajbet/value_scan.py").read_text()
    alerts = (PROV / "source/home-context/aj-bet-bot/ajbet/alerts.py").read_text()
    export = (PROV / "source/home-context/bin/export_props.py").read_text()
    assert "if self.notifier.send(text, alert_id=aid):\n            self.db.mark_sent(aid)" in scanner
    assert "if self.notifier.send(text, alert_id=aid):\n            self.db.mark_sent(aid)" in value
    assert "if self._send_one" in alerts and "db.mark_sent(int(aid))" in alerts
    assert "a.sent=1 or (a.strategy='team_trend' and a.league='nfl')" in export
    assert "sent" not in (PROV / "source/kalshi-demo/prop_feed.py").read_text()
    schedule = json.loads((ROOT / "kalpoly_alert/schedule.json").read_text())
    assert schedule["installed"] is False and schedule["cron_active"] is False
    policy = read_policy(ROOT / "kalpoly_alert/loss_policy.json")
    assert policy["enabled"] is False
    assert policy["baseline_bankroll"]["kalshi_demo"] == "UNKNOWN"


def test_weekend_receipt_weekday_skip_and_same_feed(tmp_path, monkeypatch):
    def run(when, receipt):
        con = db()
        calls = []
        outcome = deliver_ordinary(con, now=when, alert_id=1, gates_passed=True, transport=lambda aid: calls.append(aid) or receipt)
        again = deliver_ordinary(con, now=when, alert_id=1, gates_passed=True, transport=lambda aid: calls.append(aid) or receipt)
        held = deliver_ordinary(con, now=when, alert_id=1, gates_passed=False, transport=lambda aid: (_ for _ in ()).throw(AssertionError("held")))
        return con, outcome, again, held, calls

    weekend, ok, again, held, calls = run(SAT, Receipt("http_200"))
    assert ok.sent and ok.posted and weekend.execute("SELECT sent FROM alerts").fetchone()[0] == 1
    assert again.posted is False and calls == [1]
    assert held.status == "not_eligible"
    monday, skipped, _, _, mcalls = run(MON, Receipt("http_200"))
    assert skipped.status == "suppressed_weekday" and skipped.sent is False and mcalls == []
    assert monday.execute("SELECT sent FROM alerts").fetchone()[0] == 0
    assert monday.execute("SELECT count(*) FROM outbox").fetchone()[0] == 0
    for con in (weekend, monday):
        con.execute("INSERT INTO alerts(ts, dedupe_key, event_id, league, strategy, market, message) VALUES (?,?,?,?,?,?,?)",
                    ("2026-11-09T20:00:00+00:00", "trend", "ev1", "nfl", "team_trend", "spreads", "trend"))
        con.execute("INSERT INTO alerts(ts, dedupe_key, event_id, league, strategy, market, message, details_json) VALUES (?,?,?,?,?,?,?,?)",
                    ("2026-11-09T20:00:00+00:00", "veto", "ev9", "nba", "value", "h2h", "no", '{"paper_reason":"marv_h2h_bad"}'))
    left = {row["id"] for row in export_alerts(weekend, "2026-11-01")}
    right = {row["id"] for row in export_alerts(monday, "2026-11-01")}
    assert left == right == {1, 2}
    assert "feed_eligibility" in ALERT_SQL and "a.sent=1" in ALERT_SQL
    feeds = []
    for con in (weekend, monday):
        path = tmp_path / f"{con.execute('SELECT sent FROM alerts WHERE id=1').fetchone()[0]}.json"
        path.write_text(json.dumps({"exported": "t", "alerts": export_alerts(con, "2026-11-01")}))
        os.environ["PROPS_EXPORT"] = str(path)
        sys.path.insert(0, str(PROV / "source/kalshi-demo"))
        sys.modules.pop("me_alerts", None)
        me = importlib.import_module("me_alerts")
        monkeypatch.setattr(me, "espn_game", lambda league, event: {"home": "Home Team", "away": "Away Team", "state": "pre"})
        loaded = me.load({}, since_hours=100000)
        bets = []
        for alert in loaded:
            entry, why = me.to_entry(alert)
            if why is None:
                bets.append(entry["me_alert_id"])
        feeds.append(bets)
    assert feeds[0] == feeds[1] == [1]


def test_failed_unknown_retry_and_restart():
    con = db()
    failed = deliver_ordinary(con, now=SAT, alert_id=1, gates_passed=True, transport=lambda aid: Receipt("http_500"))
    assert failed.sent is False and failed.enqueued and con.execute("SELECT sent FROM alerts").fetchone()[0] == 0
    assert con.execute("SELECT status FROM feed_eligibility").fetchone()[0] == "failed"
    assert deliver_ordinary(con, now=SAT, alert_id=1, gates_passed=True, transport=lambda aid: (_ for _ in ()).throw(AssertionError("repost"))).posted is False
    other = db()
    unknown = deliver_ordinary(other, now=SAT, alert_id=1, gates_passed=True, transport=lambda aid: Receipt("unknown"))
    assert unknown.status == "unknown" and other.execute("SELECT sent FROM alerts").fetchone()[0] == 0
    fixed = commit_retry_receipt(other, alert_id=1, receipt=Receipt("http_200"))
    assert fixed.sent and other.execute("SELECT sent FROM alerts").fetchone()[0] == 1
    assert commit_retry_receipt(other, alert_id=99, receipt=Receipt("http_200")).eligible is False


def test_receipt_commit_failure_does_not_claim_sent():
    con = db()
    con.execute("CREATE TRIGGER boom BEFORE UPDATE OF sent ON alerts BEGIN SELECT RAISE(ABORT, 'disk'); END")
    outcome = deliver_ordinary(con, now=SAT, alert_id=1, gates_passed=True, transport=lambda aid: Receipt("http_200"))
    assert outcome.sent is False and outcome.status == "pending"
    assert con.execute("SELECT sent FROM alerts").fetchone()[0] == 0


def test_double_dip_sees_undelivered_eligibility():
    con = db()
    deliver_ordinary(con, now=MON, alert_id=1, gates_passed=True, transport=lambda aid: Receipt("http_200"))
    con.execute("INSERT INTO alerts(ts, dedupe_key, event_id, league, strategy, market) VALUES (?,?,?,?,?,?)",
                ("2026-11-09T20:01:00+00:00", "k2", "ev1", "nba", "value", "player_points"))
    assert backing_ids(con, "ev1", (2,)) == [1]
    assert con.execute("SELECT sent FROM alerts WHERE id=1").fetchone()[0] == 0


def test_summary_is_weekday_idempotent_and_does_not_invent_settlement():
    con = sqlite3.connect(":memory:", isolation_level=None)
    assert render(con, now=SAT.replace(hour=21), poly_rows=[], kalshi_rows=[]) is None
    assert render(con, now=MON_2059, poly_rows=[], kalshi_rows=[]) is None
    assert render(con, now=SUN_DST, poly_rows=[], kalshi_rows=[]) is None
    rows = [
        {"status": "settled", "result": "loss", "payout": 0, "cost": 10.5, "fee": 0.5, "pnl": -10.5, "settled_ts": "2026-11-10T02:30:00+00:00"},
        {"status": "open", "result": None, "payout": None, "cost": 4, "pnl": 0, "settled_ts": None},
        {"status": "settled", "result": None, "payout": None, "cost": 3, "pnl": 0, "settled_ts": None},
    ]
    first = render(con, now=MON_2100, poly_rows=rows, kalshi_rows=[])
    second = render(con, now=MON_2130, poly_rows=rows, kalshi_rows=[])
    assert first["created"] and second["created"] is False and first["body"] == second["body"]
    assert "0-1-0" in first["body"] and "not a mark-to-market" in first["body"]
    assert "unknown finishes 1" in first["body"] and "baseline UNKNOWN" in first["body"]
    spring = render(sqlite3.connect(":memory:", isolation_level=None), now=MON_DST, poly_rows=[], kalshi_rows=[])
    assert spring["created"] and "2026-03-09" in spring["body"]


def test_urgent_bypasses_weekday_and_loss_stays_off():
    con = sqlite3.connect(":memory:", isolation_level=None)
    calls = []
    out = notify(con, now=MON, kind="crash", dedupe_key="poly-down", transport=lambda kind: calls.append(kind) or Receipt("http_200"))
    assert out.delivered and calls == ["crash"]
    again = notify(con, now=MON, kind="crash", dedupe_key="poly-down", transport=lambda kind: calls.append(kind) or Receipt("http_200"))
    assert again.posted is False and calls == ["crash"]
    missed = notify(con, now=MON, kind="stall", dedupe_key="kalshi-stall", transport=lambda kind: Receipt("unknown"))
    assert missed.delivered is False and missed.status == "unknown"
    policy = read_policy(ROOT / "kalpoly_alert/loss_policy.json")
    assert evaluate(con, now=MON, policy=policy, venue="kalshi_demo", rows=[], day_opening="UNKNOWN") == []
    armed = dict(policy, enabled=True)
    notes = evaluate(con, now=MON_2100, policy=armed, venue="polymarket_paper", rows=[
        {"status": "settled", "result": "loss", "payout": 0, "cost": 110, "pnl": -110, "settled_ts": "2026-11-10T03:00:00+00:00"},
    ], day_opening=1000)
    assert [note["kind"] for note in notes] == ["day_loss_1", "day_loss_2", "baseline_unknown"]
    assert evaluate(con, now=MON_2130, policy=armed, venue="polymarket_paper", rows=[
        {"status": "settled", "result": "loss", "payout": 0, "cost": 110, "pnl": -110, "settled_ts": "2026-11-10T03:00:00+00:00"},
    ], day_opening=1000) == []


def test_bet_card_weekend_unchanged_weekday_not_claimed(tmp_path):
    calls = []
    weekend = deliver_card(tmp_path / "sat", now=SAT, bets=["a"], transport=lambda bets: calls.append(bets))
    assert weekend["posted"] and weekend["claimed_sent"] and calls == [["a"]]
    assert deliver_card(tmp_path / "sat", now=SAT, bets=["a"], transport=lambda bets: calls.append("x"))["status"] == "already_sent"
    weekday = deliver_card(tmp_path / "mon", now=MON, bets=["a"], transport=lambda bets: (_ for _ in ()).throw(AssertionError("posted")))
    assert weekday["posted"] is False and weekday["claimed_sent"] is False and weekday["logged"]
    assert not (tmp_path / "mon" / "betcard_sent.json").exists()
    assert deliver_card(tmp_path / "mon", now=MON, bets=["a"], transport=lambda bets: (_ for _ in ()).throw(AssertionError("posted")))["status"] == "already_held"
