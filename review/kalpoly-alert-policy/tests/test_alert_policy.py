"""Offline checks against the exported source. These are not live selection parity."""
import json
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
PROV = ROOT / "provenance"
CT = ZoneInfo("America/Chicago")

from kalpoly_alert.betcard import deliver_card
from kalpoly_alert.clock import is_weekend, summary_slot
from kalpoly_alert.delivery import Receipt, backing_ids, note_transport_without_send, record_disposition
from kalpoly_alert.export_feed import PRODUCTION_WHERE, export_alerts
from kalpoly_alert.external import external_failure_path
from kalpoly_alert.gate_proof import export_request, line_text, prove_gates, write_request
from kalpoly_alert.loss_notice import evaluate, read_policy
from kalpoly_alert.summary import publish_summary, render
from kalpoly_alert.urgent import notify

FRI_2059 = datetime(2026, 11, 6, 20, 59, tzinfo=CT)
FRI_2100 = datetime(2026, 11, 6, 21, 0, tzinfo=CT)
FRI_2359 = datetime(2026, 11, 6, 23, 59, tzinfo=CT)
SAT_0000 = datetime(2026, 11, 7, 0, 0, tzinfo=CT)
SAT_2100 = datetime(2026, 11, 7, 21, 0, tzinfo=CT)
SUN_2359 = datetime(2026, 11, 8, 23, 59, tzinfo=CT)
MON_0000 = datetime(2026, 11, 9, 0, 0, tzinfo=CT)
MON_2100 = datetime(2026, 11, 9, 21, 0, tzinfo=CT)
MON_2130 = datetime(2026, 11, 9, 21, 30, tzinfo=CT)
SUN_DST = datetime(2026, 11, 1, 1, 30, tzinfo=CT)
MON_AFTER_SPRING = datetime(2026, 3, 9, 0, 0, tzinfo=CT)


def schema() -> str:
    text = (PROV / "source/home-context/aj-bet-bot/ajbet/db.py").read_text()
    return text.split('SCHEMA = """')[1].split('"""')[0]


def db():
    con = sqlite3.connect(":memory:", isolation_level=None)
    con.executescript(schema())
    con.execute("ALTER TABLE paper_bets ADD COLUMN player TEXT")
    con.execute("ALTER TABLE paper_bets ADD COLUMN commence TEXT")
    con.execute(
        "INSERT INTO alerts(ts, dedupe_key, event_id, league, strategy, market, selection, fair_p, message) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        ("2026-11-09T20:00:00+00:00", "k1", "ev1", "nba", "value", "player_points", "Over", 0.6, "Example"),
    )
    return con


def production_ids(con):
    return [row[0] for row in con.execute(
        f"SELECT a.id FROM alerts a WHERE ({PRODUCTION_WHERE}) AND a.ts >= ? ORDER BY a.id",
        ("2026-11-01",),
    )]


def test_gate_request_matches_immutable_source_lines():
    request = export_request()
    assert request["do_not_include"] == ["configs", "env", "credentials", "databases", "raw logs", "recipient ids"]
    seen = set()
    for row in request["files"]:
        caller = Path(row["needed_by_file"]).name
        text = line_text(PROV, caller, row["needed_by_line"])
        assert row["call"] in text
        seen.add(row["absolute_path"])
    assert "/home/moonrider1968/aj-bet-bot/ajbet/cfb_track.py" in seen
    assert "/home/moonrider1968/aj-bet-bot/ajbet/live_guard.py" in seen
    assert "/home/moonrider1968/aj-bet-bot/ajbet/gamecast.py" in seen
    write_request(ROOT / "EXPORT_REQUEST.json")
    assert json.loads((ROOT / "EXPORT_REQUEST.json").read_text()) == request


def test_stub_file_is_not_executed_or_accepted(tmp_path):
    stub = tmp_path / "cfb_track.py"
    stub.write_text("raise SystemExit('stub must not be imported')\n")
    proof = prove_gates(tmp_path)
    assert proof["executed"] is False and proof["eligible"] is False
    assert proof["status"] == "missing_dependency"
    assert "ajbet.cfb_track" in proof["present_untrusted_modules"]
    assert "ajbet.gamecast" in proof["absent_modules"]


