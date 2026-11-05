#!/bin/bash
# usage: bash zn.sh backtest | status | results | zones SYM ... | plan | submit | cron
cd ~/scalp_bot && source ~/venv/bin/activate
set -a; source <(grep -E '^[A-Za-z_][A-Za-z0-9_]*=' ~/.env); set +a
X=""; [ "$MF" = "1" ] && X="--market-filter"
case "$1" in
  backtest) rm -f zones_done.txt
            nohup bash -c 'python zones.py --start 2017-01-01 --end 2021-12-31 > zones.log 2>&1
                           python zones.py --start 2017-01-01 --end 2021-12-31 --market-filter > zones_mf.log 2>&1
                           echo done > zones_done.txt' > /dev/null 2>&1 &
            echo "2 zone backtests started (pid $!). Check: bash zn.sh status" ;;
  status)   if [ -f zones_done.txt ]; then echo "finished - run: bash zn.sh results";
            else pgrep -af "zones.py" || echo "not running"; fi ;;
  results)  for f in zones.log zones_mf.log; do echo; echo "######## $f"; [ -f $f ] && tail -32 $f || echo "(not run)"; done ;;
  zones)    shift; python zones.py --zones "$@" ;;
  plan)     python zones_bot.py $X ;;
  submit)   python zones_bot.py --submit $X 2>&1 | tee -a zones_bot.log ;;
  cron)     echo "Add this line with 'crontab -e' (every 15 min, Mon-Fri; the bot itself checks the market is open):"
            echo "*/15 13-21 * * 1-5 bash \$HOME/scalp_bot/zn.sh submit > /dev/null 2>&1" ;;
  *)        echo "usage: bash zn.sh backtest | status | results | zones SYM ... | plan | submit | cron" ;;
esac
