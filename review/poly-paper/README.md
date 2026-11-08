# Polymarket PAPER identity review

Review-only source. It is not wired into startup, timers, workflows, or the VM updater.
It does not place orders, read feeds, or open a production ledger. It is not
deployment-ready. No host migration is authorized, and historical fills are not rewritten.

Two different things live here. They are not the same fix.

## Boundary library (`poly_paper.writers.insert`)

A caller builds a request and the library decides. It does **not** run `runner.main`
or `pm_fill.fill`. Stake, edge, daily cap, one-bet-per-slug, and the runner's NFL-dog
and CFB card exceptions are not applied. Two different keys for one event can both be
accepted. Use this only to test the identity and audit rules in isolation.

## Integrated writers (`poly_paper.integrated`)

This loads the exported `runner.py` and `pm_fill.py` unchanged (hash-checked copies)
and guards the connection `runner.db()` actually returns. `runner.main`, `pm_fill.fill`,
`pm_me_track.main`, `marv_cbb`, `pm_nba_live.main`, and `live_props.poly_fill` all reach
that connection. Their allocation, dedupe, edge, daily cap, and card-exception code is
the exported code. The only new reject is identity: the insert is dropped when the
provider event does not uniquely match the entry, or when the caller never supplied an
entry and snapshot.

Known writer connections are listed in `poly_paper.integrated.WRITER_CONNECTIONS`.
`grader.db()` updates settlement columns and is not an insert path. A separate
`sqlite3.connect` opened outside these functions is not what those writers use.

Incident shape this is built to reject: a Samford at UNLV moneyline (source game
401924912) was simulated as long "Bulldogs" on the Boyce–Liberty event after exact
event lookup failed. Fill 619 is historical. This package does not regrade it, refund
it, or open a database that is not its own review schema.

## Boundary

`poly_paper.writers.insert` is the entry point for `runner`, `pm_fill`, and the callers
that only reach a fill through `pm_fill` (`pm_nba_live`, `pm_me_track`, `marv_cbb`,
`live_props.poly_fill`). All of them call `authorize_and_commit` before any fill row is
written. `GuardedConnection.execute` rejects a direct `INSERT`/`UPDATE`/`DELETE` and
writes a rejected audit. The sqlite authorizer on that connection also denies those
mutations unless the authorization code is in the middle of its own transaction.

A fill is accepted only when all of the following hold:

- The reviewed policy file and the type gate are present and valid.
- `paper_only` is true, preprod is false, and the request is not a live order.
- Sport, event, America/Chicago date, both participants, outcome, and requested
  long/short side identify one open event. A provider id without those participants
  does not select an event. A shared nickname does not either.
- If the provider sent a token id, it is the token on that side. If the provider sent
  no token field, the id is stored as null and the audit says the provider omitted it.
  The code does not invent one.
- `force_track`, the NBA moneyline hold, a hard `KILL` file, and a pm_fill lane switch
  are applied. `PM_TRADE_ALL` cannot skip them or skip identity.
- Contracts are a positive integer, and fee and cost equal the existing formula
  `round(0.0695 * contracts * price * (1 - price), 4)` and
  `round(contracts * price + fee, 4)`.

The accept path writes the fill and the audit in one transaction. A failure rolls both
back, then writes a failure audit with no fill. Rejected attempts write an audit and no
fill. Settlement may set result and pnl only when the settlement names the stored event,
participants, and token. It does not change the stored game, and it does not settle a
row twice.

## Library policy precedence

This order is what `writers.insert` applies. It is **not** what `runner.main` applies.
The integrated path leaves the exported writer's own gate, caps, and card exceptions in
place and only adds the identity reject. Comments in the old source are not new authority.

1. Missing switch directory, missing or malformed policy, or an unapproved conflict
   (Nov 17 auto switch, NCAAB ban, identity optional, NBA moneyline hold turned off,
   paper mode not strictly paper) fails closed.