def test_missing_proof_does_not_authorize_a_weekday_trade():
    con = db()
    proof = prove_gates(None)
    record_disposition(con, now=MON_2100, alert_id=1, status=proof["status"])
    for status in ("held", "demoted", "vetoed", "dry_run", "double_dip"):
        con.execute(
            "INSERT INTO alerts(ts, dedupe_key, event_id, league, strategy, market) VALUES (?,?,?,?,?,?)",
            ("2026-11-09T20:00:00+00:00", status, "ev1", "nba", "value", "h2h"),
        )
        aid = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        outcome = record_disposition(con, now=FRI_2359, alert_id=aid, status=status)
        assert outcome.eligible is False and outcome.sent is False and outcome.status == status
    try:
        con.execute(
            "INSERT INTO alerts(ts, dedupe_key, event_id, league, strategy, market) VALUES (?,?,?,?,?,?)",
            ("2026-11-09T20:00:00+00:00", "proof", "ev1", "nba", "value", "h2h"),
        )
        fresh = con.execute("SELECT last_insert_rowid()").fetchone()[0]
        con.execute(
            "INSERT INTO feed_eligibility(alert_id, decided_at, status, gate_proof) VALUES (?, 't', 'suppressed_weekday', 'executed')",
            (fresh,),
        )
        raised = False
    except sqlite3.IntegrityError:
        raised = True
    assert raised
    con.execute(
        "INSERT INTO alerts(ts, dedupe_key, event_id, league, strategy, market, message) VALUES (?,?,?,?,?,?,?)",
        ("2026-11-09T20:00:00+00:00", "trend", "ev2", "nfl", "team_trend", "spreads", "trend"),
    )
    con.execute("UPDATE alerts SET sent=1 WHERE dedupe_key='k1'")
    exported = [row["id"] for row in export_alerts(con, "2026-11-01")]
    assert exported == production_ids(con)
    assert exported == [1, con.execute("SELECT id FROM alerts WHERE strategy='team_trend'").fetchone()[0]]
    assert backing_ids(con, "ev1", ()) == [1]


def test_http_200_without_send_is_not_sent():
    con = db()
    outcome = note_transport_without_send(con, now=SAT_2100, alert_id=1, receipt=Receipt("http_200"))
    assert outcome.status == "missing_dependency" and outcome.eligible is False and outcome.sent is False
    assert con.execute("SELECT sent FROM alerts").fetchone()[0] == 0
    failed = note_transport_without_send(con, now=SAT_2100, alert_id=1, receipt=Receipt("http_500"))
    assert failed.sent is False
    assert con.execute("SELECT receipt FROM feed_eligibility WHERE alert_id=1").fetchone()[0] == "failed"
    other = db()
    unknown = note_transport_without_send(other, now=SAT_0000, alert_id=1, receipt=Receipt("unknown"))
    assert unknown.status == "missing_dependency"
    assert other.execute("SELECT sent FROM alerts").fetchone()[0] == 0
    assert export_alerts(other, "2026-11-01") == []


def test_central_weekend_boundaries_and_dst():
    assert is_weekend(FRI_2359) is False and summary_slot(FRI_2059) is None
    assert summary_slot(FRI_2100) == "2026-11-06"
    assert is_weekend(SAT_0000) and summary_slot(SAT_2100) is None
    assert is_weekend(SUN_2359) and summary_slot(SUN_2359) is None
    assert is_weekend(MON_0000) is False and summary_slot(MON_0000) is None
    assert summary_slot(MON_2100) == "2026-11-09"
    assert is_weekend(SUN_DST)
    assert is_weekend(MON_AFTER_SPRING) is False
    assert summary_slot(datetime(2026, 3, 8, 21, 0, tzinfo=CT)) is None


def test_summary_scope_receipt_crash_and_restart():
    con = sqlite3.connect(":memory:", isolation_level=None)
    assert render(con, now=SAT_2100, poly_rows=[], kalshi_rows=[]) is None
    try:
        render(con, now=FRI_2100, poly_rows=[], kalshi_rows=[], scope=("aj_bet",))
        assert False
    except ValueError:
        pass
    body = render(con, now=FRI_2100, poly_rows=[], kalshi_rows=[])["body"]
    assert "Kalshi DEMO" in body and "Polymarket PAPER" in body and "AJ Bet" not in body
    calls = []

    def boom(text):
        calls.append(text)
        raise RuntimeError("crash after the socket and before a receipt")

    crashed = publish_summary(con, now=FRI_2100, poly_rows=[], kalshi_rows=[], transport=boom)
    assert crashed["status"] == "ambiguous" and crashed["delivery_proven"] is False and crashed["receipt"] is None
    assert crashed["exactly_once_telegram"] is False and calls
    restarted = publish_summary(con, now=datetime(2026, 11, 6, 21, 30, tzinfo=CT), poly_rows=[], kalshi_rows=[],
                                transport=lambda text: (_ for _ in ()).throw(AssertionError("second post")))
    assert restarted["posted"] is False and restarted["status"] == "ambiguous"
    fresh = sqlite3.connect(":memory:", isolation_level=None)
    posted = publish_summary(fresh, now=MON_2100, poly_rows=[], kalshi_rows=[], transport=lambda text: Receipt("http_200"))
    assert posted["delivery_proven"] is True and posted["exactly_once_telegram"] is False
    again = publish_summary(fresh, now=MON_2130, poly_rows=[], kalshi_rows=[], transport=lambda text: Receipt("http_200"))
    assert again["posted"] is False


