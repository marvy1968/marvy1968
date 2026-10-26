# OANDA Master AI — Segment 3

## Runtime

- Environment: **practice**
- Practice only: **True**
- Automatic execution: **False**
- Practice order submission enabled: **False**
- Submit requested: **False**
- Candidates evaluated: **1**
- Risk approved: **0**
- Orders submitted: **0**

## Candidate decisions

| Instrument | Side | Score | Risk approved | Units | Authorization | Reasons |
|---|---|---:|---|---:|---|---|
| GBP_USD | buy | 70.91 | False | 0 | False | alliance_score_below_minimum, risk_not_approved |

## Guardrails

- Segment 2 candidates are revalidated before authorization.
- Position size uses account equity, risk percentage and stop distance.
- Daily, weekly, total-open-risk and currency-exposure limits remain active.
- Broker stop-loss and take-profit attachments are required.
- Duplicate signals and duplicate instrument positions are blocked.
- Practice submission requires both `automatic_execution` and `practice_order_submission_enabled`.
- The kill-switch file blocks every new authorization.
