# Conference power ranks + champion references (standalone)
python3 power_ranks.py --dir <folder> [--n-champs 3] [--derive-conf-champs]
Folder: games.csv (season,date,home,away,hg,ag[,neutral]), teams.csv (season,team,conference),
champions.csv (season,kind=national|conference,team[,conference]), coaches.csv (team,coach,career_w,career_l)
optional, injuries.csv (team,player,position,status) optional.
Tests: python3 -m unittest test_power_ranks -v   (synthetic leagues, no network)
Nothing here touches marv/.  Coach weight is fitted on history and only applied if significantly positive.
Injury points per position are heuristics, not validated.

## Blind backtest
python3 blind_backtest.py --dir <folder with games.csv [teams.csv]> --season 2026 --top 10
Rates every game from earlier games only (previous season carries over, time-decayed); logistic scale fitted on the PRIOR
season's blind predictions; reports predicted vs actual favourite win rate for top-N vs top-N, any game with a top-N team,
and all games, with 95% Wilson intervals, plus every top-vs-top matchup. Tests: python3 -m unittest test_blind_backtest -v

## Getting real data (VM)
python3 fetch_cfbd.py --years 2024 2025 2026 --out ./cfb_data   # needs CFBD_API_KEY in the env
python3 blind_backtest.py --dir ./cfb_data --season 2026 --top 10
