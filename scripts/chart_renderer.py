#!/usr/bin/env python3
"""Static annotated chart export for ATLAS Quant.

Matplotlib/Agg is used when installed. A deterministic standard-library PNG
renderer is included so the local bridge remains functional offline on the
current lightweight Python runtime. Both paths save a static PNG only.
"""

from __future__ import annotations

import binascii
import math
import re
import struct
import zlib
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from wyckoff_engine import _records

try:  # Optional preferred renderer.
    import matplotlib

    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from matplotlib.patches import Rectangle

    MATPLOTLIB_AVAILABLE = True
except Exception:  # pragma: no cover - exercised only when dependency exists
    MATPLOTLIB_AVAILABLE = False


class ChartRenderError(ValueError):
    """Raised when a chart payload or destination is invalid."""


FONT = {
    "A": ["01110", "10001", "10001", "11111", "10001", "10001", "10001"], "B": ["11110", "10001", "10001", "11110", "10001", "10001", "11110"],
    "C": ["01111", "10000", "10000", "10000", "10000", "10000", "01111"], "D": ["11110", "10001", "10001", "10001", "10001", "10001", "11110"],
    "E": ["11111", "10000", "10000", "11110", "10000", "10000", "11111"], "F": ["11111", "10000", "10000", "11110", "10000", "10000", "10000"],
    "G": ["01111", "10000", "10000", "10111", "10001", "10001", "01111"], "H": ["10001", "10001", "10001", "11111", "10001", "10001", "10001"],
    "I": ["11111", "00100", "00100", "00100", "00100", "00100", "11111"], "J": ["00111", "00010", "00010", "00010", "10010", "10010", "01100"],
    "K": ["10001", "10010", "10100", "11000", "10100", "10010", "10001"], "L": ["10000", "10000", "10000", "10000", "10000", "10000", "11111"],
    "M": ["10001", "11011", "10101", "10101", "10001", "10001", "10001"], "N": ["10001", "11001", "10101", "10011", "10001", "10001", "10001"],
    "O": ["01110", "10001", "10001", "10001", "10001", "10001", "01110"], "P": ["11110", "10001", "10001", "11110", "10000", "10000", "10000"],
    "Q": ["01110", "10001", "10001", "10001", "10101", "10010", "01101"], "R": ["11110", "10001", "10001", "11110", "10100", "10010", "10001"],
    "S": ["01111", "10000", "10000", "01110", "00001", "00001", "11110"], "T": ["11111", "00100", "00100", "00100", "00100", "00100", "00100"],
    "U": ["10001", "10001", "10001", "10001", "10001", "10001", "01110"], "V": ["10001", "10001", "10001", "10001", "10001", "01010", "00100"],
    "W": ["10001", "10001", "10001", "10101", "10101", "10101", "01010"], "X": ["10001", "10001", "01010", "00100", "01010", "10001", "10001"],
    "Y": ["10001", "10001", "01010", "00100", "00100", "00100", "00100"], "Z": ["11111", "00001", "00010", "00100", "01000", "10000", "11111"],
    "0": ["01110", "10001", "10011", "10101", "11001", "10001", "01110"], "1": ["00100", "01100", "00100", "00100", "00100", "00100", "01110"],
    "2": ["01110", "10001", "00001", "00010", "00100", "01000", "11111"], "3": ["11110", "00001", "00001", "01110", "00001", "00001", "11110"],
    "4": ["00010", "00110", "01010", "10010", "11111", "00010", "00010"], "5": ["11111", "10000", "10000", "11110", "00001", "00001", "11110"],
    "6": ["01110", "10000", "10000", "11110", "10001", "10001", "01110"], "7": ["11111", "00001", "00010", "00100", "01000", "01000", "01000"],
    "8": ["01110", "10001", "10001", "01110", "10001", "10001", "01110"], "9": ["01110", "10001", "10001", "01111", "00001", "00001", "01110"],
    ".": ["00000", "00000", "00000", "00000", "00000", "00110", "00110"], ":": ["00000", "00110", "00110", "00000", "00110", "00110", "00000"],
    "-": ["00000", "00000", "00000", "11111", "00000", "00000", "00000"], "/": ["00001", "00010", "00010", "00100", "01000", "01000", "10000"],
    "_": ["00000", "00000", "00000", "00000", "00000", "00000", "11111"], " ": ["00000"] * 7,
}


