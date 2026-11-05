#!/bin/bash
CRON_JOB="5 0,4,8,12,16,20 * * * /home/moonrider1968/bots/oanda_active/venv/bin/python /home/moonrider1968/bots/f0_stage3/run_oanda_h4_eval.py >> /home/moonrider1968/bots/f0_stage3/paper_logs/cron_stdout.log 2>&1"

# Check if cron job already exists to avoid duplicates
(crontab -l 2>/dev/null | grep -Fq "run_oanda_h4_eval.py") && echo "[INFO] Oanda H4 cron job already installed." || (
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    echo "[SUCCESS] Oanda H4 cron job successfully installed."
)

echo "Current crontab entries:"
crontab -l
