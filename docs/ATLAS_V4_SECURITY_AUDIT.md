# ATLAS V4 Phase 15 security audit

Audit date: 2026-08-21  
Scope: active V4 FastAPI boundary, Vinext proxy, MT5 bridge boundary, secrets, mutation controls, operational health, persistence, and broker-execution surface.

## Verified controls

| Control | Result | Evidence |
|---|---|---|
| Local access | PASS | FastAPI rejects non-loopback clients; Uvicorn, PostgreSQL, Redis, MT5, Ollama, and Vinext backend proxies use loopback addresses. |
| Broker execution | PASS | V4 gateway is GET-only, no order route exists, and response contracts keep `execution_enabled=false` and `order_submission=false`. |
| Analytical signal authority | PASS | Only the server-side calibrated publication gate can construct a canonical LONG/SHORT signal. Browser signal authority remains false. |
| Mutation authentication | PASS | Optional 32+ character `ATLAS_ADMIN_TOKEN` is compared in constant time for every state-changing `/api/v4` request. Unset means local single-user mode, not public access. |
| Rate limiting | PASS | State-changing V4 requests have a bounded process-local per-minute limiter. |
| Request bounds | PASS | Invalid/oversized declared request bodies are rejected before route handling; Pydantic contracts reject malformed payloads. |
| Secret redaction | PASS | Provider keys, database/Redis URLs, FRED keys, MT5 tokens, and the admin token are excluded from settings representations. HTTP errors redact query values. |
| Browser proxy | PASS | GET-only loopback proxy, bounded timeout, no-store caching, no fake fallback, and authority/security headers. |
| Security headers | PASS | `nosniff`, frame denial, no-referrer, restricted browser permissions, no-store V4 responses, and explicit execution-disabled header. |
| LLM health | PASS | When V4 AI review is enabled, `/api/v4/health/operational` checks the actual Ollama inventory for all four configured local models. Configuration alone never reports connected. |
| Persistence recovery | PASS | Alembic baseline is check-first and idempotent; destructive downgrade is blocked; backup/restore procedure is documented. |

## Residual limitations

- `ATLAS_ADMIN_TOKEN` is optional because this is a loopback-only single-user application. Set it whenever another local process or user is not trusted.
- The mutation limiter is process-local. A future multi-worker or remote deployment requires a Redis-backed identity-aware limiter and formal user authentication before exposure.
- Body limits rely on the declared `Content-Length`; the application remains loopback-only, and the production reverse proxy should also enforce a body limit if one is introduced.
- Local administrators and malware with access to the user profile can still read local environment files and databases. OS account security, disk encryption, and endpoint protection remain external controls.
- AI reviewers are non-voting and optional. A missing reviewer degrades AI health and never relaxes model, Quant, expectancy, data, or risk gates.

These controls do not turn Atlas into a broker or adviser. They protect a local analytical signal system whose order-placement surface remains absent.