2. A live-order flag or `KILL` fails closed.
3. Identity failure track-rejects. Definite unknown identity does not buy.
4. `force_track` track-rejects, including when `PM_TRADE_ALL` is on. A `me_type` label
   cannot hide `prop:nfl:pre`.
5. NBA moneyline track-rejects on every path. Other NBA markets are not held.
6. A pm_fill lane that is off, or `PM_TRADE_ALL` off for a pm_fill writer, track-rejects.
7. `PM_TRADE_ALL` default **on** authorizes the current paper set, including NCAAB when
   identity is unique. That default supersedes the narrow `trade: ["ml:cfb"]` list.
   It still does not beat items 1–6. It does still beat `pin_track` and `never_trade`,
   which is the observed `allowed()` short-circuit.
8. `PM_TRADE_ALL` off uses the narrow list: `never_trade`, then `pin_track`, then the
   gate. NCAAB is not on that list. That is the existing narrow list, not a new NCAAB ban.
   The reviewed file says NCAAB stays eligible under the default-on policy, and a file
   that bans it is an unapproved conflict.

There is no date switch on 2026-11-17.

## Tests

From the repository root, with pytest installed:

```bash
python3 -m pytest review/poly-paper/tests -q
```

The tests use the synthetic fixture and an in-memory-style temporary sqlite file. They
do not open a network socket, a real `pm_paper.db`, or `/opt`.

## Identity added on both paths

`assess` is shared. A fill needs both participants by full alias (`Samford` matches
`Samford Bulldogs`; `North Samford` and a shared nickname do not), the same UTC start
instant, and one outcome side. The calendar date is not enough. A one-second difference
does not match. Two events with the same teams and the same start are ambiguous unless
the entry names exactly one canonical event id. An ESPN id is not that id.

`token_verified` is true only when the provider sent a token and it is the matched
side's token. An omitted token can still paper-fill, and the audit says
`token_verified: false` and `token_omitted_by_provider`. That null is not a verified
token. A later settlement that supplies a token for that fill is rejected.

Settlement of a new integrated fill changes result and pnl only. It does not change
pick, slug, key, side, contracts, price, fee, or cost. `read_legacy_fills` reads a
pre-existing fills table and does not write it. `connect()` still refuses to open that
file as a review schema.

## Blockers and uncovered behavior

- **Reconciled.** `policy.decide_fill` is the only authorization-plus-identity
  decision. With valid policy files it keeps the exported NFL-dog, CFB-dog,
  CFB-favorite, and card-rule results, and NCAAB stays eligible while trade-all is
  on. A missing or malformed policy fails closed. That is the approved difference
  from the writers' old fail-open config read. `writers.insert` calls the same
  decision and is quarantined: it does not run stake, edge, cap, or dedupe, so it
  is not a supported fill path.
- **Calibration is a labeled mock when exercised.** The writers still call
  `/opt/kalshi-demo/calib_size.py`. That file is not created here. A test can put a
  synthetic `size()` in `sys.modules` so the existing branch runs. That mock is not
  a verified calibration result. Without it, the writers' own exception keeps
  `max_stake_usd`.
- **Not fully integrated — live props.** `pm_nba_live.main` and `live_props.poly_fill`
  reach the gate and are rejected. Their source objects have no independent start
  instant and no home/away pair. The event is chosen by a date in the slug and
  team-abbreviation overlap. Those calls are not claimed as identity-complete
  accepts. See `ADAPTER_GAPS`.
- **Exact start is fail-closed.** Any difference, including one second, does not
  match. No tolerance is added for provider rounding.
- **In-process hold.** The hashed `runner.main` adds a slug to its in-memory held
  set even when the insert returns no row. That does not write a position or
  consume the daily cap. A later process does not inherit it.
- **Not a host migration.** A pre-existing fills file with no audit table is refused by
  `runner.db()`. Nothing here rewrites fill 619 or authorizes a production cutover.
