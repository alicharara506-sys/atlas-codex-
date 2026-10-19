"""Unified structured-output client for cloud and local LLM providers.

Provider availability is configuration only. Constructing this client performs
no network request. Callers must explicitly choose a provider for each call.
"""

from __future__ import annotations

import asyncio
import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from threading import RLock
from typing import Any, Protocol
from urllib.parse import quote

from app.core.config import AtlasSettings, ProviderSettings
from app.core.http import JsonRequest, JsonTransport, TransportError, UrllibJsonTransport


class LLMClientError(RuntimeError):
    """Base error for safe, fail-closed LLM calls."""


class ProviderNotConfigured(LLMClientError):
    pass


class StructuredOutputError(LLMClientError):
    pass


@dataclass(frozen=True, slots=True)
class TokenUsage:
    input_tokens: int = 0
    output_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens


@dataclass(frozen=True, slots=True)
class LLMRequest:
    provider: str
    prompt: str
    schema: dict[str, Any]
    model: str | None = None
    system_prompt: str = "You are an Atlas research assistant. Return only the requested JSON object."
    max_tokens: int = 700
    temperature: float = 0.0
    schema_name: str = "atlas_structured_output"
    timeout_seconds: float = 45.0


@dataclass(frozen=True, slots=True)
class LLMResponse:
    provider: str
    model: str
    data: dict[str, Any]
    usage: TokenUsage
    latency_ms: float
    attempts: int


class ProviderAdapter(Protocol):
    name: str
    def build(self, settings: ProviderSettings, request: LLMRequest, model: str) -> JsonRequest: ...
    def parse(self, payload: dict[str, Any] | list[Any]) -> tuple[str, TokenUsage]: ...


def _chat_body(request: LLMRequest, model: str) -> dict[str, Any]:
    return {
        "model": model,
        "messages": [
            {"role": "system", "content": request.system_prompt},
            {"role": "user", "content": request.prompt},
        ],
        "temperature": max(0.0, min(2.0, request.temperature)),
        "max_tokens": max(1, min(8_192, request.max_tokens)),
        "response_format": {
            "type": "json_schema",
            "json_schema": {"name": request.schema_name, "strict": True, "schema": request.schema},
        },
    }


class OpenAIResponsesAdapter:
    name = "openai"

    def build(self, settings: ProviderSettings, request: LLMRequest, model: str) -> JsonRequest:
        body = {
            "model": model,
            "input": [
                {"role": "system", "content": request.system_prompt},
                {"role": "user", "content": request.prompt},
            ],
            "temperature": request.temperature,
            "max_output_tokens": request.max_tokens,
            "text": {"format": {"type": "json_schema", "name": request.schema_name, "strict": True, "schema": request.schema}},
        }
        return JsonRequest("POST", settings.base_url.rstrip("/") + "/responses", {"Authorization": f"Bearer {settings.api_key}"}, body, request.timeout_seconds)

    def parse(self, payload: dict[str, Any] | list[Any]) -> tuple[str, TokenUsage]:
        row = _dict(payload, "OpenAI response")
        text = row.get("output_text")
        if not isinstance(text, str):
            for item in row.get("output", []):
                for content in item.get("content", []) if isinstance(item, dict) else []:
                    if isinstance(content, dict) and isinstance(content.get("text"), str):
                        text = content["text"]
                        break
        usage = row.get("usage", {}) if isinstance(row.get("usage"), dict) else {}
        return _text(text, "OpenAI"), TokenUsage(_int(usage.get("input_tokens")), _int(usage.get("output_tokens")))


class OpenAICompatibleAdapter:
    def __init__(self, name: str) -> None:
        self.name = name

    def build(self, settings: ProviderSettings, request: LLMRequest, model: str) -> JsonRequest:
        return JsonRequest("POST", settings.base_url.rstrip("/") + "/chat/completions", {"Authorization": f"Bearer {settings.api_key}"}, _chat_body(request, model), request.timeout_seconds)

    def parse(self, payload: dict[str, Any] | list[Any]) -> tuple[str, TokenUsage]:
        row = _dict(payload, f"{self.name} response")
        choices = row.get("choices")
        if not isinstance(choices, list) or not choices or not isinstance(choices[0], dict):
            raise StructuredOutputError(f"{self.name} returned no choices")
        message = choices[0].get("message")
        content = message.get("content") if isinstance(message, dict) else None
        usage = row.get("usage", {}) if isinstance(row.get("usage"), dict) else {}
        return _text(content, self.name), TokenUsage(_int(usage.get("prompt_tokens")), _int(usage.get("completion_tokens")))


