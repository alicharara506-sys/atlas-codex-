"""Local-only API boundary, mutation authentication, and bounded request controls."""

from __future__ import annotations

import hmac
from collections import defaultdict, deque
from threading import RLock
from time import monotonic

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from app.core.config import AtlasSettings

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
LOCAL_CLIENTS = frozenset({"127.0.0.1", "::1", "localhost", "testclient"})


class MutationRateLimiter:
    """Small process-local limiter for the single-user research mutation surface."""

    def __init__(self, limit_per_minute: int) -> None:
        self.limit = limit_per_minute
        self._requests: dict[str, deque[float]] = defaultdict(deque)
        self._lock = RLock()

    def allow(self, client: str, *, now: float | None = None) -> bool:
        observed = monotonic() if now is None else now
        cutoff = observed - 60.0
        with self._lock:
            requests = self._requests[client]
            while requests and requests[0] <= cutoff:
                requests.popleft()
            if len(requests) >= self.limit:
                return False
            requests.append(observed)
            return True


class AtlasSecurityMiddleware:
    """Fail closed outside loopback and protect all state-changing V4 requests."""

    def __init__(self, app: ASGIApp, settings: AtlasSettings) -> None:
        self.app = app
        self.admin_token = settings.atlas_admin_token
        self.max_request_bytes = settings.max_request_bytes
        self.rate_limiter = MutationRateLimiter(settings.mutation_rate_limit_per_minute)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        client_host = client[0].casefold() if client else ""
        if client_host not in LOCAL_CLIENTS:
            await self._reject(scope, receive, send, 403, "LOCAL_ACCESS_REQUIRED")
            return

        headers = Headers(scope=scope)
        content_length = headers.get("content-length")
        if content_length is not None:
            try:
                request_bytes = int(content_length)
            except ValueError:
                await self._reject(scope, receive, send, 400, "INVALID_CONTENT_LENGTH")
                return
            if request_bytes < 0 or request_bytes > self.max_request_bytes:
                await self._reject(scope, receive, send, 413, "REQUEST_TOO_LARGE")
                return

        method = str(scope.get("method", "GET")).upper()
        path = str(scope.get("path", ""))
        if path.startswith("/api/v4") and method not in SAFE_METHODS:
            if self.admin_token is not None:
                supplied = headers.get("x-atlas-admin-token", "")
                if not hmac.compare_digest(supplied, self.admin_token):
                    await self._reject(scope, receive, send, 401, "V4_MUTATION_AUTH_REQUIRED")
                    return
            if not self.rate_limiter.allow(client_host):
                await self._reject(scope, receive, send, 429, "V4_MUTATION_RATE_LIMITED")
                return

        async def secured_send(message: Message) -> None:
            if message["type"] == "http.response.start":
                mutable = MutableHeaders(scope=message)
                mutable["x-content-type-options"] = "nosniff"
                mutable["x-frame-options"] = "DENY"
                mutable["referrer-policy"] = "no-referrer"
                mutable["permissions-policy"] = "camera=(), microphone=(), geolocation=()"
                if path.startswith("/api/v4"):
                    mutable["cache-control"] = "no-store"
                    mutable["x-atlas-execution"] = "disabled"
            await send(message)

        await self.app(scope, receive, secured_send)

    @staticmethod
    async def _reject(
        scope: Scope,
        receive: Receive,
        send: Send,
        status_code: int,
        code: str,
    ) -> None:
        response = JSONResponse(
            status_code=status_code,
            content={
                "detail": code,
                "research_only": True,
                "execution_enabled": False,
                "order_submission": False,
            },
            headers={
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "X-Atlas-Execution": "disabled",
            },
        )
        await response(scope, receive, send)


__all__ = ["LOCAL_CLIENTS", "SAFE_METHODS", "AtlasSecurityMiddleware", "MutationRateLimiter"]
