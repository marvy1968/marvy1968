import unittest

from marv import live_props

SUMMARY = {"boxscore": {"players": [
    {"team": {"displayName": "Dallas Cowboys"}, "statistics": [
        {"name": "passing", "labels": ["C/ATT", "YDS", "AVG", "TD", "INT"], "athletes": [
            {"athlete": {"displayName": "Dak Prescott"}, "stats": ["12/17", "151", "8.9", "1", "0"]}]},
        {"name": "receiving", "labels": ["REC", "YDS", "AVG", "TD", "LONG", "TGTS"], "athletes": [
            {"athlete": {"displayName": "CeeDee Lamb"}, "stats": ["5", "62", "12.4", "1", "28", "7"]}]}]}]}}

ODDS = {"bookmakers": [{"key": "bovada", "markets": [
    {"key": "player_reception_yds", "last_update": "2026-10-09T01:10:00Z", "outcomes": [
        {"name": "Over", "description": "CeeDee Lamb", "point": 91.5, "price": -120},
        {"name": "Under", "description": "CeeDee Lamb", "point": 91.5, "price": -110}]},
    {"key": "player_pass_yds", "last_update": "2026-10-09T01:10:00Z", "outcomes": [
        {"name": "Over", "description": "Dak Prescott", "point": 270.5, "price": -115},
        {"name": "Under", "description": "Dak Prescott", "point": 270.5, "price": -115}]}]}]}


class LivePropsTests(unittest.TestCase):
    def test_box_and_snapshot(self):
        box = live_props.box_stats(SUMMARY)
        self.assertEqual(box["CeeDee Lamb"]["receiving"]["YDS"], 62.0)
        self.assertEqual(box["Dak Prescott"]["passing"]["YDS"], 151.0)
        rows = live_props.live_prop_snapshot(ODDS, box)
        lamb = next(r for r in rows if r["player"] == "CeeDee Lamb")
        self.assertEqual((lamb["line"], lamb["so_far"], lamb["over"], lamb["under"]), (91.5, 62.0, -120, -110))
        self.assertEqual(next(r for r in rows if r["player"] == "Dak Prescott")["so_far"], 151.0)


if __name__ == "__main__":
    unittest.main()