class AnthropicAdapter:
    name = "anthropic"

    def build(self, settings: ProviderSettings, request: LLMRequest, model: str) -> JsonRequest:
        body = {
            "model": model,
            "system": request.system_prompt,
            "messages": [{"role": "user", "content": request.prompt}],
            "temperature": request.temperature,
            "max_tokens": request.max_tokens,
            "output_config": {"format": {"type": "json_schema", "schema": request.schema}},
        }
        headers = {"x-api-key": settings.api_key or "", "anthropic-version": "2023-06-01"}
        return JsonRequest("POST", settings.base_url.rstrip("/") + "/messages", headers, body, request.timeout_seconds)

    def parse(self, payload: dict[str, Any] | list[Any]) -> tuple[str, TokenUsage]:
        row = _dict(payload, "Anthropic response")
        content = row.get("content")
        text = content[0].get("text") if isinstance(content, list) and content and isinstance(content[0], dict) else None
        usage = row.get("usage", {}) if isinstance(row.get("usage"), dict) else {}
        return _text(text, "Anthropic"), TokenUsage(_int(usage.get("input_tokens")), _int(usage.get("output_tokens")))


class GoogleAdapter:
    name = "google"

    def build(self, settings: ProviderSettings, request: LLMRequest, model: str) -> JsonRequest:
        body = {
            "systemInstruction": {"parts": [{"text": request.system_prompt}]},
            "contents": [{"role": "user", "parts": [{"text": request.prompt}]}],
            "generationConfig": {
                "temperature": request.temperature,
                "maxOutputTokens": request.max_tokens,
                "responseFormat": {"text": {"mimeType": "application/json", "schema": request.schema}},
            },
        }
        url = f"{settings.base_url.rstrip('/')}/models/{quote(model, safe='-._')}:generateContent"
        return JsonRequest("POST", url, {"x-goog-api-key": settings.api_key or ""}, body, request.timeout_seconds)

    def parse(self, payload: dict[str, Any] | list[Any]) -> tuple[str, TokenUsage]:
        row = _dict(payload, "Google response")
        candidates = row.get("candidates")
        candidate = candidates[0] if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict) else None
        content = candidate.get("content") if isinstance(candidate, dict) else None
        parts = content.get("parts") if isinstance(content, dict) else None
        part = parts[0] if isinstance(parts, list) and parts and isinstance(parts[0], dict) else None
        text = part.get("text") if isinstance(part, dict) else None
        if not isinstance(text, str):
            raise StructuredOutputError("Google returned no candidate text")
        usage = row.get("usageMetadata", {}) if isinstance(row.get("usageMetadata"), dict) else {}
        return _text(text, "Google"), TokenUsage(_int(usage.get("promptTokenCount")), _int(usage.get("candidatesTokenCount")))


class OllamaAdapter:
    name = "ollama"

    def build(self, settings: ProviderSettings, request: LLMRequest, model: str) -> JsonRequest:
        body = {
            "model": model,
            "system": request.system_prompt,
            "prompt": request.prompt + "\nJSON_SCHEMA:\n" + json.dumps(request.schema, separators=(",", ":")),
            "stream": False,
            "format": request.schema,
            "options": {"temperature": request.temperature, "num_predict": request.max_tokens},
        }
        headers = {"Authorization": f"Bearer {settings.api_key}"} if settings.api_key else {}
        return JsonRequest("POST", settings.base_url.rstrip("/") + "/api/generate", headers, body, request.timeout_seconds)

    def parse(self, payload: dict[str, Any] | list[Any]) -> tuple[str, TokenUsage]:
        row = _dict(payload, "Ollama response")
        return _text(row.get("response"), "Ollama"), TokenUsage(_int(row.get("prompt_eval_count")), _int(row.get("eval_count")))


class UsageLedger:
    def __init__(self) -> None:
        self._rows: dict[tuple[str, str], TokenUsage] = {}
        self._lock = RLock()

    def add(self, provider: str, model: str, usage: TokenUsage) -> None:
        with self._lock:
            prior = self._rows.get((provider, model), TokenUsage())
            self._rows[(provider, model)] = TokenUsage(prior.input_tokens + usage.input_tokens, prior.output_tokens + usage.output_tokens)

    def snapshot(self) -> dict[str, dict[str, int]]:
        with self._lock:
            return {f"{provider}:{model}": {"input_tokens": usage.input_tokens, "output_tokens": usage.output_tokens, "total_tokens": usage.total_tokens} for (provider, model), usage in sorted(self._rows.items())}


