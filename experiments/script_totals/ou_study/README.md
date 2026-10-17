O/U extremes signal study (Marvin 2026-10-16). Paper/tracking only. Reuses state/cache (nflverse_games, nfl_stats_team_week,
nfl_injuries, cfb_games/sched/teamgames, cfb_lines_oc, wnba_lines_hist/open_lines, wnba_box); nothing re-downloaded.
Target: y = final total - CLOSING total. High Shootout y >= 3 (4 WNBA), Defensive Grind y <= -3 (-4). Pregame walk-forward features only.
Split: NFL train 2020-23 / test 2024-26; CFB (FBS v FBS) train 2018-22 / test 2023-26; WNBA train 2018-23 / test 2024-26.
CURRENT = Script O/U / Master extremes proxy (bt_totals.py projection, |proj-close| >= 3/3/4, UF=1, EV>0), graded on the same games.
Signals tried: Marv proxy d, pace, |spread|, line level vs prior-365d avg, open->close move, NFL wind/temp/outdoor,
team O/U trends (avg total-minus-line last 5, over hit-rate last 10). Model = OLS on 1-3 standardized signals, combo + threshold
picked on TRAIN only (ROI - 1/sqrt(n), n >= 60). Prices: NFL actual nflverse O/U odds, CFB/WNBA -110.
Unseen-test results (ou_report.json):
  NFL  CURRENT 57-33 63.3% ROI +20.9% n=90 | best train-selected new (wind/trend/pace) 31-30 -3.4% n=61 -> current kept.
  CFB  CURRENT 381-380-14 50.1% -4.3% n=775 | LINE LEVEL (fade totals far from the 365d avg) 113-89-1 55.9% +6.8% n=203 -> INSTALLED tracking-only.
       Robust to other splits (cfb_check.py): test from 2022 +5.9% n=591 vs current -3.5%; from 2024 +5.6% n=303 vs -3.4%.
       Fade team over-rate + Marv d also beat current (90-73, +5.4%, n=163) but not the simpler line rule; not installed.
  WNBA CURRENT 184-166 52.6% +0.4% n=350 | best train-selected new (|spread|, d, line level) 88-88 -4.5% n=176 -> current kept.
Team O/U trends: following them lost in all three sports (test ROI -4.8%..-10.7%) and as an agreement filter on CURRENT they cut
ROI (NFL +20.9% -> +13-15%; CFB -4.3% -> -5..-6%; WNBA +0.4% -> -2..-5%). Fading them in CFB was only ~+1-2% and added nothing
on top of the line-level rule. NCAAB skipped: no historical closing totals in the cache.
Installed: experiments/script_totals/ou_linelevel.py (hooked in script_ou.py main), log state/script_ou/linelevel_ledger.json +
linelevel_summary.json. MARV_OU_LINELEVEL_LIVE default OFF (tracking only). MARV_OU_LINELEVEL=0 disables tracking.
Rerun: python ou_signals.py /opt/marv-bot/state/cache   (needs pandas+pyarrow)
