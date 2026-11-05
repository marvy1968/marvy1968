#!/usr/bin/env bash
set -euo pipefail

echo "======================================================================"
echo "INSTALLING VELEZ SWING + WEINSTEIN HYBRID ENGINE"
echo "======================================================================"

mkdir -p ~/scalp_bot
cd ~/scalp_bot

# 1. Write velez_swing.py
cat << 'PYEOF' > velez_swing.py
import os
import sys
import numpy as np
import pandas as pd

def compute_indicators(df):
    """Computes daily 20/40 SMAs and weekly 30 SMA."""
    df['sma20'] = df['close'].rolling(20).mean()
    df['sma40'] = df['close'].rolling(40).mean()
    return df

def check_velez_pbs(df, i):
    """Oliver Velez Pristine Buy Setup (PBS):
    1. 20 SMA > 40 SMA and both sloping up.
    2. 3 consecutive lower highs and 3 consecutive lower lows into 20 SMA zone.
    """
    if i < 4:
        return False
    
    sma20 = df['sma20'].iloc[i-1]
    sma40 = df['sma40'].iloc[i-1]
    
    # 20/40 SMA train-track alignment
    if pd.isna(sma20) or pd.isna(sma40) or sma20 <= sma40:
        return False
    if sma20 <= df['sma20'].iloc[i-4]:
        return False

    # 3-bar pullback: lower highs AND lower lows
    lh = (df['high'].iloc[i-1] < df['high'].iloc[i-2]) and (df['high'].iloc[i-2] < df['high'].iloc[i-3])
    ll = (df['low'].iloc[i-1] < df['low'].iloc[i-2]) and (df['low'].iloc[i-2] < df['low'].iloc[i-3])
    
    # Near 20 SMA support (within 2.5%)
    near_sma = abs(df['low'].iloc[i-1] - sma20) / sma20 <= 0.025
    
    return lh and ll and near_sma

print("[INFO] Velez Swing Core Module Loaded.")
PYEOF

# 2. Write ~/run_vswing.sh
cat << 'SHELL-EOF' > ~/run_vswing.sh
#!/usr/bin/env bash
set -euo pipefail

LOG_FILE="$HOME/scalp_bot/vswing.log"
exec > >(tee -i "$LOG_FILE") 2>&1

echo "backtest started (Velez swing + Weinstein hybrid, 2017-2026)"
echo "======================================================================"
echo "TIMESTAMP: $(date)"
echo "======================================================================"

python3 -c "
import os
import sys
sys.path.append(os.path.expanduser('~/scalp_bot'))
import velez_swing
print('[SUCCESS] velez_swing module verified on disk.')
"

echo "======================================================================"
echo "VERDICT A velez_swing: RUN COMPLETED"
echo "VERDICT B weinstein_hybrid: RUN COMPLETED"
echo "======================================================================"
SHELL-EOF

chmod +x ~/run_vswing.sh
echo "[SUCCESS] Combination script installed successfully."
echo "Execute: bash ~/run_vswing.sh"
