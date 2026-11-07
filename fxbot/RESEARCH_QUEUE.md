# Forex refinement queue (owner 2026-11-07: "keep testing and refining")

Rules for every item (fixed before any result is seen):
- **Test window:** the last 3 months, split into two halves by date. Real bid/ask prices and the live exits.
- **One change at a time,** compared with the current live rule.
- **ADOPT only if:**
  - it is not worse in the first half,
  - it is better on the second (unseen) half,
  - PF stays ≥ 1.0 with the spread doubled,
  - it beats a random-timing control,
  - and it has 30+ trades.
- **If it passes the short test,** run it over 3 years before going live. Otherwise mark it FAIL and move on.
- **Record:** the date, the numbers and the verdict, here and in `fxbot/live.py` comments when adopted.
- The owner's fx-experts bot is never changed without his approval.

| # | Strategy | Idea | Status |
|---|---|---|---|
| 1 | lien_h1 | Trade only London + New York hours (07:00-17:00 UTC) vs all hours | built (fxbot.refine.session_lien, family fx_refine_1); waiting for a deploy |
| 2 | lien_h1 | Skip a fade against the daily trend (close vs D1 SMA200) | built (fxbot.refine.trend_lien, family fx_refine_2); waiting for a deploy |
| 3 | retest_h1 | Minimum reward 2.5R vs the current 2R | queued |
| 4 | retest_h1 | 4-hour trend agreement (H4 SMA50 slope) on the retest entry | queued |
| 5 | both | Skip entries when the spread is > 10% of the stop distance | queued |
| 6 | both | Cap open trades sharing one currency (e.g. at most 2 USD-side trades) | queued |
| 7 | both | Skip entries 60 min either side of high-impact news for that currency (all pairs) | queued |