def test_urgent_is_not_a_trade_and_loss_proposal_stays_off():
    con = db()
    calls = []
    out = notify(con, now=FRI_2100, kind="stall", dedupe_key="paper-timer",
                 transport=lambda kind: calls.append(kind) or Receipt("http_200"))
    assert out.delivered and calls == ["stall"]
    assert con.execute("SELECT sent FROM alerts").fetchone()[0] == 0
    assert export_alerts(con, "2026-11-01") == []
    policy = read_policy(ROOT / "kalpoly_alert/loss_policy.json")
    assert policy["enabled"] is False
    assert evaluate(con, now=MON_2100, policy=policy, venue="kalshi_demo", rows=[], day_opening="UNKNOWN") == []
    armed = dict(policy, enabled=True, baseline_bankroll={"polymarket_paper": "UNKNOWN", "kalshi_demo": "UNKNOWN"})
    unknown = evaluate(con, now=MON_2100, policy=armed, venue="polymarket_paper", rows=[], day_opening="UNKNOWN")
    assert [note["kind"] for note in unknown] == ["day_opening_unknown", "baseline_unknown"]
    assert "red" not in " ".join(note["kind"] for note in unknown)
    above = dict(policy, enabled=True, baseline_bankroll={"polymarket_paper": 900})
    loss = [{"status": "settled", "result": "loss", "payout": 0, "cost": 60, "pnl": -60,
             "settled_ts": "2026-11-10T03:00:00+00:00"}]
    first = evaluate(con, now=MON_2100, policy=above, venue="polymarket_paper", rows=loss, day_opening=1000)
    assert [note["kind"] for note in first] == ["day_loss_1"]
    crossed = evaluate(sqlite3.connect(":memory:", isolation_level=None), now=MON_2100,
                       policy=dict(policy, enabled=True, baseline_bankroll={"polymarket_paper": 1000}),
                       venue="polymarket_paper",
                       rows=[{"status": "settled", "result": "loss", "payout": 0, "cost": 200, "pnl": -200,
                              "settled_ts": "2026-11-10T03:00:00+00:00"}],
                       day_opening=1000)
    assert "baseline_red" in [note["kind"] for note in crossed]
    already = evaluate(sqlite3.connect(":memory:", isolation_level=None), now=MON_2100,
                       policy=dict(policy, enabled=True, baseline_bankroll={"polymarket_paper": 1000}),
                       venue="polymarket_paper", rows=[], day_opening=900)
    assert [note["kind"] for note in already] == ["already_below_at_open"]


def test_bet_card_mark_then_post_is_not_a_receipt(tmp_path):
    calls = []
    weekend = deliver_card(tmp_path / "sat", now=SAT_2100, bets=["card"], transport=lambda bets: calls.append(bets))
    assert weekend["posted"] and weekend["claimed_sent"] and weekend["delivery_proven"] is False
    assert json.loads((tmp_path / "sat" / "betcard_sent.json").read_text())["2026-11-07"]["telegram_receipt"] is None
    weekday = deliver_card(tmp_path / "fri", now=FRI_2100, bets=["card"],
                           transport=lambda bets: (_ for _ in ()).throw(AssertionError("posted")))
    assert weekday["posted"] is False and weekday["claimed_sent"] is False and weekday["delivery_proven"] is False
    assert not (tmp_path / "fri" / "betcard_sent.json").exists()


def test_source_control_flow_and_external_path_stay_blocked():
    scanner = (PROV / "source/home-context/aj-bet-bot/ajbet/scanner.py").read_text()
    alerts = (PROV / "source/home-context/aj-bet-bot/ajbet/alerts.py").read_text()
    main = (PROV / "source/marv-bot/marv/main.py").read_text()
    hold = scanner.split("if act == \"hold\":")[1].split("return None")[0]
    assert "notifier.send" not in hold
    demote = scanner.split("if demote:")[1].split("if live:")[0]
    assert "notifier.send" not in demote and "trigger=\"watchlist\"" in demote
    assert alerts.index("if self._marv_veto") < alerts.index("for chat in self.recipients()")
    assert alerts.index("if self._double_dip") < alerts.index("for chat in self.recipients()")
    assert alerts.index("if self.dry_run:") < alerts.index("for chat in self.recipients()")
    assert "sent = 1" in alerts
    card = main.split("the daily pregame bet card", 1)[1]
    assert card.index("betcard.mark_sent") < card.index("send_message")
    blocked = external_failure_path()
    assert blocked["activated"] is False and blocked["status"] == "blocked"
