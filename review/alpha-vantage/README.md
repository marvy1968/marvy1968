# Alpha Vantage review adapter

Review-only research helper. It is not imported by `alpacabot`, it does not change the bot's feed or fallback, and importing it does not read a key or open a network connection. This tree is for review on `review/alpha-vantage-base-20261108`. It is not a change to the deployed `claude/analysis-ak180w` branch.

The useful role is optional end-of-day reconciliation for a small symbol set, plus weekly adjusted research. It is not a realtime fallback, not an automatic trading feed, and not a source of deep daily history.

## What the free contract is

Observed free endpoints only:

| Call | What the bars are |
|---|---|
| `TIME_SERIES_DAILY` with `outputsize=compact` | Latest 100 raw as-traded daily OHLCV bars. `full` is premium and is refused. |
| `TIME_SERIES_WEEKLY_ADJUSTED` | Raw weekly OHLC and volume, plus a separate adjusted close and a dividend amount. |

`TIME_SERIES_DAILY_ADJUSTED` and `TIME_SERIES_INTRADAY` are premium. This package refuses them before any network call or quota reservation.

Daily OHLC and weekly OHLC are raw. The weekly adjusted close is the publisher's split-and-cash-dividend close. It is its own column. This package never copies it into `close` and never rescales OHLC by the adjusted/raw ratio. A ratio is not a split-only factor.

Session dates are `US/Eastern` labels, stored on an `America/New_York` timestamp at midnight so they line up with the Alpaca bar layout (`open`, `high`, `low`, `close`, `volume`, index name `time`). Alpaca's own downloader requests `adjustment=all`, so these raw frames are not a drop-in substitute for that adjusted history.

## Quota

The support page used for the evaluation states a standard free allowance of 25 requests per day. It does not state a per-minute cap or the reset clock. This tool therefore:

- counts an attempt when it reserves the call, including when the call then fails
- does not retry
- will not reserve the same symbol and function again on the same UTC day unless `allow_repeat=True`, which spends another slot
- keeps the count in a file lock shared by processes using that cache directory

The UTC day is this ledger's bucket, not a claim about the provider's reset. Other programs may already be using the key. A free slot here does not promise that the provider quota is free.

## Commands

From a checkout:

```text
PYTHONPATH=review/alpha-vantage python -m alpha_vantage validate-fixture \
  --json local.json --symbol TEST --function TIME_SERIES_DAILY \
  --as-of 2026-11-08T12:00:00-05:00
```

```text
PYTHONPATH=review/alpha-vantage python -m alpha_vantage compare \
  --av-response local.json --iex iex.csv --symbol TEST \
  --function TIME_SERIES_DAILY --as-of 2026-11-08T12:00:00-05:00 \
  --iex-adjustment alpaca_adjustment_all --iex-venue iex --iex-timeframe 1Day
```

`compare` only reads the two local files. It labels venue, volume, and adjustment differences. It does not rank them.

A live fetch is off unless both the flag and the environment variable are set. The key is read from `ALPHA_VANTAGE_API_KEY` only on that path. It is not written to the cache, the ledger, or the error text. Do not enable debug logging on `urllib3` or `requests` while doing this; the command refuses, because those loggers can record the query string.

```text
ALPHA_VANTAGE_RESEARCH_OPT_IN=1 PYTHONPATH=review/alpha-vantage python -m alpha_vantage fetch \
  --opt-in --symbol TEST --function TIME_SERIES_DAILY \
  --as-of 2026-11-08T12:00:00-05:00 --cache-dir /path/outside/the/repo
```

One symbol per invocation. There is no symbol list and no scheduled refresh.

Validation rejects a stale newest session (default more than 4 calendar days before `--as-of`), a wrong symbol, a weekend daily or weekly date, a future date, and OHLC that is not a real bar. An HTTP 200 body that is `Note`, `Information`, or `Error Message` is not treated as bars. A fresh cache is used with zero network calls. A stale or rejected cache is not.

## Boundaries

- Code lives under `review/alpha-vantage/` and is not on alpacabot's import path.
- No change to `alpacabot` data loading, `ALPACA_FEED`, order paths, or service units.
- No premium endpoint, no `outputsize=full`, no intraday, no automatic 20-symbol sweep.
- Tests use synthetic bars. Provider responses are not copied into the repo.

## Remaining limitations

- No independent Alpaca or IEX accuracy result is included. The comparison entrypoint only labels bars the caller already has.
- Compact daily history is about 100 sessions. It is not a long daily backtest. Weekly history is a different timeframe, not a substitute.
- The local ledger cannot see other users of the key, and deleting the ledger file forgets only this tool's count.
- The provider's daily reset time and any per-minute free limit were not stated on the support page used here, so none is implemented. Calls are one at a time because the entrypoint accepts one symbol.
- Calendar gaps inside a series are reported by not being filled. They are not explained with extra requests.
- Weekly adjusted close mixes splits and cash dividends. This package does not separate them.
- Live use still depends on a human opting in on a machine that already has the key. This review does not deploy or enable that.
