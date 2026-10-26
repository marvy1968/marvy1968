# OANDA Master AI — Segment 4B-4

## Shadow campaign runtime

- Generated: **2026-07-16T15:26:46.267505+00:00**
- State: **cycle_complete**
- Running: **False**
- Cycle number: **1**
- Last cycle successful: **True**
- Consecutive errors: **0**
- Paused: **False**
- Shutdown requested: **False**

## Campaign progress

- Current checkpoint: **data_collection**
- Open shadow trades: **0**
- Closed shadow trades: **0**
- Target closed trades: **250**
- Campaign complete: **False**
- Checkpoint stop requested: **False**
- Checkpoint reason: **continue_campaign**

## Latest cycle

- Candidates loaded: **0**
- Candidates approved: **0**
- Shadow trades opened: **0**
- Open trades checked: **0**
- Shadow trades closed: **0**
- Errors: **none**

## Pipeline stages

| Stage | Attempted | Successful | Return code | Runtime seconds | Error |
|---|---:|---:|---:|---:|---|
| segment2_refresh | True | True | 0 | 6.2641 | none |
| segment3_research_preview | True | True | 0 | 6.3513 | none |
| segment4b2_shadow_cycle | True | True | 0 | 0.2454 | none |
| segment4b3_readiness | True | True | 0 | 0.1111 | none |

## Campaign controls

- Campaign enabled: **True**
- Manual pause: **False**
- Stop at observation checkpoint: **False**
- Stop at provisional checkpoint: **False**
- Stop at advanced checkpoint: **False**
- Stop at production-review checkpoint: **True**

## Guardrails

- Segment 4B-4 orchestrates research and shadow-analysis stages only.
- Shadow trades are hypothetical and do not create broker positions.
- Campaign checkpoints do not authorize practice or live execution.
- Automatic promotion remains disabled.
- Automatic execution remains disabled.
- Practice-order submission remains disabled.
- Live-environment execution remains blocked.
- Orders submitted remain zero.
