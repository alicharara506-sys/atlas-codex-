from __future__ import annotations

import math
import os
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path
from statistics import fmean
from typing import cast
from unittest.mock import Mock, patch

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.store import MemoryRecordStore, RecordStore, stable_key
from app.v4.contracts import OfficialTimeframe, RegimeState
from app.v4.historical_meta import HistoricalMetaResearchService
from app.v4.meta_contracts import (
    EvidenceFamily,
    EvidenceObservation,
    MetaFeatureVector,
    MetaLabelSample,
    MetaPredictionRequest,
    MetaSession,
    MetaTarget,
    MetaTrainingConfig,
)
from app.v4.meta_labeling import (
    EVIDENCE_DEPENDENCY_COLLECTION,
    META_MODEL_COLLECTION,
    META_PREDICTION_COLLECTION,
    EvidenceDependencyEngine,
    MetaLabelEngine,
    MetaLabelTrainer,
)
from app.v4.ml_contracts import (
    MLTarget,
    ModelGovernanceStatus,
    PrimaryCandidate,
    PrimaryModelArtifact,
)
from app.v4.primary_ml import PRIMARY_CANDIDATE_COLLECTION
from app.v4.quant_contracts import WalkForwardConfig

NOW = datetime(2021, 1, 1, tzinfo=UTC)


def observations() -> tuple[EvidenceObservation, ...]:
    return (
        EvidenceObservation(
            evidence_id="rsi",
            name="RSI momentum",
            family=EvidenceFamily.MOMENTUM,
            value=0.7,
            upstream_ids=("xau-h1-close",),
        ),
        EvidenceObservation(
            evidence_id="llm-rsi",
            name="LLM summary of RSI",
            family=EvidenceFamily.NEWS,
            value=0.8,
            upstream_ids=("xau-h1-close", "rsi"),
        ),
        EvidenceObservation(
            evidence_id="bos",
            name="Confirmed BOS",
            family=EvidenceFamily.STRUCTURE,
            value=1,
            upstream_ids=("xau-h1-structure",),
        ),
    )


def meta_dataset(
    store: RecordStore,
    dependency_report_id: str,
    *,
    count: int = 180,
    prefix: str = "meta",
) -> tuple[MetaLabelSample, ...]:
    rows: list[MetaLabelSample] = []
    for index in range(count):
        observed_at = NOW + timedelta(days=index)
        accepted = index % 4 in {1, 2}
        event_risk = 0.08 if accepted else 0.88
        disagreement = 0.1 if accepted else 0.8
        mtf_conflict = 0.12 if accepted else 0.75
        raw_probability = 0.58 + (index % 3) * 0.08
        direction = MLTarget.LONG if index % 2 == 0 else MLTarget.SHORT
        candidate_id = stable_key(prefix, "candidate", index)
        candidate = PrimaryCandidate(
            candidate_id=candidate_id,
            model_id="a" * 64,
            instrument="XAUUSD",
            timeframe=OfficialTimeframe.H1,
            regime=RegimeState.TREND_UP if accepted else RegimeState.TRANSITION,
            observed_at=observed_at,
            direction=direction,
            raw_probability_long=(raw_probability if direction is MLTarget.LONG else 1 - raw_probability),
            raw_direction_probability=raw_probability,
        )
        store.upsert(
            PRIMARY_CANDIDATE_COLLECTION,
            candidate.candidate_id,
            candidate.model_dump(mode="json"),
        )
        features = MetaFeatureVector(
            raw_primary_probability=raw_probability,
            model_disagreement=disagreement,
            regime=candidate.regime,
            volatility_percentile=0.4 + 0.1 * math.sin(index / 9),
            spread_bps=1.2 + (index % 5) * 0.1,
            session=tuple(MetaSession)[index % len(MetaSession)],
            event_risk=event_risk,
            macro_state=0.4 if accepted else -0.25,
            mtf_conflict=mtf_conflict,
            data_quality_score=98 if accepted else 82,
            ai_contradiction_ratio=0,
            positioning_zscore=0.2 if index % 3 else None,
            liquidity_score=0.8 if accepted else 0.3,
            evidence_dependency_report_id=dependency_report_id,
        )
        rows.append(
            MetaLabelSample(
                sample_id=f"{prefix}-{index}",
                candidate_id=candidate_id,
                observed_at=observed_at,
                label_end_at=observed_at + timedelta(hours=6),
                primary_direction=direction,
                features=features,
                triple_barrier_label_id=stable_key(prefix, "barrier", index),
                tp_before_sl=accepted,
                target=MetaTarget.ACCEPT if accepted else MetaTarget.REJECT,
                forward_return_net=0.012 if accepted else -0.018,
            )
        )
    return tuple(rows)


def training_config() -> MetaTrainingConfig:
    return MetaTrainingConfig(
        walk_forward=WalkForwardConfig(
            train_size=80,
            validation_size=20,
            test_size=20,
            step_size=20,
            embargo_size=2,
        ),
        epochs=250,
        learning_rate=0.05,
    )


