# The owner's forex / OANDA bots, copied from the VM (2026-10-26)

Redacted copies (tokens, account ids, API keys, chat ids removed by `deploy/find_odds_bot.sh`; the export aborts if secret-like text is left).
Archive folders, logs, databases and files over 300 KB were skipped. Source folders on the VM (under /home/moonrider1968):

| Folder here | VM source |
|---|---|
| oanda_active | bots/oanda_active |
| masterbot_oanda | MasterBot_Center/Trading_Oanda |
| oanda_master_alliance | oanda_master_alliance |
| rangebox_master | bots/RangeBox_Master |
| forex_bot | forex_bot |
| scalp_bot | scalp_bot |
| velez_bot | velez-bot |
| orchestrator | orchestrator |

Reference only: nothing here is imported by Marv, the Alpaca bot or `fxbot`. Loose OANDA scripts in the VM home folder itself
(oanda_forex_v8.3.py, oanda_hybrid_v7.3.py, backtest_*.py ...) were not copied (that folder also holds unrelated files and secrets);
ask for specific files. Re-export with the vm-link job `export-bot <name> /abs/folder`.
