"""Lazy adapter for a pinned checkout of the official Kronos repository."""

from __future__ import annotations

import importlib
import importlib.util
import sys
from pathlib import Path
from threading import RLock
from typing import Any

from app.v4.contracts import OfficialTimeframe, V4Bar

_TIMEFRAME_MINUTES = {
    OfficialTimeframe.M15: 15,
    OfficialTimeframe.H1: 60,
    OfficialTimeframe.H4: 240,
    OfficialTimeframe.D1: 1_440,
}


class OfficialKronosProvider:
    """Load Kronos only on first inference and keep failures outside core Atlas."""

    def __init__(
        self,
        repository_path: str | None,
        *,
        model_id: str,
        tokenizer_id: str,
        device: str | None,
        source_revision: str | None,
        preprocessing_audit_approved: bool,
        pretraining_cutoff_verified: bool,
    ) -> None:
        self.repository_path = Path(repository_path).expanduser().resolve() if repository_path else None
        self.model_id = model_id
        self.tokenizer_id = tokenizer_id
        self.device = device
        self.source_revision = source_revision
        self.provider_preprocessing_verified = preprocessing_audit_approved
        self.pretraining_cutoff_verified = pretraining_cutoff_verified
        revision = source_revision or "UNPINNED"
        self.model_version = f"{model_id}@{revision}"
        self._predictor: Any | None = None
        self._lock = RLock()

    def availability(self) -> tuple[bool, str | None]:
        if self.repository_path is None:
            return False, "KRONOS_REPOSITORY_PATH is not configured"
        if not (self.repository_path / "model" / "__init__.py").is_file():
            return False, "Configured Kronos repository does not contain model/__init__.py"
        if self.source_revision is None:
            return False, "KRONOS_SOURCE_REVISION must pin the audited source revision"
        missing = tuple(
            module
            for module in ("numpy", "pandas", "torch", "huggingface_hub", "tqdm")
            if importlib.util.find_spec(module) is None
        )
        if missing:
            return False, f"Kronos runtime dependencies are missing: {', '.join(missing)}"
        if not self.provider_preprocessing_verified:
            return False, "Kronos preprocessing leakage audit has not been approved"
        return True, None

    def generate_paths(
        self,
        bars: tuple[V4Bar, ...],
        timeframe: OfficialTimeframe,
        *,
        horizon_bars: int,
        sample_count: int,
        seed: int,
    ) -> tuple[tuple[float, ...], ...]:
        available, reason = self.availability()
        if not available:
            raise RuntimeError(reason or "Kronos provider is unavailable")
        predictor = self._load_predictor()
        pandas = importlib.import_module("pandas")
        torch = importlib.import_module("torch")
        frame = pandas.DataFrame(
            {
                "open": [bar.open for bar in bars],
                "high": [bar.high for bar in bars],
                "low": [bar.low for bar in bars],
                "close": [bar.close for bar in bars],
                "volume": [bar.volume for bar in bars],
            }
        )
        input_timestamps = pandas.Series([bar.timestamp for bar in bars])
        frequency_minutes = _TIMEFRAME_MINUTES[timeframe]
        future_timestamps = pandas.Series(
            pandas.date_range(
                start=bars[-1].timestamp,
                periods=horizon_bars + 1,
                freq=f"{frequency_minutes}min",
            )[1:]
        )
        paths: list[tuple[float, ...]] = []
        # The public KronosPredictor averages the internal sample dimension.
        # Repeating one-sample calls preserves individual scenario paths. This
        # is intentionally background-only because it is compute intensive.
        for index in range(sample_count):
            torch.manual_seed(seed + index)
            if hasattr(torch, "cuda") and torch.cuda.is_available():
                torch.cuda.manual_seed_all(seed + index)
            prediction = predictor.predict(
                df=frame,
                x_timestamp=input_timestamps,
                y_timestamp=future_timestamps,
                pred_len=horizon_bars,
                T=1.0,
                top_k=0,
                top_p=0.9,
                sample_count=1,
                verbose=False,
            )
            paths.append(tuple(float(value) for value in prediction["close"].tolist()))
        return tuple(paths)

    def _load_predictor(self) -> Any:
        with self._lock:
            if self._predictor is not None:
                return self._predictor
            if self.repository_path is None:
                raise RuntimeError("Kronos repository path is unavailable")
            repository = str(self.repository_path)
            if repository not in sys.path:
                sys.path.insert(0, repository)
            existing = sys.modules.get("model")
            if existing is not None:
                module_file = Path(str(getattr(existing, "__file__", ""))).resolve()
                if self.repository_path not in module_file.parents:
                    raise RuntimeError("Python module name 'model' is already owned by another package")
            module = importlib.import_module("model")
            tokenizer_class = module.KronosTokenizer
            model_class = module.Kronos
            predictor_class = module.KronosPredictor
            tokenizer = tokenizer_class.from_pretrained(self.tokenizer_id)
            model = model_class.from_pretrained(self.model_id)
            self._predictor = predictor_class(
                model,
                tokenizer,
                device=self.device,
                max_context=512,
            )
            return self._predictor


__all__ = ["OfficialKronosProvider"]
