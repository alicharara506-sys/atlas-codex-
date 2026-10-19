"""Persistent, failure-isolated background orchestration for ATLAS V4."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from threading import RLock
from time import perf_counter

from pydantic import ValidationError

from app.core.config import AtlasSettings
from app.core.store import RecordStore, stable_key
from app.data.market.pipeline import FredEconomicProvider
from app.llm.client import LLMClient
from app.models.database import Database, SqlModelRecordStore
from app.v4.ai_contracts import (
    AIReviewBundle,
    AIReviewBundleStatus,
    AIReviewEvidence,
    AIReviewRequest,
    ScalarEvidence,
)
from app.v4.ai_review import AIReviewLLMClient, AIReviewOrchestrator
from app.v4.calibration import ProbabilityCalibrationEngine
from app.v4.calibration_contracts import CalibratedProbabilityRequest, CalibrationStatus
from app.v4.context_contracts import ContextComputationRequest, ContextSet
from app.v4.context_ingestion import V4ContextIngestionService
from app.v4.context_service import V4ContextService
from app.v4.contracts import DataQualityStatus, FeatureSet, MarketSnapshot, OfficialTimeframe, RegimeState
from app.v4.feature_service import FeatureComputationError, V4FeatureService
from app.v4.historical_meta import HistoricalMetaResearchService
from app.v4.historical_ml import (
    HistoricalDatasetError,
    HistoricalPrimaryDataset,
    HistoricalPrimaryDatasetBuilder,
)
from app.v4.kronos import KronosShadowEngine
from app.v4.kronos_contracts import KronosMode
from app.v4.kronos_provider import OfficialKronosProvider
from app.v4.meta_contracts import MetaModelArtifact, MetaPrediction, MetaPredictionRequest
from app.v4.meta_labeling import (
    META_MODEL_COLLECTION,
    META_PREDICTION_COLLECTION,
    EvidenceDependencyEngine,
    MetaLabelEngine,
    MetaLabelTrainer,
)
from app.v4.ml_contracts import (
    CandidateRequest,
    ModelGovernanceStatus,
    ModelStatusRecord,
    PrimaryCandidate,
    PrimaryModelArtifact,
)
from app.v4.mt5 import MT5GatewayProtocol
from app.v4.orchestration_contracts import (
    InstrumentOrchestrationResult,
    OrchestrationRun,
    OrchestrationRunState,
    OrchestrationStage,
    OrchestrationStageResult,
    OrchestrationStageState,
    OrchestrationStatus,
)
from app.v4.positioning import CFTCPositioningClient
from app.v4.primary_ml import (
    MODEL_ARTIFACT_COLLECTION,
    MODEL_STATUS_COLLECTION,
    PrimaryMLEngine,
    PrimaryMLTrainer,
    PrimaryModelRegistry,
)
from app.v4.providers import FredMacroBundleProvider, FredReleaseCalendarProvider, OfficialRssNewsProvider
from app.v4.quant_backtest import V4BacktestEngine
from app.v4.quant_contracts import CostModel
from app.v4.quant_validation import PredictionDriftEngine, QuantValidationEngine, RegimeStabilityEngine
from app.v4.quant_validation_contracts import (
    PredictionDriftRequest,
    QuantDecision,
    QuantValidationRequest,
    RegimeStabilityRequest,
)
from app.v4.risk_contracts import (
    ExpectancyRequest,
    PortfolioExposureRequest,
    RiskAccountState,
    RiskDecision,
    RiskOverlayRequest,
    RiskOverlayResult,
    StructuralTargetRequest,
)
from app.v4.risk_engine import (
    RISK_OVERLAY_COLLECTION,
    ExpectancyEngine,
    PortfolioExposureEngine,
    RiskOverlay,
    StructuralTargetEngine,
)
from app.v4.robustness import RobustnessValidator
from app.v4.signal_contracts import SignalPublicationRequest
from app.v4.signal_engine import SignalPublicationGate
from app.v4.snapshots import MarketSnapshotService, SnapshotUnavailable
from app.v4.symbols import approved_universe, normalize_instrument

ORCHESTRATION_RUN_COLLECTION = "v4_orchestration_runs"
ORCHESTRATION_STATUS_COLLECTION = "v4_orchestration_status"
ORCHESTRATION_STATUS_KEY = "atlas-v4-orchestration-singleton"
_TIMEFRAME_PRIORITY = (
    OfficialTimeframe.H1,
    OfficialTimeframe.H4,
    OfficialTimeframe.D1,
    OfficialTimeframe.M15,
)


class OrchestrationBusy(RuntimeError):
    """Raised when a manual cycle overlaps the active background cycle."""


class V4OrchestrationService:
    """Continuously advance V4 evidence without bypassing statistical gates."""

    version = "v4-background-orchestration-v3"

    def __init__(
        self,
        store: RecordStore,
        snapshots: MarketSnapshotService,
        features: V4FeatureService,
        contexts: V4ContextService,
        context_ingestion: V4ContextIngestionService,
        trainer: PrimaryMLTrainer,
        model_registry: PrimaryModelRegistry,
        primary_ml: PrimaryMLEngine,
        robustness: RobustnessValidator,
        publication: SignalPublicationGate,
        *,
        enabled: bool,
        interval_seconds: int,
        instruments: tuple[str, ...],
        context_refresh_enabled: bool,
        train_models: bool,
        auto_shadow: bool = False,
        dataset_builder: HistoricalPrimaryDatasetBuilder | None = None,
        ai_reviews: AIReviewOrchestrator | None = None,
        historical_meta: HistoricalMetaResearchService | None = None,
        meta_engine: MetaLabelEngine | None = None,
        calibration: ProbabilityCalibrationEngine | None = None,
        regime_stability: RegimeStabilityEngine | None = None,
        prediction_drift: PredictionDriftEngine | None = None,
        backtest: V4BacktestEngine | None = None,
        quant_validation: QuantValidationEngine | None = None,
        expectancy: ExpectancyEngine | None = None,
        structural_targets: StructuralTargetEngine | None = None,
        portfolio_exposure: PortfolioExposureEngine | None = None,
        risk_overlay: RiskOverlay | None = None,
        kronos: KronosShadowEngine | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not 10 <= interval_seconds <= 86_400:
            raise ValueError("orchestration interval must be between 10 seconds and 24 hours")
        canonical = tuple(dict.fromkeys(normalize_instrument(value).canonical for value in instruments))
        if not canonical:
            raise ValueError("orchestration requires at least one approved instrument")
        self.store = store
        self.snapshots = snapshots
        self.features = features
        self.contexts = contexts
        self.context_ingestion = context_ingestion
        self.trainer = trainer
        self.model_registry = model_registry
        self.primary_ml = primary_ml
        self.robustness = robustness
        self.publication = publication
        self.enabled = enabled
        self.interval_seconds = interval_seconds
        self.instruments = canonical
        self.context_refresh_enabled = context_refresh_enabled
        self.train_models = train_models
        self.auto_shadow = auto_shadow
        self.dataset_builder = dataset_builder or HistoricalPrimaryDatasetBuilder()
        dependency_engine = EvidenceDependencyEngine(store)
        meta_trainer = MetaLabelTrainer(store)
        self.ai_reviews = ai_reviews
        self.historical_meta = historical_meta or HistoricalMetaResearchService(
            store,
            self.dataset_builder,
            dependency_engine,
            meta_trainer,
        )
        self.meta_engine = meta_engine or MetaLabelEngine(store)
        self.calibration = calibration or ProbabilityCalibrationEngine(store)
        self.regime_stability = regime_stability or RegimeStabilityEngine(store)
        self.prediction_drift = prediction_drift or PredictionDriftEngine(store)
        self.backtest = backtest or V4BacktestEngine(store)
        self.quant_validation = quant_validation or QuantValidationEngine(store)
        self.expectancy = expectancy or ExpectancyEngine(store)
        self.structural_targets = structural_targets or StructuralTargetEngine(store)
        self.portfolio_exposure = portfolio_exposure or PortfolioExposureEngine(store)
        self.risk_overlay = risk_overlay or RiskOverlay(store)
        self.kronos = kronos
        self.clock = clock or (lambda: datetime.now(UTC))
        self._cycle_lock = asyncio.Lock()
        self._state_lock = RLock()
        self._stop_event = asyncio.Event()
        self._task: asyncio.Task[None] | None = None
        self._status = self._initial_status()

    async def start(self) -> None:
        """Start fixed-delay universe scans while leaving manual evaluation responsive."""

        with self._state_lock:
            if self._task is not None and not self._task.done():
                return
            if not self.enabled:
                self._status = self._status.model_copy(update={"running": False})
                self._persist_status()
                return
            self._restore_status()
            self._stop_event = asyncio.Event()
            started_at = self._now()
            self._status = self._status.model_copy(
                update={
                    "running": True,
                    "service_started_at": started_at,
                    "last_error": None,
                    "next_cycle_at": started_at + timedelta(seconds=self.interval_seconds),
                }
            )
            self._persist_status()
            self._task = asyncio.create_task(self._run_loop(), name="atlas-v4-orchestration")

    async def stop(self) -> None:
        """Stop future cycles and wait for the active cycle to finish safely."""

        self._stop_event.set()
        task = self._task
        if task is not None:
            await task
        with self._state_lock:
            self._task = None
            self._status = self._status.model_copy(
                update={"running": False, "cycle_in_progress": False, "next_cycle_at": None}
            )
            self._persist_status()

    async def run_once(self, instruments: tuple[str, ...] | None = None) -> OrchestrationRun:
        """Run one complete, overlap-protected cycle for the requested scope."""

        scope = self.instruments if instruments is None else tuple(
            dict.fromkeys(normalize_instrument(value).canonical for value in instruments)
        )
        if not scope:
            raise ValueError("orchestration scope requires at least one instrument")
        unsupported = tuple(instrument for instrument in scope if instrument not in self.instruments)
        if unsupported:
            raise ValueError(f"orchestration scope is not enabled: {', '.join(unsupported)}")

        if self._cycle_lock.locked():
            raise OrchestrationBusy("an ATLAS V4 orchestration cycle is already running")
        async with self._cycle_lock:
            started_at = self._now()
            with self._state_lock:
                sequence = self._status.cycles_started + 1
                self._status = self._status.model_copy(
                    update={
                        "cycle_in_progress": True,
                        "current_instrument": None,
                        "current_stage": "PREPARING_META_MODELS",
                        "instruments_completed": 0,
                        "instruments_total": len(scope),
                        "cycles_started": sequence,
                        "current_run_started_at": started_at,
                        "last_cycle_started_at": started_at,
                        "next_cycle_at": None,
                    }
                )
                self._persist_status()
            try:
                run = await asyncio.to_thread(self._run_cycle, started_at, sequence, scope)
            except Exception as exc:
                completed_at = self._now()
                with self._state_lock:
                    self._status = self._status.model_copy(
                        update={
                            "cycle_in_progress": False,
                            "current_instrument": None,
                            "current_stage": None,
                            "current_run_started_at": None,
                            "last_cycle_completed_at": completed_at,
                            "consecutive_cycle_failures": self._status.consecutive_cycle_failures + 1,
                            "last_error": f"{type(exc).__name__}: {exc}",
                            "next_cycle_at": completed_at + timedelta(seconds=self.interval_seconds),
                        }
                    )
                    self._persist_status()
                raise
            with self._state_lock:
                self._status = self._status.model_copy(
                    update={
                        "cycle_in_progress": False,
                        "current_instrument": None,
                        "current_stage": None,
                        "instruments_completed": len(run.instruments),
                        "instruments_total": len(run.instruments),
                        "cycles_completed": self._status.cycles_completed + 1,
                        "consecutive_cycle_failures": 0,
                        "current_run_started_at": None,
                        "last_cycle_completed_at": run.completed_at,
                        "next_cycle_at": run.completed_at + timedelta(seconds=self.interval_seconds),
                        "last_run_id": run.run_id,
                        "last_state": run.state,
                        "last_error": None,
                        "latest_results": run.instruments,
                    }
                )
                self._persist_status()
            return run

    def status(self) -> OrchestrationStatus:
        with self._state_lock:
            return self._status.model_copy(deep=True)

    def runs(self, *, limit: int = 20) -> tuple[OrchestrationRun, ...]:
        if not 1 <= limit <= 200:
            raise ValueError("orchestration run limit must be between 1 and 200")
        rows: list[OrchestrationRun] = []
        for payload in self.store.values(ORCHESTRATION_RUN_COLLECTION):
            try:
                rows.append(OrchestrationRun.model_validate(payload))
            except ValidationError:
                continue
        rows.sort(key=lambda row: (row.completed_at, row.run_id), reverse=True)
        return tuple(rows[:limit])

    async def _run_loop(self) -> None:
        while not self._stop_event.is_set():
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=self.interval_seconds)
                break
            except TimeoutError:
                pass
            try:
                await self.run_once()
            except OrchestrationBusy:
                pass
            except Exception as exc:  # noqa: BLE001 - supervised worker must survive one failed cycle
                # run_once persists the sanitized failure; the supervised loop
                # remains alive for the next cycle.
                del exc
    def _run_cycle(
        self,
        started_at: datetime,
        sequence: int,
        instruments: tuple[str, ...],
    ) -> OrchestrationRun:
        run_id = stable_key(self.version, started_at.isoformat(), sequence)
        cycle_meta_model_ids = self._latest_meta_model_ids() if not self.train_models else {}
        cycle_meta_errors: dict[OfficialTimeframe, str] = {}
        shadow_models, shadow_statuses = self._shadow_inventory()
        # Freeze one pre-cycle cross-asset artifact per timeframe for the whole
        # universe. This keeps symbol order irrelevant while ensuring that
        # chronological validation and drift compare like-for-like horizons.
        for timeframe in OfficialTimeframe:
            self._update_progress(
                current_instrument=None,
                current_stage=f"PREPARING_META_{timeframe.value}",
                instruments_completed=0,
                instruments_total=len(instruments),
            )
            if self.train_models:
                try:
                    cycle_meta_model_ids[timeframe] = self.historical_meta.train_pooled(
                        timeframe
                    ).artifact.meta_model_id
                except ValueError as exc:
                    cycle_meta_errors[timeframe] = str(exc)
            elif timeframe not in cycle_meta_model_ids:
                cycle_meta_errors[timeframe] = (
                    f"No persisted pooled {timeframe.value} meta model is available"
                )
        result_rows: list[InstrumentOrchestrationResult] = []
        for index, instrument in enumerate(instruments):
            self._update_progress(
                current_instrument=instrument,
                current_stage="EVALUATING_MARKET",
                instruments_completed=index,
                instruments_total=len(instruments),
            )
            result_rows.append(
                self._process_instrument(
                    instrument,
                    self._now(),
                    cycle_meta_model_ids,
                    cycle_meta_errors,
                    shadow_models,
                    shadow_statuses,
                )
            )
            self._update_progress(
                current_instrument=instrument,
                current_stage="MARKET_COMPLETE",
                instruments_completed=index + 1,
                instruments_total=len(instruments),
            )
        self._update_progress(
            current_instrument=None,
            current_stage="FINALIZING",
            instruments_completed=len(result_rows),
            instruments_total=len(instruments),
        )
        results = tuple(result_rows)
        completed_at = self._now()
        failed = sum(result.state is OrchestrationRunState.FAILED for result in results)
        blocked = sum(result.state is OrchestrationRunState.COMPLETED_WITH_BLOCKERS for result in results)
        completed = sum(result.state is OrchestrationRunState.COMPLETED for result in results)
        state = (
            OrchestrationRunState.FAILED
            if failed == len(results)
            else (
                OrchestrationRunState.COMPLETED_WITH_BLOCKERS if failed or blocked else OrchestrationRunState.COMPLETED
            )
        )
        run = OrchestrationRun(
            run_id=run_id,
            started_at=started_at,
            completed_at=completed_at,
            state=state,
            instruments=results,
            completed_instruments=completed,
            blocked_instruments=blocked,
            failed_instruments=failed,
            signals_published=sum(result.signal_id is not None for result in results),
        )
        self.store.upsert(ORCHESTRATION_RUN_COLLECTION, run_id, run.model_dump(mode="json"))
        return run

    def _process_instrument(
        self,
        instrument: str,
        observed_at: datetime,
        cycle_meta_model_ids: dict[OfficialTimeframe, str],
        cycle_meta_errors: dict[OfficialTimeframe, str],
        shadow_models: tuple[PrimaryModelArtifact, ...],
        shadow_statuses: dict[str, ModelStatusRecord],
    ) -> InstrumentOrchestrationResult:
        stages: list[OrchestrationStageResult] = []
        blockers: list[str] = []
        snapshot: MarketSnapshot | None = None
        feature_set: FeatureSet | None = None
        context: ContextSet | None = None
        context_id: str | None = None
        model_id: str | None = None
        robustness_id: str | None = None
        candidate: PrimaryCandidate | None = None
        selected_dataset: HistoricalPrimaryDataset | None = None
        selected_artifact: PrimaryModelArtifact | None = None
        ai_bundle: AIReviewBundle | None = None
        meta_model_id: str | None = None
        cycle_meta_error: str | None = None
        meta_prediction_id: str | None = None
        calibration_id: str | None = None
        calibrated_probability_id: str | None = None
        quant_validation_id: str | None = None
        risk_overlay_id: str | None = None
        publication_attempt_id: str | None = None
        signal_id: str | None = None

        started = perf_counter()
        try:
            snapshot = self.snapshots.capture(instrument, now=observed_at, persist=True)
            stages.append(
                self._completed(OrchestrationStage.SNAPSHOT, "MT5 snapshot persisted", snapshot.snapshot_id, started)
            )
        except SnapshotUnavailable as exc:
            stages.append(self._failed(OrchestrationStage.SNAPSHOT, str(exc), exc.code, started))
            return self._result(instrument, stages, (exc.code,))
        except Exception as exc:  # noqa: BLE001 - isolate one market from the remaining universe
            code = "SNAPSHOT_CAPTURE_FAILED"
            stages.append(self._failed(OrchestrationStage.SNAPSHOT, f"{type(exc).__name__}: {exc}", code, started))
            return self._result(instrument, stages, (code,))

        if (
            snapshot.data_quality.status is not DataQualityStatus.HEALTHY
            or not snapshot.data_quality.publication_eligible
            or not snapshot.health.data_fresh
        ):
            blockers.append("DATA_NOT_PUBLICATION_ELIGIBLE")

        if self.kronos is not None and self.kronos.enabled:
            started = perf_counter()
            try:
                kronos_bundle = self.kronos.forecast_all(snapshot, persist=True, now=observed_at)
                if kronos_bundle.available_forecasts:
                    stages.append(
                        self._completed(
                            OrchestrationStage.KRONOS_SHADOW,
                            (
                                f"Kronos shadow bundle persisted with "
                                f"{kronos_bundle.available_forecasts}/4 available forecasts; weight remains zero"
                            ),
                            kronos_bundle.bundle_id,
                            started,
                        )
                    )
                else:
                    stages.append(
                        self._blocked(
                            OrchestrationStage.KRONOS_SHADOW,
                            "Kronos shadow evidence is unavailable; canonical Atlas gates are unchanged",
                            "KRONOS_SHADOW_UNAVAILABLE",
                            started,
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - shadow specialist cannot fail the Atlas cycle
                stages.append(
                    self._blocked(
                        OrchestrationStage.KRONOS_SHADOW,
                        f"Kronos shadow failure: {type(exc).__name__}: {exc}",
                        "KRONOS_SHADOW_FAILED",
                        started,
                    )
                )

        started = perf_counter()
        try:
            feature_set = self.features.compute(snapshot, persist=True)
            stages.append(
                self._completed(
                    OrchestrationStage.FEATURES,
                    "Deterministic feature set persisted",
                    feature_set.feature_set_id,
                    started,
                )
            )
        except FeatureComputationError as exc:
            code = "FEATURE_COMPUTATION_BLOCKED"
            blockers.append(code)
            stages.append(self._blocked(OrchestrationStage.FEATURES, str(exc), code, started))
            return self._result(instrument, stages, tuple(blockers), snapshot=snapshot)
        except Exception as exc:  # noqa: BLE001 - isolate one feature failure per market
            code = "FEATURE_COMPUTATION_FAILED"
            blockers.append(code)
            stages.append(self._failed(OrchestrationStage.FEATURES, f"{type(exc).__name__}: {exc}", code, started))
            return self._result(instrument, stages, tuple(blockers), snapshot=snapshot)

        started = perf_counter()
        try:
            if self.context_refresh_enabled:
                context = self.context_ingestion.refresh(instrument, as_of=observed_at, persist=True)
                message = "Provider-managed context refresh persisted"
            else:
                context = self.contexts.compute(
                    instrument,
                    ContextComputationRequest(
                        as_of=observed_at,
                        liquidity_state=feature_set.liquidity.state,
                    ),
                    persist=True,
                    provider_managed_ingestion=False,
                    provider_issues=("BACKGROUND_CONTEXT_REFRESH_DISABLED",),
                )
                message = "Explicit unavailable context persisted; provider refresh is disabled"
            context_id = context.context_set_id
            stages.append(self._completed(OrchestrationStage.CONTEXT, message, context_id, started))
            if context.candidate_veto:
                blockers.append("CONTEXT_CANDIDATE_VETO")
        except Exception as exc:  # noqa: BLE001 - optional providers must not stop the worker
            code = "CONTEXT_COMPUTATION_FAILED"
            blockers.append(code)
            stages.append(self._failed(OrchestrationStage.CONTEXT, f"{type(exc).__name__}: {exc}", code, started))

        datasets: dict[OfficialTimeframe, HistoricalPrimaryDataset] = {}
        winners: dict[OfficialTimeframe, PrimaryModelArtifact] = {}
        robustness_by_model: dict[str, str] = {}
        started = perf_counter()
        try:
            if self.train_models:
                historical_samples = 0
                qualified = 0
                timeframe_errors: list[str] = []
                for timeframe in OfficialTimeframe:
                    try:
                        dataset = self.dataset_builder.build(snapshot, timeframe)
                        training = self.trainer.train(
                            dataset.request.train,
                            dataset.request.validation,
                            dataset.request.test,
                            dataset.request.config,
                        )
                        artifact = self.model_registry.artifact(training.validation_winner_model_id)
                        test_returns = self.dataset_builder.model_returns(
                            artifact,
                            dataset.request.test,
                        )
                        robustness = self.robustness.validate(
                            test_returns,
                            bootstrap_samples=200,
                            stress_cost_bps=max(0.0, snapshot.spread_bps),
                            persist=True,
                        )
                        historical = self.historical_meta.add_primary_oos_samples(
                            snapshot,
                            dataset,
                            artifact,
                            training.artifacts,
                        )
                        datasets[timeframe] = dataset
                        winners[timeframe] = artifact
                        robustness_by_model[artifact.model_id] = robustness.robustness_run_id
                        historical_samples += historical.sample_count
                        if self.auto_shadow:
                            try:
                                status = self.model_registry.current_status(artifact.model_id)
                                if status.status is ModelGovernanceStatus.EXPERIMENTAL:
                                    self.model_registry.qualify_for_shadow(
                                        artifact.model_id,
                                        robustness.robustness_run_id,
                                        changed_at=observed_at,
                                    )
                                if (
                                    self.model_registry.current_status(artifact.model_id).status
                                    is ModelGovernanceStatus.SHADOW
                                ):
                                    qualified += 1
                            except ValueError:
                                pass
                    except HistoricalDatasetError as exc:
                        timeframe_errors.append(f"{timeframe.value}: {exc}")
                if not datasets:
                    raise HistoricalDatasetError("; ".join(timeframe_errors))
                preferred = next(
                    (timeframe for timeframe in _TIMEFRAME_PRIORITY if timeframe in winners),
                    next(iter(winners)),
                )
                model_id = winners[preferred].model_id
                robustness_id = robustness_by_model[model_id]
                stages.append(
                    self._completed(
                        OrchestrationStage.PRIMARY_MODEL,
                        (
                            f"Trained {len(datasets)} official timeframe model sets; persisted "
                            f"{historical_samples} pooled primary OOS meta labels; "
                            f"{qualified} winners satisfy governed SHADOW policy"
                        ),
                        model_id,
                        started,
                    )
                )
            else:
                timeframe_errors: list[str] = []
                for timeframe in OfficialTimeframe:
                    try:
                        dataset = self.dataset_builder.build(snapshot, timeframe)
                    except HistoricalDatasetError as exc:
                        timeframe_errors.append(f"{timeframe.value}: {exc}")
                        continue
                    compatible = self._shadow_model(
                        instrument,
                        timeframe,
                        dataset.current_regime,
                        dataset.current_features,
                        shadow_models,
                    )
                    if compatible is None:
                        continue
                    datasets[timeframe] = dataset
                    winners[timeframe] = compatible
                if not datasets:
                    stages.append(
                        self._blocked(
                            OrchestrationStage.PRIMARY_MODEL,
                            (
                                "Scheduled retraining is disabled and no compatible persisted "
                                "SHADOW model is available"
                            ),
                            "MODEL_TRAINING_DISABLED",
                            started,
                        )
                    )
                    blockers.append("MODEL_TRAINING_DISABLED")
                else:
                    preferred = next(
                        (timeframe for timeframe in _TIMEFRAME_PRIORITY if timeframe in winners),
                        next(iter(winners)),
                    )
                    model_id = winners[preferred].model_id
                    status = shadow_statuses[model_id]
                    robustness_id = status.robustness_evidence_id
                    stages.append(
                        self._completed(
                            OrchestrationStage.PRIMARY_MODEL,
                            (
                                f"Reused governed SHADOW models for {len(datasets)} official "
                                "timeframes; scheduled retraining is disabled for low-latency inference"
                            ),
                            model_id,
                            started,
                        )
                    )
        except HistoricalDatasetError as exc:
            code = "MODEL_TRAINING_DATA_INSUFFICIENT"
            blockers.append(code)
            stages.append(self._blocked(OrchestrationStage.PRIMARY_MODEL, str(exc), code, started))
        except Exception as exc:  # noqa: BLE001 - model failure is persisted as a stage result
            code = "MODEL_TRAINING_FAILED"
            blockers.append(code)
            stages.append(self._failed(OrchestrationStage.PRIMARY_MODEL, f"{type(exc).__name__}: {exc}", code, started))

        if self.train_models:
            # Include any winner that was objectively promoted to SHADOW in
            # this cycle without repeating one status-table scan per artifact.
            shadow_models, shadow_statuses = self._shadow_inventory()

        started = perf_counter()
        if not datasets:
            code = "PRIMARY_CANDIDATE_INPUT_UNAVAILABLE"
            blockers.append(code)
            stages.append(
                self._blocked(
                    OrchestrationStage.PRIMARY_CANDIDATE, "Current model features are unavailable", code, started
                )
            )
        else:
            selection: tuple[PrimaryModelArtifact, HistoricalPrimaryDataset] | None = None
            for timeframe in _TIMEFRAME_PRIORITY:
                current_dataset = datasets.get(timeframe)
                if current_dataset is None:
                    continue
                shadow = self._shadow_model(
                    instrument,
                    timeframe,
                    current_dataset.current_regime,
                    current_dataset.current_features,
                    shadow_models,
                )
                if shadow is not None:
                    selection = (shadow, current_dataset)
                    break
            if selection is None:
                code = "MODEL_AWAITING_GOVERNED_SHADOW"
                blockers.append(code)
                stages.append(
                    self._blocked(
                        OrchestrationStage.PRIMARY_CANDIDATE,
                        (
                            "No compatible model passed the objective SHADOW gates "
                            "(positive baseline-adjusted OOS return and positive robustness interval)"
                        ),
                        code,
                        started,
                    )
                )
            else:
                try:
                    shadow, selected_dataset = selection
                    selected_artifact = shadow
                    candidate = self.primary_ml.generate_candidate(
                        CandidateRequest(
                            model_id=shadow.model_id,
                            instrument=instrument,
                            timeframe=selected_dataset.timeframe,
                            regime=selected_dataset.current_regime,
                            observed_at=observed_at,
                            features=selected_dataset.current_features,
                        )
                    )
                    model_id = shadow.model_id
                    status = shadow_statuses.get(model_id)
                    robustness_id = robustness_by_model.get(model_id) or (
                        status.robustness_evidence_id if status is not None else None
                    )
                    stages.append(
                        self._completed(
                            OrchestrationStage.PRIMARY_CANDIDATE,
                            (f"Governed SHADOW primary candidate persisted on {selected_dataset.timeframe.value}"),
                            candidate.candidate_id,
                            started,
                        )
                    )
                except Exception as exc:  # noqa: BLE001 - candidate failure is instrument-local
                    code = "PRIMARY_CANDIDATE_FAILED"
                    blockers.append(code)
                    stages.append(
                        self._failed(
                            OrchestrationStage.PRIMARY_CANDIDATE, f"{type(exc).__name__}: {exc}", code, started
                        )
                    )

        if candidate is not None:
            meta_model_id = cycle_meta_model_ids.get(candidate.timeframe)
            cycle_meta_error = cycle_meta_errors.get(candidate.timeframe)

        started = perf_counter()
        if candidate is None:
            code = "AI_REVIEW_AWAITS_SHADOW_CANDIDATE"
            blockers.append(code)
            stages.append(
                self._blocked(
                    OrchestrationStage.AI_REVIEW,
                    "Four-model review awaits a governed SHADOW candidate",
                    code,
                    started,
                )
            )
        elif self.ai_reviews is None or context is None:
            code = "AI_REVIEW_SERVICE_UNAVAILABLE"
            blockers.append(code)
            stages.append(
                self._blocked(
                    OrchestrationStage.AI_REVIEW,
                    "Four-model review service or current context is unavailable",
                    code,
                    started,
                )
            )
        else:
            try:
                response = asyncio.run(
                    self.ai_reviews.review(
                        AIReviewRequest(
                            candidate_id=candidate.candidate_id,
                            evidence=self._ai_evidence(snapshot, feature_set, context, candidate),
                        )
                    )
                )
                ai_bundle = response.bundle
                if ai_bundle.status is AIReviewBundleStatus.COMPLETE:
                    stages.append(
                        self._completed(
                            OrchestrationStage.AI_REVIEW,
                            "All four bounded local-LLM reviews completed",
                            ai_bundle.bundle_id,
                            started,
                        )
                    )
                else:
                    code = "AI_REVIEW_INCOMPLETE"
                    blockers.append(code)
                    stages.append(
                        self._blocked(
                            OrchestrationStage.AI_REVIEW,
                            ai_bundle.skip_reason or "Mandatory AI review evidence is incomplete",
                            code,
                            started,
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - publication failure is instrument-local
                code = "AI_REVIEW_FAILED"
                blockers.append(code)
                stages.append(
                    self._failed(
                        OrchestrationStage.AI_REVIEW,
                        f"{type(exc).__name__}: {exc}",
                        code,
                        started,
                    )
                )

        started = perf_counter()
        meta_prediction = None
        try:
            if meta_model_id is None:
                code = "META_TRAINING_EVIDENCE_INSUFFICIENT"
                blockers.append(code)
                stages.append(
                    self._blocked(
                        OrchestrationStage.META_MODEL,
                        cycle_meta_error or "A pre-cycle pooled meta model is unavailable",
                        code,
                        started,
                    )
                )
            elif (
                candidate is not None
                and ai_bundle is not None
                and ai_bundle.status is AIReviewBundleStatus.COMPLETE
                and context is not None
            ):
                meta_features = self.historical_meta.current_features(
                    candidate,
                    snapshot,
                    feature_set,
                    context,
                    ai_bundle,
                )
                meta_prediction = self.meta_engine.predict(
                    MetaPredictionRequest(
                        meta_model_id=meta_model_id,
                        candidate_id=candidate.candidate_id,
                        observed_at=observed_at,
                        features=meta_features,
                    )
                )
                meta_prediction_id = meta_prediction.meta_prediction_id
                stages.append(
                    self._completed(
                        OrchestrationStage.META_MODEL,
                        (
                            f"{candidate.timeframe.value} cross-asset purged-walk-forward meta "
                            f"model evaluated current candidate "
                            f"as {meta_prediction.research_classification.value}"
                        ),
                        meta_prediction_id,
                        started,
                    )
                )
            else:
                code = "META_PREDICTION_INPUT_PENDING"
                blockers.append(code)
                stages.append(
                    self._blocked(
                        OrchestrationStage.META_MODEL,
                        "Pooled meta model is available but a complete current candidate review is pending",
                        code,
                        started,
                    )
                )
        except ValueError as exc:
            code = "META_MODEL_EVIDENCE_INVALID"
            blockers.append(code)
            stages.append(self._blocked(OrchestrationStage.META_MODEL, str(exc), code, started))
        except Exception as exc:  # noqa: BLE001 - meta failure remains instrument-local
            code = "META_MODEL_FAILED"
            blockers.append(code)
            stages.append(
                self._failed(
                    OrchestrationStage.META_MODEL,
                    f"{type(exc).__name__}: {exc}",
                    code,
                    started,
                )
            )

        started = perf_counter()
        calibrated_probability = None
        if meta_model_id is None or meta_prediction is None:
            code = "CALIBRATION_AWAITS_META_PREDICTION"
            blockers.append(code)
            stages.append(
                self._blocked(
                    OrchestrationStage.CALIBRATION,
                    "Calibration awaits pooled OOS meta evidence and a current prediction",
                    code,
                    started,
                )
            )
        else:
            try:
                calibration = self.calibration.train(meta_model_id)
                calibration_id = calibration.calibration_id
                calibrated_probability = self.calibration.apply(
                    CalibratedProbabilityRequest(
                        calibration_id=calibration.calibration_id,
                        meta_prediction_id=meta_prediction.meta_prediction_id,
                    )
                )
                calibrated_probability_id = calibrated_probability.calibrated_probability_id
                if (
                    calibration.status is CalibrationStatus.AVAILABLE
                    and calibrated_probability.atlas_probability is not None
                ):
                    stages.append(
                        self._completed(
                            OrchestrationStage.CALIBRATION,
                            "Chronological OOS calibration produced Atlas probability",
                            calibrated_probability_id,
                            started,
                        )
                    )
                else:
                    code = "CALIBRATION_INSUFFICIENT_EVIDENCE"
                    blockers.append(code)
                    stages.append(
                        self._blocked(
                            OrchestrationStage.CALIBRATION,
                            calibration.reason,
                            code,
                            started,
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - calibration failure is isolated
                code = "CALIBRATION_FAILED"
                blockers.append(code)
                stages.append(
                    self._failed(
                        OrchestrationStage.CALIBRATION,
                        f"{type(exc).__name__}: {exc}",
                        code,
                        started,
                    )
                )

        started = perf_counter()
        quant = None
        if (
            calibrated_probability is None
            or meta_model_id is None
            or selected_dataset is None
            or selected_artifact is None
            or candidate is None
        ):
            code = "QUANT_INPUT_EVIDENCE_PENDING"
            blockers.append(code)
            stages.append(
                self._blocked(
                    OrchestrationStage.QUANT_VALIDATION,
                    "Quant validation awaits calibrated probability and selected primary lineage",
                    code,
                    started,
                )
            )
        else:
            try:
                plans = self.historical_meta.backtest_plans(
                    snapshot,
                    selected_dataset,
                    selected_artifact,
                )
                backtest = self.backtest.run(
                    instrument,
                    snapshot.timeframes[selected_dataset.timeframe],
                    plans,
                    CostModel(
                        spread_bps=snapshot.spread_bps,
                        slippage_bps=snapshot.spread_bps * 0.25,
                    ),
                    persist=True,
                )
                executed_returns = tuple(trade.net_return for trade in backtest.trades if trade.entry_at is not None)
                quant_robustness = self.robustness.validate(
                    executed_returns,
                    variants_tested=1,
                    bootstrap_samples=500,
                    stress_cost_bps=snapshot.spread_bps,
                    persist=True,
                )
                robustness_id = quant_robustness.robustness_run_id
                regime = self.regime_stability.assess(
                    RegimeStabilityRequest(
                        meta_model_id=meta_model_id,
                        current_regime=candidate.regime,
                    )
                )
                current_prediction_ids = self._current_meta_prediction_ids(meta_model_id)
                drift = self.prediction_drift.assess(
                    PredictionDriftRequest(
                        meta_model_id=meta_model_id,
                        current_meta_prediction_ids=current_prediction_ids,
                    )
                )
                quant = self.quant_validation.validate(
                    QuantValidationRequest(
                        calibrated_probability_id=calibrated_probability.calibrated_probability_id,
                        robustness_run_id=quant_robustness.robustness_run_id,
                        regime_stability_id=regime.regime_stability_id,
                        drift_report_id=drift.drift_report_id,
                        backtest_run_id=backtest.backtest_run_id,
                        snapshot_id=snapshot.snapshot_id,
                    )
                )
                quant_validation_id = quant.quant_validation_id
                if quant.decision is QuantDecision.PASS:
                    stages.append(
                        self._completed(
                            OrchestrationStage.QUANT_VALIDATION,
                            "All governed statistical checks passed",
                            quant.quant_validation_id,
                            started,
                        )
                    )
                else:
                    code = f"QUANT_{quant.decision.value}"
                    blockers.append(code)
                    stages.append(
                        self._blocked(
                            OrchestrationStage.QUANT_VALIDATION,
                            quant.reason,
                            code,
                            started,
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - Quant evidence remains instrument-local
                code = "QUANT_VALIDATION_FAILED"
                blockers.append(code)
                stages.append(
                    self._failed(
                        OrchestrationStage.QUANT_VALIDATION,
                        f"{type(exc).__name__}: {exc}",
                        code,
                        started,
                    )
                )

        started = perf_counter()
        risk: RiskOverlayResult | None = None
        if quant is None:
            code = "RISK_AWAITS_QUANT_EVIDENCE"
            blockers.append(code)
            stages.append(
                self._blocked(
                    OrchestrationStage.RISK,
                    "Risk overlay awaits a persisted Quant decision",
                    code,
                    started,
                )
            )
        else:
            try:
                expectancy = self.expectancy.evaluate(
                    ExpectancyRequest(
                        quant_validation_id=quant.quant_validation_id,
                        backtest_run_id=quant.backtest_run_id,
                    )
                )
                targets = self.structural_targets.build(
                    StructuralTargetRequest(
                        quant_validation_id=quant.quant_validation_id,
                        feature_set_id=feature_set.feature_set_id,
                        snapshot_id=snapshot.snapshot_id,
                    )
                )
                exposure = self.portfolio_exposure.assess(
                    PortfolioExposureRequest(
                        candidate_id=quant.candidate_id,
                        proposed_risk_fraction=0.005,
                    )
                )
                risk = self.risk_overlay.evaluate(
                    RiskOverlayRequest(
                        quant_validation_id=quant.quant_validation_id,
                        expectancy_id=expectancy.expectancy_id,
                        target_plan_id=targets.target_plan_id,
                        exposure_id=exposure.exposure_id,
                        context_set_id=context_id or "",
                        snapshot_id=snapshot.snapshot_id,
                        account_state=RiskAccountState(
                            as_of=observed_at,
                            daily_drawdown=0,
                            weekly_drawdown=0,
                            consecutive_losses=0,
                        ),
                    )
                )
                risk_overlay_id = risk.risk_overlay_id
                if risk.decision is RiskDecision.PASS:
                    stages.append(
                        self._completed(
                            OrchestrationStage.RISK,
                            "Expectancy, structural targets, exposure, and hard risk checks passed",
                            risk.risk_overlay_id,
                            started,
                        )
                    )
                else:
                    code = f"RISK_{risk.decision.value}"
                    blockers.append(code)
                    stages.append(
                        self._blocked(
                            OrchestrationStage.RISK,
                            risk.reason,
                            code,
                            started,
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - risk failure remains instrument-local
                code = "RISK_EVALUATION_FAILED"
                blockers.append(code)
                stages.append(
                    self._failed(
                        OrchestrationStage.RISK,
                        f"{type(exc).__name__}: {exc}",
                        code,
                        started,
                    )
                )

        started = perf_counter()
        if risk is None or ai_bundle is None or ai_bundle.status is not AIReviewBundleStatus.COMPLETE:
            code = "PUBLICATION_EVIDENCE_PENDING"
            blockers.append(code)
            stages.append(
                self._blocked(
                    OrchestrationStage.PUBLICATION,
                    "Canonical publication awaits complete AI, Quant, and risk lineage",
                    code,
                    started,
                )
            )
        else:
            try:
                published = self.publication.evaluate(
                    SignalPublicationRequest(
                        risk_overlay_id=risk.risk_overlay_id,
                        feature_set_id=feature_set.feature_set_id,
                        ai_review_bundle_id=ai_bundle.bundle_id,
                    )
                )
                publication_attempt_id = published.publication_attempt_id
                signal_id = published.signal.signal_id if published.signal is not None else None
                if published.signal is not None:
                    stages.append(
                        self._completed(
                            OrchestrationStage.PUBLICATION,
                            "Canonical analytical signal published; order submission remains disabled",
                            publication_attempt_id,
                            started,
                        )
                    )
                else:
                    code = f"PUBLICATION_{published.decision.value}"
                    blockers.append(code)
                    stages.append(
                        self._blocked(
                            OrchestrationStage.PUBLICATION,
                            "; ".join(published.reasons),
                            code,
                            started,
                        )
                    )
            except Exception as exc:  # noqa: BLE001 - publication failure is instrument-local
                code = "PUBLICATION_GATE_FAILED"
                blockers.append(code)
                stages.append(
                    self._failed(
                        OrchestrationStage.PUBLICATION,
                        f"{type(exc).__name__}: {exc}",
                        code,
                        started,
                    )
                )

        return self._result(
            instrument,
            stages,
            tuple(dict.fromkeys(blockers)),
            snapshot=snapshot,
            feature_set=feature_set,
            context_id=context_id,
            model_id=model_id,
            robustness_id=robustness_id,
            candidate=candidate,
            ai_review_bundle_id=ai_bundle.bundle_id if ai_bundle else None,
            meta_model_id=meta_model_id,
            meta_prediction_id=meta_prediction_id,
            calibration_id=calibration_id,
            calibrated_probability_id=calibrated_probability_id,
            quant_validation_id=quant_validation_id,
            risk_overlay_id=risk_overlay_id,
            publication_attempt_id=publication_attempt_id,
            signal_id=signal_id,
        )

    def _shadow_model(
        self,
        instrument: str,
        timeframe: OfficialTimeframe,
        regime: RegimeState,
        features: dict[str, float],
        shadow_models: tuple[PrimaryModelArtifact, ...],
    ) -> PrimaryModelArtifact | None:
        candidates: list[PrimaryModelArtifact] = []
        for artifact in shadow_models:
            if instrument not in artifact.supported_instruments:
                continue
            if timeframe not in artifact.supported_timeframes or regime not in artifact.supported_regimes:
                continue
            if set(features) != set(artifact.feature_schema.names):
                continue
            candidates.append(artifact)
        return max(candidates, key=lambda row: (row.created_at, row.model_id), default=None)

    def _shadow_inventory(
        self,
    ) -> tuple[tuple[PrimaryModelArtifact, ...], dict[str, ModelStatusRecord]]:
        """Load the latest governance state once instead of an N+1 ledger scan."""

        statuses: dict[str, ModelStatusRecord] = {}
        for payload in self.store.latest_values(
            MODEL_STATUS_COLLECTION,
            "model_id",
            "changed_at",
        ):
            try:
                status = ModelStatusRecord.model_validate(payload)
            except ValidationError:
                continue
            if status.status is ModelGovernanceStatus.SHADOW:
                statuses[status.model_id] = status
        artifacts: list[PrimaryModelArtifact] = []
        for payload in self.store.values(MODEL_ARTIFACT_COLLECTION):
            try:
                artifact = PrimaryModelArtifact.model_validate(payload)
            except ValidationError:
                continue
            if artifact.model_id in statuses:
                artifacts.append(artifact)
        return tuple(artifacts), statuses

    def _latest_meta_model_ids(self) -> dict[OfficialTimeframe, str]:
        """Reuse the newest pooled per-timeframe meta artifacts for inference."""

        codes = {
            OfficialTimeframe.M15: 0.0,
            OfficialTimeframe.H1: 1 / 3,
            OfficialTimeframe.H4: 2 / 3,
            OfficialTimeframe.D1: 1.0,
        }
        selected: dict[OfficialTimeframe, MetaModelArtifact] = {}
        for payload in self.store.values(META_MODEL_COLLECTION):
            try:
                artifact = MetaModelArtifact.model_validate(payload)
                index = artifact.feature_names.index("timeframe_code")
                mean_code = artifact.standardization_means[index]
            except (ValidationError, ValueError, IndexError):
                continue
            timeframe = min(codes, key=lambda row: abs(codes[row] - mean_code))
            if abs(codes[timeframe] - mean_code) > 0.01:
                continue
            current = selected.get(timeframe)
            if current is None or (artifact.created_at, artifact.meta_model_id) > (
                current.created_at,
                current.meta_model_id,
            ):
                selected[timeframe] = artifact
        return {timeframe: artifact.meta_model_id for timeframe, artifact in selected.items()}

    @staticmethod
    def _ai_evidence(
        snapshot: MarketSnapshot,
        feature_set: FeatureSet,
        context: ContextSet,
        candidate: PrimaryCandidate,
    ) -> AIReviewEvidence:
        summary = feature_set.timeframes[candidate.timeframe]
        macro: dict[str, ScalarEvidence] = {
            f"{name}_change_1": factor.change_1 for name, factor in tuple(context.macro.factors.items())[:32]
        }
        macro["context_availability"] = context.availability.value
        market: dict[str, ScalarEvidence] = {
            "instrument": candidate.instrument,
            "timeframe": candidate.timeframe.value,
            "candidate_direction": candidate.direction.value,
            "raw_direction_probability": candidate.raw_direction_probability,
            "regime": summary.regime.primary.value,
            "trend": summary.structure.trend.value,
            "bos": summary.structure.bos.value,
            "choch": summary.structure.choch.value,
            "atr_percent": summary.volatility.atr_percent,
            "volatility_percentile": summary.volatility.volatility_percentile,
            "auction_state": summary.auction.state.value,
            "value_migration": summary.auction.value_migration,
            "liquidity_state": feature_set.liquidity.state.value,
            "spread_bps": snapshot.spread_bps,
            "data_quality_score": snapshot.data_quality.score,
            "multi_timeframe_alignment": feature_set.multi_timeframe.alignment,
        }
        return AIReviewEvidence(
            snapshot_id=snapshot.snapshot_id,
            feature_set_id=feature_set.feature_set_id,
            context_set_id=context.context_set_id,
            macro=macro,
            market=market,
            events=tuple(assessment.name for assessment in context.events.assessments[:32]),
            news_context=tuple(category.value for category in context.news.categories),
            missing_data=tuple(dict.fromkeys(context.provider_issues))[:32],
        )

    def _current_meta_prediction_ids(self, meta_model_id: str) -> tuple[str, ...]:
        artifact = self.meta_engine.artifact(meta_model_id)
        rows: list[MetaPrediction] = []
        for payload in self.store.values(META_PREDICTION_COLLECTION):
            try:
                prediction = MetaPrediction.model_validate(payload)
            except ValidationError:
                continue
            if prediction.meta_model_id == meta_model_id and prediction.observed_at > artifact.created_at:
                rows.append(prediction)
        rows.sort(key=lambda row: (row.observed_at, row.meta_prediction_id))
        return tuple(row.meta_prediction_id for row in rows)

    def _matching_risk_overlay(
        self,
        instrument: str,
        snapshot_id: str,
        context_id: str | None,
    ) -> RiskOverlayResult | None:
        if context_id is None:
            return None
        rows: list[RiskOverlayResult] = []
        for payload in self.store.values(RISK_OVERLAY_COLLECTION):
            try:
                risk = RiskOverlayResult.model_validate(payload)
            except ValidationError:
                continue
            if risk.instrument == instrument and risk.snapshot_id == snapshot_id and risk.context_set_id == context_id:
                rows.append(risk)
        return max(rows, key=lambda row: (row.created_at, row.risk_overlay_id), default=None)

    def _result(
        self,
        instrument: str,
        stages: list[OrchestrationStageResult],
        blockers: tuple[str, ...],
        *,
        snapshot: MarketSnapshot | None = None,
        feature_set: FeatureSet | None = None,
        context_id: str | None = None,
        model_id: str | None = None,
        robustness_id: str | None = None,
        candidate: PrimaryCandidate | None = None,
        ai_review_bundle_id: str | None = None,
        meta_model_id: str | None = None,
        meta_prediction_id: str | None = None,
        calibration_id: str | None = None,
        calibrated_probability_id: str | None = None,
        quant_validation_id: str | None = None,
        risk_overlay_id: str | None = None,
        publication_attempt_id: str | None = None,
        signal_id: str | None = None,
    ) -> InstrumentOrchestrationResult:
        state = (
            OrchestrationRunState.FAILED
            if any(stage.state is OrchestrationStageState.FAILED for stage in stages)
            else (OrchestrationRunState.COMPLETED_WITH_BLOCKERS if blockers else OrchestrationRunState.COMPLETED)
        )
        return InstrumentOrchestrationResult(
            instrument=instrument,
            state=state,
            stages=tuple(stages),
            blocker_codes=blockers,
            snapshot_id=snapshot.snapshot_id if snapshot else None,
            feature_set_id=feature_set.feature_set_id if feature_set else None,
            kronos_bundle_id=next(
                (
                    stage.evidence_id
                    for stage in stages
                    if stage.stage is OrchestrationStage.KRONOS_SHADOW and stage.evidence_id is not None
                ),
                None,
            ),
            context_set_id=context_id,
            model_id=model_id,
            robustness_run_id=robustness_id,
            candidate_id=candidate.candidate_id if candidate else None,
            ai_review_bundle_id=ai_review_bundle_id,
            meta_model_id=meta_model_id,
            meta_prediction_id=meta_prediction_id,
            calibration_id=calibration_id,
            calibrated_probability_id=calibrated_probability_id,
            quant_validation_id=quant_validation_id,
            risk_overlay_id=risk_overlay_id,
            publication_attempt_id=publication_attempt_id,
            signal_id=signal_id,
        )

    @staticmethod
    def _completed(
        stage: OrchestrationStage, message: str, evidence_id: str, started: float
    ) -> OrchestrationStageResult:
        return OrchestrationStageResult(
            stage=stage,
            state=OrchestrationStageState.COMPLETED,
            message=message,
            evidence_id=evidence_id,
            latency_ms=(perf_counter() - started) * 1_000,
        )

    @staticmethod
    def _blocked(stage: OrchestrationStage, message: str, code: str, started: float) -> OrchestrationStageResult:
        return OrchestrationStageResult(
            stage=stage,
            state=OrchestrationStageState.BLOCKED,
            message=message,
            blocker_code=code,
            latency_ms=(perf_counter() - started) * 1_000,
        )

    @staticmethod
    def _failed(stage: OrchestrationStage, message: str, code: str, started: float) -> OrchestrationStageResult:
        return OrchestrationStageResult(
            stage=stage,
            state=OrchestrationStageState.FAILED,
            message=message[:1_000],
            blocker_code=code,
            latency_ms=(perf_counter() - started) * 1_000,
        )

    def _initial_status(self) -> OrchestrationStatus:
        return OrchestrationStatus(
            enabled=self.enabled,
            running=False,
            cycle_in_progress=False,
            interval_seconds=self.interval_seconds,
            instruments=self.instruments,
            current_instrument=None,
            current_stage=None,
            instruments_completed=0,
            instruments_total=len(self.instruments),
            cycles_started=0,
            cycles_completed=0,
            consecutive_cycle_failures=0,
        )

    def _restore_status(self) -> None:
        """Recover durable counters at application startup, never at import time."""

        payloads = self.store.values(ORCHESTRATION_STATUS_COLLECTION)
        if not payloads:
            return
        try:
            previous = OrchestrationStatus.model_validate(payloads[-1])
        except ValidationError:
            return
        self._status = previous.model_copy(
            update={
                "enabled": self.enabled,
                "running": False,
                "cycle_in_progress": False,
                "interval_seconds": self.interval_seconds,
                "instruments": self.instruments,
                "current_instrument": None,
                "current_stage": None,
                "instruments_completed": 0,
                "instruments_total": len(self.instruments),
                "current_run_started_at": None,
                "next_cycle_at": None,
            }
        )

    def _persist_status(self) -> None:
        self.store.upsert(
            ORCHESTRATION_STATUS_COLLECTION,
            ORCHESTRATION_STATUS_KEY,
            self._status.model_dump(mode="json"),
        )

    def _update_progress(
        self,
        *,
        current_instrument: str | None,
        current_stage: str,
        instruments_completed: int,
        instruments_total: int,
    ) -> None:
        """Persist honest, bounded progress while the worker thread is active."""

        with self._state_lock:
            self._status = self._status.model_copy(
                update={
                    "current_instrument": current_instrument,
                    "current_stage": current_stage,
                    "instruments_completed": instruments_completed,
                    "instruments_total": instruments_total,
                }
            )
            self._persist_status()

    def _now(self) -> datetime:
        value = self.clock()
        if value.tzinfo is None:
            raise ValueError("orchestration clock must return a timezone-aware datetime")
        return value.astimezone(UTC)


def build_v4_orchestration_service(
    settings: AtlasSettings,
    database: Database,
    gateway: MT5GatewayProtocol,
    llm_client: AIReviewLLMClient | None = None,
) -> V4OrchestrationService:
    """Construct the production worker with the same durable V4 evidence store."""

    store = SqlModelRecordStore(database)
    snapshots = MarketSnapshotService(
        gateway,
        store,
        enabled=settings.mt5_enabled,
        expected_account_mode=settings.mt5_expected_account_mode,
    )
    features = V4FeatureService(store)
    contexts = V4ContextService(store)
    fred_macro = None
    fred_calendar = None
    if settings.fred_api_key is not None:
        fred_macro = FredMacroBundleProvider(FredEconomicProvider(settings.fred_api_key))
        fred_calendar = FredReleaseCalendarProvider(settings.fred_api_key)
    context_ingestion = V4ContextIngestionService(
        store,
        contexts,
        fred_macro=fred_macro,
        fred_calendar=fred_calendar,
        official_news=OfficialRssNewsProvider() if settings.official_news_enabled else None,
        cftc=CFTCPositioningClient(),
    )
    registry = PrimaryModelRegistry(store)
    dataset_builder = HistoricalPrimaryDatasetBuilder()
    dependency_engine = EvidenceDependencyEngine(store)
    meta_trainer = MetaLabelTrainer(store)
    historical_meta = HistoricalMetaResearchService(
        store,
        dataset_builder,
        dependency_engine,
        meta_trainer,
    )
    configured = settings.v4_orchestration_instruments or approved_universe()
    return V4OrchestrationService(
        store,
        snapshots,
        features,
        contexts,
        context_ingestion,
        PrimaryMLTrainer(store),
        registry,
        PrimaryMLEngine(registry),
        RobustnessValidator(store),
        SignalPublicationGate(store),
        enabled=settings.v4_orchestration_enabled,
        interval_seconds=settings.v4_orchestration_interval_seconds,
        instruments=configured,
        context_refresh_enabled=settings.v4_context_refresh_enabled,
        train_models=settings.v4_orchestration_train_models,
        auto_shadow=settings.v4_orchestration_auto_shadow,
        dataset_builder=dataset_builder,
        ai_reviews=AIReviewOrchestrator(
            store,
            llm_client or LLMClient(settings, max_retries=1),
            enabled=settings.v4_ai_review_enabled,
            candidate_quality_threshold=settings.v4_ai_candidate_threshold,
            timeout_seconds=settings.v4_ai_review_timeout_seconds,
            max_tokens=settings.v4_ai_review_max_tokens,
        ),
        historical_meta=historical_meta,
        meta_engine=MetaLabelEngine(store),
        calibration=ProbabilityCalibrationEngine(store),
        regime_stability=RegimeStabilityEngine(store),
        prediction_drift=PredictionDriftEngine(store),
        backtest=V4BacktestEngine(store),
        quant_validation=QuantValidationEngine(store),
        expectancy=ExpectancyEngine(store),
        structural_targets=StructuralTargetEngine(store),
        portfolio_exposure=PortfolioExposureEngine(store),
        risk_overlay=RiskOverlay(store),
        kronos=KronosShadowEngine(
            store,
            OfficialKronosProvider(
                settings.kronos_repository_path,
                model_id=settings.kronos_model_id,
                tokenizer_id=settings.kronos_tokenizer_id,
                device=settings.kronos_device,
                source_revision=settings.kronos_source_revision,
                preprocessing_audit_approved=settings.kronos_preprocessing_audit_approved,
                pretraining_cutoff_verified=settings.kronos_pretraining_cutoff_verified,
            ),
            enabled=settings.kronos_enabled,
            mode=KronosMode(settings.kronos_mode),
            sample_count=settings.kronos_sample_count,
            horizon_bars=settings.kronos_horizon_bars,
            effective_weight=settings.kronos_effective_weight,
            max_weight=settings.kronos_max_weight,
        ),
    )


__all__ = [
    "ORCHESTRATION_RUN_COLLECTION",
    "ORCHESTRATION_STATUS_COLLECTION",
    "OrchestrationBusy",
    "V4OrchestrationService",
    "build_v4_orchestration_service",
]
