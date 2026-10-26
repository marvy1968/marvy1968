from __future__ import annotations

from dotenv import load_dotenv

from alerts.formatter import AlertData, format_alert
from alerts.telegram_bot import TelegramAlertBot
from risk_engine.bankroll_guard import GuardConfig, approve_bet, ProposedBet


class AlertDispatcher:
    def __init__(self):
        load_dotenv(".env")
        self.bot = TelegramAlertBot()
        self.config = GuardConfig()

    def dispatch(self, opportunity, bankroll, existing_bets, event_time_utc=None):
        proposed = ProposedBet(
            game_id=opportunity.get("game_id", 0),
            market_key=opportunity["market"],
            stake_fraction=opportunity["stake_fraction"],
            edge_pct=opportunity["edge"],
            confidence_score=opportunity["confidence"],
            event_time_utc=event_time_utc or opportunity.get("event_time_utc", ""),
            is_live=opportunity.get("is_live", False),
        )

        decision = approve_bet(
            proposed=proposed,
            existing_bets=existing_bets,
            config=self.config,
        )

        if not decision.approved:
            return {"sent": False, "reason": decision.reason}

        message = format_alert(
            AlertData(
                game=opportunity["game"],
                market=opportunity["market"],
                sportsbook=opportunity["sportsbook"],
                market_line=opportunity["market_line"],
                consensus_line=opportunity["consensus_line"],
                fair_line=opportunity["fair_line"],
                edge=opportunity["edge"],
                confidence=opportunity["confidence"],
                stake=opportunity["stake_fraction"],
                reason=opportunity["reason"],
                live=opportunity.get("is_live", False),
            )
        )

        self.bot.send(message)
        return {"sent": True, "reason": decision.reason}
