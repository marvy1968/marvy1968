# OANDA v20 REST API — notes for fx-experts

Sources: developer.oanda.com/rest-live-v20 (Introduction, Development Guide, Best Practices, Order/Trade/Instrument/Pricing definitions). Read on 2026-10-02.

## Environments and auth
| | Practice (fxTrade Practice) | Live (fxTrade) |
|---|---|---|
| REST | `https://api-fxpractice.oanda.com` | `https://api-fxtrade.oanda.com` |
| Streaming | `https://stream-fxpractice.oanda.com` | `https://stream-fxtrade.oanda.com` |

- Every request needs the header `Authorization: Bearer <personal access token>`. Practice and live tokens are separate. A token is made (or revoked and re-made) under "Manage API Access" in the OANDA hub. Making a new token **invalidates the old one**.
- Account IDs look like `101-001-XXXXXXXX-001`. `101` = practice (live starts `001`). The second group is the division: `001` = OANDA Corporation (US, NFA rules).
- `GET /v3/accounts` lists the accounts a token can use. Sub-accounts (`…-002`, `…-003`) are created in the hub, not through the API.
- Send `Accept-Datetime-Format: RFC3339` to get ISO times (the other option is UNIX).

## Rate limits and connection practice (Best Practices page)
- At most **2 new connections per second**.
- At most **100 requests per second** on a persistent (keep-alive) connection.
- Use one `requests.Session`. Our client spaces calls ≥60 ms apart and backs off on 429/5xx.
- For a live account view: `GET /accounts/{id}` once, then `GET /accounts/{id}/changes?sinceTransactionID=…`. Our 5-minute one-shot runner only needs `openTrades`, `pendingOrders`, `summary` and `pricing`.

## Market data
- `GET /v3/instruments/{instrument}/candles` takes these parameters:
  - `granularity`: S5…M1, M5, M15, H1, H4, D, W, M
  - `price`: M (mid), B (bid), A (ask), or a combination like `MBA`
  - `count` (max **5000**), or `from` / `to`
  - `dailyAlignment` (default 17) and `alignmentTimezone` (default America/New_York): daily candles roll at **17:00 New York**
  - `weeklyAlignment`
  - `smooth`
- Each candle has `complete: true/false`. Only complete candles are used for signals.
- `GET /v3/accounts/{id}/pricing?instruments=A,B` returns current bids/asks, `tradeable`, and the `closeoutBid/Ask` used for margin. Spread = ask − bid.
- `GET /v3/accounts/{id}/instruments` returns:
  - `pipLocation` (−4 for EUR_USD, −2 for JPY pairs)
  - `displayPrecision` (5 or 3)
  - `tradeUnitsPrecision` (0 = whole units)
  - `minimumTradeSize`
  - `marginRate`
  - `minimumTrailingStopDistance`
  - `guaranteedStopLossOrderMode`, etc.

## Orders (POST /v3/accounts/{id}/orders, body `{"order": {...}}`)
- **MARKET**: `timeInForce` must be FOK (default) or IOC. Optional `priceBound` = worst acceptable price.
- **LIMIT / STOP / MARKET_IF_TOUCHED**: price threshold; `timeInForce` GTC (default), GTD + `gtdTime`, or **GFD** (cancelled at 17:00 NY). A STOP fills at "equal or worse" than its price. If the market gaps through both the price and `priceBound`, the stop order is **cancelled** instead of filled.
- `triggerCondition`: DEFAULT (ask for buys, bid for sells), INVERSE, BID, ASK, MID. Orders always *fill* on their default side.
- `positionFill`: DEFAULT, OPEN_ONLY, REDUCE_FIRST, REDUCE_ONLY. We use **OPEN_ONLY** so one system can never reduce another system's position.
- Dependent orders created when the entry fills:
  - `takeProfitOnFill {price}`
  - `stopLossOnFill {price | distance}`: `distance` is measured from the fill, so a book's "30 pips from entry" is exact even after slippage
  - `trailingStopLossOnFill {distance}`
  - `guaranteedStopLossOnFill`: needs GSLO enabled on the account, costs a premium, and has a minimum distance