class Raster:
    def __init__(self, width: int, height: int, background: tuple[int, int, int] = (7, 12, 15)):
        self.width, self.height = width, height
        self.pixels = bytearray(background * (width * height))

    def pixel(self, x: int, y: int, color: tuple[int, int, int], alpha: float = 1.0) -> None:
        if not (0 <= x < self.width and 0 <= y < self.height):
            return
        offset = (y * self.width + x) * 3
        for channel in range(3):
            self.pixels[offset + channel] = round(self.pixels[offset + channel] * (1 - alpha) + color[channel] * alpha)

    def rectangle(self, x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int], alpha: float = 1.0, outline: bool = False) -> None:
        left, right = sorted((max(0, x0), min(self.width - 1, x1)))
        top, bottom = sorted((max(0, y0), min(self.height - 1, y1)))
        if outline:
            self.line(left, top, right, top, color); self.line(left, bottom, right, bottom, color); self.line(left, top, left, bottom, color); self.line(right, top, right, bottom, color)
            return
        for y in range(top, bottom + 1):
            for x in range(left, right + 1):
                self.pixel(x, y, color, alpha)

    def line(self, x0: int, y0: int, x1: int, y1: int, color: tuple[int, int, int], thickness: int = 1, dashed: bool = False) -> None:
        dx, dy = abs(x1 - x0), -abs(y1 - y0)
        sx, sy = (1 if x0 < x1 else -1), (1 if y0 < y1 else -1)
        error, step = dx + dy, 0
        while True:
            if not dashed or (step // 7) % 2 == 0:
                for oy in range(-(thickness // 2), thickness // 2 + 1):
                    for ox in range(-(thickness // 2), thickness // 2 + 1):
                        self.pixel(x0 + ox, y0 + oy, color)
            if x0 == x1 and y0 == y1:
                break
            twice = 2 * error
            if twice >= dy:
                error += dy; x0 += sx
            if twice <= dx:
                error += dx; y0 += sy
            step += 1

    def text(self, x: int, y: int, value: str, color: tuple[int, int, int], scale: int = 2) -> None:
        cursor = x
        for character in value.upper():
            glyph = FONT.get(character, FONT[" "])
            for row_index, row in enumerate(glyph):
                for column, bit in enumerate(row):
                    if bit == "1":
                        self.rectangle(cursor + column * scale, y + row_index * scale, cursor + column * scale + scale - 1, y + row_index * scale + scale - 1, color)
            cursor += 6 * scale

    def triangle(self, x: int, y: int, color: tuple[int, int, int], upward: bool = True) -> None:
        for offset in range(7):
            width = offset if upward else 6 - offset
            yy = y + (6 - offset if upward else offset)
            self.line(x - width, yy, x + width, yy, color)

    def save_png(self, path: Path) -> None:
        raw = b"".join(b"\x00" + bytes(self.pixels[row * self.width * 3 : (row + 1) * self.width * 3]) for row in range(self.height))
        def chunk(kind: bytes, data: bytes) -> bytes:
            return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", binascii.crc32(kind + data) & 0xFFFFFFFF)
        payload = b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", self.width, self.height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(raw, 9)) + chunk(b"IEND", b"")
        path.write_bytes(payload)


def _signal_geometry(signal: dict[str, Any]) -> tuple[tuple[float, float] | None, float | None, list[float]]:
    packet = signal.get("signal", signal)
    zone = packet.get("entry_zone", packet.get("entryZone"))
    if isinstance(zone, dict):
        low, high = zone.get("min", zone.get("low")), zone.get("max", zone.get("high"))
        entry = (float(low), float(high)) if isinstance(low, (int, float)) and isinstance(high, (int, float)) else None
    else:
        entry = None
    stop = packet.get("stop_loss", packet.get("stopLoss", packet.get("invalidation")))
    stop_value = float(stop) if isinstance(stop, (int, float)) else None
    take_profit = packet.get("take_profit", packet.get("takeProfits", packet.get("targets", [])))
    if isinstance(take_profit, dict):
        targets = [float(value) for key in ("tp1", "tp2", "tp3") if isinstance((value := take_profit.get(key)), (int, float))]
    else:
        targets = [float(value) for value in take_profit if isinstance(value, (int, float))] if isinstance(take_profit, list) else []
    return entry, stop_value, targets[:3]


def _label_lanes(rows: list[tuple[float, str, tuple[int, int, int]]], y_for: Any, top: int, bottom: int) -> list[tuple[int, str, tuple[int, int, int]]]:
    placed: list[tuple[int, str, tuple[int, int, int]]] = []
    previous = top - 18
    for price, label, color in sorted(rows, key=lambda item: y_for(item[0])):
        position = max(y_for(price) - 7, previous + 18)
        position = min(bottom - 14, max(top, position))
        placed.append((position, label, color)); previous = position
    return placed


def _render_builtin(path: Path, rows: list[dict[str, float]], signal: dict[str, Any], smc: dict[str, Any], wyckoff: dict[str, Any], width: int, height: int, symbol: str, timeframe: str) -> None:
    chart_rows = rows[-120:]
    offset = len(rows) - len(chart_rows)
    left, right, top, bottom = 78, width - 180, 62, height - 145
    entry, stop, targets = _signal_geometry(signal)
    levels = [row["low"] for row in chart_rows] + [row["high"] for row in chart_rows]
    if entry:
        levels.extend(entry)
    if stop is not None:
        levels.append(stop)
    levels.extend(targets)
    low, high = min(levels), max(levels)
    padding = max((high - low) * 0.08, abs(high) * 0.0001, 1e-6)
    low, high = low - padding, high + padding
    x_for = lambda index: left + int((index - offset + 0.5) * (right - left) / len(chart_rows))
    y_for = lambda price: bottom - int((price - low) * (bottom - top) / max(high - low, 1e-12))
    canvas = Raster(width, height)
    canvas.rectangle(left, top, right, bottom, (12, 21, 25))
    for grid in range(6):
        y = top + int(grid * (bottom - top) / 5)
        canvas.line(left, y, right, y, (31, 47, 54))
        price = high - grid * (high - low) / 5
        canvas.text(8, y - 7, f"{price:.5f}", (122, 143, 152), 1)
    for block in smc.get("order_blocks", [])[-8:]:
        if block.get("status") == "broken":
            continue
        index = int(block.get("index", 0))
        if index < offset:
            index = offset
        color = (42, 150, 103) if block.get("type") == "bullish" else (184, 71, 81)
        canvas.rectangle(x_for(index), y_for(float(block["high"])), right, y_for(float(block["low"])), color, 0.16)
        canvas.rectangle(x_for(index), y_for(float(block["high"])), right, y_for(float(block["low"])), color, outline=True)
    for gap in smc.get("fvgs", [])[-10:]:
        if gap.get("mitigated"):
            continue
        index = max(offset, int(gap.get("index", offset)))
        color = (74, 130, 190) if gap.get("type") == "bullish" else (150, 92, 177)
        canvas.rectangle(x_for(index), y_for(float(gap["top"])), right, y_for(float(gap["bottom"])), color, 0.12)
    if entry:
        canvas.rectangle(left, y_for(max(entry)), right, y_for(min(entry)), (43, 171, 111), 0.18)
    label_rows: list[tuple[float, str, tuple[int, int, int]]] = []
    if stop is not None:
        canvas.line(left, y_for(stop), right, y_for(stop), (241, 91, 101), 2, True); label_rows.append((stop, f"SL {stop:.5f}", (241, 91, 101)))
    for index, target in enumerate(targets):
        canvas.line(left, y_for(target), right, y_for(target), (83, 157, 232), 1, True); label_rows.append((target, f"TP{index + 1} {target:.5f}", (111, 177, 242)))
    avwap = signal.get("indicators", {}).get("avwap", {}).get("value")
    if isinstance(avwap, (int, float)) and low <= avwap <= high:
        canvas.line(left, y_for(float(avwap)), right, y_for(float(avwap)), (73, 211, 199), 1, True); label_rows.append((float(avwap), f"AVWAP {avwap:.5f}", (73, 211, 199)))
    candle_width = max(2, int((right - left) / len(chart_rows) * 0.55))
    max_volume = max((row["volume"] for row in chart_rows), default=1.0) or 1.0
    for local_index, row in enumerate(chart_rows):
        x = x_for(offset + local_index)
        color = (75, 213, 143) if row["close"] >= row["open"] else (239, 91, 103)
        canvas.line(x, y_for(row["high"]), x, y_for(row["low"]), color)
        y0, y1 = y_for(row["open"]), y_for(row["close"])
        canvas.rectangle(x - candle_width // 2, min(y0, y1), x + candle_width // 2, max(y0, y1) + 1, color)
        volume_height = int(row["volume"] / max_volume * 70)
        canvas.rectangle(x - candle_width // 2, height - 38 - volume_height, x + candle_width // 2, height - 38, color, 0.55)
    for event in wyckoff.get("events", []):
        index = int(event.get("index", -1))
        if offset <= index < len(rows):
            event_type = str(event.get("type", ""))
            price = float(event.get("price", rows[index]["close"]))
            bullish = event_type in {"SC", "ST", "SPRING", "AR"} and wyckoff.get("phase") == "ACCUMULATION"
            marker_y = y_for(price) + (15 if bullish else -20)
            canvas.triangle(x_for(index), marker_y, (244, 193, 79), bullish)
            canvas.text(max(left, x_for(index) - 15), marker_y + (9 if bullish else -15), event_type[:8], (244, 210, 118), 1)
    for y, label, color in _label_lanes(label_rows, y_for, top, bottom):
        canvas.text(right + 12, y, label, color, 1)
    structure = signal.get("indicators", {}).get("market_structure", {})
    canvas.text(left, 18, f"ATLAS {symbol} {timeframe}  WYCKOFF {wyckoff.get('phase', 'UNKNOWN')}", (224, 237, 240), 2)
    canvas.text(left, 42, f"BOS {structure.get('bos', 'NONE')}  CHOCH {structure.get('choch', 'NONE')}", (118, 151, 159), 1)
    canvas.text(left, height - 24, "STATIC RESEARCH CHART - CLOSED BROKER BARS - NO ORDER EXECUTION", (118, 136, 144), 1)
    canvas.save_png(path)


def _render_matplotlib(path: Path, rows: list[dict[str, float]], signal: dict[str, Any], smc: dict[str, Any], wyckoff: dict[str, Any], width: int, height: int, symbol: str, timeframe: str) -> None:  # pragma: no cover
    chart_rows = rows[-120:]
    offset = len(rows) - len(chart_rows)
    figure, axis = plt.subplots(figsize=(width / 100, height / 100), dpi=100)
    figure.patch.set_facecolor("#070c0f"); axis.set_facecolor("#0c1519")
    for local_index, row in enumerate(chart_rows):
        color = "#4bd58f" if row["close"] >= row["open"] else "#ef5b67"
        axis.vlines(local_index, row["low"], row["high"], color=color, linewidth=0.8)
        axis.add_patch(Rectangle((local_index - 0.32, min(row["open"], row["close"])), 0.64, max(abs(row["close"] - row["open"]), 1e-10), facecolor=color, edgecolor=color))
    for block in smc.get("order_blocks", [])[-8:]:
        if block.get("status") != "broken":
            start = max(0, int(block.get("index", offset)) - offset)
            axis.add_patch(Rectangle((start, block["low"]), len(chart_rows) - start, block["high"] - block["low"], color="#2a9667" if block.get("type") == "bullish" else "#b84751", alpha=0.15))
    for gap in smc.get("fvgs", [])[-10:]:
        if not gap.get("mitigated"):
            start = max(0, int(gap.get("index", offset)) - offset)
            axis.add_patch(Rectangle((start, gap["bottom"]), len(chart_rows) - start, gap["top"] - gap["bottom"], color="#4a82be", alpha=0.12))
    entry, stop, targets = _signal_geometry(signal)
    if entry:
        axis.axhspan(min(entry), max(entry), color="#2bab6f", alpha=0.16)
    if stop is not None:
        axis.axhline(stop, color="#f15b65", linestyle="--", label="SL")
    for index, target in enumerate(targets):
        axis.axhline(target, color="#539de8", linestyle="--", linewidth=0.8); axis.text(len(chart_rows) - 1, target, f" TP{index + 1}", color="#6fb1f2", va="bottom")
    avwap = signal.get("indicators", {}).get("avwap", {}).get("value")
    if isinstance(avwap, (int, float)):
        axis.axhline(avwap, color="#49d3c7", linestyle=":", label="AVWAP")
    for event in wyckoff.get("events", []):
        index = int(event.get("index", -1)) - offset
        if 0 <= index < len(chart_rows):
            axis.annotate(str(event.get("type")), (index, event.get("price")), xytext=(0, 14), textcoords="offset points", ha="center", color="#f4c976", fontsize=7, arrowprops={"arrowstyle": "-|>", "color": "#f4c976"})
    axis.set_title(f"ATLAS {symbol} {timeframe} | Wyckoff {wyckoff.get('phase', 'UNKNOWN')}", color="#e0edf0")
    axis.tick_params(colors="#81949d"); axis.grid(color="#203039", alpha=0.5)
    for spine in axis.spines.values(): spine.set_color("#263840")
    figure.tight_layout(); figure.savefig(path, facecolor=figure.get_facecolor()); plt.close(figure)


def render_chart(
    bars: Any,
    signal: dict[str, Any],
    *,
    smc: dict[str, Any] | None = None,
    wyckoff: dict[str, Any] | None = None,
    output_directory: str | Path = "data/charts",
    symbol: str = "ATLAS",
    timeframe: str = "1H",
    width: int = 1400,
    height: int = 800,
) -> dict[str, Any]:
    rows = _records(bars)
    if len(rows) < 5:
        raise ChartRenderError("At least five closed bars are required to render a chart")
    safe_symbol = re.sub(r"[^A-Z0-9_-]", "", symbol.upper())[:20] or "ATLAS"
    safe_timeframe = re.sub(r"[^A-Z0-9_-]", "", timeframe.upper())[:10] or "1H"
    directory = Path(output_directory).expanduser().resolve()
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    path = directory / f"{safe_symbol}_{safe_timeframe}_{stamp}.png"
    width, height = min(2400, max(900, int(width))), min(1600, max(600, int(height)))
    if MATPLOTLIB_AVAILABLE:
        _render_matplotlib(path, rows, signal, smc or {}, wyckoff or {}, width, height, safe_symbol, safe_timeframe)
        renderer = "MATPLOTLIB_AGG"
    else:
        _render_builtin(path, rows, signal, smc or {}, wyckoff or {}, width, height, safe_symbol, safe_timeframe)
        renderer = "ATLAS_BUILTIN_PNG"
    return {"path": str(path), "filename": path.name, "mimeType": "image/png", "bytes": path.stat().st_size, "renderer": renderer, "width": width, "height": height, "static": True, "readOnly": True, "orderSubmission": False}


__all__ = ["ChartRenderError", "MATPLOTLIB_AVAILABLE", "render_chart"]
