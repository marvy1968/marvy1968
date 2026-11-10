"""Unit tests for engine.py — run: python3 -m pytest test_engine.py -v  (or python3 test_engine.py)"""
from engine import (
    PaceInput, BasketballPaceInput, TeamPaceInput, project_final, project_basketball_final,
    project_team_total, team_possessions, evaluate_team_pick,
    prob_over_yards, prob_over_td, anchor_projection,
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

def test_vig_removal():
    # -110 / -110 => implied 52.38% each, no-vig 50% each
    o, u = remove_vig(american_to_implied(-110), american_to_implied(-110))
    assert abs(o - 0.5) < 0.001 and abs(u - 0.5) < 0.001

def test_ev_and_gates():
    # Model 64%, fair 50%, market -110 => EV ~ +22%
    ev = evaluate_pick(0.64, -110)["ev"]
    assert ev > 0.15
    ok, _ = passes_gates(0.64, 0.50, ev)
    assert ok
    ok2, _ = passes_gates(0.53, 0.50, evaluate_pick(0.53, -110)["ev"])
    assert not ok2  # 3pp edge is noise

def test_td_poisson():
    assert prob_over_td(0.7) > 0.4  # P(at least 1 TD) with mean 0.7
    assert prob_over_td(0.1) < 0.15

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

def test_basketball_use_live_rate_false_forces_pregame_rate():
    # pace-vs-avg decomposition test (pace-vs-avg-test-spec-2026-11-08):
    # use_live_rate=False must use the pregame per-minute rate exactly,
    # regardless of the player's live stat/minutes. Same pace input in both
    # calls, so the only difference is the rate source. And the default
    # (True) must be unchanged behaviour.
    common = dict(minutes_played=18, elapsed_min=20, pregame_mean=18.5,
                  pregame_sd=5.0, pregame_per_min=0.62, pregame_minutes=30,
                  fouls=1, poss_so_far=70, season_poss_per_min=1.75)
    hot, _ = project_basketball_final(BasketballPaceInput(
        current=20, use_live_rate=False, **common))
    cold, _ = project_basketball_final(BasketballPaceInput(
        current=2, use_live_rate=False, **common))
    # The remaining projection (projected - current) must be identical: the
    # rate used is the pregame rate in both cases, whatever the live stat.
    assert abs((hot - 20) - (cold - 2)) < 1e-9, (hot, cold)
    # With the live rate on, a hot start must project higher than a cold one.
    hot_live, _ = project_basketball_final(BasketballPaceInput(
        current=20, **common))
    cold_live, _ = project_basketball_final(BasketballPaceInput(
        current=2, **common))
    assert hot_live > cold_live, (hot_live, cold_live)
    # And the live-rate projection must differ from the forced-pregame one
    # (the parameter actually does something).
    assert abs(hot_live - hot) > 1e-9, (hot_live, hot)

def test_team_use_live_pace_false_uses_season_pace():
    # Rate-only rework (pace-off-test-spec-2026-11-09): use_live_pace=False
    # must project remaining possessions at season pace exactly, whatever
    # the live game pace is. Same live rate in both calls, so the only
    # difference is the pace source. And the flag must actually do
    # something (differ from the fast-pace projection).
    base = dict(current=40, elapsed_min=20, pregame_mean=78, pregame_sd=10,
                poss_so_far=42, season_poss_per_min=1.75)
    off_fast, _ = project_team_total(TeamPaceInput(
        game_poss_per_min_live=2.2, use_live_pace=False, **base))
    on_season, _ = project_team_total(TeamPaceInput(
        game_poss_per_min_live=1.75, **base))
    assert abs(off_fast - on_season) < 1e-9, (off_fast, on_season)
    on_fast, _ = project_team_total(TeamPaceInput(
        game_poss_per_min_live=2.2, **base))
    assert abs(on_fast - off_fast) > 1e-9, (on_fast, off_fast)
    assert on_fast > off_fast, (on_fast, off_fast)


def test_basketball_use_live_pace_false_skips_pace_ratio():
    # Rate-only rework (pace-off-test-spec-2026-11-09): use_live_pace=False
    # must equal the projection with the pace ratio at exactly 1.0
    # (poss_so_far = elapsed x season pace). Same live rate in both calls.
    common = dict(minutes_played=18, elapsed_min=20, pregame_mean=18.5,
                  pregame_sd=5.0, pregame_per_min=0.62, pregame_minutes=30,
                  fouls=1, season_poss_per_min=1.75)
    off_fast, _ = project_basketball_final(BasketballPaceInput(
        current=14, poss_so_far=90, use_live_pace=False, **common))
    on_neutral, _ = project_basketball_final(BasketballPaceInput(
        current=14, poss_so_far=35, **common))  # 35 = 20 min x 1.75
    assert abs(off_fast - on_neutral) < 1e-9, (off_fast, on_neutral)
    on_fast, _ = project_basketball_final(BasketballPaceInput(
        current=14, poss_so_far=90, **common))
    assert abs(on_fast - off_fast) > 1e-9, (on_fast, off_fast)
    assert on_fast > off_fast, (on_fast, off_fast)


def test_anchor_projection():
    # Grok flaw 1: the projection is shrunk toward the CURRENT posted line.
    # weight 0 = raw projection, weight 1 = the line, 0.5 = the midpoint.
    assert anchor_projection(300.0, 245.5, 0.0) == 300.0
    assert anchor_projection(300.0, 245.5, 1.0) == 245.5
    assert abs(anchor_projection(300.0, 245.5, 0.5) - 272.75) < 1e-9
    # A projection already on the line does not move, whatever the weight.
    assert anchor_projection(245.5, 245.5, 0.5) == 245.5
    # Out-of-range weights are rejected, not silently clamped.
    for bad in (-0.1, 1.1):
        try:
            anchor_projection(300.0, 245.5, bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"weight {bad} should raise")

def test_football_use_live_pace_false_uses_season_pace():
    # Rate-only rework (pace-off-test-spec-2026-11-09), football arm:
    # use_live_pace=False must project remaining plays at season pace
    # exactly, whatever the live plays/min is. Same live rate in both calls.
    # current=250 pins the live per-play rate at its cap (1.5x pregame) in
    # every call, so the ONLY difference between the calls is the pace.
    base = dict(current=250, elapsed_min=20, pregame_mean=245.5,
                pregame_sd=55, pregame_per_play=3.8,
                season_plays_per_min=1.05)
    # Fast live pace (40 plays / 20 min = 2.0/min) with pace off == season
    # pace (21 plays / 20 min = 1.05/min) with pace on.
    off_fast, _ = project_final(PaceInput(plays_so_far=40, use_live_pace=False, **base))
    on_season, _ = project_final(PaceInput(plays_so_far=21, **base))
    assert abs(off_fast - on_season) < 1e-9, (off_fast, on_season)
    on_fast, _ = project_final(PaceInput(plays_so_far=40, **base))
    assert abs(on_fast - off_fast) > 1e-9, (on_fast, off_fast)
    assert on_fast > off_fast, (on_fast, off_fast)

def test_evaluate_team_pick_anchor():
    # Grok flaw 1, team picks: the projection is anchored to the line before
    # the gate. A projection far above the line still fires (edge survives
    # the anchor), and the anchor weight is honored (weight 0 = raw).
    pick = evaluate_team_pick(85, 8, 75.5, -110, -110)
    assert pick["side"] == "over" and pick["ok"], pick
    # With the anchor off, the same projection fires with a bigger edge.
    raw = evaluate_team_pick(85, 8, 75.5, -110, -110, line_anchor_weight=0.0)
    assert raw["ok"] and raw["model_p"] > pick["model_p"], (raw, pick)
    # Anchoring a projection already on the line changes nothing.
    on_line = evaluate_team_pick(75.5, 8, 75.5, -110, -110)
    assert not on_line["ok"], on_line

def test_sport_registry_has_all_four():
    assert set(SPORTS) == {"nfl", "ncaaf", "ncaab", "ncaaw", "nba"}
    assert SPORTS["ncaaf"]["sd_inflation"] > SPORTS["nfl"]["sd_inflation"]
    assert SPORTS["ncaab"]["total_min"] == 40


def test_sport_registry_nba():
    # NBA added 2026-11-08 (pace-props-nba-test-spec-2026-11-08): 48-min game.
    assert SPORTS["nba"]["total_min"] == 48
    assert SPORTS["nba"]["sd_inflation"] == 1.25
    assert SPORTS["nba"]["espn_path"] == "basketball/nba"

def test_team_possessions_formula():
    # Boxscore line: FGA 67, OREB 16, TOV 17, FTA 26
    # poss = 67 - 16 + 17 + 0.44*26 = 79.44
    assert abs(team_possessions(67, 16, 17, 26) - 79.44) < 1e-9
    assert team_possessions(0, 0, 0, 0) == 0.0

def test_project_team_total_normal():
    # Team has 40 pts at 20 min elapsed, pregame 78 pts (sd 10).
    # Season pace 1.75; live game pace 2.0 poss/min (fast game).
    # Team's own possessions so far: 42 -> live per-poss 40/42 = 0.952,
    # pregame per-poss 78/70 = 1.114. Projection must be above current
    # and below a no-cap extrapolation.
    mean, sd = project_team_total(TeamPaceInput(
        current=40, elapsed_min=20, pregame_mean=78, pregame_sd=10,
        poss_so_far=42, game_poss_per_min_live=2.0, season_poss_per_min=1.75))
    assert 40 < mean < 100, mean
    assert 0 < sd < 15, sd

def test_project_team_total_fast_pace_beats_season_pace():
    # Same team state; only the live pace differs. Faster pace must project
    # higher (more remaining possessions).
    base = dict(current=40, elapsed_min=20, pregame_mean=78, pregame_sd=10,
                poss_so_far=40, season_poss_per_min=1.75)
    slow, _ = project_team_total(TeamPaceInput(game_poss_per_min_live=1.75, **base))
    fast, _ = project_team_total(TeamPaceInput(game_poss_per_min_live=2.2, **base))
    assert fast > slow, (slow, fast)

def test_project_team_total_pace_cap():
    # Absurd live pace (3.0 poss/min) must be capped at 1.15x season pace:
    # identical projection to pace exactly at the cap (1.75*1.15 = 2.0125).
    base = dict(current=40, elapsed_min=20, pregame_mean=78, pregame_sd=10,
                poss_so_far=40, season_poss_per_min=1.75)
    capped, _ = project_team_total(TeamPaceInput(game_poss_per_min_live=3.0, **base))
    at_cap, _ = project_team_total(TeamPaceInput(game_poss_per_min_live=1.75 * 1.15, **base))
    assert abs(capped - at_cap) < 1e-9, (capped, at_cap)

def test_evaluate_team_pick_gates():
    # Projection 85, sd 8, line 75.5, -110/-110: model ~89% over -> fires.
    pick = evaluate_team_pick(85, 8, 75.5, -110, -110)
    assert pick["side"] == "over" and pick["ok"], pick
    # Projection right on the line: ~50/50, no edge -> no pick.
    pick2 = evaluate_team_pick(75.5, 8, 75.5, -110, -110)
    assert not pick2["ok"], pick2
    # Threes (low count): projection 9 threes vs line 5.5 -> over fires.
    pick3 = evaluate_team_pick(9, 2, 5.5, -110, -110, low_count=True)
    assert pick3["side"] == "over" and pick3["ok"], pick3

if __name__ == "__main__":
    for name, fn in list(globals().items()):
        if name.startswith("test_"):
            fn(); print(f"PASS {name}")
    print("all engine tests passed")
