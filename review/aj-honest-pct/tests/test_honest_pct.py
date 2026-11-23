"""Offline checks for the AJ honest-percent stand-in.

No network and no Telegram. ``AJ_HONEST_PCT`` is set only inside each test
and restored afterward.
"""

from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from ajbet.calib import FAIR_CAP, calibrate
from ajbet.live_wincap import cap as wincap
from ajbet.me_shrink import NBA_SLOPE
from ajbet.paper_conf import MAX_SHIFT, assess
from ajbet.stake import kelly_fraction
from ajbet.value_scan import SEND_EDGE, SEND_P, emit_game_line, emit_prop


class _Flag:
    def __init__(self, value: str | None):
        self.value = value
        self.old = None

    def __enter__(self):
        self.old = os.environ.get("AJ_HONEST_PCT")
        if self.value is None:
            os.environ.pop("AJ_HONEST_PCT", None)
        else:
            os.environ["AJ_HONEST_PCT"] = self.value
        return self

    def __exit__(self, *_exc):
        if self.old is None:
            os.environ.pop("AJ_HONEST_PCT", None)
        else:
            os.environ["AJ_HONEST_PCT"] = self.old


def _prop(flag, **kwargs):
    defaults = dict(
        consensus_fair_p=0.72,
        pacer_p=0.78,
        sport="nba",
        shift=-0.12,
        real_hit=0.58,
        bovada_implied=0.55,
        decimal_odds=1.91,
    )
    defaults.update(kwargs)
    with _Flag(flag):
        return emit_prop(**defaults)


