# OANDA Master AI — Segment 4A-2

## Operations configuration

- Watchdog enabled: **True**
- Automatic restart enabled: **False**
- Recovery enabled: **True**
- Block unresolved recovery: **True**
- Automatic execution: **False**
- Order submission: **False**

## Watchdog

- State: **stopped**
- Watchdog running: **False**
- Main process running: **False**
- Heartbeat fresh: **False**
- Action: **none**
- Action reason: **none**

## Health

- Healthy: **False**
- Critical failures: **1**
- Warnings: **1**

| Check | Passed | Severity | Message |
|---|---:|---|---|
| segment4_process | True | info | Segment 4 daemon is not running. |
| heartbeat_freshness | True | info | Daemon is stopped; stale heartbeat is not treated as a failure. |
| status_freshness | True | info | Daemon is stopped; status age is informational. |
| foundation_snapshot | False | critical | Foundation snapshot is missing or data quality failed. |
| segment2_shortlist | True | info | Segment 2 shortlist is missing or stale. |
| segment3_status | False | warning | Segment 3 status is missing, stale or unsafe. |
| free_disk_space | True | info | Free disk space is sufficient. |
| log_file_sizes | True | info | Log files are within limits. |

## Recovery

- Recovery required: **False**
- Recovery passed: **True**
- Unresolved blockers: **0**
- Open positions recorded: **0**
- Pending orders recorded: **0**
- Unresolved-recovery file: **False**

| Check | Passed | Blocking | Message |
|---|---:|---|---|
| prior_shutdown_state | True | False | Prior runtime ended cleanly. |
| segment3_safety | True | True | Segment 3 safety settings passed. |
| open_position_reconciliation | True | True | Open-position state is structurally valid. |
| pending_order_reconciliation | True | True | Pending-order state is structurally valid. |

## Duplicate protection

- Registered signals: **0**
- Registered authorizations: **0**
- Registered orders: **0**

## Restart history

- Restart attempts in stored history: **0**
- Automatic restart remains: **disabled**

## Guardrails

- Watchdog health checks do not authorize trades.
- Automatic restart is disabled by default.
- Unresolved recovery can block daemon startup.
- Segment 3 kill switch and pause remain authoritative.
- Duplicate protection tracks signals, authorizations and orders.
- Practice-only mode remains enforced.
- Automatic execution and order submission remain disabled.
