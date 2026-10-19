"""Single canonical V4 signal-publication authority and immutable revisions."""

from __future__ import annotations

import math
from datetime import UTC, datetime, timedelta
from typing import ClassVar, TypeVar

from pydantic import BaseModel

from app.core.store import RecordStore, stable_key
from app.v4.ai_contracts import AIReviewBundle, AIReviewBundleStatus, AIReviewStatus
from app.v4.ai_review import AI_REVIEW_COLLECTION
from app.v4.calibration import (
    CALIBRATED_PROBABILITY_COLLECTION,
    CALIBRATION_ARTIFACT_COLLECTION,
)
from app.v4.calibration_contracts import (
    CalibratedProbability,
    CalibrationArtifact,
    CalibrationStatus,
)
from app.v4.context_contracts import ContextSet
from app.v4.context_service import CONTEXT_COLLECTION
from app.v4.contracts import DataQualityStatus, FeatureSet, MarketSnapshot, OfficialTimeframe
from app.v4.feature_service import FEATURE_COLLECTION
from app.v4.meta_contracts import MetaPrediction, MetaTarget
from app.v4.meta_labeling import META_PREDICTION_COLLECTION
from app.v4.ml_contracts import PrimaryCandidate
from app.v4.primary_ml import PRIMARY_CANDIDATE_COLLECTION
from app.v4.quant_validation import QUANT_VALIDATION_COLLECTION
from app.v4.quant_validation_contracts import QuantDecision, QuantValidationResult
from app.v4.risk_contracts import (
    ExpectancyReport,
    ExpectancyStatus,
    ExposureStatus,
    PortfolioExposureReport,
    RiskCheckName,
    RiskCheckStatus,
    RiskDecision,
    RiskOverlayResult,
    StructuralTargetPlan,
    TargetStatus,
)
from app.v4.risk_engine import (
    EXPECTANCY_COLLECTION,
    PORTFOLIO_EXPOSURE_COLLECTION,
    RISK_OVERLAY_COLLECTION,
    STRUCTURAL_TARGET_COLLECTION,
)
from app.v4.signal_contracts import (
    CanonicalSignal,
    PUBLICATION_PROBABILITY_THRESHOLD,
    PublicationDecision,
    PublicationValidation,
    SignalLineage,
    SignalPublicationRequest,
    SignalPublicationResult,
    SignalRevision,
    SignalRevisionRequest,
    grade_for_probability,
)
from app.v4.snapshots import SNAPSHOT_COLLECTION

SIGNAL_COLLECTION = "v4_canonical_signals"
PUBLICATION_ATTEMPT_COLLECTION = "v4_signal_publication_attempts"
SIGNAL_REVISION_COLLECTION = "v4_signal_revisions"

_ModelT = TypeVar("_ModelT", bound=BaseModel)


