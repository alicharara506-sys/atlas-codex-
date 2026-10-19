"""Net expectancy, structural targets, portfolio exposure, and hard risk for Phase 10."""

from __future__ import annotations

from collections import defaultdict
from statistics import fmean

from app.core.store import RecordStore, stable_key
from app.v4.context_contracts import ContextSet
from app.v4.context_service import CONTEXT_COLLECTION
from app.v4.contracts import (
    DataQualityStatus,
    FeatureSet,
    LiquidityState,
    MarketSnapshot,
)
from app.v4.feature_service import FEATURE_COLLECTION
from app.v4.ml_contracts import MLTarget, PrimaryCandidate
from app.v4.primary_ml import PRIMARY_CANDIDATE_COLLECTION
from app.v4.quant_backtest import BACKTEST_COLLECTION
from app.v4.quant_contracts import BacktestRun
from app.v4.quant_validation import QUANT_VALIDATION_COLLECTION
from app.v4.quant_validation_contracts import (
    QuantCheckStatus,
    QuantDecision,
    QuantValidationResult,
)
from app.v4.risk_contracts import (
    EntryZone,
    ExpectancyReport,
    ExpectancyRequest,
    ExpectancyStatus,
    ExposureStatus,
    PortfolioExposureReport,
    PortfolioExposureRequest,
    PortfolioPosition,
    RiskCheck,
    RiskCheckName,
    RiskCheckStatus,
    RiskDecision,
    RiskOverlayRequest,
    RiskOverlayResult,
    StructuralTarget,
    StructuralTargetPlan,
    StructuralTargetRequest,
    TargetStatus,
)
from app.v4.snapshots import SNAPSHOT_COLLECTION
from app.v4.symbols import normalize_instrument

EXPECTANCY_COLLECTION = "v4_expectancy_reports"
STRUCTURAL_TARGET_COLLECTION = "v4_structural_target_plans"
PORTFOLIO_EXPOSURE_COLLECTION = "v4_portfolio_exposure_reports"
RISK_OVERLAY_COLLECTION = "v4_risk_overlay_results"


class ExpectancyEngine:
    """Calculate net EV from calibrated probability and cost-aware outcomes."""

    version = "net-expectancy-v1"
    minimum_samples = 30
    minimum_class_samples = 10

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def evaluate(self, request: ExpectancyRequest) -> ExpectancyReport:
        quant = _load_quant(self.store, request.quant_validation_id)
        backtest = _load_backtest(self.store, request.backtest_run_id)
        if backtest.instrument != quant.instrument:
            raise ValueError("expectancy backtest and Quant result instruments must match")
        executed = tuple(trade for trade in backtest.trades if trade.entry_at is not None)
        wins = tuple(trade for trade in executed if trade.net_return > 0)
        losses = tuple(trade for trade in executed if trade.net_return < 0)
        probability = quant.atlas_probability
        average_win = fmean(trade.gross_return for trade in wins) if wins else None
        average_loss = fmean(abs(trade.gross_return) for trade in losses) if losses else None
        average_cost = (
            fmean(max(0.0, trade.gross_return - trade.net_return) for trade in executed)
            if executed
            else None
        )
        expected_value: float | None = None
        if quant.decision not in {QuantDecision.PASS, QuantDecision.PASS_WITH_CAUTION}:
            status = ExpectancyStatus.BLOCKED
            reason = "Quant validation did not pass, so expectancy cannot authorize risk"
        elif probability is None:
            status = ExpectancyStatus.INSUFFICIENT_EVIDENCE
            reason = "Calibrated Atlas probability is unavailable"
        elif (
            len(executed) < self.minimum_samples
            or len(wins) < self.minimum_class_samples
            or len(losses) < self.minimum_class_samples
            or average_win is None
            or average_loss is None
            or average_cost is None
        ):
            status = ExpectancyStatus.INSUFFICIENT_EVIDENCE
            reason = "Expectancy requires 30 cost-aware trades with 10 wins and 10 losses"
        else:
            expected_value = (
                probability * average_win
                - (1 - probability) * average_loss
                - average_cost
            )
            if expected_value > 0:
                status = ExpectancyStatus.POSITIVE
                reason = "Validated net expectancy is positive after observed trading friction"
            else:
                status = ExpectancyStatus.NON_POSITIVE
                reason = "Net expectancy is non-positive after spread/slippage/commission/swap"
        report_id = stable_key(
            self.version,
            request.model_dump(mode="json"),
            probability,
            len(executed),
            average_win,
            average_loss,
            average_cost,
        )
        report = ExpectancyReport(
            expectancy_id=report_id,
            version=self.version,
            created_at=max(quant.created_at, backtest.created_at),
            instrument=quant.instrument,
            quant_validation_id=quant.quant_validation_id,
            backtest_run_id=backtest.backtest_run_id,
            status=status,
            probability_win=probability,
            sample_count=len(executed),
            win_count=len(wins),
            loss_count=len(losses),
            average_gross_win=average_win,
            average_gross_loss=average_loss,
            average_cost_return=average_cost,
            expected_value_net=expected_value,
            positive_validated_expectancy=status is ExpectancyStatus.POSITIVE,
            reason=reason,
        )
        self.store.upsert(
            EXPECTANCY_COLLECTION,
            report.expectancy_id,
            report.model_dump(mode="json"),
        )
        return report


