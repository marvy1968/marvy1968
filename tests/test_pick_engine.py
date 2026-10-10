import pandas as pd

from marv import pick_engine as PE


def _res():
    rows, gid = [], 0
    teams = ["A", "B", "C", "D"]
    strength = {"A": 10, "B": 3, "C": -3, "D": -10}
    for wk in range(12):
        for i, h in enumerate(teams):
            a = teams[(i + 1 + wk) % 4]
            if a == h:
                continue
            gid += 1
            rows.append({"game_id": str(gid), "season": 2026, "date": pd.Timestamp("2026-09-01") + pd.Timedelta(days=7 * wk),
                         "home": h, "away": a, "neutral": False, "hp": 24 + strength[h] / 2 + 1.5,
                         "ap": 24 + strength[a] / 2 - 1.5})
    return pd.DataFrame(rows)


def test_power_orders_teams():
    pw = PE.fit_power(_res(), pd.Timestamp("2026-12-31"), "nfl")
    m, t = PE.power_read(pw, "A", "D")
    assert m > 5 and 40 < t < 60
    assert PE.power_read(pw, "D", "A")[0] < 0


def test_simulate_and_decide_no_pct_text():
    sim = PE.simulate(7.0, 50.0, 13.0, 13.0, 0.1, spread=-3.0, line=45.5)
    assert sim["p_home"] > 0.6 and sim["p_home_cover"] > 0.5 and sim["p_over"] > 0.5
    d = PE.decide(sim, 7.0, 50.0, -3.0, 45.5)
    assert d["picks"] == {"ml": "home", "spread": "home", "total": "over"}
    r = {"home": "A", "away": "B", "power_margin": 6.0, "power_total": 49.0, "trend_margin": 8.0, "trend_total": 51.0,
         "margin": 7.0, "total": 50.0, "spread": -3.0, "line": 45.5, "sim": sim, **d,
         "ml_agree": {"power": True, "trend": True, "mc": True}}
    txt = PE.text(r, "cfb")
    assert "ML A" in txt and "3/3 agree" in txt and "%" not in txt


def test_game_never_raises(tmp_path):
    assert PE.game(tmp_path, "cfb", "A", "B") is None
    assert PE.game(tmp_path, "nba", "A", "B") is None
