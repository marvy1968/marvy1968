"""Synthetic tests for log-only pacer v2. No network, no orders."""

from __future__ import annotations

import io
import json
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from pacer_v2.adjust import per_minute_rate
from pacer_v2.cli import main
from pacer_v2.metrics import evaluate, summarize
from pacer_v2.model import (
    apply_isotonic,
    decide,
    default_model,
    fit_isotonic,
    fit_overs,
    fit_w,
    split_games,
)
from pacer_v2.off import switched_off
from pacer_v2.parse import parse_row


def candidate(**kw):
    base = {
        "ts": "2026-02-01T20:00:00+00:00",
        "game_id": "g1",
        "player": "A",
        "stat": "pts",
        "line": 22.5,
        "side": "over",
        "cur": 8,
        "minutes_played": 14,
        "period": 2,
        "clock": "6:00",
        "fouls": 1,
        "margin": 2,
        "season_rate": 0.7,
        "p_model_raw": 0.80,
        "bid": 0.50,
        "ask": 0.50,
        "price": 0.50,
        "b2b": False,
        "result": True,
    }
    base.update(kw)
    return base


def one(model=None, stats=None, **kw):
    return decide(parse_row(candidate(**kw)), model or default_model(), stats)


def iso(day: int) -> str:
    return (datetime(2026, 1, 1, tzinfo=timezone.utc) + timedelta(days=day)).isoformat()


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


class _Env(unittest.TestCase):
    def setUp(self):
        self._saved = os.environ.pop("PACER_V2_OFF", None)

    def tearDown(self):
        os.environ.pop("PACER_V2_OFF", None)
        if self._saved is not None:
            os.environ["PACER_V2_OFF"] = self._saved


class ParseTests(_Env):
    def test_missing_fields_do_not_raise(self):
        row = parse_row({})
        self.assertIsNone(row.player)
        self.assertIsNone(row.p_model_raw)
        d = decide(row, default_model(), None)
        self.assertEqual(d["action"], "skip")
        self.assertEqual(d["skip_reason"], "no_side")

    def test_aliases_american_clock_and_result(self):
        raw = candidate(
            stat="player_pass_yds",
            side="Over",
            clock="Q4 2:00",
            price=-110,
            result="won",
            pnl=0.4,
        )
        row = parse_row(raw)
        self.assertEqual(row.stat, "py")
        self.assertEqual(row.side, "over")
        self.assertEqual(row.clock_remaining_sec, 120)
        self.assertAlmostEqual(row.price, 110 / 210, places=6)
        self.assertTrue(row.result)
        self.assertEqual(row.pnl, 0.4)
        self.assertEqual(parse_row(candidate(clock="6:30")).clock_remaining_sec, 390)
        self.assertEqual(parse_row(candidate(clock=6.5)).clock_remaining_sec, 390)
        self.assertEqual(parse_row(candidate(clock=390)).clock_remaining_sec, 390)
        self.assertEqual(parse_row(candidate(clock="0:02:00")).clock_remaining_sec, 120)
        pushed = parse_row(candidate(result="push"))
        self.assertTrue(pushed.push)
        self.assertIsNone(pushed.result)

    def test_bad_json_line_is_skipped(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.jsonl"
            out = Path(tmp) / "out.jsonl"
            good = candidate(player="Kept")
            src.write_text("this is not json\n" + json.dumps(good) + "\n")
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = main(["score", "--in", str(src), "--out", str(out)])
            self.assertEqual(rc, 0)
            self.assertIn("skip bad json", err.getvalue())
            rows = [json.loads(line) for line in out.read_text().splitlines()]
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0]["player"], "Kept")

    def test_per_game_rate_is_divided_by_typical_minutes(self):
        gaps = []
        rate = per_minute_rate(parse_row(candidate(season_rate=24)), "basketball", gaps)
        self.assertAlmostEqual(rate, 24 / 32)
        self.assertIn("season_rate_treated_as_per_game", gaps)


