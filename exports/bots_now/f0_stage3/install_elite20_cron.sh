#!/bin/bash
CRON_JOB="10 16 * * 1-5 /home/moonrider1968/bots/oanda_active/venv/bin/python /home/moonrider1968/bots/f0_stage3/run_elite20_paper_scheduler.py >> /home/moonrider1968/bots/f0_stage3/paper_logs/cron_stdout.log 2>&1"

# Check if cron job already exists to avoid duplicates
(crontab -l 2>/dev/null | grep -Fq "run_elite20_paper_scheduler.py") && echo "[INFO] Elite-20 paper scheduler cron job already installed." || (
    (crontab -l 2>/dev/null; echo "$CRON_JOB") | crontab -
    echo "[SUCCESS] Elite-20 paper scheduler cron job successfully installed."
)

echo "Current crontab entries:"
crontab -l