class HonestPctTests(unittest.TestCase):
    def test_nba_pacer_header_card_gate_and_stake_use_honest_map(self):
        decision = _prop("1")
        self.assertTrue(decision.flag)
        self.assertEqual(NBA_SLOPE, -0.047)
        self.assertIn("pacer:nba:flat_skip", decision.tags)
        self.assertLessEqual(decision.fair_p_after_calib, FAIR_CAP)
        self.assertAlmostEqual(decision.fair_p_after_pacer, 0.78)
        self.assertAlmostEqual(decision.capped_fair_p, 0.65)
        self.assertAlmostEqual(decision.p_final, 0.58)
        self.assertAlmostEqual(decision.header_p, 0.58)
        self.assertAlmostEqual(decision.card_p, 0.58)
        self.assertAlmostEqual(decision.gate_p, 0.58)
        self.assertAlmostEqual(decision.ev_p, 0.58)
        self.assertAlmostEqual(decision.stake_p, 0.58)
        self.assertEqual(decision.label, "calibrated")
        self.assertIn("(calibrated)", decision.header)
        self.assertIn("58%", decision.header)
        self.assertIn("58%", decision.win_line)
        self.assertIn("58%", decision.footnote)
        # The paper shift still exists, and the card does not use it.
        self.assertAlmostEqual(decision.p_cal, 0.78 + MAX_SHIFT)
        self.assertNotAlmostEqual(decision.card_p, decision.p_cal)
        self.assertAlmostEqual(decision.ev, 0.58 * 1.91 - 1.0)
        self.assertAlmostEqual(decision.stake, kelly_fraction(0.58, 1.91))

    def test_no_honest_map_caps_at_65_without_calibrated_label(self):
        decision = _prop("on", real_hit=None, pacer_p=0.78)
        self.assertAlmostEqual(decision.fair_p_after_pacer, 0.78)
        self.assertAlmostEqual(decision.capped_fair_p, 0.65)
        self.assertAlmostEqual(decision.p_final, 0.65)
        for probability in (
            decision.header_p,
            decision.card_p,
            decision.gate_p,
            decision.ev_p,
            decision.stake_p,
        ):
            self.assertAlmostEqual(probability, 0.65)
        self.assertEqual(decision.label, "model estimate")
        self.assertNotIn("calibrated", decision.header)
        self.assertNotIn("calibrated", decision.win_line)
        self.assertIn("(model estimate)", decision.header)
        self.assertIn("65%", decision.win_line)
        self.assertIn("none", decision.footnote)
        self.assertIn("pacer:nba:flat_skip", decision.tags)

    def test_alert_that_sends_at_78_does_not_send_at_58_unless_edge_is_4pct(self):
        off = _prop(None, bovada_implied=0.55)
        on = _prop("true", bovada_implied=0.55)
        self.assertAlmostEqual(off.gate_p, 0.78)
        self.assertGreaterEqual(off.gate_p, SEND_P)
        self.assertTrue(off.send)
        self.assertAlmostEqual(on.gate_p, 0.58)
        self.assertLess(on.gate_p, SEND_P)
        self.assertAlmostEqual(on.edge, 0.03)
        self.assertLess(on.edge, SEND_EDGE)
        self.assertFalse(on.send)

        priced = _prop("yes", bovada_implied=0.54)
        self.assertAlmostEqual(priced.edge, SEND_EDGE)
        self.assertTrue(priced.send)
        self.assertAlmostEqual(priced.gate_p, 0.58)

        # The 4% edge is measured from p_final. The raw 0.78 edge would be
        # 0.24 here and would send for the wrong reason.
        self.assertNotAlmostEqual(priced.edge, 0.78 - 0.54)

    def test_flag_off_matches_today(self):
        off = _prop(None)
        also_off = _prop("0")
        word_off = _prop("false")
        blank = _prop("")
        no = _prop("no")
        spelled = _prop("off")
        for decision in (off, also_off, word_off, blank, no, spelled):
            self.assertFalse(decision.flag)
            self.assertIsNone(decision.p_final)
            self.assertLessEqual(decision.fair_p_after_calib, FAIR_CAP)
            self.assertAlmostEqual(decision.fair_p_after_pacer, 0.78)
            self.assertAlmostEqual(decision.header_p, 0.78)
            self.assertAlmostEqual(decision.p_cal, 0.78 + MAX_SHIFT)
            self.assertAlmostEqual(decision.card_p, decision.p_cal)
            self.assertAlmostEqual(decision.gate_p, 0.78)
            self.assertAlmostEqual(decision.ev_p, 0.78)
            self.assertAlmostEqual(decision.stake_p, 0.78)
            self.assertEqual(decision.label, "calibrated")
            self.assertIn("(calibrated)", decision.header)
            self.assertIn("pacer:nba:flat_skip", decision.tags)
            self.assertTrue(decision.send)
            self.assertAlmostEqual(decision.stake, kelly_fraction(0.78, 1.91))
            self.assertIn("58%", decision.footnote)
            self.assertNotAlmostEqual(decision.card_p, 0.58)
            self.assertNotAlmostEqual(decision.gate_p, 0.58)
            self.assertEqual(wincap(0.78, "prop"), 0.78)

        mild = _prop(None, shift=-0.02)
        self.assertAlmostEqual(mild.card_p, 0.76)
        self.assertAlmostEqual(assess(0.78, shift=-0.02).p_cal, 0.76)
        self.assertAlmostEqual(assess(0.78, shift=-0.12).p_cal, 0.78 + MAX_SHIFT)

    def test_game_line_path_is_unchanged(self):
        self.assertEqual(wincap(0.90, "prop"), 0.90)
        self.assertEqual(wincap(0.90, "game"), FAIR_CAP)
        kwargs = dict(
            consensus_fair_p=0.90,
            bovada_implied=0.55,
            decimal_odds=1.91,
            real_hit=0.58,
            shift=-0.12,
        )
        with _Flag(None):
            off = emit_game_line(**kwargs)
        with _Flag("1"):
            on = emit_game_line(**kwargs)
        expected = wincap(calibrate(0.90), "game")
        self.assertAlmostEqual(expected, FAIR_CAP)
        for decision in (off, on):
            self.assertEqual(decision.kind, "game")
            self.assertIsNone(decision.p_final)
            self.assertIsNone(decision.fair_p_after_pacer)
            self.assertNotIn("pacer:nba:flat_skip", decision.tags)
            self.assertEqual(decision.label, "calibrated")
            self.assertAlmostEqual(decision.header_p, expected)
            self.assertAlmostEqual(decision.card_p, expected)
            self.assertAlmostEqual(decision.gate_p, expected)
            self.assertAlmostEqual(decision.stake_p, expected)
            self.assertNotAlmostEqual(decision.card_p, 0.58)
            self.assertIn("none", decision.footnote)
        self.assertEqual(off.header_p, on.header_p)
        self.assertEqual(off.card_p, on.card_p)
        self.assertEqual(off.gate_p, on.gate_p)
        self.assertEqual(off.stake, on.stake)
        self.assertEqual(off.send, on.send)
        self.assertEqual(off.label, on.label)

        with _Flag(None):
            plain = emit_prop(
                0.80,
                None,
                bovada_implied=0.55,
                decimal_odds=1.91,
                real_hit=0.58,
                shift=-0.12,
            )
        with _Flag("1"):
            flagged = emit_prop(
                0.80,
                None,
                bovada_implied=0.55,
                decimal_odds=1.91,
                real_hit=0.58,
                shift=-0.12,
            )
        capped = calibrate(0.80)
        self.assertLessEqual(capped, FAIR_CAP)
        self.assertNotAlmostEqual(capped, 0.58)
        for decision in (plain, flagged):
            self.assertEqual(decision.label, "calibrated")
            self.assertIsNone(decision.p_final)
            self.assertAlmostEqual(decision.header_p, capped)
            self.assertAlmostEqual(decision.card_p, capped)
            self.assertAlmostEqual(decision.gate_p, capped)
            self.assertAlmostEqual(decision.stake_p, capped)
            self.assertNotIn("pacer:nba:flat_skip", decision.tags)
        self.assertEqual(plain.header, flagged.header)
        self.assertEqual(plain.stake, flagged.stake)
        self.assertEqual(plain.send, flagged.send)

    def test_stake_uses_p_final(self):
        on = _prop("1", real_hit=0.58, decimal_odds=1.91)
        fallback = _prop("1", real_hit=None, decimal_odds=1.91)
        off = _prop(None, real_hit=0.58, decimal_odds=1.91)
        self.assertAlmostEqual(on.stake_p, on.p_final)
        self.assertAlmostEqual(on.stake, kelly_fraction(on.p_final, 1.91))
        self.assertAlmostEqual(on.stake, kelly_fraction(0.58, 1.91))
        self.assertNotAlmostEqual(on.stake, kelly_fraction(0.78, 1.91))
        self.assertAlmostEqual(fallback.stake_p, 0.65)
        self.assertAlmostEqual(fallback.stake, kelly_fraction(0.65, 1.91))
        self.assertAlmostEqual(off.stake, kelly_fraction(0.78, 1.91))
        self.assertNotAlmostEqual(off.stake, on.stake)


if __name__ == "__main__":
    unittest.main()
