from __future__ import annotations

import asyncio
import json
import sys
import unittest
from pathlib import Path
from typing import Any

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import AtlasSettings
from app.core.http import JsonRequest, TransportError
from app.llm.client import (
    LLMClient,
    LLMClientError,
    LLMRequest,
    ProviderNotConfigured,
    StructuredOutputError,
    validate_json_schema,
)

SCHEMA = {
    "type": "object",
    "properties": {
        "signal": {"type": "string", "enum": ["BULLISH", "BEARISH", "NEUTRAL"]},
        "confidence": {"type": "number", "minimum": 0, "maximum": 1},
    },
    "required": ["signal", "confidence"],
    "additionalProperties": False,
}


class SequenceTransport:
    def __init__(self, responses: list[Any]) -> None:
        self.responses = list(responses)
        self.requests: list[JsonRequest] = []

    def send(self, request: JsonRequest) -> dict[str, Any] | list[Any]:
        self.requests.append(request)
        if not self.responses:
            raise AssertionError("Unexpected transport call")
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        if not isinstance(response, (dict, list)):
            raise TypeError("Fixture response must be JSON-compatible")
        return response


class UnifiedLLMClientTests(unittest.TestCase):
    def test_environment_auto_detects_all_provider_keys_without_network(self) -> None:
        settings = AtlasSettings.from_env({
            "OPENAI_API_KEY": "openai", "OPENAI_MODEL": "model-a",
            "ANTHROPIC_API_KEY": "anthropic", "ANTHROPIC_MODEL": "model-b",
            "GOOGLE_API_KEY": "google", "GOOGLE_MODEL": "model-c",
            "GROQ_API_KEY": "groq", "GROQ_MODEL": "model-d",
            "ATLAS_API_KEY": "atlas", "ATLAS_CLOUD_MODEL": "model-e", "ATLAS_CLOUD_BASE_URL": "https://atlas.example/v1",
            "OLLAMA_MODEL": "local-model", "OLLAMA_BASE_URL": "http://127.0.0.1:11434",
        })
        self.assertEqual(set(settings.providers), {"openai", "anthropic", "google", "groq", "atlas_cloud", "ollama"})
        self.assertTrue(all(row.configured for row in settings.providers.values()))

    def test_structured_output_retries_then_tracks_usage(self) -> None:
        settings = AtlasSettings.from_env({"OPENAI_API_KEY": "secret", "OPENAI_MODEL": "test-model"})
        transport = SequenceTransport([
            {"output_text": json.dumps({"signal": "BULLISH"}), "usage": {"input_tokens": 5, "output_tokens": 2}},
            {"output_text": json.dumps({"signal": "BULLISH", "confidence": 0.8}), "usage": {"input_tokens": 7, "output_tokens": 3}},
        ])
        delays: list[float] = []
        client = LLMClient(settings, transport=transport, max_retries=3, backoff_seconds=0.1, sleep=delays.append)
        result = client.complete(LLMRequest("openai", "Analyze deterministic evidence", SCHEMA))
        self.assertEqual(result.data, {"signal": "BULLISH", "confidence": 0.8})
        self.assertEqual(result.attempts, 2)
        self.assertEqual(delays, [0.1])
        self.assertEqual(client.usage.snapshot()["openai:test-model"]["total_tokens"], 10)
        self.assertTrue(transport.requests[0].url.endswith("/responses"))
        self.assertEqual(transport.requests[0].body["text"]["format"]["schema"], SCHEMA)

    def test_invalid_structured_output_fails_closed_at_retry_budget(self) -> None:
        settings = AtlasSettings.from_env({"GROQ_API_KEY": "secret", "GROQ_MODEL": "test-model"})
        malformed = {"choices": [{"message": {"content": "not-json"}}], "usage": {}}
        client = LLMClient(settings, transport=SequenceTransport([malformed, malformed, malformed, malformed]), max_retries=3, backoff_seconds=0, sleep=lambda _: None)
        with self.assertRaisesRegex(LLMClientError, "failed closed after 4 attempt"):
            client.complete(LLMRequest("groq", "Analyze", SCHEMA))

    def test_unconfigured_cloud_provider_is_not_called(self) -> None:
        transport = SequenceTransport([])
        client = LLMClient(AtlasSettings.from_env({}), transport=transport)
        with self.assertRaises(ProviderNotConfigured):
            client.complete(LLMRequest("anthropic", "Analyze", SCHEMA, model="some-model"))
        self.assertEqual(transport.requests, [])

    def test_google_key_is_sent_in_header_not_logged_in_url(self) -> None:
        settings = AtlasSettings.from_env({"GOOGLE_API_KEY": "top-secret", "GOOGLE_MODEL": "gemini-test"})
        client = LLMClient(settings, transport=SequenceTransport([]))
        outbound = client.adapters["google"].build(settings.providers["google"], LLMRequest("google", "Analyze", SCHEMA), "gemini-test")
        self.assertNotIn("top-secret", outbound.url)
        self.assertEqual(outbound.headers["x-goog-api-key"], "top-secret")

    def test_anthropic_google_ollama_and_nested_openai_responses(self) -> None:
        provider_cases = [
            (
                {"ANTHROPIC_API_KEY": "secret", "ANTHROPIC_MODEL": "claude-test"},
                "anthropic",
                {"content": [{"type": "text", "text": json.dumps({"signal": "NEUTRAL", "confidence": 0.5})}], "usage": {"input_tokens": 4, "output_tokens": 2}},
            ),
            (
                {"GOOGLE_API_KEY": "secret", "GOOGLE_MODEL": "gemini-test"},
                "google",
                {"candidates": [{"content": {"parts": [{"text": json.dumps({"signal": "BULLISH", "confidence": 0.7})}]}}], "usageMetadata": {"promptTokenCount": 5, "candidatesTokenCount": 3}},
            ),
            (
                {"OLLAMA_MODEL": "local-test"},
                "ollama",
                {"response": json.dumps({"signal": "BEARISH", "confidence": 0.6}), "prompt_eval_count": 6, "eval_count": 4},
            ),
            (
                {"OPENAI_API_KEY": "secret", "OPENAI_MODEL": "openai-test"},
                "openai",
                {"output": [{"content": [{"type": "output_text", "text": json.dumps({"signal": "NEUTRAL", "confidence": 0.4})}]}], "usage": {}},
            ),
        ]
        for env, provider, payload in provider_cases:
            with self.subTest(provider=provider):
                transport = SequenceTransport([payload])
                client = LLMClient(AtlasSettings.from_env(env), transport=transport, backoff_seconds=0)
                response = client.complete(LLMRequest(provider, "Analyze", SCHEMA))
                self.assertIn(response.data["signal"], {"BULLISH", "BEARISH", "NEUTRAL"})
                self.assertEqual(response.attempts, 1)

    def test_async_entrypoint_and_fail_closed_transport_path(self) -> None:
        settings = AtlasSettings.from_env({"OLLAMA_MODEL": "local-test"})
        payload = {"response": json.dumps({"signal": "NEUTRAL", "confidence": 0.5})}
        client = LLMClient(settings, transport=SequenceTransport([payload]))
        result = asyncio.run(client.complete_async(LLMRequest("ollama", "Analyze", SCHEMA)))
        self.assertEqual(result.data["signal"], "NEUTRAL")
        self.assertTrue(client.available_providers()["ollama"])

        failure = TransportError("unauthorized", status=401, retryable=False)
        blocked = LLMClient(settings, transport=SequenceTransport([failure]), max_retries=3, backoff_seconds=0)
        with self.assertRaisesRegex(LLMClientError, "after 1 attempt"):
            blocked.complete(LLMRequest("ollama", "Analyze", SCHEMA))
        with self.assertRaises(ProviderNotConfigured):
            client.complete(LLMRequest("unknown", "Analyze", SCHEMA))

    def test_schema_validator_covers_nested_arrays_and_constraints(self) -> None:
        nested_schema = {
            "type": "object",
            "properties": {
                "items": {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 2,
                    "items": {"type": "number", "minimum": 0, "maximum": 1},
                },
                "mode": {"type": "string", "const": "SAFE", "enum": ["SAFE"]},
            },
            "required": ["items", "mode"],
            "additionalProperties": False,
        }
        validate_json_schema({"items": [0.2, 0.8], "mode": "SAFE"}, nested_schema)
        invalid_values = [
            ({"items": [], "mode": "SAFE"}, "too few"),
            ({"items": [0.1, 0.2, 0.3], "mode": "SAFE"}, "too many"),
            ({"items": [-0.1], "mode": "SAFE"}, "below"),
            ({"items": [1.1], "mode": "SAFE"}, "above"),
            ({"items": [float("inf")], "mode": "SAFE"}, "finite"),
            ({"items": [0.5], "mode": "TRADE"}, "enum"),
            ({"items": [0.5], "mode": "SAFE", "extra": True}, "unsupported"),
            ({"items": "bad", "mode": "SAFE"}, "schema type"),
        ]
        for value, message in invalid_values:
            with self.subTest(message=message), self.assertRaisesRegex(StructuredOutputError, message):
                validate_json_schema(value, nested_schema)

        array_schema = {"type": "array", "items": {"type": "integer"}}
        client = LLMClient(
            AtlasSettings.from_env({"OLLAMA_MODEL": "local-test"}),
            transport=SequenceTransport([{"response": "[1,2]"}]),
            max_retries=0,
        )
        with self.assertRaisesRegex(LLMClientError, "Top-level"):
            client.complete(LLMRequest("ollama", "Analyze", array_schema))

    def test_missing_provider_payload_fields_are_rejected(self) -> None:
        cases = [
            ("groq", {"GROQ_API_KEY": "x", "GROQ_MODEL": "m"}, {}),
            ("google", {"GOOGLE_API_KEY": "x", "GOOGLE_MODEL": "m"}, {"candidates": []}),
            ("anthropic", {"ANTHROPIC_API_KEY": "x", "ANTHROPIC_MODEL": "m"}, {"content": []}),
        ]
        for provider, env, payload in cases:
            with self.subTest(provider=provider):
                client = LLMClient(AtlasSettings.from_env(env), transport=SequenceTransport([payload]), max_retries=0)
                with self.assertRaises(LLMClientError):
                    client.complete(LLMRequest(provider, "Analyze", SCHEMA))


if __name__ == "__main__":
    unittest.main()
