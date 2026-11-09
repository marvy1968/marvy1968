# Install COS1 Alpaca (paper, dry-run)

The unit runs `python -m cos1_alpaca run --dry-run` every 5 minutes from 09:30 to 15:55 America/New_York, Monday to Friday. It does not place orders. `--paper` is a separate, manual choice and still cannot reach the live host.

This tree installs only under `/opt/cos1-alpaca` with its own user and virtualenv. Do not copy it into `/opt/marv-bot`, do not edit the Marv units, and do not restart the betting or forex services.

## Layout

```
/opt/cos1-alpaca/            code
/opt/cos1-alpaca/.venv/      this bot's virtualenv
/opt/cos1-alpaca/.env        ALPACA_API_KEY and ALPACA_SECRET_KEY, mode 600
/opt/cos1-alpaca/state/      JSON state, SQLite journal, daily reports, lock
```

## Steps

```bash
sudo useradd --system --create-home --home-dir /opt/cos1-alpaca --shell /usr/sbin/nologin cos1
sudo mkdir -p /opt/cos1-alpaca
sudo rsync -a --exclude .venv --exclude state --exclude .env ./ /opt/cos1-alpaca/
sudo python3 -m venv /opt/cos1-alpaca/.venv
sudo /opt/cos1-alpaca/.venv/bin/pip install -r /opt/cos1-alpaca/requirements.txt
sudo install -m 600 -o cos1 -g cos1 /dev/null /opt/cos1-alpaca/.env
# edit /opt/cos1-alpaca/.env yourself. Never commit it and never paste it into chat.
# ALPACA_API_KEY=...
# ALPACA_SECRET_KEY=...
sudo chown -R cos1:cos1 /opt/cos1-alpaca
sudo cp /opt/cos1-alpaca/deploy/cos1-alpaca.service /etc/systemd/system/
sudo cp /opt/cos1-alpaca/deploy/cos1-alpaca.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now cos1-alpaca.timer
```

Check a single pass:

```bash
sudo -u cos1 env COS1_ENV_FILE=/opt/cos1-alpaca/.env \
  /opt/cos1-alpaca/.venv/bin/python -m cos1_alpaca run --dry-run
```

Journal: `state/cos1.sqlite`. Report: `state/reports/daily-YYYY-MM-DD.txt`. Logs go to the journald unit `cos1-alpaca`.

Do not change the unit to `--paper`. Top-down, the news filter, and the SPY-versus-open rule stay off until `validate` prints `pass=True` for that study. A failure stays off.

```bash
sudo -u cos1 env COS1_ENV_FILE=/opt/cos1-alpaca/.env \
  /opt/cos1-alpaca/.venv/bin/python -m cos1_alpaca validate
```

Earnings dates are optional. A CSV at the path in `paths.earnings_csv` (`symbol,YYYY-MM-DD`) blocks the session before, of, and after each date. With no file the guard is a stub and does not block.

To place paper orders, run once by hand with `--paper`. The timer stays on `--dry-run` until you edit the unit on purpose. There is no live switch.
