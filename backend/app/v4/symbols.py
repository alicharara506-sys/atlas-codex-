"""Canonical V4 universe and broker-boundary symbol translation."""

from __future__ import annotations

from dataclasses import dataclass

from app.v4.contracts import AssetClass, OfficialTimeframe


class UnsupportedInstrumentError(ValueError):
    """Raised when a symbol is outside the explicitly approved V4 universe."""


@dataclass(frozen=True, slots=True)
class SymbolDefinition:
    canonical: str
    asset_class: AssetClass
    bridge_symbol: str


_FX = (
    "EURUSD",
    "GBPUSD",
    "USDJPY",
    "USDCHF",
    "USDCAD",
    "AUDUSD",
    "NZDUSD",
    "EURGBP",
    "EURJPY",
    "GBPJPY",
    "AUDJPY",
)
_COMMODITIES = ("XAUUSD", "XAGUSD", "WTI", "BRENT")
_DEFINITIONS = {
    symbol: SymbolDefinition(symbol, AssetClass.FOREX, symbol)
    for symbol in _FX
} | {
    "XAUUSD": SymbolDefinition("XAUUSD", AssetClass.COMMODITY, "XAUUSD"),
    "XAGUSD": SymbolDefinition("XAGUSD", AssetClass.COMMODITY, "XAGUSD"),
    "WTI": SymbolDefinition("WTI", AssetClass.COMMODITY, "USOIL"),
    "BRENT": SymbolDefinition("BRENT", AssetClass.COMMODITY, "UKOIL"),
}
_ALIASES = {
    "GOLD": "XAUUSD",
    "SILVER": "XAGUSD",
    "USOIL": "WTI",
    "WTICOUSD": "WTI",
    "CL": "WTI",
    "UKOIL": "BRENT",
    "BRN": "BRENT",
}
BRIDGE_TIMEFRAMES: dict[OfficialTimeframe, str] = {
    OfficialTimeframe.M15: "15M",
    OfficialTimeframe.H1: "1H",
    OfficialTimeframe.H4: "4H",
    OfficialTimeframe.D1: "D1",
}
OFFICIAL_TIMEFRAMES: tuple[OfficialTimeframe, ...] = tuple(BRIDGE_TIMEFRAMES)


def normalize_instrument(raw: str) -> SymbolDefinition:
    normalized = raw.strip().upper().replace("/", "").replace("-", "").replace("_", "")
    canonical = _ALIASES.get(normalized, normalized)
    try:
        return _DEFINITIONS[canonical]
    except KeyError as exc:
        raise UnsupportedInstrumentError(f"'{raw}' is outside the approved ATLAS V4 universe") from exc


def approved_universe() -> tuple[str, ...]:
    return tuple(_DEFINITIONS)


__all__ = [
    "BRIDGE_TIMEFRAMES",
    "OFFICIAL_TIMEFRAMES",
    "SymbolDefinition",
    "UnsupportedInstrumentError",
    "approved_universe",
    "normalize_instrument",
]