class OffTests(_Env):
    def test_env_and_files(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            pkg = root / "pkg"
            pkg.mkdir()
            cwd = root / "work"
            cwd.mkdir()
            self.assertFalse(switched_off(package_dir=pkg, cwd=cwd))
            (pkg / "OFF").write_text("")
            self.assertTrue(switched_off(package_dir=pkg, cwd=cwd))
            (pkg / "OFF").unlink()
            off = cwd / "pacer_v2"
            off.mkdir()
            (off / "OFF").write_text("")
            self.assertTrue(switched_off(package_dir=pkg, cwd=cwd))
        os.environ["PACER_V2_OFF"] = ""
        self.assertTrue(switched_off(package_dir=Path("/no/such"), cwd=Path("/no/such")))
        err = io.StringIO()
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            rc = main(["score", "--in", "/no/such.jsonl", "--out", "/no/such-out.jsonl"])
        self.assertEqual(rc, 0)
        self.assertIn("OFF", out.getvalue())
        self.assertFalse(Path("/no/such-out.jsonl").exists())


class DecisionTests(_Env):
    def test_edge_gate_is_fifteen_points(self):
        bet = one()  # blend 0.65, price 0.50, edge 0.15
        self.assertEqual(bet["action"], "bet")
        self.assertIsNone(bet["skip_reason"])
        self.assertAlmostEqual(bet["edge"], 0.15, places=6)
        shy = one(p_model_raw=0.78)
        self.assertEqual(shy["skip_reason"], "edge")
        self.assertLess(shy["edge"], 0.15)

    def test_overs_band_and_min_hit_do_not_apply_to_unders(self):
        over = one(p_model_raw=0.99, price=0.68, bid=0.68, ask=0.68)
        under = one(p_model_raw=0.99, price=0.68, bid=0.68, ask=0.68, side="under")
        self.assertEqual(over["skip_reason"], "overs_band")
        self.assertEqual(under["action"], "bet")
        over_low = one(p_model_raw=0.70, price=0.40, bid=0.40, ask=0.40)
        under_low = one(p_model_raw=0.70, price=0.40, bid=0.40, ask=0.40, side="under")
        self.assertEqual(over_low["skip_reason"], "overs_min_p")
        self.assertAlmostEqual(over_low["p"], 0.55, places=6)
        self.assertEqual(under_low["action"], "bet")

    def test_timing_filter_skips_first_six_and_last_three(self):
        early = one(period=1, clock="8:00", p_model_raw=0.99, price=0.40, bid=0.40, ask=0.40)
        self.assertEqual(early["elapsed_min"], 4)
        self.assertEqual(early["skip_reason"], "timing_early")
        boundary = one(period=1, clock="6:00")
        self.assertEqual(boundary["elapsed_min"], 6)
        self.assertEqual(boundary["action"], "bet")
        late = one(period=4, clock="2:00", p_model_raw=0.99, price=0.40, bid=0.40, ask=0.40)
        self.assertEqual(late["remaining_min_base"], 2)
        self.assertEqual(late["skip_reason"], "timing_late")
        late_edge = one(period=4, clock="3:00")
        self.assertEqual(late_edge["remaining_min_base"], 3)
        self.assertNotEqual(late_edge["skip_reason"], "timing_late")
        nfl = one(stat="py", period=1, clock="10:00", season_rate=4.0,
                  p_model_raw=0.99, price=0.40, bid=0.40, ask=0.40)
        self.assertEqual(nfl["skip_reason"], "timing_early")

    def test_unknown_clock_does_not_invent_a_skip(self):
        raw = candidate()
        raw.pop("period")
        raw.pop("clock")
        d = decide(parse_row(raw), default_model(), None)
        self.assertIn("game_clock_unknown", d["gaps"])
        self.assertEqual(d["action"], "bet")

    def test_blowout_foul_b2b_and_backup(self):
        close = one(period=4, clock="8:00", margin=2, p_model_raw=0.70)
        blow = one(period=4, clock="8:00", margin=30, p_model_raw=0.70)
        self.assertEqual(close["blowout_factor"], 1.0)
        self.assertEqual(blow["blowout_factor"], 0.4)
        self.assertEqual(blow["remaining_min_base"], 8)
        self.assertAlmostEqual(blow["remaining_min_adj"], 3.2, places=6)
        self.assertLess(blow["p_pacer"], close["p_pacer"] - 0.05)

        clean = one(fouls=1)
        trouble = one(fouls=5)
        self.assertEqual(trouble["foul_factor"], 0.5)
        self.assertLess(trouble["p_pacer"], clean["p_pacer"] - 0.05)

        rested = one(b2b=False)
        tired = one(b2b=True)
        self.assertEqual(tired["b2b_factor"], 0.92)
        self.assertLess(tired["p_pacer"], rested["p_pacer"])

        backup = one(period=4, clock="8:00", margin=30, role="backup", p_model_raw=0.70)
        self.assertEqual(backup["blowout_factor"], 1.0)
        self.assertIn("blowout_skipped_backup_role", backup["gaps"])

        nfl = one(stat="py", period=4, clock="10:00", margin=20, season_rate=4.0, fouls=6)
        self.assertEqual(nfl["blowout_factor"], 0.5)
        self.assertEqual(nfl["foul_factor"], 1.0)
        self.assertIn("fouls_ignored_football", nfl["gaps"])

    def test_defense_and_pace_default_and_apply(self):
        base = one()
        self.assertEqual(base["def_mult"], 1.0)
        self.assertEqual(base["pace_mult"], 1.0)
        self.assertIn("no_team_stats", base["gaps"])
        suppressed = one(stats={"teams": {"BOS": {"def": {"pts": 0.8}}}}, opponent="BOS")
        self.assertEqual(suppressed["def_mult"], 0.8)
        self.assertLess(suppressed["p_pacer"], base["p_pacer"] - 0.05)
        faster = one(stats={"games": {"g1": {"pace": 1.25}}})
        self.assertEqual(faster["pace_mult"], 1.25)
        self.assertGreater(faster["p_pacer"], base["p_pacer"] + 0.05)
        raw = candidate()
        raw.pop("b2b")
        listed = decide(parse_row(raw), default_model(), {"b2b_players": ["A"]})
        self.assertTrue(listed["b2b"])
        self.assertEqual(listed["b2b_factor"], 0.92)

    def test_explicit_old_probability_is_kept(self):
        d = one(p_model_raw=0.80)
        self.assertAlmostEqual(d["p_old"], 0.65, places=6)
        explicit = one(p_model_raw=0.20, p_old=0.90)
        self.assertAlmostEqual(explicit["p_old"], 0.90, places=6)
        self.assertEqual(explicit["skip_reason"], "edge")

    def test_college_clock_uses_halves(self):
        d = one(league="ncaab", period=2, clock="10:00")
        self.assertEqual(d["elapsed_min"], 30)
        self.assertEqual(d["remaining_min_base"], 10)


class FitTests(_Env):
    def test_blend_weight_follows_the_better_source(self):
        y = [0.0] * 20 + [1.0] * 20
        mid = [0.5] * 40
        w_good, fitted = fit_w(y, mid, y)
        self.assertTrue(fitted)
        self.assertGreater(w_good, 0.9)
        w_bad, fitted_bad = fit_w([1.0 - v for v in y], mid, y)
        self.assertTrue(fitted_bad)
        self.assertLess(w_bad, 0.1)
        w_thin, fitted_thin = fit_w(y[:10], mid[:10], y[:10])
        self.assertFalse(fitted_thin)
        self.assertEqual(w_thin, 0.5)

    def test_overs_band_keeps_the_winning_prices(self):
        p = [0.70] * 40 + [0.92] * 40
        price = [0.50] * 40 + [0.75] * 40
        y = [1.0] * 40 + [0.0] * 40
        over = [True] * 80
        rule = fit_overs(p, price, y, over)
        self.assertTrue(rule["fitted"])
        self.assertLessEqual(rule["price_lo"], 0.50)
        self.assertGreaterEqual(rule["price_hi"], 0.50)
        self.assertLess(rule["price_hi"], 0.75)
        self.assertGreater(rule["train_roi"], 0.5)

    def test_isotonic_pulls_a_flat_score_to_the_hit_rate(self):
        iso = fit_isotonic([0.8] * 40, [0.0] * 20 + [1.0] * 20)
        self.assertFalse(iso["identity"])
        self.assertAlmostEqual(apply_isotonic(iso, 0.8), 0.5, places=2)
        thin = fit_isotonic([0.8] * 5, [0, 1, 0, 1, 0])
        self.assertTrue(thin["identity"])


class ScoreTests(_Env):
    def test_score_is_idempotent_and_deduped(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "in.jsonl"
            out = root / "decisions.jsonl"
            first = candidate(player="A", p_model_raw=0.80)
            later = candidate(player="A", p_model_raw=0.99)
            other = candidate(player="B", ts="2026-02-02T20:00:00+00:00", game_id="g2")
            write_jsonl(src, [later, first, other, later])
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["score", "--in", str(src), "--out", str(out)]), 0)
            blob = out.read_bytes()
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["score", "--in", str(src), "--out", str(out)]), 0)
            self.assertEqual(out.read_bytes(), blob)
            rows = [json.loads(line) for line in blob.decode().splitlines()]
            self.assertEqual(len(rows), 2)
            by_player = {r["player"]: r for r in rows}
            self.assertEqual([r["player"] for r in rows], ["A", "B"])  # sorted by ts
            self.assertAlmostEqual(by_player["A"]["p_model_raw"], 0.99)  # last line wins
            # a pre-existing key that this run does not touch is kept
            extra = dict(rows[0])
            extra["key"] = "foreign|key"
            extra["player"] = "Z"
            extra["ts"] = "2026-01-01T00:00:00+00:00"
            out.write_text(json.dumps(extra) + "\n" + blob.decode())
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                main(["score", "--in", str(src), "--out", str(out)])
            kept = [json.loads(line) for line in out.read_text().splitlines()]
            self.assertEqual([r["player"] for r in kept], ["Z", "A", "B"])

    def test_missing_input_exits_nonzero(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            err = io.StringIO()
            with redirect_stderr(err), redirect_stdout(io.StringIO()):
                rc = main(["score", "--in", str(Path(tmp) / "nope.jsonl"), "--out", str(Path(tmp) / "o.jsonl")])
            self.assertEqual(rc, 2)
            self.assertIn("missing input", err.getvalue())


class ReportTests(_Env):
    def test_contract_roi_and_slippage(self):
        bets = [(True, 0.40), (False, 0.40)]
        m = summarize(bets)
        self.assertEqual(m["n"], 2)
        self.assertEqual(m["wins"], 1)
        self.assertAlmostEqual(m["wr"], 0.5)
        self.assertAlmostEqual(m["roi"], 0.25)
        self.assertAlmostEqual(m["roi_slip_2c"], 0.16 / 0.84)
        self.assertFalse(m["n_ge_30"])

    def test_three_systems(self):
        decisions = []
        for i in range(4):
            decisions.append({
                "action": "bet" if i < 2 else "skip",
                "result": i % 2 == 0,
                "push": False,
                "price": 0.40,
                "p_model_raw": 0.80,
                "p_old": 0.20,
            })
        metrics = evaluate(decisions)
        self.assertEqual(metrics["v2"]["n"], 2)
        self.assertAlmostEqual(metrics["v2"]["roi"], 0.25)
        self.assertEqual(metrics["v1"]["n"], 4)
        self.assertAlmostEqual(metrics["v1"]["wr"], 0.5)
        self.assertEqual(metrics["old"]["n"], 0)
        self.assertIsNone(metrics["old"]["roi"])

    def test_report_command_on_a_decision_log(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            src = Path(tmp) / "in.jsonl"
            decisions = Path(tmp) / "decisions.jsonl"
            report = Path(tmp) / "report.json"
            write_jsonl(src, [candidate(result=True), candidate(player="B", game_id="g2", result=False)])
            out = io.StringIO()
            with redirect_stdout(out), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["score", "--in", str(src), "--out", str(decisions)]), 0)
                self.assertEqual(main(["report", "--in", str(decisions), "--out", str(report)]), 0)
            text = out.getvalue()
            self.assertIn("n>=30", text)
            self.assertIn("v2", text)
            body = json.loads(report.read_text())
            self.assertEqual(body["v2"]["n"], 2)
            self.assertIn("old", body)
            self.assertIn("v1", body)


