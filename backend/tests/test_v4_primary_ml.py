from __future__ import annotations

import math
import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest.mock import patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.store import MemoryRecordStore
from app.v4.contracts import OfficialTimeframe, RegimeState
from app.v4.ml_contracts import (
    CandidateRequest,
    MLSample,
    MLTarget,
    ModelFamily,
    ModelGovernanceStatus,
    ModelTrainingConfig,
    ShadowPromotionRequest,
)
from app.v4.primary_ml import (
    MODEL_ARTIFACT_COLLECTION,
    MODEL_STATUS_COLLECTION,
    MODEL_TRAINING_COLLECTION,
    PrimaryMLEngine,
    PrimaryMLTrainer,
    PrimaryModelRegistry,
)
from app.v4.robustness import RobustnessValidator

NOW = datetime(2020, 1, 1, tzinfo=UTC)


def ml_samples(start_day: int, count: int, prefix: str) -> tuple[MLSample, ...]:
    rows = []
    for local_index in range(count):
        index = start_day + local_index
        signal = math.sin(index / 3) + 0.3 * math.cos(index / 7)
        target = MLTarget.LONG if signal >= 0 else MLTarget.SHORT
        forward_return = 0.01 if target is MLTarget.LONG else -0.01
        observed_at = NOW + timedelta(days=index)
        rows.append(
            MLSample(
                sample_id=f"{prefix}-{index}",
                instrument="XAUUSD",
                timeframe=OfficialTimeframe.H1,
                regime=RegimeState.TREND_UP,
                observed_at=observed_at,
                label_end_at=observed_at + timedelta(hours=6),
                features={
                    "momentum": signal,
                    "trend": signal * 0.8 + math.sin(index / 11) * 0.1,
                    "volatility": 0.5 + abs(math.cos(index / 9)),
                },
                target=target,
                forward_return_net=forward_return,
            )
        )
    return tuple(rows)


class PrimaryMLTrainingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryRecordStore()
        self.train = ml_samples(0, 120, "train")
        self.validation = ml_samples(121, 40, "validation")
        self.test = ml_samples(162, 40, "test")
        self.config = ModelTrainingConfig(epochs=250, extra_trees_estimators=32, random_seed=9)

    def test_trainer_creates_reproducible_experimental_artifacts_with_raw_oos_metrics(self) -> None:
        trainer = PrimaryMLTrainer(self.store)
        first = trainer.train(self.train, self.validation, self.test, self.config)
        second = trainer.train(self.train, self.validation, self.test, self.config)
        self.assertEqual(first, second)
        self.assertEqual({artifact.family for artifact in first.artifacts}, set(ModelFamily))
        self.assertEqual(len(self.store.values(MODEL_ARTIFACT_COLLECTION)), 2)
        self.assertEqual(len(self.store.values(MODEL_STATUS_COLLECTION)), 2)
        self.assertEqual(len(self.store.values(MODEL_TRAINING_COLLECTION)), 1)
        self.assertFalse(first.winner_automatically_promoted)
        self.assertFalse(first.holdout_used)
        for artifact in first.artifacts:
            self.assertEqual(artifact.initial_status, ModelGovernanceStatus.EXPERIMENTAL)
            self.assertFalse(artifact.test_metrics.calibrated)
            self.assertFalse(artifact.atlas_probability_authority)
            self.assertFalse(artifact.execution_enabled)
            self.assertGreater(artifact.test_metrics.balanced_accuracy or 0, 0.5)

    def test_dataset_overlap_and_schema_drift_fail_closed(self) -> None:
        overlapping_validation = tuple(
            sample.model_copy(update={"observed_at": sample.observed_at - timedelta(days=2)})
            for sample in self.validation
        )
        with self.assertRaises(ValueError):
            PrimaryMLTrainer(self.store).train(self.train, overlapping_validation, self.test, self.config)

        drifted = list(self.test)
        drifted[0] = drifted[0].model_copy(update={"features": {"different": 1.0}})
        with self.assertRaises(ValueError):
            PrimaryMLTrainer(self.store).train(self.train, self.validation, tuple(drifted), self.config)

    def test_manual_shadow_promotion_requires_positive_oos_and_governed_robustness(self) -> None:
        result = PrimaryMLTrainer(self.store).train(self.train, self.validation, self.test, self.config)
        winner = next(row for row in result.artifacts if row.model_id == result.validation_winner_model_id)
        robustness = RobustnessValidator(self.store).validate(
            tuple([0.01] * 60),
            bootstrap_samples=200,
            block_size=5,
            seed=3,
        )
        registry = PrimaryModelRegistry(self.store)
        request = ShadowPromotionRequest(
            approved_by="human-quant-reviewer",
            reason="Positive net OOS test and robustness interval reviewed",
            robustness_evidence_id=robustness.robustness_run_id,
        )
        promoted = registry.promote_to_shadow(winner.model_id, request, changed_at=self.test[-1].label_end_at)
        self.assertEqual(promoted.status, ModelGovernanceStatus.SHADOW)
        self.assertFalse(promoted.automatic)
        self.assertFalse(promoted.decision_influence)
        self.assertEqual(registry.current_status(winner.model_id).status, ModelGovernanceStatus.SHADOW)
        self.assertFalse(hasattr(registry, "promote_to_active"))

        with self.assertRaises(ValueError):
            registry.promote_to_shadow(winner.model_id, request, changed_at=self.test[-1].label_end_at)

    def test_objective_shadow_qualification_is_audited_but_never_grants_authority(self) -> None:
        result = PrimaryMLTrainer(self.store).train(self.train, self.validation, self.test, self.config)
        winner = next(row for row in result.artifacts if row.model_id == result.validation_winner_model_id)
        robustness = RobustnessValidator(self.store).validate(
            tuple([0.01] * 60),
            bootstrap_samples=200,
            block_size=5,
            seed=4,
        )

        qualified = PrimaryModelRegistry(self.store).qualify_for_shadow(
            winner.model_id,
            robustness.robustness_run_id,
            changed_at=self.test[-1].label_end_at,
        )

        self.assertEqual(qualified.status, ModelGovernanceStatus.SHADOW)
        self.assertTrue(qualified.automatic)
        self.assertEqual(qualified.approved_by, "ATLAS_V4_OBJECTIVE_SHADOW_POLICY")
        self.assertFalse(qualified.decision_influence)
        self.assertFalse(winner.atlas_probability_authority)
        self.assertFalse(winner.execution_enabled)

    def test_candidate_is_uncalibrated_non_publishable_and_requires_shadow_scope(self) -> None:
        result = PrimaryMLTrainer(self.store).train(self.train, self.validation, self.test, self.config)
        winner = next(row for row in result.artifacts if row.model_id == result.validation_winner_model_id)
        registry = PrimaryModelRegistry(self.store)
        candidate_request = CandidateRequest(
            model_id=winner.model_id,
            instrument="XAUUSD",
            timeframe=OfficialTimeframe.H1,
            regime=RegimeState.TREND_UP,
            observed_at=self.test[-1].observed_at + timedelta(days=1),
            features=self.test[-1].features,
        )
        with self.assertRaises(ValueError):
            PrimaryMLEngine(registry).generate_candidate(candidate_request)

        robustness = RobustnessValidator(self.store).validate(
            tuple([0.01] * 60),
            bootstrap_samples=100,
            block_size=5,
        )
        registry.promote_to_shadow(
            winner.model_id,
            ShadowPromotionRequest(
                approved_by="reviewer",
                reason="reviewed",
                robustness_evidence_id=robustness.robustness_run_id,
            ),
            changed_at=self.test[-1].label_end_at,
        )
        candidate = PrimaryMLEngine(registry).generate_candidate(candidate_request)
        self.assertIn(candidate.direction, set(MLTarget))
        self.assertGreaterEqual(candidate.raw_direction_probability, 0.5)
        self.assertFalse(candidate.calibrated)
        self.assertIsNone(candidate.atlas_probability)
        self.assertFalse(candidate.publication_eligible)
        self.assertFalse(candidate.execution_enabled)

        bad_features = candidate_request.model_copy(update={"features": {"momentum": 1.0}})
        with self.assertRaises(ValueError):
            PrimaryMLEngine(registry).generate_candidate(bad_features)


