# Later review, not this change

This package is the rank-1 order intent candidate only. Rank 2 identity, rank 3 liquidity, and rank 4 exposure stay separate. A missing-config fail-closed proposal belongs in later review code. This package does not change the current runtime's config behavior.

## PR 7 pause and health, read-only

PR 7 is open: https://github.com/marvy1968/marvy1968/pull/7

- Title: Review: lane pause, health arithmetic, and pick audit
- Base: `claude/analysis-ak180w`
- Head: `cursor/card-fixes-review-b0f2`
- This branch does not edit that head, that base, or `deploy/marv-health.sh`.

PR 7 already contains both later items this audit would otherwise have duplicated:

- `deploy/marv-health.sh` gains `die`, `normalize_uint`, and `uptime_us`, and exits 1 when the uptime or the last-run timestamp is not an integer. The sanitized VM script `marv-bot/deploy/marv-health.sh` (`5b881154c6fbc6efeb4df4339444277d2cd1bae0adb1bdc8635ae4c66d739b4a`) still has `set -uo pipefail` and `awk '{print int($1 * 1000000)}' /proc/uptime`.
- `marv/lane_pause.py` is the repo candidate for the card pause predicate (NHL ML, NHL total, MLB total, CFB total), wired through the card, daily-picks, and parlay paths in that PR. The VM file `marv-feed/marv_lane_pause.py` (`8929f00007afcf00d4d9cce32b4c8f3cff801af7d48472ece027378586774cf4`) is the feed overlay with the same default lanes. PR 7 is not deployed.

Gaps that remain after both this branch and PR 7, for a later review:

- The VM health script and the VM feed pause overlay are the copies that a running host would execute. PR 7's repo copies are not on that host.
- Lane pause removes lanes from the card. It does not durable-log an order intent and does not stop `KalshiDemo.place_order`.
- Health arithmetic does not observe order acceptance or cancel results.
- No deployment recommendation follows from either review until the remaining barriers are reviewed on their own.

## Order-lifecycle risks that remain after this candidate

- DEMO orders already accepted under a `uuid4` client id are outside this ledger. Nothing here migrates them.
- `submit_unknown` stays unknown until an explicit snapshot names that client id. Silence does not become filled or cancelled, and the account stays blocked.
- A cancel request can leave a resting order live at the venue. The ledger shows `cancel_unknown` until a snapshot says `canceled` or `cancelled` with a fill count.
- The shard transfer in `ensure_shard_funds` is still a separate POST with its own fresh id. This candidate does not record or recover it.
- The seven call sites above are not loaded by the VM processes. The live path still creates the client id at POST and logs after the response.
- Polymarket paper persistence has no venue order to reconcile. There is no real Polymarket order API in this candidate.
- Passing the offline tests does not qualify a real order.
