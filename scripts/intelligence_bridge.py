#!/usr/bin/env python3
"""Loopback HTTP bridge for ATLAS Python intelligence modules.

Routes are analysis-only.  There is no broker connection and no order API in
this process.  Optional bearer authentication protects loopback requests.
"""

from __future__ import annotations

import hmac
import asyncio
import base64
import json
import os
import signal
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from chart_renderer import ChartRenderError, render_chart
from llm_orchestrator import LLMOrchestrator, OrchestratorError
from orderflow_engine import OrderFlowValidationError, analyze_orderflow
from atlas_core import ATLASQuantEngine, AtlasV2ValidationError
from smc_engine import SMCAnalyzer
from wyckoff_engine import WyckoffAnalyzer, WyckoffValidationError

BIND_HOST = "127.0.0.1"
PORT = int(os.environ.get("ATLAS_INTELLIGENCE_PORT", "8776"))
TOKEN = os.environ.get("ATLAS_INTELLIGENCE_TOKEN", "").strip()
MAX_BODY_BYTES = 2_000_000
SYMBOLS = {
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF", "AUDUSD", "USDCAD",
    "NZDUSD", "EURGBP", "XAUUSD", "XAGUSD", "USOIL", "UKOIL",
}


class RequestError(ValueError):
    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def _symbol(value: Any) -> str:
    result = str(value or "").upper().replace("/", "")
    if result not in SYMBOLS:
        raise RequestError(400, "unsupported Atlas symbol")
    return result


def route_orderflow(body: dict[str, Any]) -> dict[str, Any]:
    symbol = _symbol(body.get("symbol"))
    bars = body.get("bars")
    ticks = body.get("ticks")
    if not isinstance(bars, list) or not isinstance(ticks, list):
        raise RequestError(400, "bars and ticks must be arrays")
    return {
        "provider": "ATLAS_PYTHON_ORDERFLOW",
        "readOnly": True,
        "orderSubmission": False,
        "result": analyze_orderflow(
            symbol,
            bars,
            ticks,
            anchor_time=body.get("anchorTime"),
            profile_bins=body.get("profileBins", 24),
            footprint_bins=body.get("footprintBins", 30),
        ),
    }


def route_llm(body: dict[str, Any]) -> dict[str, Any]:
    task = body.get("task")
    payload = body.get("payload")
    if not isinstance(task, str) or not isinstance(payload, dict):
        raise RequestError(400, "task must be a string and payload must be an object")
    result = LLMOrchestrator().route_task(task, payload)
    return {"provider": "LOCAL_OLLAMA_ONLY", "readOnly": True, "orderSubmission": False, "result": result}


def route_pipeline(body: dict[str, Any]) -> dict[str, Any]:
    payload = body.get("payload")
    if not isinstance(payload, dict):
        raise RequestError(400, "payload must be an object")
    result = LLMOrchestrator().run_signal_pipeline(payload)
    return {"provider": "LOCAL_OLLAMA_ONLY", "readOnly": True, "orderSubmission": False, "result": result}


def route_v2_signal(body: dict[str, Any]) -> dict[str, Any]:
    instrument = _symbol(body.get("instrument"))
    timeframe = str(body.get("timeframe", "1H")).upper()
    if timeframe not in {"15M", "1H", "4H", "D1"}:
        raise RequestError(400, "timeframe must be 15M, 1H, 4H, or D1")
    bars = body.get("bars")
    ticks = body.get("ticks", [])
    if not isinstance(bars, list) or not isinstance(ticks, list):
        raise RequestError(400, "bars and ticks must be arrays")
    engine = ATLASQuantEngine(os.environ.get("ATLAS_CONFIG_PATH", "config.yaml"))
    result = asyncio.run(
        engine.generate_signal_from_data(
            instrument=instrument,
            timeframe=timeframe,
            bars=bars,
            ticks=ticks,
            quote=body.get("quote") if isinstance(body.get("quote"), dict) else None,
            macro_inputs=body.get("macroInputs") if isinstance(body.get("macroInputs"), dict) else None,
            headlines=body.get("headlines") if isinstance(body.get("headlines"), list) else None,
            calendar_events=body.get("calendarEvents") if isinstance(body.get("calendarEvents"), list) else None,
            active_signals=body.get("activeSignals") if isinstance(body.get("activeSignals"), list) else None,
            run_llm=bool(body.get("runLlm", False)),
            write_audit=bool(body.get("writeAudit", True)),
        )
    )
    return {"provider": "ATLAS_QUANT_V2", "readOnly": True, "orderSubmission": False, "result": result}


