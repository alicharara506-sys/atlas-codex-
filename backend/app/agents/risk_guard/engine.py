"""Mandatory fail-closed Risk Guard for every non-HOLD trade intent."""

from __future__ import annotations

from app.agents.contracts import (
    ArbitrationResult,
    MarketObservation,
    PortfolioRiskContext,
    RiskAssessment,
    RiskCheck,
    RiskLimits,
    TradeIntent,
)


def _check(name: str, passed: bool, detail: str) -> RiskCheck:
    return RiskCheck(name=name, passed=passed, detail=detail)


def assess_risk(
    observation: MarketObservation,
    arbitration: ArbitrationResult,
    context: PortfolioRiskContext,
    limits: RiskLimits,
    *,
    manager_override: str | None = None,
) -> RiskAssessment:
    projected_leverage = context.gross_leverage - abs(observation.current_weight) + observation.requested_weight
    volatility_known = observation.volatility_percentile is not None
    correlation_known = observation.correlation_to_book is not None
    checks = [
        _check(
            "universe",
            observation.ticker in limits.allowed_universe,
            f"{observation.ticker} must be in the mandate universe.",
        ),
        _check(
            "asset_class",
            observation.asset_class in limits.allowed_asset_classes,
            f"{observation.asset_class} must be an allowed asset class.",
        ),
        _check(
            "risk_data_complete",
            volatility_known and correlation_known,
            "Volatility percentile and book correlation are required for a trade intent.",
        ),
        _check(
            "position_limit",
            observation.requested_weight <= limits.max_position_weight,
            f"Requested {observation.requested_weight:.3f}; limit {limits.max_position_weight:.3f}.",
        ),
        _check(
            "gross_leverage",
            projected_leverage <= limits.max_gross_leverage,
            f"Projected {projected_leverage:.3f}; limit {limits.max_gross_leverage:.3f}.",
        ),
        _check(
            "daily_drawdown",
            context.daily_drawdown < limits.max_daily_drawdown,
            f"Current {context.daily_drawdown:.3f}; breaker {limits.max_daily_drawdown:.3f}.",
        ),
        _check(
            "weekly_drawdown",
            context.weekly_drawdown < limits.max_weekly_drawdown,
            f"Current {context.weekly_drawdown:.3f}; breaker {limits.max_weekly_drawdown:.3f}.",
        ),
        _check(
            "correlation",
            correlation_known and abs(observation.correlation_to_book or 0.0) <= limits.max_correlation,
            f"Absolute book correlation must not exceed {limits.max_correlation:.3f}.",
        ),
        _check(
            "volatility_regime",
            volatility_known and (observation.volatility_percentile or 0.0) <= limits.max_volatility_percentile,
            f"Volatility percentile must not exceed {limits.max_volatility_percentile:.3f}.",
        ),
    ]
    if arbitration.intent is TradeIntent.HOLD:
        return RiskAssessment(
            ticker=observation.ticker,
            approved=False,
            vetoed=False,
            checks=checks,
            veto_reasons=["Arbitration produced HOLD; no position was proposed."],
            approved_weight=0.0,
            manager_override_recorded=bool(manager_override),
        )
    failed = [row for row in checks if not row.passed]
    vetoed = bool(failed)
    return RiskAssessment(
        ticker=observation.ticker,
        approved=not vetoed,
        vetoed=vetoed,
        checks=checks,
        veto_reasons=[f"{row.name}: {row.detail}" for row in failed],
        approved_weight=0.0 if vetoed else observation.requested_weight,
        manager_override_recorded=bool(manager_override),
    )


__all__ = ["assess_risk"]