class StructuralTargetEngine:
    """Derive non-fixed entry, stop, and targets from structure and volatility."""

    version = "structural-targets-v1"
    minimum_reward_risk = 1.5
    maximum_stop_atr = 2.5

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def build(self, request: StructuralTargetRequest) -> StructuralTargetPlan:
        quant = _load_quant(self.store, request.quant_validation_id)
        candidate = _load_candidate(self.store, quant.candidate_id)
        features = _load_features(self.store, request.feature_set_id)
        snapshot = _load_snapshot(self.store, request.snapshot_id)
        if features.instrument != candidate.instrument or snapshot.instrument != candidate.instrument:
            raise ValueError("target inputs must share the candidate instrument")
        if features.source_snapshot_id != snapshot.snapshot_id:
            raise ValueError("feature set must originate from the requested snapshot")
        summary = features.timeframes[candidate.timeframe]
        entry_mid = (snapshot.bid + snapshot.ask) / 2
        atr = summary.volatility.atr14
        plan_id = stable_key(
            self.version,
            request.model_dump(mode="json"),
            candidate.direction.value,
            entry_mid,
        )
        if quant.decision is not QuantDecision.PASS:
            return self._blocked(
                plan_id,
                quant,
                candidate,
                features,
                snapshot,
                TargetStatus.BLOCKED,
                "Structural targets require an exact Quant PASS",
            )
        if atr <= 0:
            return self._blocked(
                plan_id,
                quant,
                candidate,
                features,
                snapshot,
                TargetStatus.INSUFFICIENT_EVIDENCE,
                "ATR is unavailable for structural invalidation",
            )
        structure = summary.structure
        auction = summary.auction
        if candidate.direction is MLTarget.LONG:
            invalidations = [point.price for point in structure.swing_lows if point.price < entry_mid]
            invalidations.extend(
                value
                for value in (structure.support, auction.value_area_low)
                if value is not None and value < entry_mid
            )
            anchors = [
                value
                for value in (structure.resistance, auction.value_area_high)
                if value is not None and value > entry_mid
            ]
            if not invalidations:
                return self._blocked(
                    plan_id,
                    quant,
                    candidate,
                    features,
                    snapshot,
                    TargetStatus.INSUFFICIENT_EVIDENCE,
                    "No confirmed structural invalidation exists below the long entry",
                )
            stop = max(invalidations) - 0.3 * atr
            risk = entry_mid - stop
            target_prices = _long_targets(entry_mid, risk, anchors)
        else:
            invalidations = [point.price for point in structure.swing_highs if point.price > entry_mid]
            invalidations.extend(
                value
                for value in (structure.resistance, auction.value_area_high)
                if value is not None and value > entry_mid
            )
            anchors = [
                value
                for value in (structure.support, auction.value_area_low)
                if value is not None and value < entry_mid
            ]
            if not invalidations:
                return self._blocked(
                    plan_id,
                    quant,
                    candidate,
                    features,
                    snapshot,
                    TargetStatus.INSUFFICIENT_EVIDENCE,
                    "No confirmed structural invalidation exists above the short entry",
                )
            stop = min(invalidations) + 0.3 * atr
            risk = stop - entry_mid
            target_prices = _short_targets(entry_mid, risk, anchors)
        stop_atr = risk / atr
        if risk <= 0 or stop_atr > self.maximum_stop_atr:
            return self._blocked(
                plan_id,
                quant,
                candidate,
                features,
                snapshot,
                TargetStatus.INVALID,
                "Structural stop is invalid or wider than the 2.5 ATR hard limit",
            )
        half_zone = min(0.1 * atr, max(snapshot.spread, 0.01 * atr))
        entry = EntryZone(low=entry_mid - half_zone, high=entry_mid + half_zone)
        reward_risks = tuple(abs(price - entry_mid) / risk for price in target_prices)
        if min(reward_risks) < self.minimum_reward_risk:
            return self._blocked(
                plan_id,
                quant,
                candidate,
                features,
                snapshot,
                TargetStatus.INVALID,
                "Nearest structural target is below 1.5 reward-to-risk",
            )
        targets = tuple(
            StructuralTarget(
                name=name,
                price=price,
                reward_risk=reward_risk,
                basis=basis,
            )
            for name, price, reward_risk, basis in zip(
                ("TP1", "TP2", "TP3"),
                target_prices,
                reward_risks,
                (
                    "nearest structural/1.5R objective",
                    "minimum 2R structural continuation",
                    "minimum 3R structural extension",
                ),
                strict=True,
            )
        )
        plan = StructuralTargetPlan(
            target_plan_id=plan_id,
            version=self.version,
            created_at=snapshot.captured_at,
            instrument=candidate.instrument,
            timeframe=candidate.timeframe,
            direction=candidate.direction,
            quant_validation_id=quant.quant_validation_id,
            feature_set_id=features.feature_set_id,
            snapshot_id=snapshot.snapshot_id,
            status=TargetStatus.VALID,
            entry=entry,
            stop_loss=stop,
            targets=targets,
            atr=atr,
            stop_atr_multiple=stop_atr,
            minimum_reward_risk=min(reward_risks),
            liquidity_state=features.liquidity.state,
            reason="Entry, stop, and targets are derived from structure, auction, ATR, and liquidity",
        )
        self._persist(plan)
        return plan

    def _blocked(
        self,
        plan_id: str,
        quant: QuantValidationResult,
        candidate: PrimaryCandidate,
        features: FeatureSet,
        snapshot: MarketSnapshot,
        status: TargetStatus,
        reason: str,
    ) -> StructuralTargetPlan:
        plan = StructuralTargetPlan(
            target_plan_id=plan_id,
            version=self.version,
            created_at=snapshot.captured_at,
            instrument=candidate.instrument,
            timeframe=candidate.timeframe,
            direction=candidate.direction,
            quant_validation_id=quant.quant_validation_id,
            feature_set_id=features.feature_set_id,
            snapshot_id=snapshot.snapshot_id,
            status=status,
            liquidity_state=features.liquidity.state,
            reason=reason,
        )
        self._persist(plan)
        return plan

    def _persist(self, plan: StructuralTargetPlan) -> None:
        self.store.upsert(
            STRUCTURAL_TARGET_COLLECTION,
            plan.target_plan_id,
            plan.model_dump(mode="json"),
        )