class BacktestTests(_Env):
    def _rows(self, n, result_fn):
        rows = []
        for i in range(n):
            rows.append(candidate(
                game_id=f"g{i:03d}",
                player="A",
                ts=iso(i),
                result=result_fn(i),
                p_model_raw=0.90,
                price=0.50,
                bid=0.50,
                ask=0.50,
            ))
        return rows

    def test_split_is_chronological_and_game_disjoint(self):
        rows = [parse_row(r) for r in self._rows(10, lambda i: True)]
        rows.append(parse_row(candidate(game_id="g000", player="B", ts=iso(0))))
        rows = list(reversed(rows))
        train, test, train_ids, test_ids = split_games(rows, 0.6)
        self.assertEqual(train_ids, [f"g{i:03d}" for i in range(6)])
        self.assertEqual(test_ids, [f"g{i:03d}" for i in range(6, 10)])
        self.assertTrue(set(train_ids).isdisjoint(test_ids))
        self.assertEqual(sum(1 for r in train if r.game_id == "g000"), 2)
        self.assertFalse(any(r.game_id == "g000" for r in test))

    def test_backtest_fits_on_train_only_and_flags_n(self):
        from tempfile import TemporaryDirectory
        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            src = root / "in.jsonl"
            report = root / "bt.json"
            model = root / "model.json"
            # Reverse the file so a file-order split would put the losses in train.
            rows = list(reversed(self._rows(50, lambda i: i < 30)))
            write_jsonl(src, rows)
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                rc = main([
                    "backtest", "--in", str(src), "--out", str(report), "--model-out", str(model),
                ])
            self.assertEqual(rc, 0)
            body = json.loads(report.read_text())
            self.assertTrue(body["game_disjoint"])
            self.assertEqual(body["train_games"], 30)
            self.assertEqual(body["test_games"], 20)
            self.assertEqual(body["train_game_ids"][0], "g000")
            self.assertEqual(body["test_game_ids"][0], "g030")
            self.assertGreater(body["model"]["w"], 0.9)
            self.assertFalse(body["v2_n_ge_30"])
            self.assertIn("v2_n>=30=no", buf.getvalue())
            saved = json.loads(model.read_text())
            self.assertGreater(saved["w"], 0.9)

            big = root / "big.jsonl"
            big_report = root / "big.json"
            write_jsonl(big, self._rows(80, lambda i: True))
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                self.assertEqual(main(["backtest", "--in", str(big), "--out", str(big_report)]), 0)
            big_body = json.loads(big_report.read_text())
            self.assertEqual(big_body["test_games"], 32)
            self.assertTrue(big_body["v2_n_ge_30"])
            self.assertGreaterEqual(big_body["test"]["v2"]["n"], 30)


