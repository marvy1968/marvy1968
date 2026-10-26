from __future__ import annotations

from expert_library.models import (
    DoctrineRule,
    ExpertProfile,
    SourceRecord,
    StrategySpecification,
)


def make_source(
    source_id: str,
    expert: str,
    title: str,
    source_type: str,
    publisher: str,
    year: int | None = None,
) -> SourceRecord:
    return SourceRecord(
        source_id=source_id,
        expert=expert,
        title=title,
        source_type=source_type,
        creator_or_publisher=publisher,
        reference=title,
        publication_year=year,
        evidence_level="primary",
        reviewed=False,
        notes=(
            "Registered primary source. "
            "Full research extraction remains pending."
        ),
    )


def make_rule(
    rule_id: str,
    expert: str,
    rule_type: str,
    statement: str,
    source_id: str,
    forex_adaptation: str,
    confidence: float = 0.80,
) -> DoctrineRule:
    return DoctrineRule(
        rule_id=rule_id,
        expert=expert,
        rule_type=rule_type,
        statement=statement,
        source_ids=(source_id,),
        confidence=confidence,
        forex_adaptation=forex_adaptation,
        mechanical=True,
        implementation_status="research",
        invalidation=(
            "Disable when the current market regime "
            "contradicts the strategy premise."
        ),
    )


def make_strategy(
    strategy_id: str,
    expert: str,
    name: str,
    description: str,
    regimes: tuple[str, ...],
    sessions: tuple[str, ...],
    timeframes: tuple[str, ...],
    features: tuple[str, ...],
    entry_rules: tuple[str, ...],
    exit_rules: tuple[str, ...],
    risk_rules: tuple[str, ...],
    disqualifiers: tuple[str, ...],
) -> StrategySpecification:
    return StrategySpecification(
        strategy_id=strategy_id,
        expert=expert,
        name=name,
        description=description,
        preferred_regimes=regimes,
        preferred_sessions=sessions,
        preferred_timeframes=timeframes,
        required_features=features,
        entry_rules=entry_rules,
        exit_rules=exit_rules,
        risk_rules=risk_rules,
        disqualifiers=disqualifiers,
        status="research_only",
    )


def velez_profile() -> ExpertProfile:
    expert = "velez"
    source_id = "velez_master_trader"

    return ExpertProfile(
        expert_id=expert,
        display_name="Oliver Velez / Master Trader",
        philosophy=(
            "Trade established directional structure, "
            "controlled pullbacks and decisive signal bars."
        ),
        edge_hypothesis=(
            "Trend-aligned pullbacks entered near defensible "
            "structure offer better risk-to-reward than chasing."
        ),
        strengths=[
            "Trend pullbacks",
            "Moving-average alignment",
            "Signal-bar quality",
            "Intraday management",
        ],
        weaknesses=[
            "Some concepts are discretionary",
            "Stock rules require forex validation",
        ],
        compatible_regimes=[
            "trending",
            "strong_trending",
        ],
        incompatible_regimes=[
            "ranging",
            "news_spike",
            "chaotic",
        ],
        sources=[
            make_source(
                source_id,
                expert,
                (
                    "Tools and Tactics for "
                    "the Master Day Trader"
                ),
                "book",
                "Oliver Velez and Greg Capra",
                2000,
            ),
        ],
        doctrine=[
            make_rule(
                "velez_mtf_alignment",
                expert,
                "confirmation",
                (
                    "Trade only when higher and lower "
                    "timeframes support the same direction."
                ),
                source_id,
                (
                    "Require agreement among 4H, 1H and "
                    "15M trend structure."
                ),
                0.88,
            ),
            make_rule(
                "velez_pullback_entry",
                expert,
                "entry",
                (
                    "Prefer a controlled pullback rather "
                    "than an extended breakout chase."
                ),
                source_id,
                (
                    "Use ATR-normalized distance from EMA20 "
                    "and prior swing structure."
                ),
                0.84,
            ),
            make_rule(
                "velez_signal_bar",
                expert,
                "confirmation",
                (
                    "Require a decisive directional signal bar "
                    "at the pullback location."
                ),
                source_id,
                (
                    "Measure candle body, close location and "
                    "rejection wick."
                ),
                0.79,
            ),
        ],
        strategies=[
            make_strategy(
                "velez_forex_pullback",
                expert,
                "Forex Trend Pullback",
                (
                    "Enter a multi-timeframe trend after a "
                    "controlled retracement."
                ),
                (
                    "trending",
                    "strong_trending",
                ),
                (
                    "london",
                    "new_york",
                    "overlap",
                ),
                (
                    "4H",
                    "1H",
                    "15M",
                    "5M",
                ),
                (
                    "ema20",
                    "ema50",
                    "ema200",
                    "ema_slope",
                    "atr",
                    "swing_structure",
                    "signal_bar",
                ),
                (
                    "4H and 1H direction agree.",
                    "15M pulls into support or resistance.",
                    "5M confirms trend resumption.",
                ),
                (
                    "Initial stop beyond invalidating swing.",
                    "Virtual stop ratchets toward profit.",
                    "Force exit when trend structure fails.",
                ),
                (
                    "Risk is sized from stop distance.",
                    "Never widen a stop.",
                    "Block excessive spread.",
                ),
                (
                    "Major news window",
                    "Mixed higher timeframes",
                    "Extended entry",
                ),
            ),
        ],
        implementation_notes=[
            "Research-only forex adaptation.",
            "Validate independently by currency pair.",
        ],
    )


