# Security review

The ledger stores a template path, sport key, market and region and bookmaker
names, an explicit account label such as `paid` or `free`, status, the parsed
usage integers of at most 9 digits, and exception class names. Values past that bound, and any header text, are dropped before `int()` or `str()`. The observer waits at most 300ms on a locked ledger and does not sleep or retry the provider. It does not store a request URL, a
query string, an API key, a key hash, a response body, or the text of an
exception.

Account labels are accepted only as short public tokens. A value that looks
like a URL, a query, or a long hex string is dropped. Header values other than
the three usage counters are discarded, and even those are stored only when
they parse as non-negative integers. Zero is kept. A negative or malformed
counter is a status code, not a number, and it is not rewritten to zero.

Global `x-requests-used` and `x-requests-remaining` snapshots are labeled as
account-wide. Reconciliation will not assign the gap between snapshots to a
consumer and will not report a decrease as a refund. Labels are not treated as
proof that two processes share one provider account.

The prop-shadow replay allowlist is unchanged. It still forces send off and
engine on, and it does not copy a provider key. The shadow credit file is
still the only place that reserves the existing 100-credit UTC-day budget.
The new ledger does not reserve, spend, or fail a call closed.

Observer errors are swallowed at the note, not at the provider exception.
Existing AJ behavior that already catches a transport error and returns none
is unchanged. Marv still re-raises the original exception object.

Enforcement is not wired in. `ENFORCEMENT_ENABLED` is false, and the cap,
priority, and polling functions raise `NotImplementedError`. No unit file,
workflow, installer, or vm-link job is changed. Nothing in this package places
an order or sends a message.

Limitations: forex and other direct HTTP callers listed in `COVERAGE.md` are
outside this ledger. A process that does not set `ODDS_ACCOUNTING_LEDGER` records
nothing. Two jobs that share a key but use different labels are not merged,
because that link was not verified. The Marv featured and props transports do
not receive a per-caller name, so live, board, and alt70 rows share a source job.
