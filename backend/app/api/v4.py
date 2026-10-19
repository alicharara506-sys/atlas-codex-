"""ATLAS V4 research-only market-data and health endpoints."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, HTTPException, Query, Response, status
from pydantic import BaseModel, ConfigDict

from app.api.system import HealthProbe
from app.core.config import AtlasSettings
from app.core.infrastructure import OllamaInventoryProbe
from app.data.market.pipeline import FredEconomicProvider
from app.llm.client import LLMClient
from app.models.database import Database, SqlModelRecordStore
from app.v4.ai_contracts import AIReviewBundle, AIReviewRequest, AIReviewResponse
from app.v4.ai_review import DEFAULT_ROLE_MODELS, AIReviewLLMClient, AIReviewOrchestrator
from app.v4.calibration import ProbabilityCalibrationEngine
from app.v4.calibration_contracts import (
    CalibratedProbability,
    CalibratedProbabilityRequest,
    CalibrationArtifact,
    CalibrationTrainingRequest,
)
from app.v4.context_contracts import ContextComputationRequest, ContextSet
from app.v4.context_ingestion import V4ContextIngestionService
from app.v4.context_service import V4ContextService
from app.v4.contracts import (
    DataQualityReport,
    FeatureSet,
    MarketSnapshot,
    MT5HealthReport,
    OfficialTimeframe,
)
from app.v4.feature_service import FeatureComputationError, V4FeatureService
from app.v4.health import V4OperationalHealthService
from app.v4.health_contracts import V4OperationalHealth
from app.v4.kronos import KronosShadowEngine
from app.v4.kronos_contracts import KronosForecast, KronosMode, KronosStatus
from app.v4.kronos_provider import OfficialKronosProvider
from app.v4.labeling import TripleBarrierLabeler
from app.v4.meta_contracts import (
    EvidenceDependencyReport,
    EvidenceDependencyRequest,
    MetaModelArtifact,
    MetaPrediction,
    MetaPredictionRequest,
    MetaTrainingRequest,
    MetaTrainingResult,
)
from app.v4.meta_labeling import EvidenceDependencyEngine, MetaLabelEngine, MetaLabelTrainer
from app.v4.ml_contracts import (
    CandidateRequest,
    ModelStatusRecord,
    ModelTrainingRequest,
    ModelTrainingResult,
    PrimaryCandidate,
    PrimaryModelArtifact,
    ShadowPromotionRequest,
)
from app.v4.mt5 import MT5Gateway, MT5GatewayProtocol
from app.v4.orchestration import OrchestrationBusy, V4OrchestrationService
from app.v4.orchestration_contracts import OrchestrationRun, OrchestrationStatus
from app.v4.positioning import CFTCPositioningClient
from app.v4.presentation import V4PresentationService
from app.v4.presentation_contracts import V4TerminalWorkspace
from app.v4.primary_ml import PrimaryMLEngine, PrimaryMLTrainer, PrimaryModelRegistry
from app.v4.providers import FredMacroBundleProvider, FredReleaseCalendarProvider, OfficialRssNewsProvider
from app.v4.quant_backtest import V4BacktestEngine
from app.v4.quant_contracts import (
    BacktestRequest,
    BacktestRun,
    ExperimentRecord,
    ExperimentSpec,
    HoldoutSealRequest,
    HoldoutSealResponse,
    RobustnessRequest,
    RobustnessResult,
    TripleBarrierRequest,
    TripleBarrierResult,
    WalkForwardRequest,
    WalkForwardResult,
)
from app.v4.quant_validation import (
    PredictionDriftEngine,
    QuantValidationEngine,
    RegimeStabilityEngine,
)
from app.v4.quant_validation_contracts import (
    PredictionDriftReport,
    PredictionDriftRequest,
    QuantValidationRequest,
    QuantValidationResult,
    RegimeStabilityReport,
    RegimeStabilityRequest,
)
from app.v4.risk_contracts import (
    ExpectancyReport,
    ExpectancyRequest,
    PortfolioExposureReport,
    PortfolioExposureRequest,
    RiskOverlayRequest,
    RiskOverlayResult,
    StructuralTargetPlan,
    StructuralTargetRequest,
)
from app.v4.risk_engine import (
    ExpectancyEngine,
    PortfolioExposureEngine,
    RiskOverlay,
    StructuralTargetEngine,
)
from app.v4.robustness import RobustnessValidator
from app.v4.signal_contracts import (
    CanonicalSignal,
    SignalPublicationRequest,
    SignalPublicationResult,
    SignalRevision,
    SignalRevisionRequest,
)
from app.v4.signal_engine import SignalPublicationGate, SignalRevisionService
from app.v4.snapshots import MarketSnapshotService, SnapshotUnavailable
from app.v4.symbols import (
    OFFICIAL_TIMEFRAMES,
    UnsupportedInstrumentError,
    approved_universe,
    normalize_instrument,
)
from app.v4.tracking_contracts import (
    SignalLineageBundle,
    SignalTrackingControlRequest,
    SignalTrackingEvent,
    SignalTrackingRequest,
    SignalTrackingResponse,
    SignalTrackingState,
)
from app.v4.tracking_engine import SignalLineageService, SignalTrackingEngine
from app.v4.validation import ExperimentRegistry, HoldoutVault, WalkForwardValidator


class V4OverviewResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    engine: Literal["ATLAS"] = "ATLAS"
    version: Literal["4.0.0"] = "4.0.0"
    phase: Literal[15] = 15
    status: Literal["operational"] = "operational"
    approved_universe: tuple[str, ...]
    official_timeframes: tuple[str, ...]
    mt5_enabled: bool
    context_refresh_enabled: bool
    fred_configured: bool
    official_news_enabled: bool
    ai_review_enabled: bool
    orchestration_enabled: bool
    orchestration_interval_seconds: int
    kronos_enabled: bool
    kronos_mode: str
    experimental_elliott_wave_enabled: bool
    experimental_fibonacci_enabled: bool
    analytical_signal_authority: Literal[True] = True
    research_only: Literal[True] = True
    execution_enabled: Literal[False] = False
    order_submission: Literal[False] = False


class V4DataHealthResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    instrument: str
    snapshot_id: str
    health: MT5HealthReport
    data_quality: DataQualityReport
    research_only: Literal[True] = True
    execution_enabled: Literal[False] = False


def build_v4_router(
    settings: AtlasSettings,
    database: Database,
    gateway: MT5GatewayProtocol | None = None,
    llm_client: AIReviewLLMClient | None = None,
    *,
    infrastructure_probe: HealthProbe | None = None,
    orchestrator: V4OrchestrationService | None = None,
) -> APIRouter:
    router = APIRouter(prefix="/api/v4", tags=["atlas-v4"])
    resolved_gateway = gateway or MT5Gateway(
        settings.mt5_base_url,
        settings.mt5_bridge_token,
        timeout_seconds=settings.mt5_timeout_seconds,
    )
    store = SqlModelRecordStore(database)
    service = MarketSnapshotService(
        resolved_gateway,
        store,
        enabled=settings.mt5_enabled,
        expected_account_mode=settings.mt5_expected_account_mode,
    )
    feature_service = V4FeatureService(store)
    context_service = V4ContextService(store)
    fred_macro = None
    fred_calendar = None
    if settings.fred_api_key is not None:
        fred_macro = FredMacroBundleProvider(FredEconomicProvider(settings.fred_api_key))
        fred_calendar = FredReleaseCalendarProvider(settings.fred_api_key)
    context_ingestion = V4ContextIngestionService(
        store,
        context_service,
        fred_macro=fred_macro,
        fred_calendar=fred_calendar,
        official_news=OfficialRssNewsProvider() if settings.official_news_enabled else None,
        cftc=CFTCPositioningClient(),
    )
    labeler = TripleBarrierLabeler()
    walk_forward = WalkForwardValidator()
    backtest_engine = V4BacktestEngine(store)
    robustness = RobustnessValidator(store)
    experiments = ExperimentRegistry(store)
    holdouts = HoldoutVault(store)
    model_trainer = PrimaryMLTrainer(store)
    model_registry = PrimaryModelRegistry(store)
    primary_ml = PrimaryMLEngine(model_registry)
    ai_reviews = AIReviewOrchestrator(
        store,
        llm_client or LLMClient(settings, max_retries=1),
        enabled=settings.v4_ai_review_enabled,
        candidate_quality_threshold=settings.v4_ai_candidate_threshold,
        timeout_seconds=settings.v4_ai_review_timeout_seconds,
        max_tokens=settings.v4_ai_review_max_tokens,
    )
    evidence_dependencies = EvidenceDependencyEngine(store)
    meta_trainer = MetaLabelTrainer(store)
    meta_engine = MetaLabelEngine(store)
    calibration = ProbabilityCalibrationEngine(store)
    regime_stability = RegimeStabilityEngine(store)
    prediction_drift = PredictionDriftEngine(store)
    quant_validation = QuantValidationEngine(store)
    expectancy = ExpectancyEngine(store)
    structural_targets = StructuralTargetEngine(store)
    portfolio_exposure = PortfolioExposureEngine(store)
    risk_overlay = RiskOverlay(store)
    signal_publication = SignalPublicationGate(store)
    signal_revisions = SignalRevisionService(store)
    signal_tracking = SignalTrackingEngine(store)
    signal_lineage = SignalLineageService(store)
    presentation = V4PresentationService(store)
    operational_health = V4OperationalHealthService(
        settings,
        database,
        infrastructure_probe or _UnavailableInfrastructureProbe(),
        service,
        store,
        OllamaInventoryProbe(
            settings.providers["ollama"].base_url,
            tuple(DEFAULT_ROLE_MODELS.values()),
        ),
    )
    kronos = KronosShadowEngine(
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
    )

    @router.get("/health", response_model=V4OverviewResponse)
    def overview() -> V4OverviewResponse:
        return V4OverviewResponse(
            approved_universe=approved_universe(),
            official_timeframes=tuple(timeframe.value for timeframe in OFFICIAL_TIMEFRAMES),
            mt5_enabled=settings.mt5_enabled,
            context_refresh_enabled=settings.v4_context_refresh_enabled,
            fred_configured=settings.fred_api_key is not None,
            official_news_enabled=settings.official_news_enabled,
            ai_review_enabled=settings.v4_ai_review_enabled,
            orchestration_enabled=settings.v4_orchestration_enabled,
            orchestration_interval_seconds=settings.v4_orchestration_interval_seconds,
            kronos_enabled=settings.kronos_enabled,
            kronos_mode=settings.kronos_mode,
            experimental_elliott_wave_enabled=settings.enable_elliott_wave,
            experimental_fibonacci_enabled=settings.enable_fibonacci,
        )

    @router.get("/kronos/status", response_model=KronosStatus)
    def kronos_status() -> KronosStatus:
        return kronos.status()

    @router.get("/kronos/{instrument}/{timeframe}/latest", response_model=KronosForecast)
    def latest_kronos_forecast(instrument: str, timeframe: OfficialTimeframe) -> KronosForecast:
        try:
            canonical = normalize_instrument(instrument).canonical
        except UnsupportedInstrumentError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        forecast = kronos.latest(canonical, timeframe)
        if forecast is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No Kronos forecast has been persisted")
        return forecast

    @router.post("/kronos/{instrument}/{timeframe}/forecast", response_model=KronosForecast)
    def run_kronos_forecast(instrument: str, timeframe: OfficialTimeframe) -> KronosForecast:
        snapshot = _capture(service, instrument, persist=True)
        return kronos.forecast(snapshot, timeframe, persist=True)

    @router.get("/orchestration/status", response_model=OrchestrationStatus)
    def orchestration_status() -> OrchestrationStatus:
        if orchestrator is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="V4 orchestration service is unavailable",
            )
        return orchestrator.status()

    @router.get("/orchestration/runs", response_model=tuple[OrchestrationRun, ...])
    def orchestration_runs(limit: int = Query(default=20, ge=1, le=200)) -> tuple[OrchestrationRun, ...]:
        if orchestrator is None:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="V4 orchestration service is unavailable",
            )
        return orchestrator.runs(limit=limit)

    @router.post("/orchestration/run", response_model=OrchestrationRun)
    async def run_orchestration_cycle(
        instrument: str | None = Query(default=None),
    ) -> OrchestrationRun:
        if orchestrator is None or not orchestrator.enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="V4 background orchestration is disabled",
            )
        try:
            scope: tuple[str, ...] | None = None
            if instrument is not None:
                canonical = normalize_instrument(instrument).canonical
                if canonical not in orchestrator.instruments:
                    raise HTTPException(
                        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                        detail=f"{canonical} is not enabled for V4 orchestration",
                    )
                scope = (canonical,)
            return await orchestrator.run_once(scope)
        except OrchestrationBusy as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
        except UnsupportedInstrumentError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.get("/health/operational", response_model=V4OperationalHealth)
    def health_operational(
        response: Response,
        instrument: str = Query(default="XAUUSD"),
    ) -> V4OperationalHealth:
        try:
            report = operational_health.report(instrument)
        except UnsupportedInstrumentError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        if report.status == "NOT_READY":
            response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return report

    @router.get("/terminal", response_model=V4TerminalWorkspace)
    def terminal_workspace(instrument: str = Query(default="XAUUSD")) -> V4TerminalWorkspace:
        try:
            return presentation.workspace(instrument)
        except UnsupportedInstrumentError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.get("/health/mt5", response_model=MT5HealthReport)
    def mt5_health(instrument: str = Query(default="XAUUSD")) -> MT5HealthReport:
        try:
            return service.health(instrument)
        except UnsupportedInstrumentError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.get("/health/data", response_model=V4DataHealthResponse)
    def data_health(instrument: str = Query(default="XAUUSD")) -> V4DataHealthResponse:
        snapshot = _capture(service, instrument, persist=False)
        return V4DataHealthResponse(
            instrument=snapshot.instrument,
            snapshot_id=snapshot.snapshot_id,
            health=snapshot.health,
            data_quality=snapshot.data_quality,
        )

    @router.post("/market/{instrument}/snapshot", response_model=MarketSnapshot)
    def capture_snapshot(instrument: str) -> MarketSnapshot:
        return _capture(service, instrument, persist=True)

    @router.get("/market/{instrument}/snapshot", response_model=MarketSnapshot)
    def latest_snapshot(instrument: str) -> MarketSnapshot:
        try:
            snapshot = service.latest(instrument)
        except UnsupportedInstrumentError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        if snapshot is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No V4 snapshot has been persisted")
        return snapshot

    @router.post("/features/{instrument}/compute", response_model=FeatureSet)
    def compute_features(instrument: str) -> FeatureSet:
        snapshot = _capture(service, instrument, persist=True)
        try:
            return feature_service.compute(snapshot, persist=True)
        except FeatureComputationError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"code": "FEATURE_COMPUTATION_BLOCKED", "message": str(exc), "signal_authority": False},
            ) from exc

    @router.get("/features/{instrument}/latest", response_model=FeatureSet)
    def latest_features(instrument: str) -> FeatureSet:
        try:
            features = feature_service.latest(instrument)
        except UnsupportedInstrumentError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        if features is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No V4 feature set has been persisted")
        return features

    @router.post("/context/{instrument}/compute", response_model=ContextSet)
    def compute_context(instrument: str, request: ContextComputationRequest) -> ContextSet:
        try:
            return context_service.compute(instrument, request, persist=True)
        except (UnsupportedInstrumentError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.post("/context/{instrument}/refresh", response_model=ContextSet)
    def refresh_context(instrument: str) -> ContextSet:
        if not settings.v4_context_refresh_enabled:
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail={
                    "code": "V4_CONTEXT_REFRESH_DISABLED",
                    "message": "Provider-managed V4 context refresh is disabled by configuration",
                    "signal_authority": False,
                    "execution_enabled": False,
                },
            )
        try:
            return context_ingestion.refresh(instrument, persist=True)
        except (UnsupportedInstrumentError, ValueError) as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.get("/context/{instrument}/latest", response_model=ContextSet)
    def latest_context(instrument: str) -> ContextSet:
        try:
            context = context_service.latest(instrument)
        except UnsupportedInstrumentError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        if context is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="No V4 context set has been persisted")
        return context

    @router.post("/quant/labels", response_model=TripleBarrierResult)
    def triple_barrier_labels(request: TripleBarrierRequest) -> TripleBarrierResult:
        try:
            return labeler.label(request.instrument, request.bars, request.direction, request.config)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.post("/quant/walk-forward", response_model=WalkForwardResult)
    def walk_forward_splits(request: WalkForwardRequest) -> WalkForwardResult:
        try:
            return walk_forward.split(request.samples, request.config)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.post("/quant/backtests", response_model=BacktestRun)
    def run_backtest(request: BacktestRequest) -> BacktestRun:
        try:
            return backtest_engine.run(
                request.instrument,
                request.bars,
                request.plans,
                request.cost_model,
                persist=True,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.post("/quant/robustness", response_model=RobustnessResult)
    def run_robustness(request: RobustnessRequest) -> RobustnessResult:
        try:
            return robustness.validate(
                request.returns,
                variants_tested=request.variants_tested,
                bootstrap_samples=request.bootstrap_samples,
                block_size=request.block_size,
                seed=request.seed,
                stress_cost_bps=request.stress_cost_bps,
            )
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.post("/quant/experiments", response_model=ExperimentRecord)
    def register_experiment(request: ExperimentSpec) -> ExperimentRecord:
        return experiments.register(request)

    @router.post("/quant/holdouts/seal", response_model=HoldoutSealResponse)
    def seal_holdout(request: HoldoutSealRequest) -> HoldoutSealResponse:
        try:
            development, manifest = holdouts.seal(request.samples, holdout_size=request.holdout_size)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
        return HoldoutSealResponse(
            manifest=manifest,
            development_sample_ids=tuple(sample.sample_id for sample in development),
        )

    @router.post("/models/train", response_model=ModelTrainingResult)
    def train_primary_models(request: ModelTrainingRequest) -> ModelTrainingResult:
        try:
            return model_trainer.train(request.train, request.validation, request.test, request.config)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.get("/models/{model_id}", response_model=PrimaryModelArtifact)
    def get_primary_model(model_id: str) -> PrimaryModelArtifact:
        try:
            return model_registry.artifact(model_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.get("/models/{model_id}/status", response_model=ModelStatusRecord)
    def get_primary_model_status(model_id: str) -> ModelStatusRecord:
        try:
            return model_registry.current_status(model_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.post("/models/{model_id}/shadow", response_model=ModelStatusRecord)
    def promote_primary_model_to_shadow(
        model_id: str,
        request: ShadowPromotionRequest,
    ) -> ModelStatusRecord:
        try:
            return model_registry.promote_to_shadow(model_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/models/candidates", response_model=PrimaryCandidate)
    def generate_primary_candidate(request: CandidateRequest) -> PrimaryCandidate:
        try:
            return primary_ml.generate_candidate(request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/ai/reviews", response_model=AIReviewResponse)
    async def run_ai_review(request: AIReviewRequest) -> AIReviewResponse:
        try:
            return await ai_reviews.review(request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.get("/ai/reviews/{bundle_id}", response_model=AIReviewBundle)
    def get_ai_review(bundle_id: str) -> AIReviewBundle:
        try:
            return ai_reviews.get(bundle_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.post("/evidence/dependencies", response_model=EvidenceDependencyReport)
    def analyze_evidence_dependencies(
        request: EvidenceDependencyRequest,
    ) -> EvidenceDependencyReport:
        try:
            return evidence_dependencies.analyze(request.observations)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.post("/meta/train", response_model=MetaTrainingResult)
    def train_meta_model(request: MetaTrainingRequest) -> MetaTrainingResult:
        try:
            return meta_trainer.train(request.samples, request.config)
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.get("/meta/models/{meta_model_id}", response_model=MetaModelArtifact)
    def get_meta_model(meta_model_id: str) -> MetaModelArtifact:
        try:
            return meta_engine.artifact(meta_model_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.post("/meta/predictions", response_model=MetaPrediction)
    def predict_meta_label(request: MetaPredictionRequest) -> MetaPrediction:
        try:
            return meta_engine.predict(request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/calibration/train", response_model=CalibrationArtifact)
    def train_calibration(request: CalibrationTrainingRequest) -> CalibrationArtifact:
        try:
            return calibration.train(request.meta_model_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.get("/calibration/{calibration_id}", response_model=CalibrationArtifact)
    def get_calibration(calibration_id: str) -> CalibrationArtifact:
        try:
            return calibration.artifact(calibration_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.post("/calibration/apply", response_model=CalibratedProbability)
    def apply_calibration(
        request: CalibratedProbabilityRequest,
    ) -> CalibratedProbability:
        try:
            return calibration.apply(request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/quant/regime-stability", response_model=RegimeStabilityReport)
    def assess_regime_stability(
        request: RegimeStabilityRequest,
    ) -> RegimeStabilityReport:
        try:
            return regime_stability.assess(request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.post("/quant/drift", response_model=PredictionDriftReport)
    def assess_prediction_drift(
        request: PredictionDriftRequest,
    ) -> PredictionDriftReport:
        try:
            return prediction_drift.assess(request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/quant/validate", response_model=QuantValidationResult)
    def validate_quant_evidence(
        request: QuantValidationRequest,
    ) -> QuantValidationResult:
        try:
            return quant_validation.validate(request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/risk/expectancy", response_model=ExpectancyReport)
    def evaluate_expectancy(request: ExpectancyRequest) -> ExpectancyReport:
        try:
            return expectancy.evaluate(request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/risk/targets", response_model=StructuralTargetPlan)
    def build_structural_targets(
        request: StructuralTargetRequest,
    ) -> StructuralTargetPlan:
        try:
            return structural_targets.build(request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/risk/exposure", response_model=PortfolioExposureReport)
    def assess_portfolio_exposure(
        request: PortfolioExposureRequest,
    ) -> PortfolioExposureReport:
        try:
            return portfolio_exposure.assess(request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc

    @router.post("/risk/overlay", response_model=RiskOverlayResult)
    def evaluate_risk_overlay(
        request: RiskOverlayRequest,
    ) -> RiskOverlayResult:
        try:
            return risk_overlay.evaluate(request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.post("/signals/evaluate", response_model=SignalPublicationResult)
    def evaluate_signal_publication(
        request: SignalPublicationRequest,
    ) -> SignalPublicationResult:
        try:
            return signal_publication.evaluate(request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.get("/signals/{signal_id}", response_model=CanonicalSignal)
    def get_canonical_signal(signal_id: str) -> CanonicalSignal:
        try:
            return signal_publication.get(signal_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.post("/signals/{signal_id}/revisions", response_model=SignalRevision)
    def revise_canonical_signal(
        signal_id: str,
        request: SignalRevisionRequest,
    ) -> SignalRevision:
        try:
            return signal_revisions.revise(signal_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.get("/signals/{signal_id}/revisions", response_model=tuple[SignalRevision, ...])
    def list_signal_revisions(signal_id: str) -> tuple[SignalRevision, ...]:
        try:
            return signal_revisions.list(signal_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.post("/signals/{signal_id}/track", response_model=SignalTrackingResponse)
    def track_canonical_signal(
        signal_id: str,
        request: SignalTrackingRequest,
    ) -> SignalTrackingResponse:
        try:
            return signal_tracking.track(signal_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    @router.get("/signals/{signal_id}/tracking", response_model=SignalTrackingState)
    def get_signal_tracking_state(signal_id: str) -> SignalTrackingState:
        try:
            return signal_tracking.state(signal_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.get("/signals/{signal_id}/events", response_model=tuple[SignalTrackingEvent, ...])
    def get_signal_tracking_events(signal_id: str) -> tuple[SignalTrackingEvent, ...]:
        try:
            return signal_tracking.events(signal_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.post("/signals/{signal_id}/control", response_model=SignalTrackingResponse)
    def control_signal_tracking(
        signal_id: str,
        request: SignalTrackingControlRequest,
    ) -> SignalTrackingResponse:
        try:
            return signal_tracking.control(signal_id, request)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    @router.get("/signals/{signal_id}/lineage", response_model=SignalLineageBundle)
    def get_signal_lineage(signal_id: str) -> SignalLineageBundle:
        try:
            return signal_lineage.reconstruct(signal_id)
        except KeyError as exc:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc

    return router


class _UnavailableInfrastructureProbe:
    def healthy(self) -> bool:
        return False


def _capture(service: MarketSnapshotService, instrument: str, *, persist: bool) -> MarketSnapshot:
    try:
        return service.capture(instrument, persist=persist)
    except UnsupportedInstrumentError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except SnapshotUnavailable as exc:
        detail: dict[str, object] = {"code": exc.code, "message": str(exc), "execution_enabled": False}
        if exc.health is not None:
            detail["health"] = exc.health.model_dump(mode="json")
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=detail) from exc


__all__ = ["V4DataHealthResponse", "V4OverviewResponse", "build_v4_router"]
