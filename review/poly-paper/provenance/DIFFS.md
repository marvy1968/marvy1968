# Untouched baseline vs review package

The files under `provenance/baseline/` are byte-for-byte copies of the screened export.
`provenance/SHA256SUMS` records those hashes. Nothing in this review edits those copies,
and nothing here is imported by startup, timers, workflows, or the VM updater.

## Mapper

`poly_paper/mapper.py` keeps the 2026-11-07 name-matching behavior (shared nicknames are
not enough; a known home/away game with no event returns no match). The only edit is
isolation: the baseline inserts a machine-local path and imports helpers from there.
Those helpers are the same functions as the baseline fallback, so this copy always uses
the fallback and does not change `sys.path`.

`provenance/baseline/polymarket-paper/mapper.py.baseline-20261107` is the older mapper
named `mapper.py.bak-20261107` in the export. Git ignores `*.bak*`, so the copy uses a
name that can be committed. It is evidence of the mascot fallback. The review package
does not import it. Tests load it only to show that it maps Samford onto Boyce and that
the retained mapper does not.

## Fill inserts

The baseline copies still contain their original inserts. This package does not patch them
in place, because importing `runner.py` reads absolute config paths and can write state.
The review entry points are `poly_paper.writers.insert` and `poly_paper.authorize.authorize_and_commit`.

Baseline insert sites, unchanged:

- `runner.py` inserts with `insert or ignore into fills` after its own edge, cap, and gate checks.
- `pm_fill.py` inserts with `insert or ignore into fills` on a separate connection from `runner.db()`.

`pm_nba_live.py`, `pm_me_track.py` (including `marv_cbb`), and `live_props.py` call `pm_fill.fill`.
They do not insert. Their model and network code is not imported. A text check in the tests
locks that.

## What the review package adds

`poly_paper/` is new. It is the authorization, identity, audit, and settlement code.
`effective_policy.json` is the reviewed policy. `type_gate.snapshot.json` is the screened
non-secret gate, not a live config.
