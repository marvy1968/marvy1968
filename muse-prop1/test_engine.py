"""Unit tests for engine.py — run: python3 -m pytest test_engine.py -v  (or python3 test_engine.py)"""
from engine import (
    PaceInput, BasketballPaceInput, project_final, project_basketball_final,
    prob_over_yards, prob_over_count, prob_over_td,
    american_to_implied, remove_vig, evaluate_pick, passes_gates, SPORTS,
)

def test_shrinkage_early_game_trusts_pregame():
    # 5 min in, hot start: 80 yds already. Projection should NOT explode.
    mean, sd = project_final(PaceInput(current=80, elapsed_min=5, pregame_mean=240,
                                       pregame_sd=55, pregame_per_play=3.8, plays_so_far=12))
    assert 200 < mean < 320, mean
    assert sd > 20

def test_pace_projection_late_game():
    mean, sd = project_final(PaceInput(current=180, elapsed_min=45, pregame_mean=240,
                                       pregame_sd=55, pregame_per_play=3.8, plays_so_far=48))
    assert mean > 180
    assert sd < 55  # less variance left

def test_prob_over_increases_with_projection():
    assert prob_over_yards(280, 50, 245.5) > prob_over_yards(220, 50, 245.5)
    assert 0 < prob_over_yards(245, 50, 245.5) < 1

def test_half_point_line_is_not_shifted_another_half():
    from engine import over_continuity_point
    # 245.5 is already the boundary. A second +0.5 would put P(over) near 0.46 at sd=5.
    assert over_continuity_point(245.5) == 245.5
    assert abs(prob_over_yards(245.5, 5, 245.5) - 0.5) < 0.01
    # Integer line still uses +0.5 so Over 245 is P(X >= 246), not a push.
    assert over_continuity_point(245) == 245.5
    assert prob_over_yards(245, 5, 245) < 0.49

def test_vig_removal():
    # -110 / -110 => implied 52.38% each, no-vig 50% each
    o, u = remove_vig(american_to_implied(-110), american_to_implied(-110))
    assert abs(o - 0.5) < 0.001 and abs(u - 0.5) < 0.001

def test_ev_and_gates():
    # Model 64%, fair 50%, Bovada -110 => EV ~ +22%
    ev = evaluate_pick(0.64, -110)["ev"]
    assert ev > 0.15
    ok, _ = passes_gates(0.64, 0.50, ev)
    assert ok
    ok2, _ = passes_gates(0.53, 0.50, evaluate_pick(0.53, -110)["ev"])
    assert not ok2  # 3pp edge is noise

def test_td_poisson():
    assert prob_over_td(0.7) > 0.4  # P(at least 1 TD) with mean 0.7
    assert prob_over_td(0.1) < 0.15

def test_count_line_does_not_add_an_extra_half():
    # Over 0.5 is P(X >= 1). Over 1.5 is P(X >= 2). Not P(X >= 2) and P(X >= 3).
    assert abs(prob_over_count(0.0, 0.5) - 0.0) < 1e-9
    assert prob_over_count(0.7, 0.5) > prob_over_count(0.7, 1.5)

def test_gate_edges_are_closed_at_the_boundary():
    assert passes_gates(0.60, 0.50, 0.05)[0]
    # 6pp is under the 7pp edge gate even when EV is large.
    assert not passes_gates(0.56, 0.50, 0.20)[0]
    # Edge can clear while EV is still short of 5%.
    assert not passes_gates(0.70, 0.50, 0.049)[0]

def test_basketball_projection_normal():
    # 14 pts in 22 min played, 20 min elapsed, pregame 18.5 pts / 30 min
    mean, sd = project_basketball_final(BasketballPaceInput(
        current=14, minutes_played=18, elapsed_min=20, pregame_mean=18.5,
        pregame_sd=5.0, pregame_per_min=0.62, pregame_minutes=30, fouls=1,
        poss_so_far=70, season_poss_per_min=1.75))
    assert 14 < mean < 28, mean
    assert sd > 0

def test_basketball_foul_trouble_shrinks():
    base, _ = project_basketball_final(BasketballPaceInput(
        current=10, minutes_played=12, elapsed_min=15, pregame_mean=18,
        pregame_sd=5, pregame_per_min=0.6, pregame_minutes=30, fouls=0))
    foul, _ = project_basketball_final(BasketballPaceInput(
        current=10, minutes_played=12, elapsed_min=15, pregame_mean=18,
        pregame_sd=5, pregame_per_min=0.6, pregame_minutes=30, fouls=3))
    assert foul < base

def test_sport_registry_has_all_four():
    assert {"nfl", "ncaaf", "ncaab", "ncaaw"} <= set(SPORTS)
    assert SPORTS["ncaaf"]["sd_inflation"] > SPORTS["nfl"]["sd_inflation"]
    assert SPORTS["ncaab"]["total_min"] == 40
    assert SPORTS["nba"]["total_min"] == 48
    assert SPORTS["nba"]["propline_key"] is None

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print(f"PASS {name}")
    print("all engine tests passed")
