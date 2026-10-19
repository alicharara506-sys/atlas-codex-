from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import yaml

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.http import JsonRequest, TransportError
from app.core.infrastructure import OllamaInventoryProbe, RedisProbe


class InventoryTransport:
    def __init__(self, payload: dict[str, object] | None = None, *, failing: bool = False) -> None:
        self.payload = payload or {}
        self.failing = failing
        self.request: JsonRequest | None = None

    def send(self, request: JsonRequest) -> dict[str, object]:
        self.request = request
        if self.failing:
            raise TransportError("offline")
        return self.payload


class InfrastructureTests(unittest.TestCase):
    def test_compose_services_are_persistent_loopback_only_and_health_checked(self) -> None:
        compose_path = BACKEND.parent / "docker-compose.infrastructure.yml"
        compose = yaml.safe_load(compose_path.read_text(encoding="utf-8"))
        services = compose["services"]
        self.assertEqual(set(services), {"postgres", "redis"})
        self.assertEqual(services["postgres"]["image"], "postgres:16-alpine")
        self.assertEqual(services["redis"]["image"], "redis:7-alpine")
        self.assertEqual(services["postgres"]["ports"], ["127.0.0.1:55432:5432"])
        self.assertEqual(services["redis"]["ports"], ["127.0.0.1:56379:6379"])
        self.assertIn("healthcheck", services["postgres"])
        self.assertIn("healthcheck", services["redis"])
        self.assertEqual(services["postgres"]["volumes"], ["atlas_postgres_data:/var/lib/postgresql/data"])
        self.assertEqual(services["redis"]["volumes"], ["atlas_redis_data:/data"])
        self.assertIn("ATLAS_POSTGRES_PASSWORD is required", services["postgres"]["environment"]["POSTGRES_PASSWORD"])
        self.assertIn("ATLAS_REDIS_PASSWORD is required", services["redis"]["environment"]["REDIS_PASSWORD"])
        self.assertNotIn("build", services["postgres"])
        self.assertNotIn("build", services["redis"])

    def test_ollama_inventory_requires_every_configured_local_model(self) -> None:
        expected = ("ministral-3:3b", "llama3.2:1b-instruct-q3_K_L", "qwen2.5:3b", "phi4-mini")
        transport = InventoryTransport(
            {
                "models": [
                    {"name": "ministral-3:3b"},
                    {"name": "llama3.2:1b-instruct-q3_K_L"},
                    {"name": "qwen2.5:3b"},
                    {"name": "phi4-mini:latest"},
                ]
            }
        )
        inventory = OllamaInventoryProbe("http://127.0.0.1:11434", expected, transport=transport).check()
        self.assertTrue(inventory.healthy)
        self.assertEqual(inventory.missing_models, ())
        self.assertIsNotNone(transport.request)
        self.assertEqual(transport.request.method, "GET")
        self.assertEqual(transport.request.url, "http://127.0.0.1:11434/api/tags")

        partial = OllamaInventoryProbe(
            "http://127.0.0.1:11434",
            expected,
            transport=InventoryTransport({"models": [{"name": "ministral-3:3b"}]}),
        ).check()
        self.assertFalse(partial.healthy)
        self.assertEqual(len(partial.available_models), 1)
        self.assertEqual(len(partial.missing_models), 3)

        offline = OllamaInventoryProbe(
            "http://127.0.0.1:11434",
            expected,
            transport=InventoryTransport(failing=True),
        ).check()
        self.assertFalse(offline.reachable)
        self.assertEqual(offline.issue, "OLLAMA_UNREACHABLE")

    def test_redis_probe_reports_success_and_failure(self) -> None:
        client = MagicMock()
        client.ping.return_value = True
        with patch("app.core.infrastructure.Redis.from_url", return_value=client):
            self.assertTrue(RedisProbe("redis://127.0.0.1:6379/0").healthy())
        client.close.assert_called_once()

        broken = MagicMock()
        from redis.exceptions import ConnectionError as RedisConnectionError

        broken.ping.side_effect = RedisConnectionError("offline")
        with patch("app.core.infrastructure.Redis.from_url", return_value=broken):
            self.assertFalse(RedisProbe("redis://127.0.0.1:6379/0").healthy())
        broken.close.assert_called_once()

    def test_configured_redis_service_when_required(self) -> None:
        url = os.environ.get("ATLAS_TEST_REDIS_URL")
        if not url:
            self.skipTest("ATLAS_TEST_REDIS_URL is not configured")
        self.assertTrue(RedisProbe(url, timeout_seconds=3).healthy())


if __name__ == "__main__":
    unittest.main()