class PortfolioExposureEngine:
    """Aggregate currency, commodity, correlation, and heat exposures."""

    version = "portfolio-exposure-v1"

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def assess(self, request: PortfolioExposureRequest) -> PortfolioExposureReport:
        candidate = _load_candidate(self.store, request.candidate_id)
        currency: dict[str, float] = defaultdict(float)
        commodity: dict[str, float] = defaultdict(float)
        factor_counts: dict[tuple[str, int], int] = defaultdict(int)
        all_positions = request.open_positions + (
            # The proposed candidate is always part of the post-trade exposure state.
            _candidate_position(candidate, request.proposed_risk_fraction),
        )
        for position in all_positions:
            _add_factor_exposure(
                position.instrument,
                position.direction,
                position.risk_fraction,
                currency,
                commodity,
                factor_counts,
            )
        heat = sum(position.risk_fraction for position in all_positions)
        usd_concentration = abs(currency.get("USD", 0.0)) / heat if heat else 0.0
        relevant = tuple(
            abs(observation.coefficient)
            for observation in request.correlations
            if candidate.instrument
            in {
                normalize_instrument(observation.left_instrument).canonical,
                normalize_instrument(observation.right_instrument).canonical,
            }
        )
        maximum_correlation = max(relevant) if relevant else None
        repeated = tuple(
            sorted(
                factor
                for (factor, _sign), count in factor_counts.items()
                if count >= 2
            )
        )
        vetoes: list[str] = []
        if heat > request.maximum_portfolio_heat:
            vetoes.append("portfolio heat exceeds the configured ceiling")
        if any(abs(value) > request.maximum_factor_exposure for value in currency.values()):
            vetoes.append("currency factor concentration exceeds the configured ceiling")
        if any(abs(value) > request.maximum_factor_exposure for value in commodity.values()):
            vetoes.append("commodity concentration exceeds the configured ceiling")
        if usd_concentration > request.maximum_usd_concentration:
            vetoes.append("USD concentration exceeds the configured ceiling")
        if maximum_correlation is not None and maximum_correlation > request.maximum_absolute_correlation:
            vetoes.append("rolling correlation exposure exceeds the configured ceiling")
        missing_correlation = bool(request.open_positions) and maximum_correlation is None
        if vetoes:
            status = ExposureStatus.VETO
        elif missing_correlation:
            status = ExposureStatus.INSUFFICIENT_EVIDENCE
        else:
            status = ExposureStatus.ACCEPTABLE
        report_id = stable_key(
            self.version,
            request.model_dump(mode="json"),
            dict(currency),
            dict(commodity),
        )
        report = PortfolioExposureReport(
            exposure_id=report_id,
            version=self.version,
            created_at=candidate.observed_at,
            candidate_id=candidate.candidate_id,
            proposed_instrument=candidate.instrument,
            proposed_risk_fraction=request.proposed_risk_fraction,
            status=status,
            portfolio_heat=heat,
            currency_factor_exposure=dict(sorted(currency.items())),
            commodity_exposure=dict(sorted(commodity.items())),
            usd_concentration=usd_concentration,
            maximum_relevant_correlation=maximum_correlation,
            repeated_factor_exposures=repeated,
            veto_reasons=tuple(vetoes),
        )
        self.store.upsert(
            PORTFOLIO_EXPOSURE_COLLECTION,
            report.exposure_id,
            report.model_dump(mode="json"),
        )
        return report


