"""Injectable JSON HTTP transport shared by provider and ingestion adapters."""

from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit, urlunsplit
from urllib.request import Request, urlopen


class TransportError(RuntimeError):
    """A normalized HTTP transport failure."""

    def __init__(self, message: str, *, status: int | None = None, retryable: bool = False):
        super().__init__(message)
        self.status = status
        self.retryable = retryable


@dataclass(frozen=True, slots=True)
class JsonRequest:
    method: str
    url: str
    headers: Mapping[str, str] = field(default_factory=dict)
    body: Any | None = None
    timeout_seconds: float = 30.0


class JsonTransport(Protocol):
    def send(self, request: JsonRequest) -> dict[str, Any] | list[Any]: ...


class UrllibJsonTransport:
    """Standard-library transport; performs no request until ``send`` is called."""

    def send(self, request: JsonRequest) -> dict[str, Any] | list[Any]:
        encoded = None if request.body is None else json.dumps(request.body, separators=(",", ":")).encode("utf-8")
        headers = {"Accept": "application/json", **dict(request.headers)}
        if encoded is not None:
            headers.setdefault("Content-Type", "application/json")
        outbound = Request(request.url, data=encoded, headers=headers, method=request.method.upper())
        try:
            with urlopen(outbound, timeout=max(1.0, request.timeout_seconds)) as response:
                payload = response.read()
        except HTTPError as exc:
            retryable = exc.code in {408, 409, 425, 429} or exc.code >= 500
            raise TransportError(f"HTTP {exc.code} from {_redact_url(request.url)}", status=exc.code, retryable=retryable) from exc
        except (URLError, TimeoutError, OSError) as exc:
            raise TransportError(f"Network failure for {_redact_url(request.url)}: {exc}", retryable=True) from exc
        try:
            parsed = json.loads(payload.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise TransportError(f"Invalid JSON response from {_redact_url(request.url)}", retryable=False) from exc
        if not isinstance(parsed, (dict, list)):
            raise TransportError(f"Unexpected JSON response from {_redact_url(request.url)}", retryable=False)
        return parsed


def _redact_url(url: str) -> str:
    """Remove all query values before a URL can enter an exception message."""

    parsed = urlsplit(url)
    query = "REDACTED" if parsed.query else ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, query, ""))


__all__ = ["JsonRequest", "JsonTransport", "TransportError", "UrllibJsonTransport"]
