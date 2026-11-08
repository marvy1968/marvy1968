# Kalshi DEMO / Polymarket paper order-intent candidate

Review-only package for the first ranked order-safety gap: a crash between venue acceptance and the caller's log can orphan an accepted order, and a later retry builds a new client id.

This tree is not imported by a running service. It does not install a unit, open a socket, read a credential, or enable a live mode. The default objects persist to a path the caller supplies and call only the transport function the caller passes in.

## What the candidate does

`order_safety.OrderMachine` commits an intent and a stable `client_order_id` before it calls the transport. The id is UUID5 of a fixed namespace and `venue.mode.account.decision`. A second writer for the same decision finds that row and does not POST. A raised or unparseable response becomes `submit_unknown` and blocks that account. The block clears only when a later snapshot is explicit and consistent.

A resting child is opened only after the parent fill count is known. Its count is the parent's remainder. A lost IOC response opens no child.

`request_cancel` records `cancel_requested` before the transport and always ends at `cancel_unknown`. A cancel body, including HTTP 200 or `reduced_by`, is not a cancelled order. Only a snapshot status `canceled` or `cancelled` with an explicit fill count can set `cancelled`.

An extra venue order, a missing non-terminal intent, a fill that disagrees, a non-explicit status, or a host that differs from the intent blocks further submits. The ledger does not rewrite those rows to filled or cancelled. Orders already `filled` or `cancelled` may be absent from a later open-order list; their state is left as it is.

Kalshi is accepted only as `kalshi_demo` plus `mode=demo` plus one of the two demo hosts copied from `kalshi_demo.py`. `mode=live` and an account label of `unknown` are refused. Polymarket is a local paper ledger (`polymarket_paper` / `paper`) and rejects any base URL.

Count, limit, and the caller's fee text are stored as given. This package does not recompute a fee and does not move shard funds.

## How a call site would use it

`order_safety.callers.place_demo` and `place_ioc_then_rest` are the seams for the sanitized Kalshi `place_order` sites listed in `CANDIDATE_PATCHES.md`. Tests call those functions with a fake transport. No production module imports them.

## Tests

```
PYTHONPATH=review/kalpoly-order-safety python3 -m unittest discover -s review/kalpoly-order-safety/tests -v
```

The suite is offline. A passing run does not qualify a real order.