class SignalPublicationGate:
    """The sole constructor of canonical V4 signals; every gate fails closed."""

    version = "signal-publication-gate-v1"
    _expiry: ClassVar[dict[OfficialTimeframe, timedelta]] = {
        OfficialTimeframe.M15: timedelta(hours=4),
        OfficialTimeframe.H1: timedelta(hours=12),
        OfficialTimeframe.H4: timedelta(days=2),
        OfficialTimeframe.D1: timedelta(days=7),
    }

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def evaluate(self, request: SignalPublicationRequest) -> SignalPublicationResult:
        evidence = self._load_evidence(request)
        decision, reasons = self._decision(evidence)
        created_at = max(
            evidence.quant.created_at,
            evidence.expectancy.created_at,
            evidence.targets.created_at,
            evidence.risk.created_at,
            evidence.context.as_of,
            evidence.features.computed_at,
            evidence.snapshot.captured_at,
            evidence.candidate.observed_at,
        )
        signal = self._publish(evidence, created_at) if decision is PublicationDecision.PUBLISHED else None
        attempt_id = stable_key(
            self.version,
            request.model_dump(mode="json"),
            decision.value,
            reasons,
            signal.signal_id if signal else None,
        )
        result = SignalPublicationResult(
            publication_attempt_id=attempt_id,
            version=self.version,
            created_at=created_at,
            decision=decision,
            instrument=evidence.candidate.instrument,
            candidate_id=evidence.candidate.candidate_id,
            atlas_probability=evidence.calibrated.atlas_probability,
            grade=signal.grade if signal else None,
            signal=signal,
            reasons=reasons,
            signal_published=signal is not None,
        )
        self.store.upsert(
            PUBLICATION_ATTEMPT_COLLECTION,
            result.publication_attempt_id,
            result.model_dump(mode="json"),
        )
        if signal is not None:
            self.store.upsert(SIGNAL_COLLECTION, signal.signal_id, signal.model_dump(mode="json"))
        return result

    def get(self, signal_id: str) -> CanonicalSignal:
        return _load(self.store, SIGNAL_COLLECTION, "signal_id", signal_id, CanonicalSignal)

    def _load_evidence(self, request: SignalPublicationRequest) -> _PublicationEvidence:
        risk = _load(
            self.store,
            RISK_OVERLAY_COLLECTION,
            "risk_overlay_id",
            request.risk_overlay_id,
            RiskOverlayResult,
        )
        quant = _load(
            self.store,
            QUANT_VALIDATION_COLLECTION,
            "quant_validation_id",
            risk.quant_validation_id,
            QuantValidationResult,
        )
        candidate = _load(
            self.store,
            PRIMARY_CANDIDATE_COLLECTION,
            "candidate_id",
            quant.candidate_id,
            PrimaryCandidate,
        )
        expectancy = _load(
            self.store,
            EXPECTANCY_COLLECTION,
            "expectancy_id",
            risk.expectancy_id,
            ExpectancyReport,
        )
        targets = _load(
            self.store,
            STRUCTURAL_TARGET_COLLECTION,
            "target_plan_id",
            risk.target_plan_id,
            StructuralTargetPlan,
        )
        exposure = _load(
            self.store,
            PORTFOLIO_EXPOSURE_COLLECTION,
            "exposure_id",
            risk.exposure_id,
            PortfolioExposureReport,
        )
        context = _load(
            self.store,
            CONTEXT_COLLECTION,
            "context_set_id",
            risk.context_set_id,
            ContextSet,
        )
        features = _load(
            self.store,
            FEATURE_COLLECTION,
            "feature_set_id",
            request.feature_set_id,
            FeatureSet,
        )
        snapshot = _load(
            self.store,
            SNAPSHOT_COLLECTION,
            "snapshot_id",
            risk.snapshot_id,
            MarketSnapshot,
        )
        calibrated = _load(
            self.store,
            CALIBRATED_PROBABILITY_COLLECTION,
            "calibrated_probability_id",
            quant.calibrated_probability_id,
            CalibratedProbability,
        )
        calibration = _load(
            self.store,
            CALIBRATION_ARTIFACT_COLLECTION,
            "calibration_id",
            calibrated.calibration_id,
            CalibrationArtifact,
        )
        meta = _load(
            self.store,
            META_PREDICTION_COLLECTION,
            "meta_prediction_id",
            calibrated.meta_prediction_id,
            MetaPrediction,
        )
        ai_bundle = (
            _load(
                self.store,
                AI_REVIEW_COLLECTION,
                "bundle_id",
                request.ai_review_bundle_id,
                AIReviewBundle,
            )
            if request.ai_review_bundle_id is not None
            else None
        )
        evidence = _PublicationEvidence(
            risk=risk,
            quant=quant,
            candidate=candidate,
            expectancy=expectancy,
            targets=targets,
            exposure=exposure,
            context=context,
            features=features,
            snapshot=snapshot,
            calibrated=calibrated,
            calibration=calibration,
            meta=meta,
            ai_bundle=ai_bundle,
        )
        _validate_lineage(evidence)
        return evidence

    @staticmethod
    def _decision(evidence: _PublicationEvidence) -> tuple[PublicationDecision, tuple[str, ...]]:
        probability = evidence.calibrated.atlas_probability
        if evidence.quant.decision is QuantDecision.VETO or evidence.quant.quant_veto:
            return PublicationDecision.QUANT_VETO, (evidence.quant.reason,)
        if (
            evidence.snapshot.data_quality.status is not DataQualityStatus.HEALTHY
            or not evidence.snapshot.data_quality.publication_eligible
            or not evidence.snapshot.health.data_fresh
            or evidence.features.input_quality is not DataQualityStatus.HEALTHY
        ):
            return PublicationDecision.DATA_DEGRADED, (
                f"MT5 data quality is {evidence.snapshot.data_quality.status.value}",
            )
        if evidence.context.candidate_veto:
            return PublicationDecision.EVENT_BLOCK, ("Event or news risk veto is active",)
        model_check = next(check for check in evidence.risk.checks if check.name is RiskCheckName.MODEL_HEALTH)
        if model_check.status is not RiskCheckStatus.PASS:
            return PublicationDecision.MODEL_UNCERTAIN, (model_check.summary,)
        if evidence.risk.decision is RiskDecision.RISK_VETO:
            reasons = tuple(
                check.summary
                for check in evidence.risk.checks
                if check.status is RiskCheckStatus.VETO
            )
            return PublicationDecision.RISK_VETO, reasons or (evidence.risk.reason,)
        if evidence.quant.decision is QuantDecision.INSUFFICIENT_EVIDENCE:
            return PublicationDecision.INSUFFICIENT_DATA, (evidence.quant.reason,)
        if evidence.quant.decision is QuantDecision.PASS_WITH_CAUTION:
            return PublicationDecision.MODEL_UNCERTAIN, ("Exact Quant PASS is required",)
        if evidence.risk.decision is RiskDecision.INSUFFICIENT_EVIDENCE:
            return PublicationDecision.INSUFFICIENT_DATA, (evidence.risk.reason,)
        if (
            evidence.calibrated.evidence_status is not CalibrationStatus.AVAILABLE
            or probability is None
            or evidence.calibration.status is not CalibrationStatus.AVAILABLE
        ):
            return PublicationDecision.INSUFFICIENT_DATA, ("Calibrated Atlas probability is unavailable",)
        if evidence.meta.research_classification is MetaTarget.REJECT:
            return PublicationDecision.NO_TRADE, ("Meta-label model rejected the primary candidate",)
        if evidence.expectancy.status is ExpectancyStatus.NON_POSITIVE:
            return PublicationDecision.NO_TRADE, (evidence.expectancy.reason,)
        if evidence.expectancy.status is not ExpectancyStatus.POSITIVE:
            return PublicationDecision.INSUFFICIENT_DATA, (evidence.expectancy.reason,)
        if evidence.targets.status is TargetStatus.INVALID:
            return PublicationDecision.NO_TRADE, (evidence.targets.reason,)
        if evidence.targets.status is not TargetStatus.VALID:
            return PublicationDecision.INSUFFICIENT_DATA, (evidence.targets.reason,)
        if evidence.exposure.status is ExposureStatus.INSUFFICIENT_EVIDENCE:
            return PublicationDecision.INSUFFICIENT_DATA, ("Portfolio exposure evidence is insufficient",)
        if probability < PUBLICATION_PROBABILITY_THRESHOLD:
            return PublicationDecision.WAIT, (
                (
                    f"Calibrated probability {probability:.4f} is below the "
                    f"{PUBLICATION_PROBABILITY_THRESHOLD:.2f} publication threshold"
                ),
            )
        if evidence.ai_bundle is not None and evidence.ai_bundle.status is not AIReviewBundleStatus.COMPLETE:
            return PublicationDecision.MODEL_UNCERTAIN, ("Requested AI review bundle is incomplete",)
        if evidence.risk.decision is not RiskDecision.PASS or evidence.risk.approved_risk_fraction <= 0:
            return PublicationDecision.RISK_VETO, (evidence.risk.reason,)
        return PublicationDecision.PUBLISHED, ("Every canonical publication gate passed",)

    def _publish(self, evidence: _PublicationEvidence, created_at: datetime) -> CanonicalSignal:
        probability = evidence.calibrated.atlas_probability
        grade = grade_for_probability(probability if probability is not None else -1)
        if probability is None or grade is None:
            raise RuntimeError("publication attempted without an eligible calibrated probability")
        if evidence.targets.entry is None or evidence.targets.stop_loss is None:
            raise RuntimeError("publication attempted without valid structural geometry")
        expected_value = evidence.expectancy.expected_value_net
        average_loss = evidence.expectancy.average_gross_loss
        if expected_value is None or expected_value <= 0 or average_loss is None or average_loss <= 0:
            raise RuntimeError("publication attempted without positive net expectancy")
        expected_r = expected_value / average_loss
        drivers = _drivers(evidence)
        contradictions = _ai_statements(evidence.ai_bundle, "contradictions")
        invalidations = (
            f"Structural stop invalidation at {evidence.targets.stop_loss}",
            *_ai_statements(evidence.ai_bundle, "invalidations"),
        )
        lineage = SignalLineage(
            candidate_id=evidence.candidate.candidate_id,
            ai_review_bundle_id=evidence.ai_bundle.bundle_id if evidence.ai_bundle else None,
            meta_prediction_id=evidence.meta.meta_prediction_id,
            calibrated_probability_id=evidence.calibrated.calibrated_probability_id,
            calibration_id=evidence.calibration.calibration_id,
            quant_validation_id=evidence.quant.quant_validation_id,
            expectancy_id=evidence.expectancy.expectancy_id,
            target_plan_id=evidence.targets.target_plan_id,
            exposure_id=evidence.exposure.exposure_id,
            risk_overlay_id=evidence.risk.risk_overlay_id,
            context_set_id=evidence.context.context_set_id,
            feature_set_id=evidence.features.feature_set_id,
            market_snapshot_id=evidence.snapshot.snapshot_id,
            backtest_run_id=evidence.expectancy.backtest_run_id,
        )
        versions = {
            "primary_model_id": evidence.candidate.model_id,
            "meta_model_id": evidence.meta.meta_model_id,
            "calibration": evidence.calibration.version,
            "quant": evidence.quant.version,
            "expectancy": evidence.expectancy.version,
            "targets": evidence.targets.version,
            "risk": evidence.risk.version,
            "features": evidence.features.feature_version,
            "context": evidence.context.context_version,
        }
        signal_id = stable_key(
            "canonical-signal-v4",
            lineage.model_dump(mode="json"),
            evidence.targets.entry.model_dump(mode="json"),
            evidence.targets.stop_loss,
            [target.model_dump(mode="json") for target in evidence.targets.targets],
            probability,
            grade.value,
        )
        return CanonicalSignal(
            signal_id=signal_id,
            instrument=evidence.candidate.instrument,
            direction=evidence.candidate.direction,
            timeframe=evidence.candidate.timeframe,
            entry=evidence.targets.entry,
            stop_loss=evidence.targets.stop_loss,
            targets=evidence.targets.targets,
            raw_probability=evidence.candidate.raw_direction_probability,
            meta_probability=evidence.meta.raw_accept_probability,
            calibrated_probability=probability,
            grade=grade,
            expected_r=expected_r,
            expected_value_net=expected_value,
            approved_risk_fraction=evidence.risk.approved_risk_fraction,
            regime=evidence.candidate.regime,
            macro_availability=evidence.context.macro.availability,
            event_risk=evidence.context.events.risk,
            data_quality_score=evidence.snapshot.data_quality.score,
            quant_validation=PublicationValidation(
                reasons=tuple(check.summary for check in evidence.quant.checks),
            ),
            risk_validation=PublicationValidation(
                reasons=tuple(check.summary for check in evidence.risk.checks),
            ),
            drivers=drivers,
            contradictions=contradictions,
            invalidations=invalidations,
            lineage=lineage,
            model_versions=versions,
            created_at=created_at,
            expires_at=created_at + self._expiry[evidence.candidate.timeframe],
        )