class RiskOverlay:
    """Apply hard risk vetoes after Quant, expectancy, targeting, and exposure."""

    version = "hard-risk-overlay-v1"

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def evaluate(self, request: RiskOverlayRequest) -> RiskOverlayResult:
        quant = _load_quant(self.store, request.quant_validation_id)
        expectancy = _load_expectancy(self.store, request.expectancy_id)
        targets = _load_targets(self.store, request.target_plan_id)
        exposure = _load_exposure(self.store, request.exposure_id)
        context = _load_context(self.store, request.context_set_id)
        snapshot = _load_snapshot(self.store, request.snapshot_id)
        if len({quant.instrument, expectancy.instrument, targets.instrument, context.instrument, snapshot.instrument}) != 1:
            raise ValueError("risk overlay inputs must share one instrument")
        if expectancy.quant_validation_id != quant.quant_validation_id:
            raise ValueError("expectancy and Quant lineage do not match")
        if targets.quant_validation_id != quant.quant_validation_id:
            raise ValueError("target plan and Quant lineage do not match")
        if exposure.candidate_id != quant.candidate_id:
            raise ValueError("portfolio exposure and candidate lineage do not match")
        checks = (
            _risk_check(
                RiskCheckName.QUANT,
                RiskCheckStatus.PASS if quant.decision is QuantDecision.PASS else RiskCheckStatus.VETO,
                "Quant decision is PASS" if quant.decision is QuantDecision.PASS else "Exact Quant PASS is required",
                quant.quant_validation_id,
            ),
            _risk_check(
                RiskCheckName.EXPECTANCY,
                RiskCheckStatus.PASS if expectancy.status is ExpectancyStatus.POSITIVE else RiskCheckStatus.VETO,
                expectancy.reason,
                expectancy.expectancy_id,
            ),
            _risk_check(
                RiskCheckName.STRUCTURAL_TARGETS,
                RiskCheckStatus.PASS if targets.status is TargetStatus.VALID else RiskCheckStatus.VETO,
                targets.reason,
                targets.target_plan_id,
            ),
            _limit_check(
                RiskCheckName.DAILY_DRAWDOWN,
                request.account_state.daily_drawdown,
                request.policy.maximum_daily_drawdown,
            ),
            _limit_check(
                RiskCheckName.WEEKLY_DRAWDOWN,
                request.account_state.weekly_drawdown,
                request.policy.maximum_weekly_drawdown,
            ),
            _risk_check(
                RiskCheckName.CONSECUTIVE_LOSSES,
                (
                    RiskCheckStatus.VETO
                    if request.account_state.consecutive_losses
                    >= request.policy.maximum_consecutive_losses
                    else RiskCheckStatus.PASS
                ),
                (
                    "consecutive-loss limit reached"
                    if request.account_state.consecutive_losses
                    >= request.policy.maximum_consecutive_losses
                    else "consecutive losses remain below the hard limit"
                ),
            ),
            _risk_check(
                RiskCheckName.PORTFOLIO_HEAT,
                (
                    RiskCheckStatus.PASS
                    if exposure.portfolio_heat <= request.policy.maximum_portfolio_heat
                    else RiskCheckStatus.VETO
                ),
                (
                    "portfolio heat is within the hard ceiling"
                    if exposure.portfolio_heat <= request.policy.maximum_portfolio_heat
                    else "portfolio heat exceeds the hard ceiling"
                ),
                exposure.exposure_id,
            ),
            _risk_check(
                RiskCheckName.FACTOR_CONCENTRATION,
                (
                    RiskCheckStatus.PASS
                    if exposure.status is ExposureStatus.ACCEPTABLE
                    else (
                        RiskCheckStatus.VETO
                        if exposure.status is ExposureStatus.VETO
                        else RiskCheckStatus.INSUFFICIENT
                    )
                ),
                "; ".join(exposure.veto_reasons) or "factor exposure is acceptable",
                exposure.exposure_id,
            ),
            _risk_check(
                RiskCheckName.CORRELATION,
                (
                    RiskCheckStatus.INSUFFICIENT
                    if exposure.status is ExposureStatus.INSUFFICIENT_EVIDENCE
                    else (
                        RiskCheckStatus.VETO
                        if any("correlation" in reason for reason in exposure.veto_reasons)
                        else RiskCheckStatus.PASS
                    )
                ),
                (
                    "relevant rolling correlation evidence is unavailable"
                    if exposure.status is ExposureStatus.INSUFFICIENT_EVIDENCE
                    else "rolling correlation is within the configured limit"
                ),
                exposure.exposure_id,
            ),
            _risk_check(
                RiskCheckName.DATA_QUALITY,
                (
                    RiskCheckStatus.PASS
                    if snapshot.data_quality.status is DataQualityStatus.HEALTHY
                    and snapshot.data_quality.publication_eligible
                    and snapshot.health.data_fresh
                    else RiskCheckStatus.VETO
                ),
                "data quality is healthy" if snapshot.data_quality.publication_eligible else "data quality failed",
                snapshot.snapshot_id,
            ),
            _risk_check(
                RiskCheckName.MODEL_HEALTH,
                (
                    RiskCheckStatus.PASS
                    if all(check.status is QuantCheckStatus.PASS for check in quant.checks)
                    else RiskCheckStatus.VETO
                ),
                "model evidence is healthy" if quant.decision is QuantDecision.PASS else "model evidence is degraded",
                quant.quant_validation_id,
            ),
            _risk_check(
                RiskCheckName.EVENT_RISK,
                RiskCheckStatus.VETO if context.candidate_veto else RiskCheckStatus.PASS,
                "event/news veto is active" if context.candidate_veto else "no event/news veto is active",
                context.context_set_id,
            ),
            _risk_check(
                RiskCheckName.LIQUIDITY,
                RiskCheckStatus.PASS if targets.liquidity_state is LiquidityState.ADEQUATE else RiskCheckStatus.VETO,
                (
                    "measured liquidity is adequate"
                    if targets.liquidity_state is LiquidityState.ADEQUATE
                    else f"liquidity is {targets.liquidity_state.value}"
                ),
                targets.target_plan_id,
            ),
        )
        statuses = {check.status for check in checks}
        if RiskCheckStatus.VETO in statuses:
            decision = RiskDecision.RISK_VETO
        elif RiskCheckStatus.INSUFFICIENT in statuses:
            decision = RiskDecision.INSUFFICIENT_EVIDENCE
        else:
            decision = RiskDecision.PASS
        probability = quant.atlas_probability or 0.0
        # A newly eligible 60% research signal starts at half the proposed
        # risk. The former 80% threshold retains its previous 0.75 multiplier.
        probability_multiplier = min(1.0, max(0.50, 0.50 + max(0.0, probability - 0.60) * 1.25))
        remaining_heat = max(0.0, request.policy.maximum_portfolio_heat - (exposure.portfolio_heat - exposure.proposed_risk_fraction))
        approved = (
            min(
                exposure.proposed_risk_fraction * probability_multiplier,
                request.policy.maximum_risk_per_signal,
                remaining_heat,
            )
            if decision is RiskDecision.PASS
            else 0.0
        )
        non_passing = tuple(
            f"{check.name.value}: {check.summary}"
            for check in checks
            if check.status is not RiskCheckStatus.PASS
        )
        reason = "All hard risk checks passed" if not non_passing else "; ".join(non_passing)
        result_id = stable_key(
            self.version,
            request.model_dump(mode="json"),
            [check.model_dump(mode="json") for check in checks],
        )
        result = RiskOverlayResult(
            risk_overlay_id=result_id,
            version=self.version,
            created_at=max(
                quant.created_at,
                expectancy.created_at,
                targets.created_at,
                context.as_of,
                snapshot.captured_at,
                request.account_state.as_of,
            ),
            instrument=quant.instrument,
            quant_validation_id=quant.quant_validation_id,
            expectancy_id=expectancy.expectancy_id,
            target_plan_id=targets.target_plan_id,
            exposure_id=exposure.exposure_id,
            context_set_id=context.context_set_id,
            snapshot_id=snapshot.snapshot_id,
            decision=decision,
            checks=checks,
            approved_risk_fraction=approved,
            probability_size_multiplier=probability_multiplier,
            reason=reason,
            risk_veto=decision is RiskDecision.RISK_VETO,
        )
        self.store.upsert(
            RISK_OVERLAY_COLLECTION,
            result.risk_overlay_id,
            result.model_dump(mode="json"),
        )
        return result