def raschke_profile() -> ExpertProfile:
    expert = "raschke"
    source_id = "raschke_street_smarts"

    return ExpertProfile(
        expert_id=expert,
        display_name="Linda Bradford Raschke",
        philosophy=(
            "Use repeatable price behavior, momentum, "
            "volatility expansion and disciplined preparation."
        ),
        edge_hypothesis=(
            "Simple patterns can retain value when traded "
            "in the correct volatility and trend context."
        ),
        strengths=[
            "Momentum continuation",
            "Volatility expansion",
            "Pullback patterns",
            "Opening-range behavior",
        ],
        weaknesses=[
            "Context dependent",
            "Discretion must be translated carefully",
        ],
        compatible_regimes=[
            "trending",
            "strong_trending",
            "compression",
            "breakout_transition",
        ],
        incompatible_regimes=[
            "random_noise",
        ],
        sources=[
            make_source(
                source_id,
                expert,
                "Street Smarts",
                "book",
                "Linda Raschke and Larry Connors",
                1995,
            ),
        ],
        doctrine=[
            make_rule(
                "raschke_context",
                expert,
                "confirmation",
                (
                    "A setup must be evaluated in its "
                    "market and volatility context."
                ),
                source_id,
                (
                    "Segment results by pair, session "
                    "and regime."
                ),
                0.91,
            ),
            make_rule(
                "raschke_expansion",
                expert,
                "entry",
                (
                    "Volatility expansion can confirm "
                    "a directional opportunity."
                ),
                source_id,
                (
                    "Compare current ATR and bar range "
                    "with recent session norms."
                ),
                0.84,
            ),
            make_rule(
                "raschke_no_outlier_fade",
                expert,
                "avoidance",
                (
                    "Do not force mean reversion against "
                    "a developing directional outlier."
                ),
                source_id,
                (
                    "Disable range entries when ATR, ADX "
                    "and structure confirm expansion."
                ),
                0.90,
            ),
        ],
        strategies=[
            make_strategy(
                "raschke_momentum_pullback",
                expert,
                "Momentum Pullback",
                (
                    "Enter a retracement after verified "
                    "directional expansion."
                ),
                (
                    "trending",
                    "strong_trending",
                    "breakout_transition",
                ),
                (
                    "london",
                    "new_york",
                    "overlap",
                ),
                (
                    "4H",
                    "1H",
                    "15M",
                    "5M",
                ),
                (
                    "adx",
                    "atr_expansion",
                    "ema_slope",
                    "opening_range",
                    "swing_structure",
                ),
                (
                    "Expansion establishes direction.",
                    "Pullback holds above or below invalidation.",
                    "Lower timeframe resumes expansion.",
                ),
                (
                    "Stop beyond pullback failure.",
                    "Trail while momentum persists.",
                ),
                (
                    "Reduce risk during abnormal volatility.",
                    "Block poor spread-to-stop ratio.",
                ),
                (
                    "Opposing 4H structure",
                    "Confirmed range",
                ),
            ),
        ],
    )


