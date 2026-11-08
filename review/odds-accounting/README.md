# Observe-only Odds API accounting

This review records each physical THE ODDS API attempt and keeps the provider's
`x-requests-last` value separate from the caller's estimate. It does not reserve
credits, skip calls, change polling, or apply a new cap.

`x-requests-used` and `x-requests-remaining` are stored as account-wide snapshots.
A difference between two snapshots is not billed to the consumer that happened to
log the later response. A decrease is not a refund. External traffic, out-of-order
responses, and the provider's UTC month reset stay unknown.

The ledger is inert until `ODDS_ACCOUNTING_LEDGER` is a path the process can write,
or a caller passes a ledger path. A locked, unwritable, or corrupt ledger is
reported and then ignored. The provider call, its result, and its exception are
unchanged. No scheduler or extra service is started.

Shared caps, priorities, and cache or polling changes from the 2026-11-08 audit
are disabled and unimplemented. See `odds_accounting/enforcement.py`. They need
a separate approval before they can gate a call.

## Run the synthetic tests

From the repository root, with network unused:

```bash
PYTHONPATH=review/odds-accounting:review/odds-accounting/integrations \
  python -m unittest discover -s review/odds-accounting/tests -v
```

The tests use mocked transports and temporary files. They do not read production
databases or call the provider.

## What is in this tree

- `odds_accounting/` is the shared ledger, header parser, and reconciliation.
- `integrations/ajbet/` is the VM AJ transport boundary. `db.py`, `sat_priority.py`,
  and the provider wrapper are byte-identical to the screened export. `oddsapi.py`
  adds only the observe calls.
- `integrations/shadow_collector/credits.py` is byte-identical. `odds_request.py`
  is the collector's Odds request and replay environment allowlist, plus the note.
- Existing Marv transports in `marv/data/oddsapi.py`, `marv/sharpgap.py`,
  `marv/parlay.py`, and `marv/props/odds.py` call the same note. `Budget.charge`
  is unchanged, including its current treatment of a missing or zero header.

`COVERAGE.md` lists what this does not instrument. That list is not claimed complete.