class RecalibTests(_Env):
    def test_recalib_refits_isotonic_only_on_the_recent_window(self):
        from tempfile import TemporaryDirectory
        end = datetime(2026, 3, 1, tzinfo=timezone.utc)
        recent = end.isoformat()
        old = (end - timedelta(days=60)).isoformat()
        rows = []
        for i in range(40):
            rows.append(candidate(
                game_id=f"old{i}", ts=old, result=True,
                p_model_raw=0.20, bid=0.80, ask=0.80, price=0.80,
            ))
        for i in range(40):
            rows.append(candidate(
                game_id=f"new{i}", ts=recent, result=(i % 2 == 0),
                p_model_raw=0.20, bid=0.80, ask=0.80, price=0.80,
            ))
        with TemporaryDirectory() as tmp:
            src = Path(tmp) / "graded.jsonl"
            model_path = Path(tmp) / "model.json"
            write_jsonl(src, rows)
            seed = default_model()
            seed["w"] = 0.0
            seed["overs"] = {"price_lo": 0.41, "price_hi": 0.61, "min_hit_p": 0.57, "fitted": True, "n": 40}
            model_path.write_text(json.dumps(seed))
            buf = io.StringIO()
            with redirect_stdout(buf), redirect_stderr(io.StringIO()):
                rc = main(["recalib", "--in", str(src), "--model", str(model_path), "--days", "28"])
            self.assertEqual(rc, 0)
            saved = json.loads(model_path.read_text())
            self.assertEqual(saved["w"], 0.0)
            self.assertEqual(saved["overs"]["price_lo"], 0.41)
            self.assertFalse(saved["isotonic"]["identity"])
            self.assertAlmostEqual(apply_isotonic(saved["isotonic"], 0.8), 0.5, places=2)
            self.assertIn("identity=False", buf.getvalue())

            seed["isotonic"] = {"x": [0.0, 1.0], "y": [0.0, 0.2], "n": 40, "identity": False}
            model_path.write_text(json.dumps(seed))
            thin = [candidate(game_id=f"t{i}", ts=recent, result=bool(i % 2)) for i in range(5)]
            write_jsonl(src, thin)
            with redirect_stdout(io.StringIO()), redirect_stderr(io.StringIO()):
                main(["recalib", "--in", str(src), "--model", str(model_path), "--days", "28"])
            kept = json.loads(model_path.read_text())
            self.assertEqual(kept["isotonic"]["y"], [0.0, 0.2])
            self.assertIn("fewer than", kept["recalib_skipped"])


class NoOrderTests(_Env):
    def test_package_does_not_talk_to_a_book(self):
        root = Path(__file__).resolve().parents[1]
        banned = ("import requests", "place_order", "submit_order", "telegram", "import urllib", "alpaca")
        for path in root.glob("*.py"):
            text = path.read_text().lower()
            for token in banned:
                self.assertNotIn(token, text, f"{path.name} contains {token}")


if __name__ == "__main__":
    unittest.main()
