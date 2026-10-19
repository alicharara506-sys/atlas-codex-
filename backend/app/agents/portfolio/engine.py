"""Research-only, bounded risk-parity portfolio proposals."""

from __future__ import annotations

from app.agents.contracts import (
    ArbitrationResult,
    MarketObservation,
    PortfolioProposal,
    PortfolioRiskContext,
    PositionProposal,
    RiskAssessment,
    RiskLimits,
    TradeIntent,
)


def build_portfolio(
    observations: list[MarketObservation],
    arbitrations: dict[str, ArbitrationResult],
    assessments: dict[str, RiskAssessment],
    context: PortfolioRiskContext,
    limits: RiskLimits,
) -> PortfolioProposal:
    by_ticker = {row.ticker: row for row in observations}
    approved = [row for row in assessments.values() if row.approved]
    risk_budgets: dict[str, float] = {}
    for assessment in approved:
        observation = by_ticker[assessment.ticker]
        volatility = max(observation.volatility_percentile or 0.01, 0.01)
        risk_budgets[assessment.ticker] = abs(arbitrations[assessment.ticker].final_score) / volatility
    total_budget = sum(risk_budgets.values())
    replaced_weight = sum(abs(by_ticker[row.ticker].current_weight) for row in approved)
    available_leverage = max(0.0, limits.max_gross_leverage - context.gross_leverage + replaced_weight)
    capacity = min(available_leverage, sum(row.approved_weight for row in approved))
    positions: list[PositionProposal] = []
    for ticker in sorted(risk_budgets):
        observation = by_ticker[ticker]
        arbitration = arbitrations[ticker]
        assessment = assessments[ticker]
        allocation = capacity * risk_budgets[ticker] / total_budget if total_budget > 0.0 else 0.0
        magnitude = min(assessment.approved_weight, limits.max_position_weight, allocation)
        target = -magnitude if arbitration.intent is TradeIntent.SHORT else magnitude
        positions.append(
            PositionProposal(
                ticker=ticker,
                intent=arbitration.intent,
                target_weight=round(target, 6),
                current_weight=observation.current_weight,
                rebalance_delta=round(target - observation.current_weight, 6),
                reason="Approved by Risk Guard and sized by bounded inverse-volatility conviction.",
            )
        )
    gross = round(sum(abs(row.target_weight) for row in positions), 6)
    return PortfolioProposal(
        positions=positions,
        gross_target_weight=gross,
        cash_weight=round(max(0.0, 1.0 - min(1.0, gross)), 6),
    )


__all__ = ["build_portfolio"]
