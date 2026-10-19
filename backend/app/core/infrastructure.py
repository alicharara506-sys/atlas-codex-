"""Bounded infrastructure health probes with no import-time connections."""

from __future__ import annotations

from dataclasses import dataclass

from redis import Redis
from redis.exceptions import RedisError

from app.core.http import JsonRequest, JsonTransport, TransportError, UrllibJsonTransport


class RedisProbe:
    def __init__(self, url: str, *, timeout_seconds: float = 1.0) -> None:
        self.url = url
        self.timeout_seconds = max(0.1, min(5.0, timeout_seconds))

    def healthy(self) -> bool:
        client: Redis = Redis.from_url(
            self.url,
            socket_connect_timeout=self.timeout_seconds,
            socket_timeout=self.timeout_seconds,
            decode_responses=False,
        )
        try:
            return bool(client.ping())
        except RedisError:
            return False
        finally:
            client.close()


@dataclass(frozen=True, slots=True)
class LocalModelInventory:
    """Credential-free result from the Ollama model inventory endpoint."""

    reachable: bool
    available_models: tuple[str, ...]
    missing_models: tuple[str, ...]
    issue: str | None = None

    @property
    def healthy(self) -> bool:
        return self.reachable and not self.missing_models


class OllamaInventoryProbe:
    """Verify all configured local reviewer models without running inference."""

    def __init__(
        self,
        base_url: str,
        expected_models: tuple[str, ...],
        *,
        timeout_seconds: float = 1.5,
        transport: JsonTransport | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.expected_models = expected_models
        self.timeout_seconds = max(0.2, min(5.0, timeout_seconds))
        self.transport = transport or UrllibJsonTransport()

    def check(self) -> LocalModelInventory:
        try:
            payload = self.transport.send(
                JsonRequest(method="GET", url=f"{self.base_url}/api/tags", timeout_seconds=self.timeout_seconds)
            )
        except TransportError:
            return LocalModelInventory(False, (), self.expected_models, "OLLAMA_UNREACHABLE")
        if not isinstance(payload, dict):
            return LocalModelInventory(False, (), self.expected_models, "OLLAMA_INVALID_INVENTORY")
        raw_models = payload.get("models")
        if not isinstance(raw_models, list):
            return LocalModelInventory(False, (), self.expected_models, "OLLAMA_INVALID_INVENTORY")
        installed = tuple(
            sorted(
                {
                    str(row.get("name") or row.get("model"))
                    for row in raw_models
                    if isinstance(row, dict) and (row.get("name") or row.get("model"))
                }
            )
        )
        available = tuple(model for model in self.expected_models if _model_installed(model, installed))
        missing = tuple(model for model in self.expected_models if model not in available)
        return LocalModelInventory(True, available, missing, "OLLAMA_MODELS_MISSING" if missing else None)


def _model_installed(expected: str, installed: tuple[str, ...]) -> bool:
    if ":" in expected:
        return expected in installed
    return any(model == expected or model.startswith(f"{expected}:") for model in installed)


__all__ = ["LocalModelInventory", "OllamaInventoryProbe", "RedisProbe"]
