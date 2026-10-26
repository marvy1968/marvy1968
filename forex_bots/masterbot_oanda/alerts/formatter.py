from dataclasses import dataclass


@dataclass
class AlertData:
    game: str
    market: str
    sportsbook: str
    market_line: float
    consensus_line: float
    fair_line: float
    edge: float
    confidence: float
    stake: float
    reason: str
    live: bool = False


def format_alert(alert: AlertData) -> str:
    header = "🚨 LIVE ALERT" if alert.live else "📊 PREGAME ALERT"

    message = f"""
{header}

Game: {alert.game}

Market: {alert.market}

Book: {alert.sportsbook}

Market Line: {alert.market_line}
Consensus Line: {alert.consensus_line}
Model Fair Line: {alert.fair_line}

Edge: {alert.edge:.2%}
Confidence: {alert.confidence:.0f}/100

Stake: {alert.stake:.2%} bankroll

Reason:
{alert.reason}
"""

    return message.strip()