def brooks_profile() -> ExpertProfile:
    expert = "brooks"
    source_id = "brooks_price_action"

    return ExpertProfile(
        expert_id=expert,
        display_name="Al Brooks",
        philosophy=(
            "Read price action in context, distinguishing "
            "trends, ranges, breakouts and failed breakouts."
        ),
        edge_hypothesis=(
            "Context, signal-bar quality and follow-through "
            "separate useful price action from noise."
        ),
        strengths=[
            "Signal bars",
            "Second entries",
            "Failed breakouts",
            "Trend-range classification",
        ],
        weaknesses=[
            "Highly discretionary",
            "Requires precise mechanical definitions",
        ],
        compatible_regimes=[
            "trending",
            "ranging",
            "breakout_transition",
        ],
        incompatible_regimes=[
            "unclassified",
        ],
        sources=[
            make_source(
                source_id,
                expert,
                "Trading Price Action Series",
                "book_series",
                "Al Brooks",
                2012,
            ),
        ],
        doctrine=[
            make_rule(
                "brooks_context_first",
                expert,
                "confirmation",
                (
                    "Interpret each signal bar within the "
                    "current trend or range context."
                ),
                source_id,
                (
                    "Use 1H and 15M context before a 5M trigger."
                ),
                0.92,
            ),
            make_rule(
                "brooks_follow_through",
                expert,
                "confirmation",
                (
                    "Require follow-through after a signal "
                    "instead of relying on one candle."
                ),
                source_id,
                (
                    "Require continuation close or successful "
                    "retest after the trigger."
                ),
                0.87,
            ),
            make_rule(
                "brooks_failed_breakout",
                expert,
                "entry",
                (
                    "A failed breakout can reverse back "
                    "toward the center of a range."
                ),
                source_id,
                (
                    "Require close back inside the range "
                    "plus confirmation."
                ),
                0.82,
            ),
        ],
        strategies=[
            make_strategy(
                "brooks_second_entry",
                expert,
                "Second-Entry Continuation",
                (
                    "Trade trend continuation after the first "
                    "pullback attempt fails and a second entry forms."
                ),
                (
                    "trending",
                    "strong_trending",
                ),
                (
                    "london",
                    "new_york",
                ),
                (
                    "1H",
                    "15M",
                    "5M",
                ),
                (
                    "swing_structure",
                    "signal_bar",
                    "follow_through",
                    "ema20",
                ),
                (
                    "Trend context is established.",
                    "First countertrend attempt fails.",
                    "Second continuation signal triggers.",
                ),
                (
                    "Stop beyond pullback structure.",
                    "Exit on strong opposing signal.",
                ),
                (
                    "Avoid oversized signal bars.",
                    "Require acceptable spread-to-risk.",
                ),
                (
                    "No clear context",
                    "Major scheduled news",
                ),
            ),
        ],
    )


def connors_profile() -> ExpertProfile:
    expert = "connors"
    source_id = "connors_short_term"

    return ExpertProfile(
        expert_id=expert,
        display_name="Larry Connors",
        philosophy=(
            "Exploit short-term mean reversion while "
            "respecting broader directional context."
        ),
        edge_hypothesis=(
            "Short-term extremes can revert when the "
            "higher-timeframe environment is not hostile."
        ),
        strengths=[
            "RSI(2)",
            "Systematic rules",
            "Mean reversion",
            "Trend filters",
        ],
        weaknesses=[
            "Strong trends can overwhelm reversion",
            "Equity findings need forex validation",
        ],
        compatible_regimes=[
            "ranging",
            "weak_trend_pullback",
        ],
        incompatible_regimes=[
            "strong_trending",
            "news_spike",
        ],
        sources=[
            make_source(
                source_id,
                expert,
                (
                    "Short Term Trading Strategies "
                    "That Work"
                ),
                "book",
                "Larry Connors and Cesar Alvarez",
                2008,
            ),
        ],
        doctrine=[
            make_rule(
                "connors_rsi2",
                expert,
                "entry",
                (
                    "Use a very short RSI to identify "
                    "temporary price extremes."
                ),
                source_id,
                (
                    "Calibrate RSI(2) thresholds by pair "
                    "and session."
                ),
                0.91,
            ),
            make_rule(
                "connors_regime_filter",
                expert,
                "confirmation",
                (
                    "Short-term mean reversion requires "
                    "a non-hostile higher-timeframe regime."
                ),
                source_id,
                (
                    "Block entries against confirmed MTF trends."
                ),
                0.89,
            ),
        ],
        strategies=[
            make_strategy(
                "connors_forex_rsi2",
                expert,
                "Forex RSI(2) Reversion",
                (
                    "Trade short-term extremes only in "
                    "confirmed ranges or weak trends."
                ),
                (
                    "ranging",
                    "weak_trend_pullback",
                ),
                (
                    "asian",
                    "late_new_york",
                ),
                (
                    "4H",
                    "1H",
                    "15M",
                ),
                (
                    "rsi2",
                    "ema200",
                    "adx",
                    "atr",
                    "range_position",
                ),
                (
                    "Higher timeframe is non-hostile.",
                    "RSI(2) reaches calibrated extreme.",
                    "Spread remains normal.",
                ),
                (
                    "Exit at mean or opposite RSI threshold.",
                    "Apply a maximum holding-time exit.",
                ),
                (
                    "Use smaller risk than trend strategies.",
                    "Never average down.",
                ),
                (
                    "Strong trend",
                    "News expansion",
                    "Abnormal spread",
                ),
            ),
        ],
    )


