from __future__ import annotations

import os
import sys
import unittest
from datetime import timedelta
from pathlib import Path
from typing import ClassVar
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.store import MemoryRecordStore, stable_key
from app.v4.calibration import (
    CALIBRATED_PROBABILITY_COLLECTION,
    CALIBRATION_ARTIFACT_COLLECTION,
    ProbabilityCalibrationEngine,
)
from app.v4.calibration_contracts import (
    PROBABILITY_SEMANTICS,
    CalibratedProbabilityRequest,
    CalibrationArtifact,
    CalibrationMethod,
    CalibrationStatus,
)
from app.v4.contracts import OfficialTimeframe
from app.v4.meta_contracts import (
    EvidenceDependencyReport,
    MetaLabelSample,
    MetaModelArtifact,
    MetaPrediction,
    MetaPredictionRequest,
    MetaTrainingConfig,
)
from app.v4.meta_labeling import (
    META_MODEL_COLLECTION,
    META_PREDICTION_COLLECTION,
    EvidenceDependencyEngine,
    MetaLabelEngine,
    MetaLabelTrainer,
)
from app.v4.ml_contracts import MLTarget, PrimaryCandidate
from app.v4.primary_ml import PRIMARY_CANDIDATE_COLLECTION
from app.v4.quant_contracts import WalkForwardConfig
from tests.test_v4_meta_labeling import meta_dataset, observations


