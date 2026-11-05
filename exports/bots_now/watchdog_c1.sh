#!/bin/bash
SERVICE="oanda-c1.service"
if ! systemctl is-active --quiet "$SERVICE"; then
    echo "$(date): $SERVICE is down! Attempting restart..." >> /home/moonrider1968/bots/watchdog.log
    sudo systemctl restart "$SERVICE"
fi
