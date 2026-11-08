# Endpoint coverage

This is not claimed complete. Source routes are not evidence that a job is
scheduled. Caller files that share one transport are not separately attributed.

## Instrumented physical boundaries

| Boundary | Endpoints | Callers reached through that boundary |
|---|---|---|
| AJ `OddsAPI._get` | `/v4/sports/{sport}/odds`, `/events`, `/events/{event}/odds` | Live, LiveProps, Value, TeamProps, NBA, NCAAB, and the other existing strategy tags on that client. Close and the optional query tag use the same method. The provider wrapper delegates to this client and is not counted again. |
| Marv `data/oddsapi.py` `fetch` | `/v4/sports/{sport}/odds` featured h2h, spreads, totals | `sources.py`, `board.py`, `betcard.py`, `live_monitor.py`, and the featured call in `cfb_props_shadow.py`. These callers are one source job, `marv-feed`. |
| Marv `sharpgap.py` `fetch` | same featured odds route, Bovada and Pinnacle | sharp-gap fetch only |
| Marv `parlay.py` `_fetch_bovada` | same featured odds route, Bovada | parlay price fetch only. A cache hit in `load_prices` does not call it. |
| Marv `props/odds.py` `_get` | events, event odds, historical events, historical event odds | `live_props.py`, `main.py`, `cfb_props_shadow.py`, `props/run.py`, `props/cfb_run.py`, `props/basketball_run.py`, and the VM `alt70.py` overlay, which imports this module. Cache hits are not attempts. A budget refusal is a skipped preflight, not a request. |
| Prop-shadow `_odds_request` | events and event odds | collector discovery and the chosen-game props call. ESPN reads through the same transport function are not Odds attempts. |

Historical AJ paths still raise before a request. That refusal is recorded as not physical. It does not enable historical calls.

## Not instrumented

These public-repo or VM files make their own HTTP calls, or they are a different provider. This review does not change them and does not count them as covered:

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
- `tools/fetch_bovada_nfl.py`
- `muse-prop1/bovada.py` (PropLine, not THE ODDS API; the active collector does not need it for this boundary)
- `ajbet/parlayapi.py`
- `ajbet/oddspapi.py`
- `ajbet/rapidodds.py`
- `ajbet/sharpapi.py`
- `ajbet/providers/oddsapiio.py`
- `ajbet/providers/base.py` records other providers in the existing AJ usage table. It is not an Odds API transport.

`marv/live_monitor.py` and `marv/main.py` in this repository are not byte-identical to the VM export. Their Odds calls still go through the matching transport files, which are byte-identical aside from the note. Those caller files were not replaced.

VM feed overlays other than the props client were not copied. `alt70.py` is covered only to the extent it calls `marv.props.odds`.

The bulk legacy ESPN replay fixtures were omitted from the export. Broad replay tests that need them are not reproduced here. New tests use the synthetic cases in `fixtures/`.

No production usage table was copied or rewritten. `import_legacy_usage_row` keeps an old cost in `legacy_cost_unlabeled` and leaves `actual_header_cost` empty. A historical zero stays unlabeled, not an actual zero.