def bollinger_profile() -> ExpertProfile:
    expert = "bollinger"
    source_id = "bollinger_bands_book"

    return ExpertProfile(
        expert_id=expert,
        display_name="John Bollinger",
        philosophy=(
            "Use volatility-relative bands as a framework; "
            "a band touch alone is not a signal."
        ),
        edge_hypothesis=(
            "Volatility envelopes identify relative extremes, "
            "contractions and expansion when independently confirmed."
        ),
        strengths=[
            "Volatility bands",
            "Squeezes",
            "Range extremes",
            "Expansion detection",
        ],
        weaknesses=[
            "Band touches are frequently misused",
            "Parameters require calibration",
        ],
        compatible_regimes=[
            "ranging",
            "compression",
            "breakout_transition",
        ],
        incompatible_regimes=[
            "unfiltered_strong_trend",
        ],
        sources=[
            make_source(
                source_id,
                expert,
                "Bollinger on Bollinger Bands",
                "book",
                "John Bollinger",
                2001,
            ),
        ],
        doctrine=[
            make_rule(
                "bollinger_touch_not_signal",
                expert,
                "confirmation",
                (
                    "A band touch alone is not sufficient "
                    "for an entry."
                ),
                source_id,
                (
                    "Require regime, range location and "
                    "price rejection confirmation."
                ),
                0.95,
            ),
            make_rule(
                "bollinger_band_walk",
                expert,
                "avoidance",
                (
                    "Repeated movement along an outer band "
                    "can represent trend continuation."
                ),
                source_id,
                (
                    "Block range fades during a confirmed band walk."
                ),
                0.88,
            ),
        ],
        strategies=[
            make_strategy(
                "bollinger_range_rejection",
                expert,
                "Confirmed Range Band Rejection",
                (
                    "Fade a volatility-relative extreme only "
                    "inside a confirmed range."
                ),
                (
                    "ranging",
                ),
                (
                    "asian",
                    "late_new_york",
                ),
                (
                    "1H",
                    "15M",
                    "5M",
                ),
                (
                    "bollinger_bands",
                    "bandwidth",
                    "adx",
                    "efficiency_ratio",
                    "range_boundaries",
                ),
                (
                    "MTF range classification is confirmed.",
                    "Price rejects an outer band near range edge.",
                    "Price closes back inside the band.",
                ),
                (
                    "First target is the middle band.",
                    "Stop beyond structural range failure.",
                ),
                (
                    "Reduce risk near breakout transition.",
                    "Require acceptable spread.",
                ),
                (
                    "Strong trend",
                    "Band walk",
                    "High-impact news",
                ),
            ),
        ],
    )