class CalibrationTests(unittest.TestCase):
    store: ClassVar[MemoryRecordStore]
    dependency: ClassVar[EvidenceDependencyReport]
    samples: ClassVar[tuple[MetaLabelSample, ...]]
    meta_config: ClassVar[MetaTrainingConfig]
    meta_artifact: ClassVar[MetaModelArtifact]
    engine: ClassVar[ProbabilityCalibrationEngine]
    calibration: ClassVar[CalibrationArtifact]
    meta_prediction: ClassVar[MetaPrediction]

    @classmethod
    def setUpClass(cls) -> None:
        cls.store = MemoryRecordStore()
        cls.dependency = EvidenceDependencyEngine(cls.store).analyze(observations())
        cls.samples = meta_dataset(cls.store, cls.dependency.report_id, count=360, prefix="calibration")
        cls.meta_config = MetaTrainingConfig(
            walk_forward=WalkForwardConfig(
                train_size=80,
                validation_size=20,
                test_size=20,
                step_size=20,
                embargo_size=2,
            ),
            epochs=50,
        )
        cls.meta_artifact = MetaLabelTrainer(cls.store).train(
            cls.samples,
            cls.meta_config,
        ).artifact
        cls.engine = ProbabilityCalibrationEngine(cls.store)
        cls.calibration = cls.engine.train(cls.meta_artifact.meta_model_id)

        future_at = cls.meta_artifact.created_at + timedelta(days=2)
        candidate = PrimaryCandidate(
            candidate_id=stable_key("calibration-live-candidate"),
            model_id="c" * 64,
            instrument="XAUUSD",
            timeframe=OfficialTimeframe.H1,
            regime=cls.samples[1].features.regime,
            observed_at=future_at,
            direction=MLTarget.LONG,
            raw_probability_long=0.74,
            raw_direction_probability=0.74,
        )
        cls.store.upsert(
            PRIMARY_CANDIDATE_COLLECTION,
            candidate.candidate_id,
            candidate.model_dump(mode="json"),
        )
        features = cls.samples[1].features.model_copy(
            update={"raw_primary_probability": 0.74}
        )
        cls.meta_prediction = MetaLabelEngine(cls.store).predict(
            MetaPredictionRequest(
                meta_model_id=cls.meta_artifact.meta_model_id,
                candidate_id=candidate.candidate_id,
                observed_at=future_at + timedelta(seconds=1),
                features=features,
            )
        )

    def test_method_selection_and_exact_probability_semantics(self) -> None:
        self.assertEqual(ProbabilityCalibrationEngine.select_method(199), CalibrationMethod.NONE)
        self.assertEqual(ProbabilityCalibrationEngine.select_method(200), CalibrationMethod.PLATT)
        self.assertEqual(ProbabilityCalibrationEngine.select_method(500), CalibrationMethod.ISOTONIC)
        self.assertEqual(self.calibration.probability_semantics, PROBABILITY_SEMANTICS)
        self.assertFalse(self.calibration.raw_primary_probability_used_directly)
        self.assertFalse(self.calibration.llm_confidence_used)
        self.assertFalse(self.calibration.committee_vote_used)

    def test_available_artifact_uses_chronological_oos_evaluation_and_audit(self) -> None:
        artifact = self.calibration
        self.assertEqual(artifact.status, CalibrationStatus.AVAILABLE)
        self.assertEqual(artifact.method, CalibrationMethod.PLATT)
        self.assertGreaterEqual(artifact.sample_count, 200)
        self.assertGreaterEqual(artifact.tp_before_sl_count, 40)
        self.assertGreaterEqual(artifact.sl_before_tp_count, 40)
        fit_end = artifact.fit_end
        evaluation_start = artifact.evaluation_start
        assert fit_end is not None
        assert evaluation_start is not None
        self.assertLess(fit_end, evaluation_start)
        self.assertIsNotNone(artifact.pre_calibration_metrics)
        self.assertIsNotNone(artifact.post_calibration_metrics)
        post_metrics = artifact.post_calibration_metrics
        assert post_metrics is not None
        self.assertEqual(len(post_metrics.reliability_bins), 10)
        self.assertTrue(artifact.calibration_audit_required)
        self.assertFalse(artifact.publication_authority)
        self.assertFalse(artifact.execution_enabled)
        self.assertEqual(len(self.store.values(CALIBRATION_ARTIFACT_COLLECTION)), 1)

    def test_calibrated_output_has_no_grade_quant_or_publication_authority(self) -> None:
        output = self.engine.apply(
            CalibratedProbabilityRequest(
                calibration_id=self.calibration.calibration_id,
                meta_prediction_id=self.meta_prediction.meta_prediction_id,
            )
        )
        self.assertIsNotNone(output.atlas_probability)
        self.assertEqual(output.probability_semantics, PROBABILITY_SEMANTICS)
        self.assertIsNone(output.grade)
        self.assertFalse(output.quant_validated)
        self.assertFalse(output.publication_eligible)
        self.assertFalse(output.execution_enabled)
        self.assertEqual(len(self.store.values(CALIBRATED_PROBABILITY_COLLECTION)), 1)

    def test_identity_map_preserves_a_better_chronological_oos_probability(self) -> None:
        template = self.meta_artifact.oos_predictions[0]
        rows = []
        for index in range(600):
            in_fit_window = index < 420
            outcome = index % 5 == 0 if in_fit_window else index % 5 != 0
            observed_at = template.observed_at + timedelta(minutes=index * 2)
            rows.append(
                template.model_copy(
                    update={
                        "sample_id": stable_key("identity-calibration", index),
                        "observed_at": observed_at,
                        "label_end_at": observed_at + timedelta(minutes=1),
                        "raw_accept_probability": 0.2 if in_fit_window else 0.8,
                        "tp_before_sl": outcome,
                    }
                )
            )
        model_id = stable_key("identity-meta-artifact")
        artifact = self.meta_artifact.model_copy(
            update={"meta_model_id": model_id, "oos_predictions": tuple(rows)}
        )
        self.store.upsert(
            META_MODEL_COLLECTION,
            model_id,
            artifact.model_dump(mode="json"),
        )

        calibration = self.engine.train(model_id)

        self.assertEqual(calibration.method, CalibrationMethod.IDENTITY)
        self.assertFalse(calibration.calibration_audit_required)
        metrics = calibration.post_calibration_metrics
        assert metrics is not None
        self.assertAlmostEqual(metrics.expected_calibration_error, 0)

        prediction = self.meta_prediction.model_copy(
            update={
                "meta_prediction_id": stable_key("identity-live-prediction"),
                "meta_model_id": model_id,
                "raw_accept_probability": 0.81,
            }
        )
        self.store.upsert(
            META_PREDICTION_COLLECTION,
            prediction.meta_prediction_id,
            prediction.model_dump(mode="json"),
        )
        output = self.engine.apply(
            CalibratedProbabilityRequest(
                calibration_id=calibration.calibration_id,
                meta_prediction_id=prediction.meta_prediction_id,
            )
        )
        self.assertAlmostEqual(output.atlas_probability or 0, 0.81)

    def test_insufficient_oos_evidence_keeps_probability_unavailable(self) -> None:
        insufficient_id = stable_key("insufficient-meta-artifact")
        insufficient_meta = self.meta_artifact.model_copy(
            update={
                "meta_model_id": insufficient_id,
                "oos_predictions": self.meta_artifact.oos_predictions[:100],
            }
        )
        self.store.upsert(
            META_MODEL_COLLECTION,
            insufficient_id,
            insufficient_meta.model_dump(mode="json"),
        )
        artifact = self.engine.train(insufficient_id)
        self.assertEqual(artifact.status, CalibrationStatus.INSUFFICIENT_EVIDENCE)
        self.assertEqual(artifact.method, CalibrationMethod.NONE)
        self.assertFalse(artifact.atlas_probability_available)
        self.assertFalse(artifact.publication_authority)

    def test_calibration_api_is_typed_and_cannot_publish_or_grade(self) -> None:
        from fastapi.testclient import TestClient

        from app.core.config import AtlasSettings
        from app.main import create_app
        from app.models.database import Database, SqlModelRecordStore
        from tests.test_v4_market_foundation import FakeGateway

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
        store = SqlModelRecordStore(database)
        store.upsert(
            META_MODEL_COLLECTION,
            self.meta_artifact.meta_model_id,
            self.meta_artifact.model_dump(mode="json"),
        )
        store.upsert(
            META_PREDICTION_COLLECTION,
            self.meta_prediction.meta_prediction_id,
            self.meta_prediction.model_dump(mode="json"),
        )
        with TestClient(application) as api:
            overview = api.get("/api/v4/health")
            trained = api.post(
                "/api/v4/calibration/train",
                json={"meta_model_id": self.meta_artifact.meta_model_id},
            )
            calibration_id = trained.json()["calibration_id"]
            fetched = api.get(f"/api/v4/calibration/{calibration_id}")
            applied = api.post(
                "/api/v4/calibration/apply",
                json={
                    "calibration_id": calibration_id,
                    "meta_prediction_id": self.meta_prediction.meta_prediction_id,
                },
            )
            forbidden = api.post(
                f"/api/v4/calibration/{calibration_id}/publish",
                json={},
            )

        self.assertEqual(overview.json()["phase"], 15)
        self.assertEqual(trained.status_code, 200, trained.text)
        self.assertEqual(fetched.status_code, 200)
        self.assertEqual(applied.status_code, 200, applied.text)
        self.assertIsNone(applied.json()["grade"])
        self.assertFalse(applied.json()["quant_validated"])
        self.assertFalse(applied.json()["publication_eligible"])
        self.assertEqual(forbidden.status_code, 404)


if __name__ == "__main__":
    unittest.main()
