"""Purged walk-forward validation, sealed holdouts, and experiment registry."""

from __future__ import annotations

from datetime import UTC, datetime
from itertools import pairwise

from app.core.store import RecordStore, stable_key
from app.v4.quant_contracts import (
    ExperimentRecord,
    ExperimentSpec,
    HoldoutAccessRecord,
    HoldoutManifest,
    ValidationSample,
    WalkForwardConfig,
    WalkForwardFold,
    WalkForwardMode,
    WalkForwardResult,
)

EXPERIMENT_COLLECTION = "v4_quant_experiments"
HOLDOUT_MANIFEST_COLLECTION = "v4_holdout_manifests"
HOLDOUT_PAYLOAD_COLLECTION = "v4_holdout_payloads"
HOLDOUT_ACCESS_COLLECTION = "v4_holdout_access"


class WalkForwardValidator:
    """Create chronological folds with label-overlap purging and explicit embargo gaps."""

    version = "purged-walk-forward-v1"

    def split(
        self,
        samples: tuple[ValidationSample, ...],
        config: WalkForwardConfig,
    ) -> WalkForwardResult:
        _validate_samples(samples)
        required = config.train_size + config.validation_size + config.test_size + 2 * config.embargo_size
        if len(samples) < required:
            raise ValueError(f"walk-forward validation requires at least {required} samples")
        sample_hash = stable_key([sample.model_dump(mode="json") for sample in samples])
        folds: list[WalkForwardFold] = []
        cursor = config.train_size
        while True:
            validation_start_index = _advance_past_timestamp(
                samples,
                cursor + config.embargo_size,
            )
            validation_end_index = validation_start_index + config.validation_size
            test_start_index = _advance_past_timestamp(
                samples,
                validation_end_index + config.embargo_size,
            )
            test_end_index = test_start_index + config.test_size
            if test_end_index > len(samples):
                break
            train_start_index = 0 if config.mode is WalkForwardMode.EXPANDING else cursor - config.train_size
            raw_train = samples[train_start_index:cursor]
            raw_validation = samples[validation_start_index:validation_end_index]
            raw_test = samples[test_start_index:test_end_index]
            validation_start = raw_validation[0].observed_at
            test_start = raw_test[0].observed_at
            train = tuple(sample for sample in raw_train if sample.label_end_at < validation_start)
            purged_train = tuple(sample for sample in raw_train if sample.label_end_at >= validation_start)
            validation = tuple(sample for sample in raw_validation if sample.label_end_at < test_start)
            purged_validation = tuple(sample for sample in raw_validation if sample.label_end_at >= test_start)
            if not train or not validation:
                raise ValueError("purging removed an entire train or validation window")
            embargoed = samples[cursor:validation_start_index] + samples[validation_end_index:test_start_index]
            folds.append(
                WalkForwardFold(
                    fold_index=len(folds),
                    train_ids=tuple(sample.sample_id for sample in train),
                    validation_ids=tuple(sample.sample_id for sample in validation),
                    test_ids=tuple(sample.sample_id for sample in raw_test),
                    purged_train_ids=tuple(sample.sample_id for sample in purged_train),
                    purged_validation_ids=tuple(sample.sample_id for sample in purged_validation),
                    embargoed_ids=tuple(sample.sample_id for sample in embargoed),
                    train_start=train[0].observed_at,
                    train_end=train[-1].observed_at,
                    validation_start=validation[0].observed_at,
                    validation_end=validation[-1].observed_at,
                    test_start=raw_test[0].observed_at,
                    test_end=raw_test[-1].observed_at,
                )
            )
            cursor += config.step_size
        if not folds:
            raise ValueError("walk-forward configuration produced no complete folds")
        run_id = stable_key(self.version, config.model_dump(mode="json"), sample_hash)
        return WalkForwardResult(
            validation_run_id=run_id,
            config=config,
            sample_hash=sample_hash,
            folds=tuple(folds),
        )


