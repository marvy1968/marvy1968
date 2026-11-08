# Endpoint coverage

This is not claimed complete. Source routes are not evidence that a job is
scheduled. Caller files that share one transport are not separately attributed.

## Instrumented physical boundaries

| Boundary | Endpoints | Callers reached through that boundary |
|---|---|---|
| AJ `OddsAPI._get` | `/v4/sports/{sport}/odds`, `/events`, `/events/{event}/odds` | Live, LiveProps, Value, TeamProps, NBA, NCAAB, and the other existing strategy tags on that client. Close and the optional query tag use the same method. The provider wrapper delegates to this client and is not counted again. |
| Marv `data/oddsapi.py` `fetch` | `/v4/sports/{sport}/odds` featured h2h, spreads, totals | Tagged where the file matches the VM export: `marv-sources`, `marv-board`, `marv-betcard`, `marv-cfb-props-shadow`, `marv-props-cfb`. A call with no tag is `unknown`. |
| Marv `sharpgap.py` `fetch` | same featured odds route, Bovada and Pinnacle | sharp-gap fetch only |
| Marv `parlay.py` `_fetch_bovada` | same featured odds route, Bovada | parlay price fetch only. A cache hit in `load_prices` does not call it. |
| Marv `props/odds.py` `_get` | events, event odds, historical events, historical event odds | Tagged on VM-matching files: `marv-live-props`, `marv-cfb-props-shadow`, `marv-props-nfl`, `marv-props-cfb`, `marv-props-basketball`. Cache hits are not attempts. A budget refusal is a skipped preflight. |
| Prop-shadow `_odds_request` | events and event odds | collector discovery and the chosen-game props call. ESPN reads through the same transport function are not Odds attempts. urllib redirect hops inside `urlopen` are not visible here. |

Historical AJ paths still raise before a request. That refusal is recorded as not physical. It does not enable historical calls.

## THE ODDS API callers with source job `unknown`

These call the instrumented transport but this review does not attach a job tag. The repo file is not the VM file, or the VM file is not in this tree. Guessing live versus board would be wrong.

| Callsite | Why the tag is unknown |
|---|---|
| Repo `marv/live_monitor.py` `_live_odds` | File is not the VM export. The VM copy also calls `oddsapi.fetch` and does not contain the repo's later prop-engine block. Neither file is edited here. |
| Repo `marv/main.py` props command | Same divergence. The VM `main.py` calls `props/odds.py` at its own lines. |
| Repo and VM `marv/live_prop_engine.py` | The two copies differ around send behavior. Both call `props/odds.py`. Neither is tagged. |
| VM `alt70.py` | Not in this repository. It calls `marv.props.odds` on the VM. A future checkout of this props module would record `unknown` until that file passes a tag. |

## THE ODDS API, not in the active Marv/AJ/shadow workflows

Real `api.the-odds-api.com` calls. They are outside the exported service entrypoints. This review does not edit them and does not count them in the shared ledger:

- `forex_bots/masterbot_oanda/app.py`
- `forex_bots/masterbot_oanda/live_odds_engine.py`
- `forex_bots/masterbot_oanda/all_engines_live.py`
- `forex_bots/masterbot_oanda/live_engine/payload_builder.py`
- `forex_bots/masterbot_oanda/basketball_team_props_engine.py`
- `forex_bots/masterbot_oanda/basketball_team_props_master.py`
- `forex_bots/masterbot_oanda/build_backtest_data.py`
- `forex_bots/masterbot_oanda/build_backtest_history.py`
- `forex_bots/masterbot_oanda/clean_master.py`
- `forex_bots/masterbot_oanda/mlb_team_props.py`
- `forex_bots/masterbot_oanda/nba_team_props.py`
- `forex_bots/masterbot_oanda/ncaaf_team_props.py`
- `forex_bots/masterbot_oanda/nfl_team_props.py`
- `forex_bots/masterbot_oanda/wnba_team_props.py`
- `tools/fetch_bovada_nfl.py` (historical featured odds)

## Different providers, not this account

These must not be added to THE ODDS API ledger. Similar header names do not make them the same bill.

| File | Host |
|---|---|
| `ajbet/providers/oddsapiio.py` | `api.odds-api.io` |
| `ajbet/oddspapi.py` | `api.oddspapi.io` |
| `ajbet/rapidodds.py` | `api.rapidoddsapi.com` |
| `ajbet/sharpapi.py` | `api.sharpapi.io` |
| `ajbet/parlayapi.py` | `parlay-api.com` (its own usage table, not `api_usage`) |
| `muse-prop1/bovada.py` | `api.prop-line.com` |
| `ajbet/http.py` `Fetcher` | ESPN and other free reads. It retries on its own and is not the Odds API client. |
| `ajbet/providers/base.py` | Logs other providers into AJ `api_usage`. Not an Odds API transport. |

## Other limits

The bulk legacy ESPN replay fixtures were omitted from the export. Broad replay tests that need them are not reproduced here. There is no full legacy replay claim.

No production usage table was copied or rewritten. `import_legacy_usage_row` keeps an old cost in `legacy_cost_unlabeled` and leaves `actual_header_cost` empty.

Shadow `urlopen` follows redirects inside one call and does not expose `response.history`. Those hops stay one `_odds_request` observation. requests clients in this review do record `response.history` because that list is already on the returned object. No extra request is made to collect it.
