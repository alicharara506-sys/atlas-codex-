#!/usr/bin/env python3
"""Zero-cost news ingestion and local sentiment utilities for ATLAS v2."""

from __future__ import annotations

import json
import os
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import asdict, dataclass
from html import unescape
from typing import Any
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen


@dataclass(frozen=True)
class Headline:
    title: str
    summary: str
    source: str
    url: str
    published_at: int | None = None


TIER_RULES = {
    "T1": {
        "CENTRAL_BANK": ("fomc", "federal reserve", "ecb", "bank of england", "boj", "rate decision"),
        "EMPLOYMENT": ("nonfarm", "nfp", "unemployment", "payrolls"),
        "INFLATION": ("cpi", "ppi", "core pce", "inflation"),
    },
    "T2": {
        "GROWTH": ("gdp", "retail sales", "pmi"),
        "COMMODITY": ("opec", "eia", "oil inventory", "crude stocks"),
    },
    "T3": {
        "SPEECH": ("speech", "remarks", "testimony"),
        "GEOPOLITICAL": ("war", "sanction", "tariff", "trade policy", "ceasefire"),
    },
}

POSITIVE = {"rise", "rises", "rally", "strong", "growth", "surge", "gain", "bullish", "cut", "easing"}
NEGATIVE = {"fall", "falls", "drop", "weak", "recession", "decline", "loss", "bearish", "hike", "tightening"}


def tier_headline(text: str) -> dict[str, str]:
    lowered = text.lower()
    for tier, categories in TIER_RULES.items():
        for category, terms in categories.items():
            if any(term in lowered for term in terms):
                return {"tier": tier, "category": category}
    return {"tier": "T3", "category": "NONE"}


class LocalSentiment:
    """Optional local Transformers inference with an explicit lexicon fallback."""

    def __init__(self, model_name: str = "distilbert-base-uncased-finetuned-sst-2-english"):
        self.model_name = model_name
        self._pipeline = None
        self.backend = "LEXICON_FALLBACK"
        if os.environ.get("ATLAS_TRANSFORMERS_ENABLED", "false").lower() == "true":
            try:
                from transformers import pipeline  # type: ignore

                self._pipeline = pipeline("sentiment-analysis", model=model_name, local_files_only=True)
                self.backend = "LOCAL_TRANSFORMERS"
            except Exception:
                self._pipeline = None

    def analyze(self, text: str, instrument: str) -> dict[str, Any]:
        prompt = f"Directional market impact on {instrument}: {text}"[:2_000]
        if self._pipeline is not None:
            row = self._pipeline(prompt, truncation=True)[0]
            score = float(row["score"]) * (1 if str(row["label"]).upper() == "POSITIVE" else -1)
        else:
            tokens = set(re.findall(r"[a-z]+", prompt.lower()))
            positive = len(tokens & POSITIVE)
            negative = len(tokens & NEGATIVE)
            score = (positive - negative) / max(1, positive + negative)
        classification = "STRONGLY_BULLISH" if score >= 0.65 else "BULLISH" if score >= 0.2 else "STRONGLY_BEARISH" if score <= -0.65 else "BEARISH" if score <= -0.2 else "NEUTRAL"
        return {"score": score, "classification": classification, "backend": self.backend}


class FreeNewsClient:
    """Fetch NewsAPI free-tier and configured RSS feeds; never hides failures."""

    def __init__(self, timeout_seconds: float = 10.0):
        self.timeout_seconds = timeout_seconds

    @staticmethod
    def _safe_url(url: str, *, https_only: bool = True) -> str:
        parsed = urlparse(url)
        if parsed.scheme not in ({"https"} if https_only else {"http", "https"}) or not parsed.hostname:
            raise ValueError("news URL must use a valid public HTTP(S) host")
        if parsed.hostname in {"localhost", "127.0.0.1", "::1"}:
            raise ValueError("news URL must not target loopback")
        return url

    def fetch_rss(self, urls: list[str], limit: int = 30) -> tuple[list[Headline], list[str]]:
        headlines: list[Headline] = []
        errors: list[str] = []
        for url in urls:
            try:
                request = Request(self._safe_url(url), headers={"User-Agent": "ATLAS-Quant/2.0 local research"})
                with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310 -- public URL validated above
                    root = ET.fromstring(response.read(2_000_000))
                for item in root.findall(".//item")[:limit]:
                    title = unescape((item.findtext("title") or "").strip())
                    if title:
                        headlines.append(Headline(title, unescape((item.findtext("description") or "").strip()), urlparse(url).hostname or "rss", (item.findtext("link") or "").strip()))
            except Exception as exc:
                errors.append(f"{url}: {exc}")
        return headlines[:limit], errors

    def fetch_newsapi(self, query: str, api_key: str | None = None, limit: int = 30) -> tuple[list[Headline], str | None]:
        key = api_key or os.environ.get("NEWSAPI_KEY")
        if not key:
            return [], "NEWSAPI_KEY is not configured"
        endpoint = "https://newsapi.org/v2/everything?" + urlencode({"q": query, "pageSize": min(100, limit), "sortBy": "publishedAt", "language": "en"})
        try:
            request = Request(endpoint, headers={"X-Api-Key": key, "User-Agent": "ATLAS-Quant/2.0"})
            with urlopen(request, timeout=self.timeout_seconds) as response:  # noqa: S310 -- fixed NewsAPI HTTPS host
                payload = json.loads(response.read(2_000_000).decode("utf-8"))
            rows = payload.get("articles", []) if isinstance(payload, dict) else []
            return [Headline(str(row.get("title", "")), str(row.get("description", "")), str((row.get("source") or {}).get("name", "NewsAPI")), str(row.get("url", "")), int(time.time())) for row in rows if row.get("title")][:limit], None
        except Exception as exc:
            return [], str(exc)


def analyze_headlines(headlines: list[Headline], instrument: str, sentiment: LocalSentiment | None = None) -> dict[str, Any]:
    engine = sentiment or LocalSentiment()
    analyzed = []
    for headline in headlines:
        text = f"{headline.title}. {headline.summary}".strip()
        analyzed.append({**asdict(headline), **tier_headline(text), "sentiment": engine.analyze(text, instrument)})
    scores = [row["sentiment"]["score"] for row in analyzed]
    average = sum(scores) / len(scores) if scores else None
    dominant = "UNAVAILABLE" if average is None else "BULLISH" if average > 0.2 else "BEARISH" if average < -0.2 else "NEUTRAL"
    return {"headlinesAnalyzed": len(analyzed), "dominantSentiment": dominant, "impactScore": average, "items": analyzed, "status": "LIVE" if analyzed else "UNAVAILABLE"}


__all__ = ["FreeNewsClient", "Headline", "LocalSentiment", "analyze_headlines", "tier_headline"]