def crabel_profile() -> ExpertProfile:
    expert = "crabel"
    source_id = "crabel_opening_range"

    return ExpertProfile(
        expert_id=expert,
        display_name="Toby Crabel",
        philosophy=(
            "Trade volatility expansion after measurable "
            "contraction and opening-range structure."
        ),
        edge_hypothesis=(
            "Narrow-range compression can precede directional "
            "expansion that is testable with breakout rules."
        ),
        strengths=[
            "NR4",
            "NR7",
            "Inside bars",
            "Opening-range breakouts",
        ],
        weaknesses=[
            "False breakouts",
            "Session definitions materially affect results",
        ],
        compatible_regimes=[
            "compression",
            "breakout_transition",
        ],
        incompatible_regimes=[
            "already_extended",
        ],
        sources=[
            make_source(
                source_id,
                expert,
                (
                    "Day Trading with Short Term Price "
                    "Patterns and Opening Range Breakout"
                ),
                "book",
                "Toby Crabel",
                1990,
            ),
        ],
        doctrine=[
            make_rule(
                "crabel_contraction",
                expert,
                "regime",
                (
                    "Measure contraction before considering "
                    "a volatility expansion trade."
                ),
                source_id,
                (
                    "Use NR4, NR7, inside bars and "
                    "session-range percentiles."
                ),
                0.93,
            ),
            make_rule(
                "crabel_opening_range",
                expert,
                "entry",
                (
                    "Opening-range behavior defines breakout "
                    "and failed-breakout opportunity."
                ),
                source_id,
                (
                    "Define Asian, London and New York "
                    "opening ranges separately."
                ),
                0.89,
            ),
        ],
        strategies=[
            make_strategy(
                "crabel_london_orb",
                expert,
                "London Opening-Range Breakout",
                (
                    "Trade London expansion after Asian-session "
                    "contraction."
                ),
                (
                    "compression",
                    "breakout_transition",
                ),
                (
                    "london",
                ),
                (
                    "1H",
                    "15M",
                    "5M",
                ),
                (
                    "nr4",
                    "nr7",
                    "inside_bar",
                    "opening_range",
                    "atr_expansion",
                    "spread",
                ),
                (
                    "Asian range is compressed.",
                    "London breaks with expansion.",
                    "Breakout candle closes outside the range.",
                ),
                (
                    "Stop inside failed-breakout structure.",
                    "Trail while expansion continues.",
                ),
                (
                    "Reject poor spread-to-range conditions.",
                    "Cap correlated currency exposure.",
                ),
                (
                    "Major news imminent",
                    "Range already expanded",
                ),
            ),
        ],
    )


def williams_profile() -> ExpertProfile:
    expert = "williams"
    source_id = "williams_short_term"

    return ExpertProfile(
        expert_id=expert,
        display_name="Larry Williams",
        philosophy=(
            "Use volatility, momentum, market tendencies "
            "and disciplined risk for short-term moves."
        ),
        edge_hypothesis=(
            "Volatility-relative triggers can capture "
            "short-term directional expansion."
        ),
        strengths=[
            "Volatility breakouts",
            "Short-term tendencies",
            "Risk management",
        ],
        weaknesses=[
            "Market-specific rules require validation",
            "Community versions may differ from originals",
        ],
        compatible_regimes=[
            "breakout_transition",
            "trending",
        ],
        incompatible_regimes=[
            "quiet_range_without_trigger",
        ],
        sources=[
            make_source(
                source_id,
                expert,
                (
                    "Long-Term Secrets to "
                    "Short-Term Trading"
                ),
                "book",
                "Larry Williams",
                1999,
            ),
        ],
        doctrine=[
            make_rule(
                "williams_validate",
                expert,
                "confirmation",
                (
                    "Short-term tendencies must be validated "
                    "for the instrument and period traded."
                ),
                source_id,
                (
                    "Use pair, session and regime "
                    "walk-forward validation."
                ),
                0.89,
            ),
            make_rule(
                "williams_volatility_trigger",
                expert,
                "entry",
                (
                    "Use a volatility-relative trigger instead "
                    "of a fixed price distance."
                ),
                source_id,
                (
                    "Scale entries to ATR and prior session range."
                ),
                0.83,
            ),
        ],
        strategies=[
            make_strategy(
                "williams_volatility_breakout",
                expert,
                "Forex Volatility Breakout",
                (
                    "Enter directional expansion relative "
                    "to a calibrated prior range."
                ),
                (
                    "breakout_transition",
                    "trending",
                ),
                (
                    "london",
                    "new_york",
                ),
                (
                    "1H",
                    "15M",
                    "5M",
                ),
                (
                    "prior_range",
                    "atr",
                    "momentum",
                    "session",
                    "spread",
                ),
                (
                    "Prior range defines trigger distance.",
                    "Momentum confirms the breakout.",
                    "MTF direction agrees.",
                ),
                (
                    "Broker emergency structural stop.",
                    "Virtual trail after favorable expansion.",
                ),
                (
                    "Cap correlated currency risk.",
                    "Block abnormal spreads.",
                ),
                (
                    "No MTF confirmation",
                    "Late-session illiquidity",
                    "Major scheduled news",
                ),
            ),
        ],
    )


def build_profiles() -> list[ExpertProfile]:
    return [
        velez_profile(),
        raschke_profile(),
        brooks_profile(),
        connors_profile(),
        bollinger_profile(),
        crabel_profile(),
        williams_profile(),
    ]
