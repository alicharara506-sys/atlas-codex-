from __future__ import annotations

import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Self
from unittest.mock import patch
from urllib.error import HTTPError, URLError

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.config import AtlasSettings, ConfigurationError
from app.core.http import JsonRequest, TransportError, UrllibJsonTransport
from app.core.store import JsonlRecordStore, stable_key
from app.data.contracts import MarketBar, SentimentObservation, finite


class FakeResponse:
    def __init__(self, payload: bytes) -> None:
        self.payload = payload

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *args: object) -> None:
        return None

    def read(self) -> bytes:
        return self.payload


class CoreTests(unittest.TestCase):
    def test_jsonl_store_replays_and_deduplicates(self) -> None:
        with TemporaryDirectory() as folder:
            path = Path(folder) / "nested" / "records.jsonl"
            store = JsonlRecordStore(path)
            self.assertTrue(store.upsert("prices", "one", {"value": 1}).inserted)
            self.assertFalse(store.upsert("prices", "one", {"value": 2}).inserted)
            self.assertEqual(JsonlRecordStore(path).values("prices"), [{"value": 2}])
            self.assertEqual(stable_key("ABC", 1), stable_key("ABC", 1))

    def test_http_transport_handles_json_and_redacts_failures(self) -> None:
        transport = UrllibJsonTransport()
        with patch("app.core.http.urlopen", return_value=FakeResponse(b'{"ok":true}')):
            self.assertEqual(transport.send(JsonRequest("POST", "https://example.test/data", body={"x": 1})), {"ok": True})

        http_error = HTTPError("https://example.test", 429, "rate limited", None, None)
        with patch("app.core.http.urlopen", side_effect=http_error), self.assertRaises(TransportError) as raised:
            transport.send(JsonRequest("GET", "https://example.test/data?api_key=secret"))
        self.assertTrue(raised.exception.retryable)
        self.assertEqual(raised.exception.status, 429)
        self.assertNotIn("secret", str(raised.exception))
        self.assertIn("REDACTED", str(raised.exception))

        with patch("app.core.http.urlopen", side_effect=URLError("offline")), self.assertRaisesRegex(
            TransportError, "Network failure"
        ):
            transport.send(JsonRequest("GET", "https://example.test/data"))
        with patch("app.core.http.urlopen", return_value=FakeResponse(b"not-json")), self.assertRaisesRegex(
            TransportError, "Invalid JSON"
        ):
            transport.send(JsonRequest("GET", "https://example.test/data"))
        with patch("app.core.http.urlopen", return_value=FakeResponse(b"42")), self.assertRaisesRegex(
            TransportError, "Unexpected JSON"
        ):
            transport.send(JsonRequest("GET", "https://example.test/data"))

    def test_configuration_and_contracts_reject_unsafe_values(self) -> None:
        with self.assertRaises(ConfigurationError):
            AtlasSettings.from_env({"OLLAMA_BASE_URL": "https://remote.example"})
        with self.assertRaises(ConfigurationError):
            AtlasSettings.from_env({"OPENAI_BASE_URL": "ftp://example.test"})
        with self.assertRaises(ValueError):
            finite(float("nan"), "value")
        with self.assertRaises(ValueError):
            MarketBar("ABC", 1, 10, 9, 8, 10, 1, "fixture")
        bounded = SentimentObservation(" abc ", "fixture", 1, 10)
        self.assertEqual((bounded.ticker, bounded.score), ("ABC", 1.0))


if __name__ == "__main__":
    unittest.main()
