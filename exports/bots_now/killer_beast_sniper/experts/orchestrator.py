"""
Panda Master AI Orchestrator
"""

from .risk_governor import RiskGovernor
from .universe_expert import UniverseExpert
from .stage_analyzer import StageAnalyzer


def run():
    print("=" * 60)
    print("Panda Master AI")
    print("=" * 60)

    governor = RiskGovernor()
    universe = UniverseExpert()
    analyzer = StageAnalyzer()

    stocks = universe.build_universe()

    print(f"Universe Size: {len(stocks)}")
    print(f"Risk Governor: {governor.__class__.__name__}")

    if stocks:
        result = analyzer.analyze(stocks[0], {})
        print(result)

    print("System Ready")


if __name__ == "__main__":
    run()