def _long_targets(entry: float, risk: float, anchors: list[float]) -> tuple[float, float, float]:
    first = max(entry + 1.5 * risk, min(anchors)) if anchors else entry + 1.5 * risk
    second = max(entry + 2 * risk, first + 0.5 * risk)
    third = max(entry + 3 * risk, second + 0.5 * risk)
    return first, second, third


def _short_targets(entry: float, risk: float, anchors: list[float]) -> tuple[float, float, float]:
    first = min(entry - 1.5 * risk, max(anchors)) if anchors else entry - 1.5 * risk
    second = min(entry - 2 * risk, first - 0.5 * risk)
    third = min(entry - 3 * risk, second - 0.5 * risk)
    return first, second, third


def _candidate_position(candidate: PrimaryCandidate, risk_fraction: float) -> PortfolioPosition:
    return PortfolioPosition(
        position_id=f"proposed-{candidate.candidate_id}",
        instrument=candidate.instrument,
        direction=candidate.direction,
        risk_fraction=risk_fraction,
    )


def _add_factor_exposure(
    instrument: str,
    direction: MLTarget,
    risk: float,
    currency: dict[str, float],
    commodity: dict[str, float],
    counts: dict[tuple[str, int], int],
) -> None:
    canonical = normalize_instrument(instrument).canonical
    sign = 1 if direction is MLTarget.LONG else -1
    currency_codes = {"USD", "EUR", "GBP", "JPY", "CHF", "AUD", "NZD", "CAD"}
    if len(canonical) == 6 and canonical[:3] in currency_codes and canonical[3:] in currency_codes:
        base, quote = canonical[:3], canonical[3:]
        currency[base] += sign * risk
        currency[quote] -= sign * risk
        counts[(base, sign)] += 1
        counts[(quote, -sign)] += 1
    elif canonical in {"XAUUSD", "XAGUSD"}:
        factor = canonical[:3]
        commodity[factor] += sign * risk
        currency["USD"] -= sign * risk
        counts[(factor, sign)] += 1
        counts[("USD", -sign)] += 1
    elif canonical in {"WTI", "BRENT"}:
        commodity[canonical] += sign * risk
        currency["USD"] -= sign * risk
        counts[(canonical, sign)] += 1
        counts[("USD", -sign)] += 1
    else:
        raise ValueError(f"unsupported portfolio exposure instrument: {instrument}")


