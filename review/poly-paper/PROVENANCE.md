# Provenance

Screened export read for this review: local archive `poly-strict-paper-review`, captured
2026-11-08. The export's own manifest hashes were checked again while copying. Matching
hashes are in `provenance/SHA256SUMS`.

| Review path | Export path | Role |
|---|---|---|
| `provenance/baseline/polymarket-paper/mapper.py` | `/opt/polymarket-paper/mapper.py` | untouched current mapper |
| `provenance/baseline/polymarket-paper/mapper.py.baseline-20261107` | `/opt/polymarket-paper/mapper.py.bak-20261107` | untouched pre-fix mapper |
| `provenance/baseline/polymarket-paper/runner.py` | `/opt/polymarket-paper/runner.py` | untouched writer |
| `provenance/baseline/polymarket-paper/pm_fill.py` | `/opt/polymarket-paper/pm_fill.py` | untouched writer |
| `provenance/baseline/polymarket-paper/pm_type_gate.py` | `/opt/polymarket-paper/pm_type_gate.py` | untouched gate |
| `provenance/baseline/polymarket-paper/grader.py` | `/opt/polymarket-paper/grader.py` | untouched settlement behavior |
| `provenance/baseline/polymarket-paper/pm_client.py` | `/opt/polymarket-paper/pm_client.py` | untouched public parser, not imported |
| `provenance/baseline/polymarket-paper/pm_nba_live.py` | `/opt/polymarket-paper/pm_nba_live.py` | caller evidence only |
| `provenance/baseline/polymarket-paper/pm_me_track.py` | `/opt/polymarket-paper/pm_me_track.py` | caller evidence only |
| `provenance/baseline/marv-feed/live_props/live_props.py` | `/opt/marv-feed/live_props/live_props.py` | caller evidence only |
| `fixtures/observed-case619-minimal.json` | export fixture | minimal observed fill 619 |
| `fixtures/synthetic-provider-policy-cases.json` | export fixture | synthetic Boyce/Liberty feed |
| `schemas/*.json` | export schemas | non-secret config and DDL, no rows |

Not imported, because they are outside the fill boundary or would pull model, network,
or order code: Kalshi helpers, pick feed, calibration, props builder, price path, lane
report, `run.sh`, and `track_grade.py`. `grader.py` is kept as evidence. The review
settlement function does not call it and does not use the network.

The standalone paper tree on the VM is not this GitHub repository. Deployment metadata
in the export says `/opt/polymarket-paper` is a local git repo with no remote, and
`/opt/marv-bot` is `claude/analysis-ak180w`. This review does not attach the paper fix
to that deployed branch, to PR7, or to any other bot.

See `provenance/DIFFS.md` for the mapper isolation diff and the untouched insert sites.
