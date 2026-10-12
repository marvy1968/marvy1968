"""Marv Soccer Predict Max: soccer node of Marv Predict (paper-tracked).

Built from the owner's "Marv Soccer Predict Max" blueprint, mapped onto Marv Predict's method:
  power   - opponent-adjusted attack/defence ratings (time-decayed Poisson on goals blended with xG / a shots-based
            xG proxy), league-specific home advantage and scoring rate          (engine.fit_ratings)
  H2H     - season-to-date category matrix (goals, xG, shots, shots on target, corners; for and against), one point
            per category won, a modest modifier on the expected goals           (engine.h2h_net)
  trend   - last-5 xG-margin vs each team's own baseline, controlled weight      (engine.trend)
  Monte Carlo - Dixon-Coles scoreline matrix sampled with a deterministic seed; 1X2, DNB, Asian handicap, totals,
            BTTS, team totals, correct-score ranking all settle from one distribution  (engine.simulate)
  market  - no-vig comparison only after the projection is frozen; edge, EV; veto stack; confidence score
  validation - walk-forward by season, hyper-parameters chosen on earlier seasons only (validate.py)

Status: PAPER. Nothing from this node prints a probability on Telegram; promotion needs marv/proven.py's bar.
"""
