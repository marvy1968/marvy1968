# OANDA Master AI — Segment 4B-1

## Runtime

- Generated: **2026-07-16T07:56:11.443788+00:00**
- Environment: **practice**
- Mode: **simulation_only**
- Execution gate allowed: **False**
- Automatic execution: **False**
- Practice-order submission: **False**
- Orders submitted: **0**

## Broker reconciliation

- Passed: **True**
- Open positions: **0**
- Open trades: **0**
- Pending orders: **0**

## Simulation results

- Segment 3 candidates: **0**
- Evaluated candidates: **0**
- Simulated acceptances: **0**
- Verified simulated fills: **0**
- Rejected candidates: **0**

## Execution counters

- Hourly simulations: **0**
- Hourly maximum: **4**
- Daily simulations: **0**
- Daily maximum: **8**

## Candidate lifecycle

| Instrument | Side | Candidate | Market | Counter | Simulated receipt | Fill verified | Rejected |
|---|---|---:|---:|---:|---:|---:|---:|
| None | — | — | — | — | — | — | — |

## Guardrails

- The market validator uses read-only OANDA pricing.
- Broker reconciliation uses read-only OANDA account endpoints.
- Simulated receipts are locally generated and are not broker receipts.
- Fill verification operates against simulated lifecycle data.
- Duplicate intent and authorization protection remains active.
- Hourly, daily and per-cycle counters remain enforced.
- The activation gate remains blocked.
- Automatic execution remains disabled.
- Practice-order submission remains disabled.
- No OANDA order endpoint is present in the simulated router.
- Orders submitted remain zero.
