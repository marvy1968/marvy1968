# Ten betting and modelling books, and what Marv takes from each

These are the books most often recommended by professional bettors and sports modellers. For each one:
the core idea, and how Marv uses it (or why it doesn't).

| # | Book | Core idea | In Marv |
|---|---|---|---|
| 1 | **The Logic of Sports Betting**, Ed Miller & Matthew Davidow (2019) | Sportsbooks move toward sharp money, so the **closing line** is a very good forecast. You win by beating it, not by picking winners, and props and smaller markets are softer than main sides. | The board anchors Marv to the no-vig market (`edges.blend`). Markets that don't beat closing prices in the backtest are hidden. Props get the most attention. |
| 2 | **Squares and Sharps, Suckers and Sharks**, Joseph Buchdahl (2016) | Luck vs skill: a few hundred bets prove almost nothing. Test significance, and use **closing-line value (CLV)** as the early signal of skill. | Every alerted bet tracks CLV until kickoff (`/record`). The board shows a p-value for each market. Nothing is RECOMMENDED without p < 0.10 on 100+ held-out bets. |
| 3 | **Fixed Odds Sports Betting** / **Monte Carlo or Bust**, Joseph Buchdahl | Remove the bookmaker's margin properly. Margins load more on longshots (**power or Shin method**), and simulation shows how wild a bankroll really swings. | `edges.devig` uses the power method. The bankroll swings of the props and moneyline rules are simulated in the stress tests. |
| 4 | **Sharp Sports Betting**, Stanford Wong (2001) | Expected value, **key numbers** (NFL margins of 3 and 7), and **shop every line**. Half a point or 10 cents is often the whole edge. | Line shopping across your books (`ODDS_BOOKS`; Bovado first, best price wins). The drive-by-drive NFL simulation makes 3 the most common margin, with 7 close behind, but its 3s come out about 8.5% of games against ~15% in real games, so pushes on -3 are understated. That's a known limitation to fix. |
| 5 | **Weighing the Odds in Sports Betting**, King Yao (2007) | Value comes from probability times price, **Kelly** staking, middles and hedging. | Quarter-Kelly stakes capped at 2% (`edges.kelly`). Every card and alert shows the edge as expected profit at the actual price. |
| 6 | **Conquering Risk**, Elihu Feustel & Ed Miller (2023) | Modern books are hard to beat on sides, but **player props** and correlated markets are priced with less care. | Player props for NFL, college football, NCAA men and women and WNBA. Projections come from usage, opponent and game script, and are priced with a Monte Carlo of past misses. |
| 7 | **Trading Bases**, Joe Peta (2013) | Separate skill from luck (Peta's "cluster luck"): results regress toward underlying stats such as run differential. Size bets with Kelly. | The in-game model uses yards per play, success rate and shooting at each quarter, not just the score. The props model regresses recent form toward season averages. |
| 8 | **Mathletics**, Wayne Winston (2009, 2nd ed. 2022) | Least-squares **power ratings**, home-field advantage, Elo and Monte Carlo, all in a spreadsheet. | The opponent-adjusted power-rating expert (fitted home edge, margin cap) and the shared Monte Carlo simulators. |
| 9 | **Calculated Bets**, Steven Skiena (2001) | Building an automated betting system end to end: data pipeline, model, honest testing on unseen data. The system's edge was small but real. | The whole pipeline: walk-forward backtests, held-out seasons, paper mode until the ledger proves an edge. |
| 10 | **The Signal and the Noise**, Nate Silver (2012) | Think in probabilities, check **calibration**, distrust overfitting, update with new information (Bayes). | Calibration checks in every stress test. Probabilities are pulled toward 50% where they were overconfident. Market-anchored blending works the way Bayesian updating does. |

Also useful: **Sharper** by Poker Joe (spreadsheet models, no-vig math) and **Analytic Methods in Sports** by Thomas Severini (regression and probability for sports data).

## What the books agree on, and what the backtests confirmed

1. **The closing market is hard to beat.** On NFL closing moneylines, spreads and totals, every
   model-plus-market blend tested lost money or was indistinguishable from luck on held-out seasons
   (moneyline -15%, totals -12%, spread +2% with p = 0.39). College football and NBA came out the same;
   NBA totals were the closest at +9% on 157 bets, p = 0.11. The board therefore labels game markets
   as leans at best.
2. **High win rates on favorites aren't an edge.** Marv's ~90% moneyline picks are the same games as
   the market's biggest favorites (which won 94-100% in the same samples), at prices around -900.
3. **Props are where a model can add something.** Against a sportsbook-style line, the NFL props
   model won 57-60% at a 5% edge on held-out seasons, and a shuffled-outcome control stayed at 50%.
   Real Bovado lines are the final test (`props-backtest --real` on the VM).
4. **Judge by CLV and significance, not streaks.** `/record` tracks closing-line value. Stay in
   paper mode until about 100+ bets show positive CLV.
