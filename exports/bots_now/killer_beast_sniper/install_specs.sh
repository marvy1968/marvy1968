#!/bin/bash

set -e

echo "Creating Panda Master AI specification files..."

mkdir -p specs

###############################################################################
# Risk Governor
###############################################################################

cat > specs/risk_governor.yaml <<'EOF'
name: Risk Governor
version: 1.0
priority: Highest

mission: Protect portfolio capital.

authority: Absolute

responsibilities:
  - Position sizing
  - Portfolio exposure
  - Sector exposure
  - Maximum drawdown
  - Daily loss limits
  - Trade approval
  - Emergency exits

inputs:
  - Account equity
  - Open positions
  - Cash
  - Market regime
  - Sector exposure
  - Candidate trade
  - Portfolio heat

outputs:
  - APPROVE
  - REJECT
  - REDUCE_SIZE
  - EXIT_POSITION

rules:
  - Risk always overrides opportunity.

override:
  - No module may bypass this expert.
EOF

###############################################################################
# Universe Expert
###############################################################################

cat > specs/universe_expert.yaml <<'EOF'
name: Universe Expert
version: 1.0

mission: Build today's investment universe.

tasks:
  - Download S&P 500
  - Clean symbols
  - Validate data
  - Update database
  - Remove delisted stocks

output:
  - Daily Universe
EOF

###############################################################################
# Stage Analyzer
###############################################################################

cat > specs/stage_analyzer.yaml <<'EOF'
name: Stage Analyzer

mission: Determine Weinstein Stage.

inputs:
  - Price
  - 150 SMA
  - 20 EMA
  - Volume
  - Mansfield Relative Strength

outputs:
  - Stage 1
  - Stage 2
  - Stage 3
  - Stage 4

confidence: 0-100
EOF

###############################################################################
# Trend Confidence
###############################################################################

cat > specs/trend_confidence.yaml <<'EOF'
name: Trend Confidence

weights:
  Stage: 30
  Mansfield_RS: 20
  SMA150: 15
  EMA20: 10
  Volume: 10
  Sector: 5
  SPY: 5
  Persistence: 5

output:
  score: 0-100
  grades:
    - A+
    - A
    - A-
    - B+
    - B
    - B-
    - Reject
EOF

###############################################################################
# Screener
###############################################################################

cat > specs/screener.yaml <<'EOF'
name: Daily Screener

workflow:
  - Download Universe
  - Stage Analysis
  - Trend Score
  - Grade
  - Rank
  - Hotlist
  - Capital Queue

outputs:
  - stage2_hotlist.json
  - daily_rankings.json
  - capital_queue.json
EOF

###############################################################################
# Entry Expert
###############################################################################

cat > specs/entry_expert.yaml <<'EOF'
name: Entry Expert

requirements:
  - Stage 2
  - Grade A-
  - Rising 20 EMA
  - Rising 150 SMA
  - Positive Mansfield RS
  - Strong Sector
  - Bullish SPY
  - 15 Minute Trigger
  - Risk Governor Approval

decision:
  - ENTER
  - WAIT
  - REJECT
EOF

###############################################################################
# Position Manager
###############################################################################

cat > specs/position_manager.yaml <<'EOF'
name: Position Manager

states:
  - Watching
  - Qualified
  - Entered
  - Scaling Out
  - Reload Eligible
  - Reloaded
  - Trend Broken
  - Closed

responsibilities:
  - Partial profits
  - Reloads
  - Trailing stops
  - Portfolio updates
EOF

###############################################################################
# Reload Expert
###############################################################################

cat > specs/reload_expert.yaml <<'EOF'
name: Reload Expert

requirements:
  - Stage Valid
  - High Trend Confidence
  - Rising 20 EMA
  - Rising 150 SMA
  - Positive Mansfield RS
  - Bullish SPY
  - Light Volume Pullback
  - Bullish Confirmation Candle
  - Risk Governor Approval

decision:
  - ADD
  - WAIT
EOF

###############################################################################
# Stage 4 Expert
###############################################################################

cat > specs/stage4_expert.yaml <<'EOF'
name: Stage 4 Expert

mission: Trade institutional downtrends.

requirements:
  - Stage 4
  - Falling 150 SMA
  - Falling 20 EMA
  - Negative Mansfield RS
  - Weak Sector
  - Weak SPY
  - 15 Minute Short Trigger
  - Risk Governor Approval

management:
  - Cover 1/3
  - Cover 1/3
  - Cover remainder
  - Reload after bearish rejection
EOF

###############################################################################
# Learning Engine
###############################################################################

cat > specs/learning_engine.yaml <<'EOF'
name: Learning Engine

record:
  - Entry
  - Exit
  - Grade
  - Trend Score
  - MFE
  - MAE
  - R Multiple
  - Holding Time
  - Sector
  - Market Regime
  - Reload Count
  - Win Rate
  - Profit Factor
  - Expectancy

objective:
  Recommend improvements.

restriction:
  Never change production rules automatically.
EOF

echo
echo "======================================"
echo "Panda Master AI specs installed."
echo "Directory: ./specs"
echo "======================================"
