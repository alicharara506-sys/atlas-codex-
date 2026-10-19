"""Environment-backed Phase 1 configuration without import-time side effects."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from urllib.parse import urlparse


class ConfigurationError(ValueError):
    """Raised when provider configuration is unsafe or incomplete."""


@dataclass(frozen=True, slots=True)
class ProviderSettings:
    """Resolved configuration for one LLM provider."""

    name: str
    base_url: str
    api_key: str | None = field(repr=False)
    default_model: str | None
    local: bool = False

    @property
    def configured(self) -> bool:
        return self.local or bool(self.api_key and self.default_model)


@dataclass(frozen=True, slots=True)
class AtlasSettings:
    """Small immutable settings object used by Phase 1 components."""

    providers: dict[str, ProviderSettings]
    sec_user_agent: str | None
    database_url: str = field(repr=False)
    redis_url: str = field(repr=False)
    mt5_enabled: bool = False
    mt5_base_url: str = "http://127.0.0.1:8765"
    mt5_bridge_token: str | None = field(default=None, repr=False)
    mt5_timeout_seconds: float = 3.0
    mt5_expected_account_mode: str | None = None
    enable_elliott_wave: bool = False
    enable_fibonacci: bool = False
    fred_api_key: str | None = field(default=None, repr=False)
    v4_context_refresh_enabled: bool = False
    official_news_enabled: bool = True
    v4_ai_review_enabled: bool = False
    v4_ai_candidate_threshold: float = 0.55
    v4_ai_review_timeout_seconds: float = 30.0
    v4_ai_review_max_tokens: int = 120
    v4_orchestration_enabled: bool = False
    v4_orchestration_interval_seconds: int = 300
    v4_orchestration_instruments: tuple[str, ...] = ()
    v4_orchestration_train_models: bool = True
    v4_orchestration_auto_shadow: bool = False
    kronos_enabled: bool = False
    kronos_mode: str = "SHADOW"
    kronos_repository_path: str | None = None
    kronos_model_id: str = "NeoQuasar/Kronos-base"
    kronos_tokenizer_id: str = "NeoQuasar/Kronos-Tokenizer-base"
    kronos_device: str | None = None
    kronos_sample_count: int = 200
    kronos_horizon_bars: int = 12
    kronos_effective_weight: float = 0.0
    kronos_max_weight: float = 0.25
    kronos_source_revision: str | None = None
    kronos_preprocessing_audit_approved: bool = False
    kronos_pretraining_cutoff_verified: bool = False
    atlas_admin_token: str | None = field(default=None, repr=False)
    max_request_bytes: int = 2_097_152
    mutation_rate_limit_per_minute: int = 120

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> AtlasSettings:
        source = dict(os.environ if env is None else env)

        def value(name: str, default: str | None = None) -> str | None:
            row = source.get(name, default)
            if row is None:
                return None
            stripped = row.strip()
            return stripped or None

        def boolean(name: str, default: bool = False) -> bool:
            raw = value(name)
            if raw is None:
                return default
            normalized = raw.casefold()
            if normalized in {"1", "true", "yes", "on"}:
                return True
            if normalized in {"0", "false", "no", "off"}:
                return False
            raise ConfigurationError(f"{name} must be true or false")

        def bounded_float(name: str, default: float, minimum: float, maximum: float) -> float:
            raw = value(name)
            try:
                parsed = default if raw is None else float(raw)
            except ValueError as exc:
                raise ConfigurationError(f"{name} must be numeric") from exc
            if not minimum <= parsed <= maximum:
                raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
            return parsed

        def bounded_int(name: str, default: int, minimum: int, maximum: int) -> int:
            raw = value(name)
            try:
                parsed = default if raw is None else int(raw)
            except ValueError as exc:
                raise ConfigurationError(f"{name} must be an integer") from exc
            if not minimum <= parsed <= maximum:
                raise ConfigurationError(f"{name} must be between {minimum} and {maximum}")
            return parsed

        google_key = value("GOOGLE_API_KEY") or value("GEMINI_API_KEY")
        providers = {
            "openai": ProviderSettings("openai", value("OPENAI_BASE_URL", "https://api.openai.com/v1") or "", value("OPENAI_API_KEY"), value("OPENAI_MODEL")),
            "anthropic": ProviderSettings("anthropic", value("ANTHROPIC_BASE_URL", "https://api.anthropic.com/v1") or "", value("ANTHROPIC_API_KEY"), value("ANTHROPIC_MODEL")),
            "google": ProviderSettings("google", value("GOOGLE_BASE_URL", "https://generativelanguage.googleapis.com/v1beta") or "", google_key, value("GOOGLE_MODEL")),
            "groq": ProviderSettings("groq", value("GROQ_BASE_URL", "https://api.groq.com/openai/v1") or "", value("GROQ_API_KEY"), value("GROQ_MODEL")),
            "atlas_cloud": ProviderSettings("atlas_cloud", value("ATLAS_CLOUD_BASE_URL", "https://api.atlas.invalid/v1") or "", value("ATLAS_API_KEY"), value("ATLAS_CLOUD_MODEL")),
            "ollama": ProviderSettings("ollama", value("OLLAMA_BASE_URL", "http://127.0.0.1:11434") or "", value("OLLAMA_API_KEY"), value("OLLAMA_MODEL", "ministral-3:3b"), local=True),
        }
        for provider in providers.values():
            _validate_provider_url(provider)
        mt5_port = value("MT5_BRIDGE_PORT", "8765") or "8765"
        mt5_base_url = value("MT5_BRIDGE_BASE_URL", f"http://127.0.0.1:{mt5_port}") or ""
        _validate_loopback_url(mt5_base_url, "MT5 bridge")
        expected_account_mode = value("MT5_EXPECTED_ACCOUNT_MODE")
        if expected_account_mode is not None:
            expected_account_mode = expected_account_mode.upper()
            if expected_account_mode not in {"DEMO", "REAL", "CONTEST"}:
                raise ConfigurationError("MT5_EXPECTED_ACCOUNT_MODE must be DEMO, REAL, or CONTEST")
        fred_api_key = value("FRED_API_KEY")
        if fred_api_key is not None and len(fred_api_key) != 32:
            raise ConfigurationError("FRED_API_KEY must contain 32 characters")
        admin_token = value("ATLAS_ADMIN_TOKEN")
        if admin_token is not None and len(admin_token) < 32:
            raise ConfigurationError("ATLAS_ADMIN_TOKEN must contain at least 32 characters")
        orchestration_instruments = tuple(
            dict.fromkeys(
                item.strip().upper()
                for item in (value("V4_ORCHESTRATION_INSTRUMENTS", "") or "").split(",")
                if item.strip()
            )
        )
        kronos_mode = (value("KRONOS_MODE", "SHADOW") or "SHADOW").upper()
        if kronos_mode not in {"SHADOW", "PRODUCTION"}:
            raise ConfigurationError("KRONOS_MODE must be SHADOW or PRODUCTION")
        kronos_sample_count = bounded_int("KRONOS_SAMPLE_COUNT", 200, 200, 1_000)
        if kronos_sample_count not in {200, 500, 1_000}:
            raise ConfigurationError("KRONOS_SAMPLE_COUNT must be 200, 500, or 1000")
        kronos_max_weight = bounded_float("KRONOS_MAX_WEIGHT", 0.25, 0.0, 0.25)
        kronos_effective_weight = bounded_float("KRONOS_EFFECTIVE_WEIGHT", 0.0, 0.0, 0.25)
        if kronos_effective_weight > kronos_max_weight:
            raise ConfigurationError("KRONOS_EFFECTIVE_WEIGHT cannot exceed KRONOS_MAX_WEIGHT")
        if kronos_mode == "SHADOW" and kronos_effective_weight != 0:
            raise ConfigurationError("KRONOS shadow mode requires KRONOS_EFFECTIVE_WEIGHT=0")
        return cls(
            providers=providers,
            sec_user_agent=value("SEC_USER_AGENT"),
            database_url=value("DATABASE_URL", "postgresql+psycopg://atlas:atlas@127.0.0.1:5432/atlas") or "",
            redis_url=value("REDIS_URL", "redis://127.0.0.1:6379/0") or "",
            mt5_enabled=boolean("MT5_ENABLED"),
            mt5_base_url=mt5_base_url.rstrip("/"),
            mt5_bridge_token=value("MT5_BRIDGE_TOKEN"),
            mt5_timeout_seconds=bounded_float("MT5_TIMEOUT_SECONDS", 3.0, 0.5, 10.0),
            mt5_expected_account_mode=expected_account_mode,
            enable_elliott_wave=boolean("ENABLE_ELLIOTT_WAVE"),
            enable_fibonacci=boolean("ENABLE_FIBONACCI"),
            fred_api_key=fred_api_key,
            v4_context_refresh_enabled=boolean("V4_CONTEXT_REFRESH_ENABLED"),
            official_news_enabled=boolean("OFFICIAL_NEWS_ENABLED", default=True),
            v4_ai_review_enabled=boolean("V4_AI_REVIEW_ENABLED"),
            v4_ai_candidate_threshold=bounded_float("V4_AI_CANDIDATE_THRESHOLD", 0.55, 0.5, 1.0),
            v4_ai_review_timeout_seconds=bounded_float(
                "V4_AI_REVIEW_TIMEOUT_SECONDS", 30.0, 5.0, 60.0
            ),
            v4_ai_review_max_tokens=bounded_int("V4_AI_REVIEW_MAX_TOKENS", 120, 50, 300),
            v4_orchestration_enabled=boolean("V4_ORCHESTRATION_ENABLED"),
            v4_orchestration_interval_seconds=bounded_int(
                "V4_ORCHESTRATION_INTERVAL_SECONDS", 300, 10, 86_400
            ),
            v4_orchestration_instruments=orchestration_instruments,
            v4_orchestration_train_models=boolean("V4_ORCHESTRATION_TRAIN_MODELS", default=True),
            v4_orchestration_auto_shadow=boolean("V4_ORCHESTRATION_AUTO_SHADOW", default=False),
            kronos_enabled=boolean("KRONOS_ENABLED"),
            kronos_mode=kronos_mode,
            kronos_repository_path=value("KRONOS_REPOSITORY_PATH"),
            kronos_model_id=value("KRONOS_MODEL_ID", "NeoQuasar/Kronos-base")
            or "NeoQuasar/Kronos-base",
            kronos_tokenizer_id=value(
                "KRONOS_TOKENIZER_ID",
                "NeoQuasar/Kronos-Tokenizer-base",
            )
            or "NeoQuasar/Kronos-Tokenizer-base",
            kronos_device=value("KRONOS_DEVICE"),
            kronos_sample_count=kronos_sample_count,
            kronos_horizon_bars=bounded_int("KRONOS_HORIZON_BARS", 12, 1, 512),
            kronos_effective_weight=kronos_effective_weight,
            kronos_max_weight=kronos_max_weight,
            kronos_source_revision=value("KRONOS_SOURCE_REVISION"),
            kronos_preprocessing_audit_approved=boolean("KRONOS_PREPROCESSING_AUDIT_APPROVED"),
            kronos_pretraining_cutoff_verified=boolean("KRONOS_PRETRAINING_CUTOFF_VERIFIED"),
            atlas_admin_token=admin_token,
            max_request_bytes=bounded_int("ATLAS_MAX_REQUEST_BYTES", 2_097_152, 65_536, 16_777_216),
            mutation_rate_limit_per_minute=bounded_int("ATLAS_MUTATION_RATE_LIMIT", 120, 1, 10_000),
        )


def _validate_provider_url(provider: ProviderSettings) -> None:
    parsed = urlparse(provider.base_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ConfigurationError(f"Invalid base URL for provider '{provider.name}'")
    if parsed.username or parsed.password or parsed.fragment:
        raise ConfigurationError(f"Unsafe base URL for provider '{provider.name}'")
    if provider.local and parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ConfigurationError("The local Ollama provider must use a loopback host")


def _validate_loopback_url(url: str, label: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme != "http" or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}:
        raise ConfigurationError(f"{label} must use a local HTTP loopback address")
    if parsed.username or parsed.password or parsed.query or parsed.fragment:
        raise ConfigurationError(f"Unsafe base URL for {label}")


__all__ = ["AtlasSettings", "ConfigurationError", "ProviderSettings"]