class SignalRevisionService:
    """Append revisions without changing the original canonical signal record."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def revise(self, signal_id: str, request: SignalRevisionRequest) -> SignalRevision:
        signal = _load(self.store, SIGNAL_COLLECTION, "signal_id", signal_id, CanonicalSignal)
        if request.replacement_signal_id is not None:
            replacement = _load(
                self.store,
                SIGNAL_COLLECTION,
                "signal_id",
                request.replacement_signal_id,
                CanonicalSignal,
            )
            if replacement.instrument != signal.instrument:
                raise ValueError("a correction replacement must use the same instrument")
        revisions = self.list(signal_id)
        previous = revisions[-1] if revisions else None
        index = len(revisions) + 1
        created_at = datetime.now(UTC)
        revision_id = stable_key(
            "canonical-signal-revision-v1",
            signal_id,
            index,
            request.model_dump(mode="json"),
            created_at.isoformat(),
        )
        revision = SignalRevision(
            revision_id=revision_id,
            signal_id=signal_id,
            revision_index=index,
            previous_revision_id=previous.revision_id if previous else None,
            kind=request.kind,
            revised_by=request.revised_by,
            reason=request.reason,
            annotation=request.annotation,
            replacement_signal_id=request.replacement_signal_id,
            created_at=created_at,
        )
        self.store.upsert(
            SIGNAL_REVISION_COLLECTION,
            revision.revision_id,
            revision.model_dump(mode="json"),
        )
        return revision

    def list(self, signal_id: str) -> tuple[SignalRevision, ...]:
        _load(self.store, SIGNAL_COLLECTION, "signal_id", signal_id, CanonicalSignal)
        revisions = tuple(
            SignalRevision.model_validate(row)
            for row in self.store.values(SIGNAL_REVISION_COLLECTION)
            if row.get("signal_id") == signal_id
        )
        return tuple(sorted(revisions, key=lambda row: row.revision_index))


class _PublicationEvidence:
    def __init__(
        self,
        *,
        risk: RiskOverlayResult,
        quant: QuantValidationResult,
        candidate: PrimaryCandidate,
        expectancy: ExpectancyReport,
        targets: StructuralTargetPlan,
        exposure: PortfolioExposureReport,
        context: ContextSet,
        features: FeatureSet,
        snapshot: MarketSnapshot,
        calibrated: CalibratedProbability,
        calibration: CalibrationArtifact,
        meta: MetaPrediction,
        ai_bundle: AIReviewBundle | None,
    ) -> None:
        self.risk = risk
        self.quant = quant
        self.candidate = candidate
        self.expectancy = expectancy
        self.targets = targets
        self.exposure = exposure
        self.context = context
        self.features = features
        self.snapshot = snapshot
        self.calibrated = calibrated
        self.calibration = calibration
        self.meta = meta
        self.ai_bundle = ai_bundle


def _validate_lineage(evidence: _PublicationEvidence) -> None:
    instruments = {
        evidence.risk.instrument,
        evidence.quant.instrument,
        evidence.candidate.instrument,
        evidence.expectancy.instrument,
        evidence.targets.instrument,
        evidence.context.instrument,
        evidence.features.instrument,
        evidence.snapshot.instrument,
    }
    if len(instruments) != 1:
        raise ValueError("publication evidence must share one canonical instrument")
    if evidence.risk.quant_validation_id != evidence.quant.quant_validation_id:
        raise ValueError("risk and Quant lineage do not match")
    if evidence.expectancy.quant_validation_id != evidence.quant.quant_validation_id:
        raise ValueError("expectancy and Quant lineage do not match")
    if evidence.targets.quant_validation_id != evidence.quant.quant_validation_id:
        raise ValueError("targets and Quant lineage do not match")
    if evidence.exposure.candidate_id != evidence.candidate.candidate_id:
        raise ValueError("exposure and candidate lineage do not match")
    if evidence.meta.candidate_id != evidence.candidate.candidate_id:
        raise ValueError("meta prediction and candidate lineage do not match")
    if evidence.quant.candidate_id != evidence.candidate.candidate_id:
        raise ValueError("Quant and candidate lineage do not match")
    if evidence.calibrated.meta_prediction_id != evidence.meta.meta_prediction_id:
        raise ValueError("calibration and meta-prediction lineage do not match")
    if evidence.calibrated.calibration_id != evidence.calibration.calibration_id:
        raise ValueError("calibrated probability and artifact lineage do not match")
    if evidence.quant.calibrated_probability_id != evidence.calibrated.calibrated_probability_id:
        raise ValueError("Quant and calibrated-probability lineage do not match")
    if evidence.meta.meta_model_id != evidence.calibration.meta_model_id:
        raise ValueError("meta prediction and calibration model lineage do not match")
    if evidence.quant.meta_model_id != evidence.meta.meta_model_id:
        raise ValueError("Quant and meta-model lineage do not match")
    probabilities = (
        evidence.quant.atlas_probability,
        evidence.calibrated.atlas_probability,
        evidence.expectancy.probability_win,
    )
    available_probabilities = tuple(value for value in probabilities if value is not None)
    if len(available_probabilities) > 1 and not all(
        math.isclose(value, available_probabilities[0], abs_tol=1e-12)
        for value in available_probabilities[1:]
    ):
        raise ValueError("Quant, calibration, and expectancy probabilities do not match")
    if evidence.risk.snapshot_id != evidence.snapshot.snapshot_id:
        raise ValueError("risk and market-snapshot lineage do not match")
    if evidence.targets.snapshot_id != evidence.snapshot.snapshot_id:
        raise ValueError("targets and market-snapshot lineage do not match")
    if evidence.targets.feature_set_id != evidence.features.feature_set_id:
        raise ValueError("targets and feature-set lineage do not match")
    if evidence.features.source_snapshot_id != evidence.snapshot.snapshot_id:
        raise ValueError("feature set must originate from the publication snapshot")
    if evidence.risk.context_set_id != evidence.context.context_set_id:
        raise ValueError("risk and context lineage do not match")
    if evidence.targets.timeframe is not evidence.candidate.timeframe:
        raise ValueError("target timeframe and candidate timeframe do not match")
    if evidence.ai_bundle is not None and evidence.ai_bundle.candidate.candidate_id != evidence.candidate.candidate_id:
        raise ValueError("AI review and candidate lineage do not match")


def _drivers(evidence: _PublicationEvidence) -> tuple[str, ...]:
    values = [
        f"Quant {evidence.quant.decision.value}: {evidence.quant.reason}",
        f"Net expectancy {evidence.expectancy.expected_value_net:.6f}",
        evidence.targets.reason,
        f"Risk {evidence.risk.decision.value}: {evidence.risk.reason}",
        f"Regime {evidence.candidate.regime.value}",
        f"MTF {evidence.features.multi_timeframe.alignment}",
    ]
    if evidence.ai_bundle is not None:
        values.extend(_ai_statements(evidence.ai_bundle, "supports"))
    return tuple(dict.fromkeys(value for value in values if value))


def _ai_statements(bundle: AIReviewBundle | None, field: str) -> tuple[str, ...]:
    if bundle is None:
        return ()
    values: list[str] = []
    for review in bundle.reviews:
        payload = review.payload
        if field == "supports" and payload.review_status is not AIReviewStatus.UNAVAILABLE:
            values.extend(payload.supports)
        elif field == "contradictions":
            values.extend(payload.contradictions)
        elif field == "invalidations":
            values.extend(payload.invalidations)
    return tuple(dict.fromkeys(values))


def _load(  # noqa: UP047 -- keep Python 3.10-compatible TypeVar syntax
    store: RecordStore,
    collection: str,
    key_name: str,
    value: str,
    model: type[_ModelT],
) -> _ModelT:
    row = next((item for item in store.values(collection) if item.get(key_name) == value), None)
    if row is None:
        raise KeyError(f"{model.__name__} not found: {value}")
    return model.model_validate(row)


__all__ = [
    "PUBLICATION_ATTEMPT_COLLECTION",
    "SIGNAL_COLLECTION",
    "SIGNAL_REVISION_COLLECTION",
    "SignalPublicationGate",
    "SignalRevisionService",
]
