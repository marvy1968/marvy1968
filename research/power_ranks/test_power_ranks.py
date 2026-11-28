"""Synthetic-league tests (known strengths). Run: python3 -m unittest test_power_ranks -v"""
import unittest
import numpy as np
import pandas as pd
import power_ranks as pr


def synth(seasons=(2022, 2023, 2024, 2025), confs=4, per=12, seed=11, coach_effect=8.0):
    rng = np.random.default_rng(seed)
    names = [f"C{c}T{i}" for c in range(confs) for i in range(per)]
    conf_of = {n: f"Conf{n[1]}" for n in names}
    coach = {n: rng.normal(0, 0.12) for n in names}           # true coach quality in win% units
    base = {n: rng.normal(0, 7) + coach_effect * coach[n] for n in names}
    games, teams, champs, truth = [], [], [], {}
    for s in seasons:
        cur = {n: 0.8 * base[n] + rng.normal(0, 3) for n in names}
        truth[s] = pd.Series(cur)
        for n in names:
            teams.append({"season": s, "team": n, "conference": conf_of[n]})
        d0 = pd.Timestamp(s, 9, 1)
        k = 0
        for c in range(confs):
            ms = [n for n in names if conf_of[n] == f"Conf{c}"]
            for i in ms:
                for j in ms:
                    if i < j:
                        k += 1
                        games.append((s, d0 + pd.Timedelta(days=k % 90), i, j))
        for _ in range(len(names) * 4):  # non-conference
            i, j = rng.choice(names, 2, replace=False)
            if conf_of[i] != conf_of[j]:
                k += 1
                games.append((s, d0 + pd.Timedelta(days=k % 90), i, j))
        champs.append({"season": s, "kind": "national", "team": truth[s].idxmax()})
    G = pd.DataFrame(games, columns=["season", "date", "home", "away"])
    s_map = {s: truth[s] for s in truth}
    G["hg"] = [max(0, round(24 + (s_map[s][h] - s_map[s][a] + 2.5) / 2 + rng.normal(0, 7))) for s, h, a in
               zip(G["season"], G["home"], G["away"])]
    G["ag"] = [max(0, round(24 - (s_map[s][h] - s_map[s][a] + 2.5) / 2 + rng.normal(0, 7))) for s, h, a in
               zip(G["season"], G["home"], G["away"])]
    # (margin is  ~ (diff+2.5)  with noise sd ~10)
    wp = lambda c: 0.5 + c
    coaches = pd.DataFrame({"team": names, "coach": [f"coach {n}" for n in names],
                            "career_w": [int(round(120 * wp(coach[n]))) for n in names],
                            "career_l": [int(round(120 * (1 - wp(coach[n])))) for n in names]})
    return G, pd.DataFrame(teams), pd.DataFrame(champs), coaches, truth


class T(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.G, cls.teams, cls.champs, cls.coaches, cls.truth = synth()
        cls.o = pr.build(cls.G, cls.teams, cls.champs, cls.coaches, None, derive=True)

    def test_ratings_track_true_strength(self):
        t = self.o["table"].set_index("team")
        self.assertGreater(t["rating"].corr(self.truth[2025][t.index]), 0.85)

    def test_national_reference_is_mean_of_last_three_title_season_ratings(self):
        R = pr.season_ratings(self.G.assign(date=pd.to_datetime(self.G["date"])))
        last3 = self.champs[self.champs["season"] < 2025].tail(3)
        exp = np.mean([R[int(s)][t] for s, t in zip(last3["season"], last3["team"])])
        self.assertAlmostEqual(self.o["national_ref"], exp, places=9)
        self.assertEqual(len(self.o["national_list"]), 3)

    def test_bottom_three_are_actually_the_weakest_in_each_conference(self):
        t = self.o["table"]
        for conf, (avg, names) in self.o["bottom"].items():
            g = t[t["conference"] == conf].sort_values("power")
            self.assertEqual(set(names), set(g.head(3)["team"]))
            self.assertAlmostEqual(avg, g.head(3)["power"].mean(), places=9)
            self.assertLess(avg, g["power"].mean())

    def test_every_team_ranked_in_every_conference(self):
        t = self.o["table"]
        self.assertEqual(len(t), 48)
        for conf, g in t.groupby("conference"):
            self.assertEqual(sorted(g["conf_rank"]), list(range(1, len(g) + 1)))

    def test_conference_champion_references_exist_for_all_conferences(self):
        self.assertEqual(len(self.o["conf_ref"]), 4)
        for c, (v, lst) in self.o["conf_ref"].items():
            self.assertTrue(np.isfinite(v)); self.assertEqual(len(lst), 3)

    def test_coach_weight_applied_when_a_real_effect_exists(self):
        G, teams, champs, coaches, _ = synth(confs=6, per=14, coach_effect=90.0, seed=0)
        o = pr.build(G, teams, champs, coaches, None, derive=True)
        self.assertGreater(o["coach_weight"], 0)
        self.assertTrue(o["coach_applied"])
        r = o["table"].set_index("team")
        self.assertAlmostEqual(r["coach_pts"].abs().max() > 0, True)

    def test_coach_never_applied_without_a_real_effect(self):
        for seed in range(6):
            G, teams, champs, coaches, _ = synth(confs=6, per=14, coach_effect=0.0, seed=seed)
            o = pr.build(G, teams, champs, coaches, None, derive=True)
            self.assertFalse(o["coach_applied"], f"seed {seed}: t={o['coach_t']:.1f}")
            self.assertTrue((o["table"]["coach_pts"] == 0).all())

    def test_injury_lowers_power_by_position_value(self):
        t = self.o["table"].iloc[0]["team"]
        inj = pd.DataFrame({"team": [t, t], "player": ["a", "b"], "position": ["QB", "WR"], "status": ["Out", "Questionable"]})
        o2 = pr.build(self.G, self.teams, self.champs, self.coaches, inj, derive=True)
        r = o2["table"].set_index("team").loc[t]
        self.assertAlmostEqual(r["injury_pts"], -(6.0 + 0.8 * 0.25), places=9)
        self.assertAlmostEqual(r["power"], r["rating"] + r["coach_pts"] + r["injury_pts"], places=9)

    def test_report_renders(self):
        s = pr.format_report(self.o)
        self.assertIn("National champion reference", s); self.assertIn("bottom-3 avg", s); self.assertIn("Worst 10", s)


if __name__ == "__main__":
    unittest.main()
