# Kalshi DEMO order-intent candidate

Review-only package for the rank-1 gap: `kalshi_demo.py` mints `uuid.uuid4()` inside `_place_ioc` and again for the resting remainder, then callers log after the HTTP call returns.

`order_safety.demo_client.DemoClient` is the source-backed review client. It builds the POST JSON from `kalshi_demo.py` lines 96-100 and 145-156, and the `client_order_id` in that JSON is the id already committed in the ledger. The HTTP callable is injected. Tests pass a fake. Nothing in this package opens a socket.

This is a review candidate. The running caller modules are not edited and are not imported. Their status is `blocked` in `caller_status.py`. Offline tests of this client are not an integrated fix and are not real-order qualification.

## What the candidate covers

- IOC and resting JSON: book side, `count` as `"N.00"`, four-decimal price, time in force, and `taker_at_cross`. The resting child id is a second persisted id. Its count is the parent's known remainder.
- A lost or malformed response, a wrong client id, a wrong ticker or book side, or a non-integer `fill_count` stays `submit_unknown`. No resting child is opened. A new `DemoClient` on the same ledger does not POST that decision again.
- Cancel sends `DELETE /portfolio/events/orders/{venue_order_id}` only when that id was stored from an explicit ack. The state stays `cancel_unknown`. A second sweep does not send another DELETE.
- A `resting_orders.jsonl` row whose order id is not already in the ledger is ambiguous. The sweep sends nothing and does not create an intent.
- Repeated identical snapshots keep the same fill counts. A snapshot that repeats one client id with two fill counts blocks and does not apply either. A missing non-terminal child blocks and does not become filled or cancelled.
- Shard index 0 needs no transfer. An unknown shard, or a shard whose supplied balance is short of `limit_cents * count + 200`, raises `FundingUnsupported` before any intent or POST. This candidate does not move balances and does not claim transfer recovery.

## What it does not cover

The seven caller sites in `caller_status.py` still call the live client, read environment or switch files, and log after acceptance. Importing them would be a service hook. They are blocked, not integrated.

`KALSHI_REST_PARTIAL` and `KALSHI_REST_S` are not read. `rest_enabled`, `now_s`, and `rest_seconds` have to be passed in. The 300-second default matches the source literal only when the caller passes it.

Polymarket stays a local paper ledger and rejects a base URL. There is no live Polymarket order API here.

## Tests

```
PYTHONPATH=review/kalpoly-order-safety python3 -m unittest discover -s review/kalpoly-order-safety/tests -v
```

21 tests, no network. Twelve exercise the ledger. Nine drive `DemoClient` and inspect the request JSON.
