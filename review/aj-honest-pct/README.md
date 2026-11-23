# Review only: honest AJ alert percent

This directory is a review package for AJ Bet Bot / March_edge. It is not an install. Merging this pull request does not copy anything onto the VM, and it does not restart AJ. Installing it or restarting AJ needs a separate yes from Marvin.

The live tree is `/home/moonrider1968/aj-bet-bot/ajbet/`. That tree is not in this repo. The modules under `review/aj-honest-pct/ajbet/` are a sanitized stand-in of the percent path the VM trace described. They contain no config, no env file, no database, no log, no Telegram id, no email, and no key. They do not call the Odds API, they do not load a pacer model, and they do not route Telegram.

The deployed-branch updater installs the bot from `claude/analysis-ak180w`. It does not install `review/aj-honest-pct/`. Leave this package here until that separate yes.

## What the alerts do today

A pacer prop card can print a header and a Win chance labeled `calibrated` from the raw pacer probability. The trace of the VM path:

1. `value_scan.evaluate_live_props` calibrates the consensus `fair_p` and caps it at 65% in `calib.adjust_finding`.
2. `pacer_hook.apply` overwrites `fair_p` with the raw pacer probability, so the cap is lost.
3. `me_shrink.apply_pacer` does not shrink NBA. The fitted slope is -0.047, so the flat-skip that would have sat on the ~60-64% lines applies, tagged `pacer:nba:flat_skip`.
4. `value_scan.emit` reads `extra['pacer_p']` and labels any non-None value `calibrated`.
5. `friendly.card` builds Win chance from `paper.p_cal`: the pacer probability plus a paper shift clamped at -5 points (`paper_conf.MAX_SHIFT`).
6. The honest number is the footnote from `paper_conf.Assessment.real_bit`. The VM docstring at `paper_conf.py` lines 44-48 says that footnote is never used by a gate, a stake, or `p_cal`.
7. The send gate sends when the probability is at least 62% or the Bovada edge is at least 4%. The live EV gate uses the raw pacer probability. `live_wincap` caps game lines at 65% and skips props.

Reported sample, not a new measurement: since Oct 31, 277 sent pacer alerts with a header of 70% or more have hit 54.2%. The header averaged 77.6% and the card line averaged 73.3%.

## Flag `AJ_HONEST_PCT`

Off (`unset`, empty, `0`, `false`, `no`, `off`) is today's path.

On (`1`, `true`, `yes`, `on`):

- Cap `fair_p` at 65% after the pacer overwrite. `live_wincap` still skips props, so this cap is the one that puts the ceiling back.
- Set `p_final` to the honest real-hit value when that map exists. On these alerts that value is typically about 55-58%.
- The header, the Win chance, the 62% / 4% send gate, the live EV gate, and the Kelly stake all use `p_final`.
- Print `calibrated` only when a real map produced `p_final`. With no map, print `model estimate` and keep the 65% cap.
- Keep the footnote on the card as a cross-check. `paper_conf` still does not write the footnote into `p_cal`.

Game lines, and props that have no pacer probability, stay on the consensus calibration they already have. The flag does not change them.

With the flag on, many pacer alerts drop from about 75% to about 55-58% and stop sending unless the edge is at least 4%. This package does not estimate how many alerts that removes. The 65% cap still applies when no real-hit map is available; a map that does return a value is used as `p_final` because that value is already the real-hit calibration.

## Tests

From the repo root:

```bash
python3 -m unittest discover -s review/aj-honest-pct/tests -v
```

The tests are offline. They check the raw NBA pacer at 0.78 with `flat_skip` and an honest map of 0.58, the 65% cap with no map, the send gate, the flag-off path, the game-line path, and the stake.

## What a later install would touch

A later yes would port this flag into the live modules at `/home/moonrider1968/aj-bet-bot/ajbet/` (`value_scan.py`, `friendly.py`, and the stake call). It would not replace the Odds API client, the pacer models, or Telegram routing. This pull request does not do that port.
