"""Pacer v2: log-only decisions for live player props.

Reads candidate JSONL, writes decision JSONL and reports. It never places orders,
never talks to a book, and never sends alerts. Turn it off with a ``pacer_v2/OFF``
file or the ``PACER_V2_OFF`` environment variable (the process then exits 0).
"""

__version__ = "2.0.0"

EDGE_MIN = 0.15
EARLY_SKIP_MIN = 6.0
LATE_SKIP_MIN = 3.0
SLIPPAGE_CENTS = 0.02
MIN_FIT_N = 30
DEFAULT_W = 0.5
# Unfitted overs rule. Replaced when ``fit_overs`` sees at least MIN_FIT_N graded overs
# inside some price band. These numbers are a conservative prior, not a result.
DEFAULT_OVERS = {
    "price_lo": 0.40,
    "price_hi": 0.62,
    "min_hit_p": 0.58,
    "fitted": False,
    "n": 0,
}

# Documented gaps in the minutes model. The README repeats these in prose.
GAPS = (
    "Remaining minutes are regulation clock scaled by coarse blowout, foul and back-to-back factors. "
    "There is no substitution log, stagger chart or coach tendency.",
    "Foul trouble uses one limit (6, or 5 when league is ncaab/ncaaw or regulation is 40). "
    "It does not model bonus free throws or lineup-specific foul rates.",
    "A back-to-back haircut (remaining minutes x 0.92) applies only when the row sets b2b / back_to_back "
    "or the team-stats file lists the team or player. No schedule is fetched.",
    "margin is treated as the player's own team margin. A home-perspective margin with no sign flip is a feed bug this tool cannot see.",
    "Overtime uses a flat 5-minute (basketball) or 10-minute (football) period, not a possession model. "
    "Past regulation with no OT flag, remaining minutes are zero.",
    "Football 'minutes' are game-clock minutes. There is no snap-count model. "
    "role=backup disables the blowout minute cut; garbage-time snap increases for backups are not modeled.",
    "Basketball defaults to 48 minutes and 12-minute periods (NBA/WNBA). College is 40 / 20 only when "
    "league is ncaab/ncaaw/cbb/wbb or regulation_minutes is set. A missing league on a college row is scored as NBA.",
    "Opponent defense and pace default to 1.0 when the team-stats JSON, the opponent id, or the stat key is missing.",
    "season_rate above a per-stat cap is treated as a per-game rate and divided by 32 (basketball) or 60 (football). "
    "There is no per-player minute baseline.",
    "The timing filter uses the game clock. If period and clock are missing it does not guess from a basketball "
    "player's minutes_played, and it does not skip the row (gap game_clock_unknown).",
    "No book, exchange or wallet is contacted. Decisions are a log.",
)