def route_render_chart(body: dict[str, Any]) -> dict[str, Any]:
    symbol = _symbol(body.get("symbol"))
    timeframe = str(body.get("timeframe", "1H")).upper()
    if timeframe not in {"15M", "1H", "4H", "D1"}:
        raise RequestError(400, "timeframe must be 15M, 1H, 4H, or D1")
    bars = body.get("bars")
    signal_packet = body.get("signal")
    if not isinstance(bars, list) or not isinstance(signal_packet, dict):
        raise RequestError(400, "bars must be an array and signal must be an object")
    config_path = os.environ.get("ATLAS_CONFIG_PATH", "config.yaml")
    engine = ATLASQuantEngine(config_path)
    chart_config = engine.config.get("charts", {})
    output_directory = Path(chart_config.get("output_directory", "data/charts"))
    if not output_directory.is_absolute():
        output_directory = engine.config_path.parent / output_directory
    wyckoff = body.get("wyckoff") if isinstance(body.get("wyckoff"), dict) else signal_packet.get("wyckoff")
    smc = body.get("smc") if isinstance(body.get("smc"), dict) else signal_packet.get("smc")
    if not isinstance(wyckoff, dict):
        wyckoff = WyckoffAnalyzer().analyze(bars)
    if not isinstance(smc, dict):
        smc = SMCAnalyzer().analyze(bars)
    metadata = render_chart(
        bars,
        signal_packet,
        smc=smc,
        wyckoff=wyckoff,
        output_directory=output_directory,
        symbol=symbol,
        timeframe=timeframe,
        width=int(chart_config.get("width", 1400)),
        height=int(chart_config.get("height", 800)),
    )
    png = Path(metadata["path"]).read_bytes()
    public_metadata = {key: value for key, value in metadata.items() if key != "path"}
    return {
        "provider": "ATLAS_STATIC_CHART",
        "readOnly": True,
        "orderSubmission": False,
        "result": {**public_metadata, "pngBase64": base64.b64encode(png).decode("ascii")},
    }


class IntelligenceHandler(BaseHTTPRequestHandler):
    server_version = "AtlasIntelligence/1.0"

    def log_message(self, fmt: str, *args: Any) -> None:
        sys.stdout.write(f"[intelligence_bridge] {self.address_string()} - {fmt % args}\n")

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, separators=(",", ":"), allow_nan=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

    def _authorized(self) -> bool:
        if not TOKEN:
            return True
        supplied = self.headers.get("Authorization", "")
        return hmac.compare_digest(supplied, f"Bearer {TOKEN}")

    def _read_body(self) -> dict[str, Any]:
        raw_length = self.headers.get("Content-Length")
        if raw_length is None:
            raise RequestError(411, "Content-Length is required")
        try:
            length = int(raw_length)
        except ValueError as exc:
            raise RequestError(400, "invalid Content-Length") from exc
        if length <= 0 or length > MAX_BODY_BYTES:
            raise RequestError(413, "request body is empty or too large")
        try:
            payload = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise RequestError(400, "request body must be valid JSON") from exc
        if not isinstance(payload, dict):
            raise RequestError(400, "request body must be a JSON object")
        return payload

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return
        if urlparse(self.path).path != "/health":
            self._send_json(404, {"error": "unknown route"})
            return
        self._send_json(
            200,
            {
                "status": "ok",
                "provider": "ATLAS_PYTHON_INTELLIGENCE",
                "readOnly": True,
                "orderSubmission": False,
                "orderflowMethod": "TICK_RULE_APPROXIMATION",
                "llmRoutes": LLMOrchestrator().routing_table(),
                "cloudFallback": False,
            },
        )

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return
        path = urlparse(self.path).path
        handlers = {"/orderflow": route_orderflow, "/llm/route": route_llm, "/llm/pipeline": route_pipeline, "/v2/signal": route_v2_signal, "/render-chart": route_render_chart}
        handler = handlers.get(path)
        if handler is None:
            self._send_json(404, {"error": "unknown route"})
            return
        try:
            self._send_json(200, handler(self._read_body()))
        except RequestError as exc:
            self._send_json(exc.status, {"error": exc.message})
        except (AtlasV2ValidationError, ChartRenderError, OrderFlowValidationError, OrchestratorError, WyckoffValidationError) as exc:
            self._send_json(422, {"error": str(exc)})
        except Exception as exc:  # noqa: BLE001
            self._send_json(500, {"error": f"internal intelligence error: {exc}"})


def main() -> None:
    if not TOKEN:
        print("[intelligence_bridge] WARNING: ATLAS_INTELLIGENCE_TOKEN is empty; loopback binding remains enforced.", file=sys.stderr)
    server = ThreadingHTTPServer((BIND_HOST, PORT), IntelligenceHandler)
    print(f"[intelligence_bridge] listening on http://{BIND_HOST}:{PORT} (analysis-only)")

    def shutdown(_signum: int, _frame: Any) -> None:
        server.shutdown()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    signal.signal(signal.SIGTERM, shutdown)
    server.serve_forever()


if __name__ == "__main__":
    main()
