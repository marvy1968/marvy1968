# Gaps that this review cannot close

Status: partial. Not deployment-ready. No process imports `candidate_calls`.
`ODDS_ACCOUNTING_LEDGER` is not set by this package, by a unit file, or by
`.env.example`. The running VM and the current repository imports are unchanged
by the candidate adapters.

| File / version | What is missing | Smallest read-only export that would close it |
|---|---|---|
| Shadow `collector.py` `_real_transport`, Muse `c3cb88de`, lines 861-871 | `urllib.request.urlopen` returns the final response only. There is no hop list. This review cannot promise one ledger row per wire request on that path. | A note of the installed redirect handler, if any. The export has the default `urlopen` call and no custom handler. Do not invent hop counts. |
| VM `alt70.py`, feed `a5d7bdbd` | The odds calls are adapted. The rest of `fetch` / the CFB path reads `/opt/marv-bot` state, rewrites `O.events`, and runs the props model. That pipeline is not reproduced. | Not required for the transport tag. A full stand-in would need the VM state files and the model modules, which this accounting review does not copy. |
| `tools/fetch_bovada_nfl.py` in the repo | The candidate issues one historical GET with timeout 40. The script's own loop retries 3 times and sleeps 3 seconds, and it is still the code that runs. | None. Wiring the candidate into that loop would edit the tool. The loop stays where it is. |
| Async callers `live_odds_engine.py`, `build_backtest_data.py`, `build_backtest_history.py`, `live_engine/payload_builder.py` | The candidate repeats `session.get(url, params=params)` and can be tested with a fake session. The cache, poll loop, and exception swallow stay in those modules and are not activated here. | None for the request shape. A claim that the running coroutine records usage would require those modules to import the candidate, which they do not. |
| Forex `masterbot_oanda` scripts | Request shapes are in the candidate table. The scripts do not import it. They are also not in the VM service export, so this review does not claim they run on the VM. | A process list only if someone needs to know whether the VM executes them. Not required to describe the source. |

Distinct providers stay out of this ledger: OddsAPI.io, Oddspapi, RapidOdds, SharpAPI, ParlayAPI, and PropLine.