- `clientExtensions {id, tag, comment}` labels the order. `tradeClientExtensions` labels the trade it opens. We tag every order and trade `tag=fxexp`, `comment=<system id>`, so the runner and the scorecard only ever touch their own trades.
- Change a pending order: `PUT /orders/{id}` (cancel/replace: you get a new order ID, so use the client ID). Cancel: `PUT /orders/{id}/cancel`.
- Trade management:
  - `PUT /trades/{id}/orders` with `{"stopLoss": {...}, "takeProfit": {...}, "trailingStopLoss": {...}}` creates, replaces or cancels the trade's dependent orders. This is how we trail stops.
  - `PUT /trades/{id}/close` with `{"units":"ALL"}` closes the trade.
  - `GET /trades?state=CLOSED` lists closed trades.
  - Positions: `GET /positions`, `PUT /positions/{instr}/close`.
- Trailing stop: the TSL follows the price by a fixed distance and is checked on the bid (long) or ask (short). None of our books uses a fixed-distance trail, so we move a normal stop-loss instead (Turtle 10-day low, Bollinger Parabolic, Raschke prior-day low).

## Units, pips, margin and costs
- Units = base-currency amount. +units = long, −units = short. Minimum 1 unit.
- Pip = 10^pipLocation: 0.0001, or 0.01 for JPY pairs. Prices quote to 1/10 pip.
- P&L per unit is in the quote currency, converted to the account currency (USD). Sizing formula: `units = NAV × risk% / (|entry − stop| × quote→USD)`.
  - quote→USD is 1 for XXX_USD.
  - It is 1/USDJPY for JPY pairs, and 1/USDCAD or 1/USDCHF for CAD and CHF.
- US (NFA) accounts:
  - Leverage caps: 50:1 on majors (2% margin), 20:1 on others (5%).
  - **No hedging.** You can't hold a long and a short on the same instrument.
  - **FIFO.** Trades on one instrument close oldest-first.
  - If two systems trade EUR_USD in one account, their trades and brackets interfere. **Fix: one OANDA sub-account per system** (`OANDA_ACCOUNT_<SYSTEM>` in the env file). Without sub-accounts the runner still works, but `OPEN_ONLY` will reject an entry that conflicts with another system's open position.
- Costs:
  - The spread is the main cost. It is widest around the 17:00 NY rollover (about 16:55–17:15) and on Sunday's open, and widens around big news.
  - Financing (rollover) is charged or paid at 17:00 NY on positions held through it. Wednesday's charge is ×3 for the weekend. The sign depends on the interest-rate differential plus OANDA's markup.
  - Backtest assumptions: spreads per pair (instruments.py), +0.3 pip slippage on stops and market orders, ×2 spread at the 17:00 open, and 0.3 pip/night financing.

## Practice vs live in this project
- `OANDA_ENV` must be `practice`, or the runner and scorecard exit.
- The client also refuses the live host unless `FX_ALLOW_LIVE=YES`. That is never set by us.

## Status of OANDA credentials on the VM (checked read-only, 2026-10-02 ~15:20 CT)
There are three env files with OANDA practice accounts (`101-001-…`, US division, `OANDA_ENV=practice`, practice URL):
- `~/bots/oanda_active/.env`
- `~/.env`
- `~/bots/killer_beast_sniper/.env`

**All three tokens get `401 Unauthorized`** from `api-fxpractice.oanda.com`.

The running `oanda-daemon` and `oanda_scalper` services log the same 401 errors. `oanda_scalper` retries about 4×/second.

So **no working OANDA practice token exists today**. Marvin needs to make a new practice token in the OANDA hub. Note that this also changes the token the existing bots use. A live token was not tested.
