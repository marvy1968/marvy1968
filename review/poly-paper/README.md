# Polymarket PAPER identity review

Review-only source for simulated Polymarket paper fills. It is not wired into startup,
timers, workflows, or the VM updater. It does not place orders, read feeds, or open a
production ledger.

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

## Policy precedence

Encoded in `poly_paper/effective_policy.json` and `poly_paper/policy.py`. Comments in the
old source are not treated as new authority. The executable order is:

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

## Limits and remaining decisions

- Token ids are not in the screened public client or the observed fill. When a payload
  has no token field, a unique participant match can still paper-fill and the audit
  records `token_omitted_by_provider`. A present token list that is not bound to a side
  (`clobTokenIds`) is rejected instead of assigned by index. Empty-string tokens are
  omitted; disagreeing token fields are rejected.
- Abbreviations alone do not identify a school. `UNLV` matches when it is a full alias,
  not when it exists only as a three-letter abbreviation.
- Game dates use America/Chicago. A naive timestamp is unknown and does not fill.
- Spread and total fills must be the identified event and the right team or over/under.
  The line picked inside 0.5 points is left to the mapper. This package does not redo it.
- Props fill only when the snapshot has both participants and explicit player, stat, and
  line. A slug and a player name are not enough.
- Edge, stake, calibration, daily cap, Bovada lookup, and one-bet-per-slug dedupe stay
  outside this package. `insert()` will accept two different keys on one event if identity
  and policy pass. The baseline runner still has those guards; this review does not run it.
- With `PM_TRADE_ALL` off, the boundary follows the allowlist. It does not recreate the
  runner's NFL-dog or CFB card exceptions. Under the default, trade-all on, those
  exceptions are not what authorizes the fill.
- `h2h_cfb` remains tradable while trade-all is on, because `allowed()` returns before
  `never_trade`. Turning that into a hold would be a policy change and is not done here.
- The guard covers this package's connection. A separate `sqlite3.connect` to the same
  file, or code that clears the authorizer on the private connection object, is outside
  it. Public `execute`, `executemany`, and `executescript` cannot insert.
- A database that already has `fills` and is not this review schema is refused. It is
  not migrated. Fill 619 is not rewritten.
- This tree does not change stake, edge, fees, model selection, or NBA prop handling.
  Fee math is checked, not replaced.