def _risk_check(
    name: RiskCheckName,
    status: RiskCheckStatus,
    summary: str,
    *artifact_ids: str,
) -> RiskCheck:
    return RiskCheck(name=name, status=status, summary=summary, artifact_ids=artifact_ids)


def _limit_check(name: RiskCheckName, value: float, limit: float) -> RiskCheck:
    return _risk_check(
        name,
        RiskCheckStatus.VETO if value >= limit else RiskCheckStatus.PASS,
        f"{name.value.lower()} is {'at/above' if value >= limit else 'below'} the hard limit",
    )


def _row(store: RecordStore, collection: str, field: str, value: str) -> dict[str, object]:
    row = next((row for row in store.values(collection) if row.get(field) == value), None)
    if row is None:
        raise KeyError(f"{collection} artifact does not exist")
    return row


def _load_quant(store: RecordStore, value: str) -> QuantValidationResult:
    return QuantValidationResult.model_validate(
        _row(store, QUANT_VALIDATION_COLLECTION, "quant_validation_id", value)
    )


def _load_backtest(store: RecordStore, value: str) -> BacktestRun:
    return BacktestRun.model_validate(_row(store, BACKTEST_COLLECTION, "backtest_run_id", value))


def _load_candidate(store: RecordStore, value: str) -> PrimaryCandidate:
    return PrimaryCandidate.model_validate(_row(store, PRIMARY_CANDIDATE_COLLECTION, "candidate_id", value))


