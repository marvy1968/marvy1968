#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-$HOME/bots/oanda_fifo_practice/MarchEdge}"
OUT="$ROOT/phase_28_30_search_report.txt"

if [ ! -d "$ROOT" ]; then
  echo "Folder not found: $ROOT"
  exit 1
fi

echo "Searching in: $ROOT"
echo "Writing report to: $OUT"

{
  echo "=================================================="
  echo " MARCH EDGE BOT - PHASE 28/29/30 SEARCH REPORT"
  echo "=================================================="
  echo "Generated: $(date)"
  echo "Root: $ROOT"
  echo

  echo "--------------------------------------------------"
  echo "1) Exact Phase Mentions"
  echo "--------------------------------------------------"
  grep -RIn --color=never \
    -E 'Phase 28|Phase 29|Phase 30|PHASE 28|PHASE 29|PHASE 30' \
    "$ROOT" \
    --exclude-dir=.git \
    --exclude-dir=__pycache__ \
    --exclude='*.pyc' \
    --exclude='*.log' \
    --exclude='*.db' \
    --exclude='*.sqlite' || true
  echo

  echo "--------------------------------------------------"
  echo "2) Broad Phase Mentions"
  echo "--------------------------------------------------"
  grep -RIn --color=never \
    -E 'Phase[[:space:]]*[0-9]+|PHASE[[:space:]]*[0-9]+' \
    "$ROOT" \
    --exclude-dir=.git \
    --exclude-dir=__pycache__ \
    --exclude='*.pyc' \
    --exclude='*.log' \
    --exclude='*.db' \
    --exclude='*.sqlite' || true
  echo

  echo "--------------------------------------------------"
  echo "3) Likely Related Keywords"
  echo "--------------------------------------------------"
  grep -RIn --color=never \
    -E 'self-learning|self learning|win rate|settlement|closing line|CLV|result tracking|results logger|production orchestrator|orchestrator|phase_28|phase_29|phase_30' \
    "$ROOT" \
    --exclude-dir=.git \
    --exclude-dir=__pycache__ \
    --exclude='*.pyc' \
    --exclude='*.log' \
    --exclude='*.db' \
    --exclude='*.sqlite' || true
  echo

  echo "--------------------------------------------------"
  echo "4) Files Most Likely To Contain Phase Notes"
  echo "--------------------------------------------------"
  find "$ROOT" -type f \
    \( -name "*.py" -o -name "*.md" -o -name "*.txt" -o -name "*.json" -o -name "*.yaml" -o -name "*.yml" -o -name "*.sh" \) \
    ! -path "*/.git/*" \
    ! -path "*/__pycache__/*" | sort
  echo

  echo "--------------------------------------------------"
  echo "5) Top 200 Comment Lines With 'phase'"
  echo "--------------------------------------------------"
  grep -RIn --color=never \
    -E '#.*phase|//.*phase|/\*.*phase|\*.*phase|phase' \
    "$ROOT" \
    --exclude-dir=.git \
    --exclude-dir=__pycache__ \
    --exclude='*.pyc' \
    --exclude='*.log' \
    --exclude='*.db' \
    --exclude='*.sqlite' | head -n 200 || true
  echo

} > "$OUT"

echo
echo "Done."
echo "Open the report with:"
echo "less \"$OUT\""
echo
echo "Quick summary hits:"
grep -nE 'Phase 28|Phase 29|Phase 30|phase_28|phase_29|phase_30' "$OUT" || true
