from __future__ import annotations

import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

from fastapi.testclient import TestClient

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import AtlasSettings
from app.core.store import MemoryRecordStore, stable_key
from app.main import create_app
from app.models.database import Database
from app.v4.ai_contracts import (
    AIReviewBundle,
    AIReviewBundleStatus,
    AIReviewerRole,
    AIReviewStatus,
    SpecialistReviewPayload,
    SpecialistReviewRecord,
)
from app.v4.ai_review import AI_REVIEW_COLLECTION
from app.v4.calibration import (
    CALIBRATED_PROBABILITY_COLLECTION,
    CALIBRATION_ARTIFACT_COLLECTION,
)
from app.v4.calibration_contracts import (
    CalibratedProbability,
    CalibrationArtifact,
    CalibrationMethod,
    CalibrationMetrics,
    CalibrationStatus,
    CalibrationUncertainty,
    ReliabilityBin,
)
from app.v4.context_service import CONTEXT_COLLECTION
from app.v4.contracts import DataQualityStatus
from app.v4.feature_service import FEATURE_COLLECTION
from app.v4.meta_contracts import MetaPrediction, MetaTarget
from app.v4.meta_labeling import META_PREDICTION_COLLECTION
from app.v4.primary_ml import PRIMARY_CANDIDATE_COLLECTION
from app.v4.quant_validation import QUANT_VALIDATION_COLLECTION
from app.v4.quant_validation_contracts import QuantCheckStatus, QuantDecision
from app.v4.risk_contracts import ExpectancyStatus
from app.v4.risk_engine import (
    EXPECTANCY_COLLECTION,
    PORTFOLIO_EXPOSURE_COLLECTION,
    RISK_OVERLAY_COLLECTION,
    STRUCTURAL_TARGET_COLLECTION,
)
from app.v4.signal_contracts import (
    PublicationDecision,
    SignalGrade,
    SignalPublicationRequest,
    SignalRevisionKind,
    SignalRevisionRequest,
    grade_for_probability,
)
from app.v4.signal_engine import (
    PUBLICATION_ATTEMPT_COLLECTION,
    SIGNAL_COLLECTION,
    SIGNAL_REVISION_COLLECTION,
    SignalPublicationGate,
    SignalRevisionService,
)
from app.v4.snapshots import SNAPSHOT_COLLECTION
from tests import test_v4_risk_engine as risk_fixtures
from tests.test_v4_market_foundation import FakeGateway

NOW = datetime(2026, 8, 20, 12, 0, tzinfo=UTC)

COLLECTIONS = (
    PRIMARY_CANDIDATE_COLLECTION,
    QUANT_VALIDATION_COLLECTION,
    EXPECTANCY_COLLECTION,
    STRUCTURAL_TARGET_COLLECTION,
    PORTFOLIO_EXPOSURE_COLLECTION,
    RISK_OVERLAY_COLLECTION,
    CONTEXT_COLLECTION,
    FEATURE_COLLECTION,
    SNAPSHOT_COLLECTION,
    META_PREDICTION_COLLECTION,
    CALIBRATION_ARTIFACT_COLLECTION,
    CALIBRATED_PROBABILITY_COLLECTION,
    AI_REVIEW_COLLECTION,
)

COLLECTION_KEYS = {
    PRIMARY_CANDIDATE_COLLECTION: "candidate_id",
    QUANT_VALIDATION_COLLECTION: "quant_validation_id",
    EXPECTANCY_COLLECTION: "expectancy_id",
    STRUCTURAL_TARGET_COLLECTION: "target_plan_id",
    PORTFOLIO_EXPOSURE_COLLECTION: "exposure_id",
    RISK_OVERLAY_COLLECTION: "risk_overlay_id",
    CONTEXT_COLLECTION: "context_set_id",
    FEATURE_COLLECTION: "feature_set_id",
    SNAPSHOT_COLLECTION: "snapshot_id",
    META_PREDICTION_COLLECTION: "meta_prediction_id",
    CALIBRATION_ARTIFACT_COLLECTION: "calibration_id",
    CALIBRATED_PROBABILITY_COLLECTION: "calibrated_probability_id",
    AI_REVIEW_COLLECTION: "bundle_id",
}