class LLMClient:
    """Explicit-provider client with bounded retry and local schema validation."""

    def __init__(
        self,
        settings: AtlasSettings | None = None,
        *,
        transport: JsonTransport | None = None,
        adapters: Mapping[str, ProviderAdapter] | None = None,
        max_retries: int = 3,
        backoff_seconds: float = 0.25,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.settings = settings or AtlasSettings.from_env()
        self.transport = transport or UrllibJsonTransport()
        default_adapters: dict[str, ProviderAdapter] = {
            "openai": OpenAIResponsesAdapter(),
            "anthropic": AnthropicAdapter(),
            "google": GoogleAdapter(),
            "groq": OpenAICompatibleAdapter("groq"),
            "atlas_cloud": OpenAICompatibleAdapter("atlas_cloud"),
            "ollama": OllamaAdapter(),
        }
        self.adapters: dict[str, ProviderAdapter] = dict(adapters) if adapters is not None else default_adapters
        self.max_retries = max(0, min(3, int(max_retries)))
        self.backoff_seconds = max(0.0, float(backoff_seconds))
        self.sleep = sleep
        self.usage = UsageLedger()

    def available_providers(self) -> dict[str, bool]:
        return {name: row.configured for name, row in self.settings.providers.items()}

    def complete(self, request: LLMRequest) -> LLMResponse:
        settings = self.settings.providers.get(request.provider)
        adapter = self.adapters.get(request.provider)
        if settings is None or adapter is None:
            raise ProviderNotConfigured(f"Unknown LLM provider '{request.provider}'")
        if not settings.configured:
            raise ProviderNotConfigured(f"LLM provider '{request.provider}' is not configured")
        model = request.model or settings.default_model
        if not model:
            raise ProviderNotConfigured(f"No model configured for '{request.provider}'")
        started = time.perf_counter()
        last_error: Exception | None = None
        total_attempts = self.max_retries + 1
        for attempt in range(1, total_attempts + 1):
            try:
                outbound = adapter.build(settings, request, model)
                payload = self.transport.send(outbound)
                text, usage = adapter.parse(payload)
                try:
                    decoded = json.loads(text)
                except json.JSONDecodeError as exc:
                    raise StructuredOutputError("Provider returned malformed JSON") from exc
                validate_json_schema(decoded, request.schema)
                if not isinstance(decoded, dict):
                    raise StructuredOutputError("Top-level structured output must be an object")
                self.usage.add(request.provider, model, usage)
                return LLMResponse(request.provider, model, decoded, usage, round((time.perf_counter() - started) * 1_000, 2), attempt)
            except TransportError as exc:
                last_error = exc
                if not exc.retryable:
                    break
            except StructuredOutputError as exc:
                last_error = exc
            if attempt < total_attempts:
                self.sleep(self.backoff_seconds * (2 ** (attempt - 1)))
        raise LLMClientError(f"Provider '{request.provider}' failed closed after {attempt} attempt(s): {last_error}") from last_error

    async def complete_async(self, request: LLMRequest) -> LLMResponse:
        return await asyncio.to_thread(self.complete, request)


def validate_json_schema(value: Any, schema: dict[str, Any], path: str = "$") -> None:
    """Validate the bounded JSON Schema subset used by Atlas agent contracts."""

    allowed_types = schema.get("type")
    types = allowed_types if isinstance(allowed_types, list) else [allowed_types] if allowed_types else []
    if types and not any(_matches_type(value, item) for item in types):
        raise StructuredOutputError(f"{path} does not match schema type {allowed_types}")
    if "enum" in schema and value not in schema["enum"]:
        raise StructuredOutputError(f"{path} is not in the allowed enum")
    if "const" in schema and value != schema["const"]:
        raise StructuredOutputError(f"{path} does not match the required constant")
    if isinstance(value, dict):
        properties = schema.get("properties", {})
        required = schema.get("required", [])
        for key in required:
            if key not in value:
                raise StructuredOutputError(f"{path}.{key} is required")
        if schema.get("additionalProperties") is False:
            extras = set(value) - set(properties)
            if extras:
                raise StructuredOutputError(f"{path} contains unsupported fields: {sorted(extras)}")
        for key, item in value.items():
            if key in properties:
                validate_json_schema(item, properties[key], f"{path}.{key}")
    elif isinstance(value, list):
        if "minItems" in schema and len(value) < int(schema["minItems"]):
            raise StructuredOutputError(f"{path} has too few items")
        if "maxItems" in schema and len(value) > int(schema["maxItems"]):
            raise StructuredOutputError(f"{path} has too many items")
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                validate_json_schema(item, item_schema, f"{path}[{index}]")
    elif isinstance(value, (int, float)) and not isinstance(value, bool):
        if not math.isfinite(float(value)):
            raise StructuredOutputError(f"{path} must be finite")
        if "minimum" in schema and value < schema["minimum"]:
            raise StructuredOutputError(f"{path} is below its minimum")
        if "maximum" in schema and value > schema["maximum"]:
            raise StructuredOutputError(f"{path} is above its maximum")


def _matches_type(value: Any, expected: str) -> bool:
    return {
        "object": isinstance(value, dict),
        "array": isinstance(value, list),
        "string": isinstance(value, str),
        "number": isinstance(value, (int, float)) and not isinstance(value, bool),
        "integer": isinstance(value, int) and not isinstance(value, bool),
        "boolean": isinstance(value, bool),
        "null": value is None,
    }.get(expected, False)


def _dict(value: dict[str, Any] | list[Any], label: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise StructuredOutputError(f"{label} must be an object")
    return value


def _text(value: Any, provider: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise StructuredOutputError(f"{provider} returned no structured text")
    return value.strip()


def _int(value: Any) -> int:
    return max(0, int(value)) if isinstance(value, (int, float)) and not isinstance(value, bool) else 0


__all__ = [
    "LLMClient", "LLMClientError", "LLMRequest", "LLMResponse", "ProviderNotConfigured",
    "StructuredOutputError", "TokenUsage", "UsageLedger", "validate_json_schema",
]
