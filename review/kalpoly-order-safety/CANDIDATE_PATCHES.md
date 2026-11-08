# Caller paths

These sites are blocked. This file is not an integration patch. None of the modules below is imported, and no function here wraps the live `place_order`, `_place_ioc`, `cancel_order`, or `sweep_resting`.

| Id | Source | Status | Why it stays blocked |
| --- | --- | --- | --- |
| `runner_game` | `kalshi-demo/runner.py:162-167` | blocked | Reads `DEMO_DRY`, calls `positions()`, logs after the live `place_order` |
| `runner_props` | `kalshi-demo/runner.py:211-216` | blocked | Reads `DEMO_DRY`, logs after the live `place_order` |
| `me_kalshi` | `kalshi-demo/me_kalshi.py:344-351` | blocked | Reads switch files, logs after the live `place_order` |
| `live_runner` | `kalshi-demo/live_runner.py:111-115` | blocked | GETs the market through the live client before `place_order` |
| `nba_pacer` | `kalshi-demo/kalshi_nba_pacer.py:196-200` | blocked | GETs the market through the live client and reads `KALSHI_NBA_LIVE_MIN10` |
| `marv_cbb` | `kalshi-demo/marv_cbb_kalshi.py:57-61` | blocked | Reads yaml and `positions()`, then calls the live `place_order` |
| `live_props` | `marv-feed/live_props/live_props.py:273-278` | blocked | Builds the live client, reads trade switches, logs after `place_order` |

The source-backed replacement for the POST itself is `DemoClient.place_order`. It is not wired into any of those functions. A future review can connect a caller only after that caller stops constructing the live client and stops reading its environment inside the order path.

`_place_ioc` still calls the shard-fund helper before POST. `DemoClient` does not call that helper. When the supplied shard fact says a transfer would be required, or the fact is missing, `place_order` raises `FundingUnsupported` and sends no request. Balances, stakes, and config files are not read or written.

`sweep_resting` in the live client records `expired_or_filled` when DELETE throws. `DemoClient.sweep_resting` leaves the row at `cancel_unknown` and refuses a legacy order id it did not create.