class PrimaryMLApiTests(unittest.TestCase):
    def test_api_trains_experimental_then_allows_manual_shadow_but_never_active(self) -> None:
        from fastapi.testclient import TestClient

        from app.core.config import AtlasSettings
        from app.main import create_app
        from app.models.database import Database
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
                "V4_CONTEXT_REFRESH_ENABLED": "false",
            },
        ):
            settings = AtlasSettings.from_env()
        database = Database(settings.database_url)
        self.addCleanup(database.engine.dispose)
        application = create_app(settings, database, ReadyProbe(), FakeGateway())
        database.create_schema()
        train = ml_samples(0, 120, "train")
        validation = ml_samples(121, 40, "validation")
        test = ml_samples(162, 40, "test")
        training_payload = {
            "train": [sample.model_dump(mode="json") for sample in train],
            "validation": [sample.model_dump(mode="json") for sample in validation],
            "test": [sample.model_dump(mode="json") for sample in test],
            "config": ModelTrainingConfig(epochs=150, extra_trees_estimators=24).model_dump(mode="json"),
        }
        with TestClient(application) as client:
            trained = client.post("/api/v4/models/train", json=training_payload)
            self.assertEqual(trained.status_code, 200, trained.text)
            winner_id = trained.json()["validation_winner_model_id"]
            artifact = client.get(f"/api/v4/models/{winner_id}")
            initial = client.get(f"/api/v4/models/{winner_id}/status")
            blocked_candidate = client.post(
                "/api/v4/models/candidates",
                json={
                    "model_id": winner_id,
                    "instrument": "XAUUSD",
                    "timeframe": "1H",
                    "regime": "TREND_UP",
                    "observed_at": (test[-1].observed_at + timedelta(days=1)).isoformat(),
                    "features": test[-1].features,
                },
            )
            robust = client.post(
                "/api/v4/quant/robustness",
                json={"returns": [0.01] * 60, "bootstrap_samples": 100, "block_size": 5},
            )
            promoted = client.post(
                f"/api/v4/models/{winner_id}/shadow",
                json={
                    "approved_by": "api-human-reviewer",
                    "reason": "reviewed positive OOS evidence",
                    "robustness_evidence_id": robust.json()["robustness_run_id"],
                },
            )
            candidate = client.post(
                "/api/v4/models/candidates",
                json={
                    "model_id": winner_id,
                    "instrument": "XAUUSD",
                    "timeframe": "1H",
                    "regime": "TREND_UP",
                    "observed_at": (test[-1].observed_at + timedelta(days=1)).isoformat(),
                    "features": test[-1].features,
                },
            )
            active = client.post(f"/api/v4/models/{winner_id}/active", json={})

        self.assertEqual(artifact.status_code, 200)
        self.assertEqual(initial.json()["status"], "EXPERIMENTAL")
        self.assertEqual(blocked_candidate.status_code, 409)
        self.assertEqual(promoted.status_code, 200, promoted.text)
        self.assertEqual(promoted.json()["status"], "SHADOW")
        self.assertFalse(promoted.json()["decision_influence"])
        self.assertEqual(candidate.status_code, 200, candidate.text)
        self.assertFalse(candidate.json()["calibrated"])
        self.assertIsNone(candidate.json()["atlas_probability"])
        self.assertFalse(candidate.json()["publication_eligible"])
        self.assertEqual(active.status_code, 404)


if __name__ == "__main__":
    unittest.main()
