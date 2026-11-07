# In-game player-prop pacer: everything we tested (for SuperGrok)

NBA, NCAA men's and NCAA women's basketball player props (points, rebounds, assists, threes), priced at quarter / half
breaks. Built and tested November 6-7, 2026. Code and full tables: `research/prop_pacer/` on branch `ccr-4a1c5d6b-xd3ygu`.

**How to read this file**
- **Brier** = probability error (lower is better). The bar is the market's own mid price on the same contracts.
- **ROI** = profit ÷ money staked, after Kalshi's taker fee (0.07 × p × (1 − p) per contract), one contract per bet.
- **90% range / P(ROI>0)** = game-level bootstrap. Bets in the same game are not independent, so whole games are resampled.
- **Tradeable quote** = bid/ask gap ≤ 10¢, bid ≥ 2¢, ask ≤ 98¢, refreshed after the period ended.
- **Train** 2023-25 seasons · **dev** 2025-26 Jan 17-Mar 31 · **validation** Apr-Jun 2026 · **holdout** 2026-27
  Oct 20-Nov 6 (132 games, scored once with a rule committed beforehand).

---

## Bottom line

**No positive expectancy has been proven.** The rule committed before the holdout broke even. One lead (large
disagreements, ≥ 8¢) is being forward-tested on games after Nov 6, 2026.

---

## A. Projection accuracy (no market involved), unseen 2025-26 season

Average miss on the player's remaining points:

| | naive pacer (so far × game ÷ elapsed) | Claude Prop 1 | **minutes model** | ML (gradient boosting) |
|---|---|---|---|---|
| NBA after Q1 | 7.30 | 4.71 | 4.21 | 4.20 |
| NCAA men's at half | 3.38 | 2.95 | 2.86 | 2.83 |
| NCAA women's after Q1 | 5.38 | 3.60 | 3.34 | 3.43 |

Calibration, NBA points (predicted → actual): naive pacer 0.55→0.42, 0.73→0.53, 0.84→0.64 (overconfident on Overs).
Minutes model 0.55→0.57, 0.75→0.75, 0.85→0.83.

**Verdict:** the minutes model is much more accurate than a straight-line pacer, and its probabilities are calibrated.
Accuracy alone does not make money; see B and C.

**Minutes model:** remaining minutes = blend(pregame minutes share, tonight's share) × minutes left × foul / blowout /
on-court factors; per-minute rate = blend(pregame rate, tonight's rate) weighted by minutes played. Eight numbers are
fitted on train seasons. The fitted weight on tonight's points rate after 24 minutes on the floor is only about 15%:
hot starts barely carry over, and remaining minutes drive the final line.

## B. What actually happens to starters' remaining minutes (vs the hand-set rules in `pacer_v2/`)

| situation | pacer_v2 rule | NBA | NCAA men's | NCAA women's |
|---|---|---|---|---|
| one foul under the limit | ×0.50 | ×0.82 | ×0.75 (n=17) | ×0.81 |
| two fouls under | ×0.80 | ×0.97 | ×0.91 | ×1.04 |
| margin 18-25, late | ×0.65 | ×0.62 | ×0.94 | ×0.87 |
| margin 25+, late | ×0.40 | ×0.28 | ×0.88 | ×0.74 |

**Verdict:** foul-trouble cuts are too harsh in every league. College blowouts barely cost minutes. NBA 25+ blowouts cost
more than assumed.

## C. Against real Kalshi prices (NBA only; Kalshi and Polymarket list no college player props)

| what | sample | result | verdict |
|---|---|---|---|
| Naive pacer, both sides, edge ≥ 6¢ | 68 games, Jan-May 2026 | 1,176 bets, **−8.2%**, range −14.0% .. −2.7% | **Loses, not luck** |
| Claude Prop 1, edge ≥ 6¢ | same | 856 bets, −6.7%, range −14.5% .. +0.7% | Loses |
| Minutes model, edge ≥ 6¢ | same | 469 bets, +8.8%, range −1.8% .. +17.8% | Looked good; see below |
| Market accuracy, tradeable quotes only | dev, 63 games | mid 0.2024 vs minutes model 0.2072 | **Market is as good or better** on quotes you can trade |
| Kalshi calibration by price bucket | dev, pregame + in-game | wins ≈ price in every bucket | **No broad longshot bias** |
| v3 = Kalshi pregame ladder anchor + minutes model | dev / validation | no edge on dev; positive on validation | Inconclusive |
| Ladder trick (price a rung from its refreshed neighbours) | dev / validation | dev "No" +29% to +72% (≤ 62 bets); validation −1% to −14% | **Failed**, dropped |
| **Pre-registered: minutes model, "No" only, edge ≥ 6¢** | validation, 23 games | 309 bets, +14.1%, range +2.7% .. +26.5% | Validation passed |
| **Same rule on the HOLDOUT** | **2026-27, 132 games** | **2,910 bets, −0.0%, range −4.5% .. +4.5%** | **FAILED: breaks even** |

## D. Exploratory holdout pattern (picked after looking, so not a result)

| holdout, both sides | edge ≥ 2¢ | ≥ 4¢ | ≥ 6¢ | ≥ 8¢ |
|---|---|---|---|---|
| minutes model | −0.5% | +0.7% | +1.4% | +4.8% (4,130 bets, +0.6% .. +9.0%) |
| v3 | +1.1% | +2.0% | +3.7% | +6.1% (2,939 bets, +1.3% .. +10.8%) |
| v3 + learned market correction | −0.5% | +1.0% | +0.2% | +5.2% (1,269 bets, −1.9% .. +12.5%) |

ROI rises with the size of the disagreement for every model. That pattern is what real information looks like: the
market is right on small disagreements and possibly wrong on big ones. It was found on data that is now used up.

**Forward test, written down before the data exists:** v3, both sides, edge ≥ 8¢, the same quote filters, NBA games
from 2026-11-07 on. Promote only if it is positive with a 90% range above −5% after 60+ games (about a week).

## E. Testing lessons from this project

1. **Wide quotes make any model look better than the market.** A mid price with a 0¢/99¢ quote means nothing. Always
   compare on tradeable quotes only. Only 22% of Kalshi in-game prop quotes at the breaks were tradeable last season.
2. **Stale quotes fake edges.** Keep only quotes refreshed after the period ended.
3. **Fix the rule, then look.** The validation +14% came from a rule partly suggested by overlapping data; the clean
   holdout broke even.
4. **Bootstrap by game, not by bet.**
5. **Data plumbing bugs are common.** A bounded optimizer walked into a flat tail; college play-by-play uses different
   substitution and shot formats; newer Kalshi candles say `close_dollars`, not `close`; Kalshi caps one batch
   request at 10,000 candles and rate-limits at about 3 requests a second.

## F. Data and reproduction

- Play-by-play: sportsdataverse `espn_nba_pbp`, `espn_mens_college_basketball_pbp`, `espn_womens_college_basketball_pbp`
  (public parquet on GitHub releases). Rebuilt points equal the final score; minutes sum to 240 / 200 per team.
- Kalshi: public API (`/historical/markets`, per-market candlesticks; batch candlesticks after the historical cutoff).
  Settlement agrees with the play-by-play box score on 100% of matched contracts.
- Scripts: `build.py` → `fetch_kalshi.py` → `evaluate.py` / `market_v3.py` (`--cv`, `--holdout`) → `bootstrap.py`,
  `ladder.py`. Rules: `research/prop_pacer/PREREGISTERED.md`.
