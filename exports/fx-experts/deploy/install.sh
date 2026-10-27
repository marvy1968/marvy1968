#!/usr/bin/env bash
# fx-experts VM install -- DO NOT RUN until Marvin approves.  Touches nothing that exists today:
# new dir /opt/fx-experts, new units fx-experts*.  No existing bot/service is stopped or modified.
set -euo pipefail
SRC=${SRC:-/tmp/fx-experts}          # unpacked bundle
DST=/opt/fx-experts
test -e /etc/systemd/system/fx-experts.service && { echo "already installed"; exit 1; }
sudo mkdir -p $DST && sudo chown moonrider1968:moonrider1968 $DST
cp -r $SRC/fxexp $SRC/config $SRC/tests $SRC/requirements.txt $SRC/*.md $DST/
mkdir -p $DST/state $DST/logs
python3 -m venv $DST/venv && $DST/venv/bin/pip install -q -r $DST/requirements.txt
if [ ! -f $DST/.env ]; then install -m 600 $SRC/deploy/env.example $DST/.env; echo ">> fill in $DST/.env (token, account ids, telegram)"; fi
chmod 600 $DST/.env
(cd $DST && $DST/venv/bin/python -m pytest -q tests)
# 1) read-only smoke test: token works, account is practice, no orders sent
(cd $DST && $DST/venv/bin/python -m fxexp.check --env $DST/.env --config $DST/config/config.json)
# 2) one dry-run pass (computes orders, sends nothing)
(cd $DST && $DST/venv/bin/python -m fxexp.live --dry-run --config $DST/config/config.json --env $DST/.env --state $DST/state/dryrun_state.json)
sudo install -m 644 $SRC/deploy/fx-experts.service $SRC/deploy/fx-experts.timer $SRC/deploy/fx-experts-scorecard.service $SRC/deploy/fx-experts-scorecard.timer /etc/systemd/system/
sudo systemctl daemon-reload
echo "Installed but NOT enabled. To start paper trading:"
echo "  sudo systemctl enable --now fx-experts.timer fx-experts-scorecard.timer"