class SignalEngineTests(unittest.TestCase):
    base: ClassVar[MemoryRecordStore]
    meta: ClassVar[MetaPrediction]
    calibrated: ClassVar[CalibratedProbability]
    ai_bundle: ClassVar[AIReviewBundle]

    @classmethod
    def setUpClass(cls) -> None:
        risk_fixtures.RiskEngineTests.setUpClass()
        cls.base = risk_fixtures.RiskEngineTests.store
        cls.meta = MetaPrediction(
            meta_prediction_id=stable_key("signal-meta"),
            meta_model_id=risk_fixtures.RiskEngineTests.quant.meta_model_id,
            candidate_id=risk_fixtures.RiskEngineTests.candidate.candidate_id,
            observed_at=NOW,
            raw_accept_probability=0.84,
            research_classification=MetaTarget.ACCEPT,
        )
        cls.base.upsert(
            META_PREDICTION_COLLECTION,
            cls.meta.meta_prediction_id,
            cls.meta.model_dump(mode="json"),
        )
        bins = (
            ReliabilityBin(
                lower_bound=0.0,
                upper_bound=0.5,
                sample_count=100,
                mean_probability=0.35,
                observed_tp_before_sl_rate=0.34,
                confidence_low=0.25,
                confidence_high=0.44,
            ),
            ReliabilityBin(
                lower_bound=0.5,
                upper_bound=1.0,
                sample_count=100,
                mean_probability=0.75,
                observed_tp_before_sl_rate=0.73,
                confidence_low=0.64,
                confidence_high=0.81,
            ),
        )
        metrics = CalibrationMetrics(
            sample_count=200,
            brier_score=0.16,
            log_loss=0.48,
            expected_calibration_error=0.03,
            reliability_bins=bins,
        )
        artifact = CalibrationArtifact(
            calibration_id=risk_fixtures.RiskEngineTests.quant.calibration_id,
            version="platt-calibration-v1",
            meta_model_id=risk_fixtures.RiskEngineTests.quant.meta_model_id,
            source_validation_run_id="9" * 64,
            created_at=NOW,
            status=CalibrationStatus.AVAILABLE,
            method=CalibrationMethod.PLATT,
            reason="Chronological OOS calibration is available",
            sample_count=200,
            tp_before_sl_count=120,
            sl_before_tp_count=80,
            fit_sample_count=140,
            evaluation_sample_count=60,
            fit_start=NOW - timedelta(days=200),
            fit_end=NOW - timedelta(days=61),
            evaluation_start=NOW - timedelta(days=60),
            evaluation_end=NOW - timedelta(days=1),
            pre_calibration_metrics=metrics,
            post_calibration_metrics=metrics,
            platt_slope=1.0,
            platt_intercept=0.0,
            uncertainty=CalibrationUncertainty.LOW,
            high_probability_rate=0.02,
            calibration_audit_required=False,
            atlas_probability_available=True,
        )
        cls.base.upsert(
            CALIBRATION_ARTIFACT_COLLECTION,
            artifact.calibration_id,
            artifact.model_dump(mode="json"),
        )
        cls.calibrated = CalibratedProbability(
            calibrated_probability_id=risk_fixtures.RiskEngineTests.quant.calibrated_probability_id,
            calibration_id=artifact.calibration_id,
            meta_prediction_id=cls.meta.meta_prediction_id,
            atlas_probability=0.85,
            uncertainty=CalibrationUncertainty.LOW,
            evidence_status=CalibrationStatus.AVAILABLE,
            meets_80_percent_research_threshold=True,
        )
        cls.base.upsert(
            CALIBRATED_PROBABILITY_COLLECTION,
            cls.calibrated.calibrated_probability_id,
            cls.calibrated.model_dump(mode="json"),
        )
        reviews = tuple(
            SpecialistReviewRecord(
                review_id=stable_key("signal-ai", role.value),
                role=role,
                provider="ollama",
                model=role.value.lower(),
                payload=SpecialistReviewPayload(
                    candidate=risk_fixtures.RiskEngineTests.candidate.direction,
                    supports=(f"{role.value} supports the supplied candidate evidence",),
                    review_status=AIReviewStatus.SUPPORTS,
                ),
                latency_ms=10,
                input_tokens=20,
                output_tokens=10,
            )
            for role in AIReviewerRole
        )
        cls.ai_bundle = AIReviewBundle(
            bundle_id=stable_key("signal-ai-bundle"),
            candidate=risk_fixtures.RiskEngineTests.candidate,
            evidence_hash="8" * 64,
            status=AIReviewBundleStatus.COMPLETE,
            reviews=reviews,
            mandatory_adversarial_review_present=True,
            input_tokens=80,
            output_tokens=40,
            total_latency_ms=40,
        )
        cls.base.upsert(
            AI_REVIEW_COLLECTION,
            cls.ai_bundle.bundle_id,
            cls.ai_bundle.model_dump(mode="json"),
        )

    def store(self) -> MemoryRecordStore:
        result = MemoryRecordStore()
        for collection in COLLECTIONS:
            for row in self.base.values(collection):
                key = str(row[COLLECTION_KEYS[collection]])
                result.upsert(collection, key, row)
        return result

    def request(self, *, with_ai: bool = False) -> SignalPublicationRequest:
        return SignalPublicationRequest(
            risk_overlay_id=risk_fixtures.RiskEngineTests.risk.risk_overlay_id,
            feature_set_id=risk_fixtures.RiskEngineTests.feature_set.feature_set_id,
            ai_review_bundle_id=self.ai_bundle.bundle_id if with_ai else None,
        )

    def test_acceptance_a_sub_80_probability_waits_without_a_grade(self) -> None:
        store = self.store()
        candidate = risk_fixtures.RiskEngineTests.candidate.model_copy(
            update={"raw_probability_long": 0.94, "raw_direction_probability": 0.94}
        )
        meta = self.meta.model_copy(update={"raw_accept_probability": 0.89})
        calibrated = self.calibrated.model_copy(
            update={"atlas_probability": 0.78, "meets_80_percent_research_threshold": False}
        )
        quant = risk_fixtures.RiskEngineTests.quant.model_copy(update={"atlas_probability": 0.78})
        expectancy = risk_fixtures.RiskEngineTests.expectancy.model_copy(
            update={"probability_win": 0.78, "expected_value_net": 0.0114}
        )
        for collection, key, model in (
            (PRIMARY_CANDIDATE_COLLECTION, candidate.candidate_id, candidate),
            (META_PREDICTION_COLLECTION, meta.meta_prediction_id, meta),
            (CALIBRATED_PROBABILITY_COLLECTION, calibrated.calibrated_probability_id, calibrated),
            (QUANT_VALIDATION_COLLECTION, quant.quant_validation_id, quant),
            (EXPECTANCY_COLLECTION, expectancy.expectancy_id, expectancy),
        ):
            store.upsert(collection, key, model.model_dump(mode="json"))
        result = SignalPublicationGate(store).evaluate(self.request())
        self.assertEqual(result.decision, PublicationDecision.WAIT)
        self.assertFalse(result.signal_published)
        self.assertIsNone(result.grade)
        self.assertEqual(store.values(SIGNAL_COLLECTION), [])

    def test_acceptance_b_and_f_quant_veto_overrides_probability_and_ai(self) -> None:
        store = self.store()
        first = risk_fixtures.RiskEngineTests.quant.checks[0].model_copy(
            update={"status": QuantCheckStatus.VETO}
        )
        quant = risk_fixtures.RiskEngineTests.quant.model_copy(
            update={
                "decision": QuantDecision.VETO,
                "checks": (first, *risk_fixtures.RiskEngineTests.quant.checks[1:]),
                "quant_veto": True,
                "reason": "OOS evidence vetoed the candidate",
            }
        )
        store.upsert(
            QUANT_VALIDATION_COLLECTION,
            quant.quant_validation_id,
            quant.model_dump(mode="json"),
        )
        result = SignalPublicationGate(store).evaluate(self.request(with_ai=True))
        self.assertEqual(result.decision, PublicationDecision.QUANT_VETO)
        self.assertFalse(result.signal_published)
        self.assertFalse(result.ai_override_allowed)
        self.assertFalse(result.probability_override_allowed)

    def test_acceptance_c_negative_expectancy_is_no_trade(self) -> None:
        store = self.store()
        expectancy = risk_fixtures.RiskEngineTests.expectancy.model_copy(
            update={
                "status": ExpectancyStatus.NON_POSITIVE,
                "expected_value_net": -0.001,
                "positive_validated_expectancy": False,
                "reason": "Net expectancy is negative after costs",
            }
        )
        store.upsert(
            EXPECTANCY_COLLECTION,
            expectancy.expectancy_id,
            expectancy.model_dump(mode="json"),
        )
        result = SignalPublicationGate(store).evaluate(self.request())
        self.assertEqual(result.decision, PublicationDecision.NO_TRADE)
        self.assertFalse(result.signal_published)

    def test_acceptance_d_stale_mt5_is_data_degraded(self) -> None:
        store = self.store()
        snapshot = next(
            row
            for row in store.values(SNAPSHOT_COLLECTION)
            if row["snapshot_id"] == risk_fixtures.RiskEngineTests.targets.snapshot_id
        )
        snapshot["data_quality"]["status"] = DataQualityStatus.STALE.value
        snapshot["data_quality"]["publication_eligible"] = False
        snapshot["health"]["data_fresh"] = False
        store.upsert(SNAPSHOT_COLLECTION, snapshot["snapshot_id"], snapshot)
        result = SignalPublicationGate(store).evaluate(self.request())
        self.assertEqual(result.decision, PublicationDecision.DATA_DEGRADED)
        self.assertFalse(result.signal_published)

    def test_acceptance_e_meta_rejection_defeats_supporting_ai(self) -> None:
        store = self.store()
        meta = self.meta.model_copy(update={"research_classification": MetaTarget.REJECT})
        store.upsert(
            META_PREDICTION_COLLECTION,
            meta.meta_prediction_id,
            meta.model_dump(mode="json"),
        )
        result = SignalPublicationGate(store).evaluate(self.request(with_ai=True))
        self.assertEqual(result.decision, PublicationDecision.NO_TRADE)
        self.assertFalse(result.signal_published)

    def test_exact_grades_publish_and_revisions_never_mutate_signal(self) -> None:
        self.assertIsNone(grade_for_probability(0.7999))
        self.assertEqual(grade_for_probability(0.80), SignalGrade.B)
        self.assertEqual(grade_for_probability(0.849999), SignalGrade.B)
        self.assertEqual(grade_for_probability(0.85), SignalGrade.A)
        self.assertEqual(grade_for_probability(0.90), SignalGrade.A_PLUS)
        self.assertEqual(grade_for_probability(0.95), SignalGrade.A_FIVE_PLUS)
        store = self.store()
        gate = SignalPublicationGate(store)
        result = gate.evaluate(self.request(with_ai=True))
        self.assertEqual(result.decision, PublicationDecision.PUBLISHED)
        self.assertIsNotNone(result.signal)
        signal = result.signal
        assert signal is not None
        self.assertEqual(signal.grade, SignalGrade.A)
        self.assertTrue(signal.signal_authority)
        self.assertFalse(signal.execution_enabled)
        before = gate.get(signal.signal_id)
        revision = SignalRevisionService(store).revise(
            signal.signal_id,
            SignalRevisionRequest(
                kind=SignalRevisionKind.ANNOTATION,
                revised_by="quant-reviewer",
                reason="Add an audit annotation",
                annotation="The original geometry and probability remain unchanged.",
            ),
        )
        self.assertFalse(revision.original_signal_mutated)
        self.assertEqual(gate.get(signal.signal_id), before)
        self.assertEqual(len(store.values(SIGNAL_COLLECTION)), 1)
        self.assertEqual(len(store.values(SIGNAL_REVISION_COLLECTION)), 1)
        self.assertGreaterEqual(len(store.values(PUBLICATION_ATTEMPT_COLLECTION)), 1)

    def test_phase_11_api_exposes_signal_decisions_but_no_orders(self) -> None:
        class ReadyProbe:
            def healthy(self) -> bool:
                return True

        with patch.dict(
            os.environ,
            {
                "DATABASE_URL": "sqlite://",
                "REDIS_URL": "redis://127.0.0.1:6379/0",
                "MT5_ENABLED": "false",
                "V4_AI_REVIEW_ENABLED": "false",
            },
        ):
            settings = AtlasSettings.from_env()
        database = Database(settings.database_url)
        self.addCleanup(database.engine.dispose)
        application = create_app(settings, database, ReadyProbe(), FakeGateway())
        database.create_schema()
        with TestClient(application) as api:
            overview = api.get("/api/v4/health")
            missing = api.post(
                "/api/v4/signals/evaluate",
                json={
                    "risk_overlay_id": "a" * 64,
                    "feature_set_id": "b" * 64,
                },
            )
            forbidden_order = api.post("/api/v4/orders", json={})
        self.assertEqual(overview.json()["phase"], 15)
        self.assertEqual(missing.status_code, 404)
        self.assertEqual(forbidden_order.status_code, 404)


if __name__ == "__main__":
    unittest.main()
