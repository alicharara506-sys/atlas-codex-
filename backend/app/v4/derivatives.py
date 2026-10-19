"""Derivatives intelligence boundary with an honest unavailable default."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from app.v4.context_contracts import ContextAvailability, DerivativesContext


class DerivativesIntelligenceProvider(Protocol):
    def fetch(self, instrument: str, *, as_of: datetime) -> DerivativesContext: ...


class UnavailableDerivativesProvider:
    """Default provider: no licensed/reliable source means DATA_UNAVAILABLE."""

    name = "NO_RELIABLE_PROVIDER"

    def fetch(self, instrument: str, *, as_of: datetime) -> DerivativesContext:
        return DerivativesContext(
            instrument=instrument,
            as_of=as_of,
            availability=ContextAvailability.DATA_UNAVAILABLE,
            provider=self.name,
            reason="No legitimate derivatives data source is configured; no values were inferred or fabricated.",
        )


__all__ = ["DerivativesIntelligenceProvider", "UnavailableDerivativesProvider"]