class EvidenceDependencyTests(unittest.TestCase):
    def test_shared_upstream_is_one_component_not_multiple_confirmations(self) -> None:
        store = MemoryRecordStore()
        report = EvidenceDependencyEngine(store).analyze(observations())
        self.assertEqual(report.raw_observation_count, 3)
        self.assertEqual(report.independent_component_count, 2)
        self.assertEqual(report.redundant_groups, (("llm-rsi", "rsi"),))
        self.assertAlmostEqual(report.redundancy_ratio, 1 / 3)
        self.assertFalse(report.arbitrary_confirmation_score_created)
        self.assertFalse(report.signal_authority)
        self.assertEqual(len(store.values(EVIDENCE_DEPENDENCY_COLLECTION)), 1)


class MetaLabelingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.store = MemoryRecordStore()
        self.report = EvidenceDependencyEngine(self.store).analyze(observations())
        self.samples = meta_dataset(self.store, self.report.report_id)

    def test_training_is_reproducible_purged_oos_and_never_promoted(self) -> None:
        trainer = MetaLabelTrainer(self.store)
        first = trainer.train(self.samples, training_config())
        second = trainer.train(self.samples, training_config())
        self.assertEqual(first, second)
        artifact = first.artifact
        self.assertEqual(artifact.initial_status, ModelGovernanceStatus.EXPERIMENTAL)
        self.assertTrue(artifact.walk_forward.purging_enabled)
        self.assertFalse(artifact.walk_forward.ordinary_shuffled_kfold_used)
        self.assertGreaterEqual(len(artifact.folds), 3)
        self.assertTrue(artifact.material_oos_net_improvement)
        self.assertGreater(artifact.aggregate_oos_metrics.net_improvement, 0)
        accepted_returns = tuple(
            row.forward_return_net
            for row in artifact.oos_predictions
            if row.raw_accept_probability >= artifact.config.decision_threshold
        )
        self.assertAlmostEqual(
            artifact.aggregate_oos_metrics.filtered_mean_net_return,
            fmean(accepted_returns),
        )
        self.assertFalse(artifact.aggregate_oos_metrics.calibrated)
        self.assertFalse(artifact.threshold_search_performed)
        self.assertFalse(artifact.automatic_promotion)
        self.assertFalse(artifact.atlas_probability_authority)
        self.assertFalse(artifact.publication_authority)
        self.assertFalse(artifact.execution_enabled)
        self.assertEqual(len(self.store.values(META_MODEL_COLLECTION)), 1)

    def test_historical_pool_keeps_earliest_point_in_time_forecast(self) -> None:
        original = self.samples[0]
        original_candidate = PrimaryCandidate.model_validate(
            next(
                row
                for row in self.store.values(PRIMARY_CANDIDATE_COLLECTION)
                if row["candidate_id"] == original.candidate_id
            )
        )
        newer_candidate = original_candidate.model_copy(
            update={
                "candidate_id": stable_key("newer-duplicate-candidate"),
                "model_id": "b" * 64,
            }
        )
        duplicate = original.model_copy(
            update={
                "sample_id": "newer-duplicate-sample",
                "candidate_id": newer_candidate.candidate_id,
            }
        )
        artifacts = cast(
            dict[str, PrimaryModelArtifact],
            {
                original_candidate.model_id: Mock(created_at=NOW + timedelta(days=1)),
                newer_candidate.model_id: Mock(created_at=NOW + timedelta(days=2)),
            },
        )

        selected = HistoricalMetaResearchService._deduplicate_point_in_time_rows(
            [duplicate, self.samples[1], original],
            {
                original_candidate.candidate_id: original_candidate,
                newer_candidate.candidate_id: newer_candidate,
                self.samples[1].candidate_id: PrimaryCandidate.model_validate(
                    next(
                        row
                        for row in self.store.values(PRIMARY_CANDIDATE_COLLECTION)
                        if row["candidate_id"] == self.samples[1].candidate_id
                    )
                ),
            },
            artifacts,
        )

        self.assertEqual({row.sample_id for row in selected}, {original.sample_id, self.samples[1].sample_id})

    def test_lineage_and_time_order_fail_closed(self) -> None:
        broken = list(self.samples)
        broken[0] = broken[0].model_copy(
            update={"features": broken[0].features.model_copy(update={"evidence_dependency_report_id": "f" * 64})}
        )
        with self.assertRaises(ValueError):
            MetaLabelTrainer(self.store).train(tuple(broken), training_config())

        out_of_order = list(self.samples)
        out_of_order[10], out_of_order[11] = out_of_order[11], out_of_order[10]
        with self.assertRaises(ValueError):
            MetaLabelTrainer(self.store).train(tuple(out_of_order), training_config())

    def test_prediction_is_raw_non_publishable_and_matches_candidate_lineage(self) -> None:
        artifact = MetaLabelTrainer(self.store).train(self.samples, training_config()).artifact
        future = meta_dataset(
            self.store,
            self.report.report_id,
            count=1,
            prefix="future",
        )[0]
        future_at = artifact.created_at + timedelta(days=2)
        candidate_row = next(
            row for row in self.store.values(PRIMARY_CANDIDATE_COLLECTION) if row["candidate_id"] == future.candidate_id
        )
        candidate = PrimaryCandidate.model_validate(candidate_row).model_copy(update={"observed_at": future_at})
        self.store.upsert(
            PRIMARY_CANDIDATE_COLLECTION,
            candidate.candidate_id,
            candidate.model_dump(mode="json"),
        )
        request = MetaPredictionRequest(
            meta_model_id=artifact.meta_model_id,
            candidate_id=candidate.candidate_id,
            observed_at=future_at + timedelta(seconds=1),
            features=future.features,
        )
        prediction = MetaLabelEngine(self.store).predict(request)
        self.assertGreaterEqual(prediction.raw_accept_probability, 0)
        self.assertLessEqual(prediction.raw_accept_probability, 1)
        self.assertFalse(prediction.calibrated)
        self.assertIsNone(prediction.atlas_probability)
        self.assertFalse(prediction.decision_authority)
        self.assertFalse(prediction.publication_eligible)
        self.assertFalse(prediction.execution_enabled)
        self.assertEqual(len(self.store.values(META_PREDICTION_COLLECTION)), 1)

        mismatch = request.model_copy(
            update={"features": request.features.model_copy(update={"raw_primary_probability": 0.99})}
        )
        with self.assertRaises(ValueError):
            MetaLabelEngine(self.store).predict(mismatch)

    def test_fixed_artifact_is_loaded_once_across_repeated_scores(self) -> None:
        artifact = MetaLabelTrainer(self.store).train(self.samples, training_config()).artifact
        engine = MetaLabelEngine(self.store)

        with patch.object(self.store, "values", wraps=self.store.values) as values:
            first = engine.score_features(artifact.meta_model_id, self.samples[0].features)
            second = engine.score_features(artifact.meta_model_id, self.samples[1].features)

        model_collection_calls = [
            call
            for call in values.call_args_list
            if call.args == (META_MODEL_COLLECTION,)
        ]
        self.assertEqual(len(model_collection_calls), 1)
        self.assertGreaterEqual(first, 0)
        self.assertLessEqual(first, 1)
        self.assertGreaterEqual(second, 0)
        self.assertLessEqual(second, 1)


