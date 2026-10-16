"""Marv soccer live prop pricer (shadow): maths sanity, no network."""
import math

from marv.soccer_max import live_props as LP

PRM = {"f2": 0.523, "beta_lead": -0.024, "beta_trail": 0.1022, "end_minute": 93.0, "bayes_k": 1e9}


def test_rem_frac_monotone_and_bounds():
    assert abs(LP.rem_frac(0, 0.523) - 1.0) < 1e-9
    assert abs(LP.rem_frac(45, 0.523, ht=True) - 0.523) < 1e-9
    assert LP.rem_frac(93, 0.523) == 0.0
    xs = [LP.rem_frac(m, 0.523) for m in range(0, 94, 3)]
    assert all(a >= b for a, b in zip(xs, xs[1:]))


def test_probabilities_consistent():
    s = LP.price_state(1.6, 1.1, 1, 0, 60, PRM)
    for ln, t in s["totals"].items():
        assert abs(t["over"] + t["push"] + t["under"] - 1) < 1e-4
    assert s["totals"]["0.5"]["over"] == 1.0                    # one goal already in
    assert s["team_totals"]["home"]["0.5"]["over"] == 1.0
    lam = s["rem_away"]
    assert abs(s["btts"]["yes"] - (1 - math.exp(-lam))) < 1e-4  # home already scored
    assert s["rem_away"] > 1.1 * LP.rem_frac(60, 0.523)         # trailing team gets the trail bump


def test_whole_line_push():
    s = LP.price_state(1.2, 1.0, 1, 1, 80, PRM)
    t = s["totals"]["2"]                                         # 2 goals in, line 2 -> push if no more goals
    assert t["over"] > 0 and t["push"] > 0.5 and t["under"] == 0
