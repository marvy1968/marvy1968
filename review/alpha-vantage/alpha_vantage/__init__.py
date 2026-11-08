"""Optional Alpha Vantage review adapter.

Importing this package does not read ``ALPHA_VANTAGE_API_KEY`` and does not open
a network connection. Nothing here is imported by alpacabot. Live calls stay off
unless ``research.load_series`` is called with ``opt_in=True`` and the environment
variable ``ALPHA_VANTAGE_RESEARCH_OPT_IN`` is exactly ``1``.
"""

LIVE_CALLS_ENABLED_BY_DEFAULT = False

__all__ = ["LIVE_CALLS_ENABLED_BY_DEFAULT"]