class HoldoutVault:
    """Seal a final chronological holdout and permit one logged final-evaluation access."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def seal(
        self,
        samples: tuple[ValidationSample, ...],
        *,
        holdout_size: int,
        created_at: datetime | None = None,
    ) -> tuple[tuple[ValidationSample, ...], HoldoutManifest]:
        _validate_samples(samples)
        if not 5 <= holdout_size < len(samples):
            raise ValueError("holdout_size must be at least 5 and smaller than the dataset")
        development = samples[:-holdout_size]
        holdout = samples[-holdout_size:]
        content_hash = stable_key([sample.model_dump(mode="json") for sample in holdout])
        holdout_id = stable_key("sealed-holdout-v1", content_hash, holdout[0].observed_at.isoformat())
        manifest = HoldoutManifest(
            holdout_id=holdout_id,
            created_at=(created_at or datetime.now(UTC)).astimezone(UTC),
            sample_count=len(holdout),
            start_at=holdout[0].observed_at,
            end_at=holdout[-1].observed_at,
            content_hash=content_hash,
        )
        self.store.upsert(HOLDOUT_MANIFEST_COLLECTION, holdout_id, manifest.model_dump(mode="json"))
        self.store.upsert(
            HOLDOUT_PAYLOAD_COLLECTION,
            holdout_id,
            {"holdout_id": holdout_id, "samples": [sample.model_dump(mode="json") for sample in holdout]},
        )
        return development, manifest

    def open_final_evaluation(
        self,
        holdout_id: str,
        *,
        experiment_id: str,
        confirmed: bool,
        accessed_at: datetime | None = None,
    ) -> tuple[tuple[ValidationSample, ...], HoldoutAccessRecord]:
        if not confirmed:
            raise PermissionError("final holdout access requires explicit confirmation")
        if any(row.get("holdout_id") == holdout_id for row in self.store.values(HOLDOUT_ACCESS_COLLECTION)):
            raise PermissionError("final holdout has already been accessed")
        payload = next(
            (row for row in self.store.values(HOLDOUT_PAYLOAD_COLLECTION) if row.get("holdout_id") == holdout_id),
            None,
        )
        if payload is None or not isinstance(payload.get("samples"), list):
            raise KeyError("sealed holdout does not exist")
        resolved_at = (accessed_at or datetime.now(UTC)).astimezone(UTC)
        access_id = stable_key(holdout_id, experiment_id, resolved_at.isoformat())
        record = HoldoutAccessRecord(
            access_id=access_id,
            holdout_id=holdout_id,
            experiment_id=experiment_id,
            accessed_at=resolved_at,
        )
        self.store.upsert(HOLDOUT_ACCESS_COLLECTION, access_id, record.model_dump(mode="json"))
        samples = tuple(ValidationSample.model_validate(row) for row in payload["samples"])
        return samples, record


class ExperimentRegistry:
    """Register every research variant without granting promotion authority."""

    def __init__(self, store: RecordStore) -> None:
        self.store = store

    def register(self, spec: ExperimentSpec, *, registered_at: datetime | None = None) -> ExperimentRecord:
        config_hash = stable_key(spec.model_dump(mode="json"))
        experiment_id = stable_key("v4-experiment-v1", config_hash)
        existing = next(
            (row for row in self.store.values(EXPERIMENT_COLLECTION) if row.get("experiment_id") == experiment_id),
            None,
        )
        if existing is not None:
            return ExperimentRecord.model_validate(existing)
        total_variants = (
            len(spec.feature_sets)
            * len(spec.model_families)
            * spec.parameter_variants
            * spec.threshold_variants
        )
        record = ExperimentRecord(
            experiment_id=experiment_id,
            registered_at=(registered_at or datetime.now(UTC)).astimezone(UTC),
            config_hash=config_hash,
            spec=spec,
            total_variants=total_variants,
        )
        self.store.upsert(EXPERIMENT_COLLECTION, experiment_id, record.model_dump(mode="json"))
        return record


def _validate_samples(samples: tuple[ValidationSample, ...]) -> None:
    if len({sample.sample_id for sample in samples}) != len(samples):
        raise ValueError("validation sample IDs must be unique")
    if any(current.observed_at < previous.observed_at for previous, current in pairwise(samples)):
        raise ValueError("validation samples must be chronologically ordered")


def _advance_past_timestamp(
    samples: tuple[ValidationSample, ...],
    index: int,
) -> int:
    """Keep equal-time cross-asset observations inside one fold boundary."""

    if index <= 0 or index >= len(samples):
        return index
    previous_time = samples[index - 1].observed_at
    while index < len(samples) and samples[index].observed_at == previous_time:
        index += 1
    return index


__all__ = [
    "EXPERIMENT_COLLECTION",
    "HOLDOUT_ACCESS_COLLECTION",
    "HOLDOUT_MANIFEST_COLLECTION",
    "HOLDOUT_PAYLOAD_COLLECTION",
    "ExperimentRegistry",
    "HoldoutVault",
    "WalkForwardValidator",
]
