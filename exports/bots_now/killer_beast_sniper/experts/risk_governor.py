"""
Panda Master AI
Risk Governor Expert v1.0
"""

from dataclasses import dataclass
from typing import Tuple


@dataclass
class PortfolioState:
    equity: float
    cash: float
    open_positions: int
    sector_positions: int
    portfolio_heat: float
    drawdown_pct: float


@dataclass
class TradeCandidate:
    symbol: str
    grade: str
    risk_percent: float
    sector: str


class RiskGovernor:

    MAX_POSITIONS = 20
    MAX_SECTOR_POSITIONS = 4
    MAX_PORTFOLIO_HEAT = 0.06
    MAX_DRAWDOWN = 0.10

    ALLOWED_GRADES = {
        "A+",
        "A",
        "A-",
    }

    def approve(
        self,
        portfolio: PortfolioState,
        trade: TradeCandidate,
    ) -> Tuple[bool, str]:

        if trade.grade not in self.ALLOWED_GRADES:
            return False, "Grade below A-."

        if portfolio.open_positions >= self.MAX_POSITIONS:
            return False, "Maximum positions reached."

        if portfolio.sector_positions >= self.MAX_SECTOR_POSITIONS:
            return False, "Too much sector exposure."

        if portfolio.portfolio_heat >= self.MAX_PORTFOLIO_HEAT:
            return False, "Portfolio heat exceeded."

        if portfolio.drawdown_pct >= self.MAX_DRAWDOWN:
            return False, "Maximum drawdown exceeded."

        if trade.risk_percent <= 0:
            return False, "Invalid risk."

        return True, "APPROVED"


def demo():

    governor = RiskGovernor()

    portfolio = PortfolioState(
        equity=100000,
        cash=35000,
        open_positions=12,
        sector_positions=2,
        portfolio_heat=0.03,
        drawdown_pct=0.02,
    )

    trade = TradeCandidate(
        symbol="NVDA",
        grade="A",
        risk_percent=0.005,
        sector="Technology",
    )

    approved, reason = governor.approve(
        portfolio,
        trade,
    )

    print("Approved:", approved)
    print("Reason:", reason)


if __name__ == "__main__":
    demo()
