"""Synthetic lane-pause coverage. No network and no live card."""
import importlib.util
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from marv import betcard, dailypicks, lane_pause
from marv.models import Game, Odds, Pick, Prediction

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / "review" / "card-fixes" / "fixtures" / "synthetic-review-cases.json"


def _overlay():
    path = ROOT / "review" / "card-fixes" / "overlays" / "feed" / "marv_lane_pause.py"
    spec = importlib.util.spec_from_file_location("review_marv_lane_pause", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _pred(sport, home, away, picks, total=56.5):
    game = Game("g-" + away, sport, datetime(2030, 1, 1, 23, tzinfo=timezone.utc), home, away,
                odds=Odds(total=total, over_price=-110, under_price=-110, spread=-3.5))
    return Prediction(game, 30, 27, 3, total, 0.55, picks=picks)


def _bet(sport, market, side, units=0.5, tier="SIGNAL", score=54.4, game="Example Away @ Example Home"):
    return betcard.Bet(sport, game, "2030-01-01T23:00:00+00:00", "Example Home", "Example Away",
                       market, side, 50.5, -110, tier, score, units, "synthetic")


def _leg(sport, game, market, side, strength, price=-110):
    home, away = game.split(" @ ")[1], game.split(" @ ")[0]
    return {"sport": sport, "game": game, "home": home, "away": away, "market": market, "side": side,
            "line": 50.5, "price": price, "tier": "SIGNAL", "strength": strength, "why": "synthetic",
            "start": "2030-01-01T23:00:00+00:00"}


class LanePauseTests(unittest.TestCase):
    def setUp(self):
        lane_pause.drain_tracking()
        self._env = mock.patch.dict(os.environ, {"MARV_LANE_PAUSE": "on"}, clear=False)
        self._env.start()
        os.environ.pop("MARV_LANE_PAUSE_LANES", None)
        self.feed = tempfile.TemporaryDirectory()
        os.environ["MARV_FEED_ROOT"] = self.feed.name

    def tearDown(self):
        lane_pause.drain_tracking()
        self._env.stop()
        self.feed.cleanup()

    def test_default_lanes_are_unchanged(self):
        self.assertEqual(lane_pause.DEFAULT, "nhl:ml,nhl:total,mlb:total,cfb:total")
        self.assertEqual(lane_pause.lanes(), {("nhl", "ml"), ("nhl", "total"), ("mlb", "total"), ("cfb", "total")})
        overlay = _overlay()
        self.assertEqual(overlay.DEFAULT, lane_pause.DEFAULT)

    def test_fallback_predicate_matches_shared_predicate(self):
        overlay = _overlay()
        chosen = {("cfb", "total"), ("nhl", "ml"), ("nhl", "total"), ("mlb", "total")}
        samples = [
            {"sport": "cfb", "market": "total", "side": "Over"},
            {"sport": "CFB", "market": "Total", "side": "Under"},
            {"sport": "cfb", "market": "total", "master_extreme": True, "side": "Over"},
            {"sport": "nfl", "market": "total", "side": "Over"},
            {"sport": "nhl", "market": "ml", "side": "Home"},
            SimpleNamespace(sport="cfb", market="total", side="Under"),
            SimpleNamespace(sport="nfl", market="spread", side="Home"),
        ]
        for sample in samples:
            self.assertEqual(overlay._paused_fallback(sample, chosen), lane_pause.paused(sample, chosen), sample)

    def test_model_tag_gap_and_direct_select_drop_both_sides(self):
        model = betcard.candidates("cfb", [
            _pred("cfb", "Example Home", "Example Away", [Pick("total", "Under", 56.5, -110, 0.57, 0.08, 0.04, ["confidence"])]),
        ]) + betcard.candidates("nfl", [
            _pred("nfl", "Other Home", "Other Away", [Pick("ml", "Other Home", -150, -150, 0.7, 0.1, 0.08)]),
        ])
        self.assertEqual([bet.sport for bet in model], ["nfl"])
        slate = [Game("t1", "cfb", datetime(2030, 1, 1, 23, tzinfo=timezone.utc), "Example Home", "Example Away",
                      odds=Odds(total=56.5)),
                 Game("t2", "nfl", datetime(2030, 1, 1, 23, tzinfo=timezone.utc), "Other Home", "Other Away",
                      odds=Odds(total=44.0))]
        tags = betcard.tag_candidates("cfb", slate[:1], {"t1": [("OVER-FADE", "Under"), ("OVER-FADE", "Over")]})
        tags += betcard.tag_candidates("nfl", slate[1:], {"t2": [("TOTAL-INFLATED", "Under")]})
        self.assertEqual([(bet.sport, bet.side) for bet in tags], [("nfl", "Under")])
        start = "2030-01-01T23:00:00+00:00"
        gaps = betcard.gap_candidates([
            {"sport": "cfb", "market": "total", "home": "Example Home", "away": "Example Away", "start": start,
             "side": "Over", "line": 39.0, "price": -110, "pinnacle_line": 40},
            {"sport": "cfb", "market": "total", "home": "Second Home", "away": "Second Away", "start": start,
             "side": "Under", "line": 56.5, "price": -110, "pinnacle_line": 55.5},
            {"sport": "nfl", "market": "total", "home": "Other Home", "away": "Other Away", "start": start,
             "side": "Over", "line": 44.0, "price": -110, "pinnacle_line": 45},
        ])
        self.assertEqual([bet.sport for bet in gaps], ["nfl"])
        mixed = [_bet("nfl", "spread", "Example Home", units=2.0, tier="HIGH", score=0.1),
                 _bet("cfb", "total", "Over"), _bet("cfb", "total", "Under", game="Second Away @ Second Home")]
        chosen = betcard.select(mixed)
        self.assertEqual([(bet.sport, bet.market, bet.units) for bet in chosen], [("nfl", "spread", 2.0)])
        tracked = lane_pause.drain_tracking()
        self.assertTrue(tracked)
        self.assertTrue(all(item[0] in {"model", "tag", "gap", "select"} for item in tracked))
        self.assertFalse(any(getattr(item, "sport", None) == "cfb" and getattr(item, "market", None) == "total"
                             for item in chosen))

    def test_pause_off_keeps_order_and_per_lane_file_reopens_one_lane(self):
        rows = [_bet("nfl", "ml", "Example Home", units=1.5, tier="HIGH", score=0.2),
                _bet("cfb", "total", "Over", units=0.5),
                _bet("nhl", "ml", "Example Home", units=0.5, tier="MODEL", score=0.05, game="Night Away @ Night Home")]
        self.assertEqual([bet.sport for bet in lane_pause.partition(rows, "select")], ["nfl"])
        os.environ["MARV_LANE_PAUSE"] = "off"
        self.assertEqual([bet.sport for bet in lane_pause.partition(rows, "select")], ["nfl", "cfb", "nhl"])
        self.assertEqual([bet.units for bet in rows], [1.5, 0.5, 0.5])
        os.environ["MARV_LANE_PAUSE"] = "on"
        Path(self.feed.name, "MARV_LANE_PAUSE_OFF_cfb_total").write_text("")
        self.assertNotIn(("cfb", "total"), lane_pause.lanes())
        self.assertIn(("nhl", "ml"), lane_pause.lanes())
        self.assertEqual([bet.sport for bet in lane_pause.partition(rows, "select")], ["nfl", "cfb"])

    def test_master_extreme_stays_actionable(self):
        leg = {"sport": "cfb", "market": "total", "side": "Over", "master_extreme": True, "game": "A @ B"}
        self.assertFalse(lane_pause.paused(leg))

    def test_daily_betcard_and_parlay_parity(self):
        legs = [
            _leg("nfl", "Alpha @ Beta", "ml", "Beta", 3.0),
            _leg("nfl", "Gamma @ Delta", "ml", "Delta", 2.0),
            _leg("cfb", "Example Away @ Example Home", "total", "Over", 9.0),
            _leg("cfb", "Second Away @ Second Home", "total", "Under", 8.0),
        ]
        now = datetime(2030, 1, 1, 15, tzinfo=timezone.utc)
        card = dailypicks.build(legs, [], ["nfl", "cfb"], now)
        self.assertEqual([row["game"] for row in card["ml"]], ["Alpha @ Beta", "Gamma @ Delta"])
        self.assertEqual(card["ou"], [])
        self.assertEqual(sorted(card["parlays"]), ["nfl"])
        paused_games = {row["game"] for row in card["track_paused"]}
        self.assertEqual(paused_games, {"Example Away @ Example Home", "Second Away @ Second Home"})
        self.assertTrue(all(row.get("tracking_only") for row in card["track_paused"]))
        for parlay in card["parlays"]["nfl"]:
            self.assertTrue(all(leg["sport"] == "nfl" for leg in parlay["legs"]))
        os.environ["MARV_LANE_PAUSE"] = "off"
        restored = lane_pause.split_card(card)
        self.assertEqual(len(restored["ou"]), 2)
        self.assertEqual(restored["track_paused"], [])
        os.environ["MARV_LANE_PAUSE"] = "on"
        logged_card = dailypicks.build(legs, [], ["nfl", "cfb"], now)
        with tempfile.TemporaryDirectory() as state:
            logged = dailypicks.log_card(Path(state), logged_card)
            again = dailypicks.log_card(Path(state), logged_card)
            book = json.loads((Path(state) / "marv_predict" / dailypicks.LOG).read_text())
            record = dailypicks.record(Path(state))
        self.assertGreaterEqual(logged, 1)
        self.assertEqual(again, 0)
        self.assertEqual(sum(row["kind"] == "straight" for row in book), 2)
        self.assertEqual(sum(row["kind"] == "paused_track" for row in book), 2)
        self.assertNotIn("paused_track", record)
        bets = betcard.select([
            _bet("nfl", "ml", "Beta", units=1.5, tier="HIGH", score=0.2, game="Alpha @ Beta"),
            _bet("cfb", "total", "Over"),
            _bet("cfb", "total", "Under", game="Second Away @ Second Home"),
        ])
        self.assertEqual([(bet.game, bet.units) for bet in bets], [("Alpha @ Beta", 1.5)])
        with tempfile.TemporaryDirectory() as state:
            lane_pause.persist_tracking(state, "2030-01-01")
            lines = (Path(state) / "betcard_lane_pause.jsonl").read_text().strip().splitlines()
            self.assertEqual(lane_pause.persist_tracking(state, "2030-01-01"), 0)
        self.assertEqual(len(lines), 2)
        self.assertTrue(all(json.loads(line)["track_only"] for line in lines))

    def test_fixture_pause_boundary(self):
        payload = json.loads(FIXTURE.read_text())
        self.assertTrue(payload["synthetic"])
        actionable = lane_pause.partition(payload["pause_boundary"]["bets"], "fixture")
        self.assertEqual(actionable, [])
        self.assertEqual(len(lane_pause.drain_tracking()), 1)

    def test_old_betcard_wrapper_filters_when_shared_flag_is_absent(self):
        overlay = _overlay()
        paused = _bet("cfb", "total", "Over")
        kept = _bet("nfl", "ml", "Example Home", tier="HIGH", score=0.2)
        names = ("candidates", "tag_candidates", "gap_candidates", "select", "log")
        saved = {name: getattr(betcard, name) for name in names}
        flags = {name: getattr(saved[name], "_lane_pause", None) for name in names if name != "log"}
        try:
            for name, fn in saved.items():
                if name != "log":
                    setattr(fn, "_lane_pause", False)

            def fake(sport, preds, projections=None):
                return [paused, kept]

            betcard.candidates = fake
            overlay.apply_betcard()
            out = betcard.candidates("cfb", None)
            self.assertEqual([bet.sport for bet in out], ["nfl"])
            self.assertTrue(any(bet.sport == "cfb" for _, bet in lane_pause.drain_tracking()))
        finally:
            for name, fn in saved.items():
                setattr(betcard, name, fn)
                if name in flags and flags[name] is not None:
                    setattr(fn, "_lane_pause", flags[name])
            lane_pause.drain_tracking()


class HealthScriptTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.mocks = self.root / "bin"
        self.mocks.mkdir()
        self.log = self.root / "runuser.log"
        self._write(self.mocks / "systemctl", """#!/bin/bash
if [[ "$*" == *is-active* ]]; then
  if [[ -n "${MOCK_INACTIVE:-}" ]]; then exit 1; fi
  exit 0
fi
if [[ "$*" == *"-p Result"* ]]; then printf '%s\\n' "${MOCK_RESULT:-success}"; exit 0; fi
if [[ "$*" == *ExecMainExitTimestampMonotonic* ]]; then printf '%s\\n' "${MOCK_LAST-0}"; exit 0; fi
exit 0
""")
        self._write(self.mocks / "df", "#!/bin/bash\nprintf 'Use%%\\n10%%\\n'\n")
        self._write(self.mocks / "journalctl", "#!/bin/bash\nexit 0\n")
        self._write(self.mocks / "runuser", f"""#!/bin/bash
printf '%s\\n' "$*" >> {self.log}
prev=""
for a in "$@"; do
  if [[ "$prev" == "--file" ]]; then cp "$a" {self.root / "alert.txt"}; fi
  prev="$a"
done
exit 0
""")
        self.script = ROOT / "deploy" / "marv-health.sh"
        self.env = os.environ.copy()
        self.env["PATH"] = f"{self.mocks}:/usr/bin:/bin"
        self.env.pop("MOCK_INACTIVE", None)
        self.env.pop("MOCK_LAST", None)
        self._bots = 0

    def tearDown(self):
        self.tmp.cleanup()

    def _write(self, path, text):
        path.write_text(text)
        path.chmod(0o755)

    def _run(self, uptime, bot=None, keep_log=False, **extra):
        self._bots += 1
        if not keep_log and self.log.exists():
            self.log.unlink()
        bot = Path(bot) if bot else self.root / f"bot{self._bots}"
        uptime_path = self.root / f"uptime{self._bots}"
        uptime_path.write_text(str(uptime) + "\n")
        trace = self.root / f"trace{self._bots}"
        alert = self.root / "alert.txt"
        if alert.exists() and bot not in (None,):
            alert.unlink()
        env = dict(self.env)
        env.update(extra)
        env["MARV_HEALTH_DIR"] = str(bot)
        env["MARV_HEALTH_UPTIME_FILE"] = str(uptime_path)
        env["MARV_HEALTH_TRACE"] = str(trace)
        import subprocess
        proc = subprocess.run(["bash", str(self.script)], env=env, text=True, capture_output=True)
        return proc, trace.read_text().strip() if trace.exists() else "", bot

    def test_script_parses(self):
        import subprocess
        proc = subprocess.run(["bash", "-n", str(self.script)], capture_output=True, text=True)
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_long_uptime_is_an_integer_and_not_a_false_stale_alert(self):
        seconds = 1997030.12
        now_us = int(seconds * 1000000)
        self.assertNotIn("e", str(now_us))
        last = now_us - 3600000000
        proc, trace, _bot = self._run(seconds, MOCK_LAST=str(last))
        self.assertEqual(proc.returncode, 0, proc.stderr)
        now, up_hours, since = trace.split()
        self.assertEqual(now, str(now_us))
        self.assertNotIn("e", trace)
        self.assertEqual(since, "1")
        self.assertGreaterEqual(int(up_hours), 26)
        self.assertFalse(self.log.exists())

    def test_scientific_uptime_is_a_failed_check_and_does_not_alert(self):
        fake = self.mocks / "awk"
        self._write(fake, "#!/bin/bash\nprintf '%s\\n' '1.99703e+12'\n")
        proc, _trace, _bot = self._run("1997030.12")
        self.assertNotEqual(proc.returncode, 0)
        self.assertIn("marv-health:", proc.stderr)
        self.assertFalse(self.log.exists())

    def test_missing_and_malformed_previous_timestamp(self):
        ok, trace, _bot = self._run(3600, MOCK_LAST="")
        self.assertEqual(ok.returncode, 0, ok.stderr)
        self.assertTrue(trace.startswith("3600000000 "))
        self.assertFalse(self.log.exists())
        bad, _trace, _bot = self._run(3600, MOCK_LAST="1.99703e+12")
        self.assertNotEqual(bad.returncode, 0)
        self.assertIn("not an integer", bad.stderr)
        self.assertFalse((self.root / "alert.txt").exists())

    def test_twenty_six_hour_boundary(self):
        stale, _trace, _bot = self._run(93600, MOCK_LAST="0")
        self.assertEqual(stale.returncode, 0, stale.stderr)
        alert = (self.root / "alert.txt").read_text()
        self.assertIn("hasn't finished since the VM started 26h ago", alert)
        fresh, _trace, _bot = self._run(93599, MOCK_LAST="0")
        self.assertEqual(fresh.returncode, 0, fresh.stderr)
        self.assertFalse(self.log.exists())
        now_us = int(200000 * 1000000)
        _exact, _trace, _bot = self._run(200000, MOCK_LAST=str(now_us - 93600000000))
        self.assertIn("no daily run in the last 26 hours", (self.root / "alert.txt").read_text())
        under, _trace, _bot = self._run(200000, MOCK_LAST=str(now_us - 93599999999))
        self.assertEqual(under.returncode, 0, under.stderr)
        self.assertFalse((self.root / "alert.txt").exists())

    def test_service_problem_still_exits_zero_and_alerts_once(self):
        first, _trace, bot = self._run(3600, MOCK_INACTIVE="1")
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertIn("marv-live is not running", (self.root / "alert.txt").read_text())
        second, _trace, _bot = self._run(3600, bot=bot, keep_log=True, MOCK_INACTIVE="1")
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.log.read_text().count("notify"), 1)

    def test_ofmt_token_matches_the_audit_example(self):
        import shutil
        import subprocess
        awk = shutil.which("gawk") or shutil.which("awk")
        proc = subprocess.run([awk, "-v", "n=1997030.12", 'BEGIN{printf "%.6g\\n", n * 1000000}'],
                              capture_output=True, text=True)
        self.assertEqual(proc.stdout.strip(), "1.99703e+12")


if __name__ == "__main__":
    unittest.main()