class MetaLabelingApiTests(unittest.TestCase):
    def test_research_api_trains_predicts_and_has_no_promotion_route(self) -> None:
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
                "V4_CONTEXT_REFRESH_ENABLED": "false",
                "V4_AI_REVIEW_ENABLED": "false",
            },
        ):
            settings = AtlasSettings.from_env()
        database = Database(settings.database_url)
        self.addCleanup(database.engine.dispose)
        application = create_app(settings, database, ReadyProbe(), FakeGateway())
        database.create_schema()
        store = SqlModelRecordStore(database)
        with TestClient(application) as api:
            dependency = api.post(
                "/api/v4/evidence/dependencies",
                json={"observations": [row.model_dump(mode="json") for row in observations()]},
            )
            samples = meta_dataset(store, dependency.json()["report_id"])
            trained = api.post(
                "/api/v4/meta/train",
                json={
                    "samples": [sample.model_dump(mode="json") for sample in samples],
                    "config": training_config().model_dump(mode="json"),
                },
            )
            artifact_id = trained.json()["artifact"]["meta_model_id"]
            artifact = api.get(f"/api/v4/meta/models/{artifact_id}")

            future_at = datetime.fromisoformat(trained.json()["artifact"]["created_at"]) + timedelta(days=2)
            direction = MLTarget.LONG
            candidate = PrimaryCandidate(
                candidate_id=stable_key("api-future-candidate"),
                model_id="b" * 64,
                instrument="XAUUSD",
                timeframe=OfficialTimeframe.H1,
                regime=RegimeState.TREND_UP,
                observed_at=future_at,
                direction=direction,
                raw_probability_long=0.74,
                raw_direction_probability=0.74,
            )
            store.upsert(
                PRIMARY_CANDIDATE_COLLECTION,
                candidate.candidate_id,
                candidate.model_dump(mode="json"),
            )
            prediction_features = samples[1].features.model_copy(update={"raw_primary_probability": 0.74})
            predicted = api.post(
                "/api/v4/meta/predictions",
                json={
                    "meta_model_id": artifact_id,
                    "candidate_id": candidate.candidate_id,
                    "observed_at": (future_at + timedelta(seconds=1)).isoformat(),
                    "features": prediction_features.model_dump(mode="json"),
                },
            )
            forbidden = api.post(f"/api/v4/meta/models/{artifact_id}/promote", json={})

        self.assertEqual(dependency.status_code, 200, dependency.text)
        self.assertEqual(trained.status_code, 200, trained.text)
        self.assertEqual(trained.json()["artifact"]["initial_status"], "EXPERIMENTAL")
        self.assertEqual(artifact.status_code, 200)
        self.assertEqual(predicted.status_code, 200, predicted.text)
        self.assertFalse(predicted.json()["calibrated"])
        self.assertIsNone(predicted.json()["atlas_probability"])
        self.assertFalse(predicted.json()["publication_eligible"])
        self.assertEqual(forbidden.status_code, 404)


if __name__ == "__main__":
    unittest.main()