def _load_features(store: RecordStore, value: str) -> FeatureSet:
    return FeatureSet.model_validate(_row(store, FEATURE_COLLECTION, "feature_set_id", value))


def _load_snapshot(store: RecordStore, value: str) -> MarketSnapshot:
    return MarketSnapshot.model_validate(_row(store, SNAPSHOT_COLLECTION, "snapshot_id", value))


def _load_expectancy(store: RecordStore, value: str) -> ExpectancyReport:
    return ExpectancyReport.model_validate(_row(store, EXPECTANCY_COLLECTION, "expectancy_id", value))


def _load_targets(store: RecordStore, value: str) -> StructuralTargetPlan:
    return StructuralTargetPlan.model_validate(
        _row(store, STRUCTURAL_TARGET_COLLECTION, "target_plan_id", value)
    )


def _load_exposure(store: RecordStore, value: str) -> PortfolioExposureReport:
    return PortfolioExposureReport.model_validate(
        _row(store, PORTFOLIO_EXPOSURE_COLLECTION, "exposure_id", value)
    )


def _load_context(store: RecordStore, value: str) -> ContextSet:
    return ContextSet.model_validate(_row(store, CONTEXT_COLLECTION, "context_set_id", value))


__all__ = [
    "EXPECTANCY_COLLECTION",
    "PORTFOLIO_EXPOSURE_COLLECTION",
    "RISK_OVERLAY_COLLECTION",
    "STRUCTURAL_TARGET_COLLECTION",
    "ExpectancyEngine",
    "PortfolioExposureEngine",
    "RiskOverlay",
    "StructuralTargetEngine",
]
