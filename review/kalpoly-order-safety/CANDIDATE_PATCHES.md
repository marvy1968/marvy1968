# Candidate call-site integration

These replacements are not applied. The running modules still call `KalshiDemo.place_order` and append a log after that call returns. The review seam is `order_safety.callers`, and `tests/test_order_safety.py` drives every site name through a mocked transport.

Each site keeps the count and price expression it already uses. The candidate does not rebuild the venue JSON (`bid`/`ask`, dollar price, `count` as `"N.00"`), does not sign a request, and does not call `ensure_shard_funds`.

```
runner.py:163
- rec["order"] = c.place_order(m["ticker"], "yes", cnt, ask)
+ rec["order"] = place_demo(machine, transport, caller="runner", account_label=ACCOUNT,
+     decision_id=DECISION, ticker=m["ticker"], side="yes", count=cnt, limit_cents=ask)

runner.py:213
- rec["order"] = c.place_order(m["ticker"], m.get("side", "yes"), max(1, int(stake * 100 // ask)), ask, meta=rec)
+ rec["order"] = place_demo(machine, transport, caller="runner_props", account_label=ACCOUNT,
+     decision_id=DECISION, ticker=m["ticker"], side=m.get("side", "yes"),
+     count=max(1, int(stake * 100 // ask)), limit_cents=ask)

me_kalshi.py:345
- rec["order"] = c.place_order(m["ticker"], side, max(1, int(stake * 100 // ask)), ask, meta=rec)
+ rec["order"] = place_ioc_then_rest(machine, ioc_transport, rest_transport, caller="me_kalshi",
+     account_label=ACCOUNT, decision_id=DECISION, ticker=m["ticker"], side=side,
+     count=max(1, int(stake * 100 // ask)), limit_cents=ask)

live_runner.py:112
- rec["order"] = c.place_order(m["ticker"], m.get("side", "yes"), max(1, int(stake * 100 // ask)), ask, meta=rec)
+ rec["order"] = place_demo(machine, transport, caller="live_runner", account_label=ACCOUNT,
+     decision_id=DECISION, ticker=m["ticker"], side=m.get("side", "yes"),
+     count=max(1, int(stake * 100 // ask)), limit_cents=ask)

kalshi_nba_pacer.py:198
- rec["order"] = c.place_order(m["ticker"], x["side"], n, ask, meta=rec)
+ rec["order"] = place_demo(machine, transport, caller="nba_pacer", account_label=ACCOUNT,
+     decision_id=DECISION, ticker=m["ticker"], side=x["side"], count=n, limit_cents=ask)

marv_cbb_kalshi.py:58
- o["order"] = c.place_order(m["ticker"], side, n, int(ask))
+ o["order"] = place_demo(machine, transport, caller="marv_cbb", account_label=ACCOUNT,
+     decision_id=DECISION, ticker=m["ticker"], side=side, count=n, limit_cents=int(ask))

live_props.py:274
- o["order"] = c.place_order(rec["ticker"], rec["side"], n, ask)
+ o["order"] = place_demo(machine, transport, caller="live_props", account_label=ACCOUNT,
+     decision_id=DECISION, ticker=rec["ticker"], side=rec["side"], count=n, limit_cents=ask)
```

`ACCOUNT` and `DECISION` are required inputs. This candidate has no default account and does not read a key file. A missing or `unknown` account is a blocker.

`place_ioc_then_rest` is the review stand-in for `KalshiDemo.place_order`'s IOC-then-remainder branch (`kalshi_demo.py` 81-117). The child decision id is the parent decision plus `.rest`, and the child count is `count - filled_count`. The existing `KALSHI_REST_PARTIAL` switch is unchanged and is not reimplemented here.

## Paths this candidate does not cover

- `KalshiDemo._place_ioc` still generates `uuid.uuid4()` at the POST (`kalshi_demo.py` 150) and calls `ensure_shard_funds` (160-185), which can POST a transfer with a fresh `client_transfer_id` and sleep. That fund movement is not reproduced.
- `sweep_resting` (122-140) still records `expired_or_filled` when cancel throws. The candidate's reconcile treats that status string as non-explicit and blocks.
- `cancel_order` (119-120) still returns the DELETE body to the caller. The candidate treats any cancel transport result as `cancel_unknown`.
- `pm_client.py` has no order method. `paper_fill` stores a local paper intent only. A base URL raises `NoRealPolymarketOrderApi`.
- `side_guard.py`, type-gate config, stake, bankroll, lane caps, and risk thresholds are unchanged.
