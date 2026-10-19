"""Idempotent SEC 13F, Schedule 13D, and Form 4 ingestion.

EDGAR network access is explicit and requires a descriptive user agent. Parsing
and storage are deterministic and independently testable from filing fixtures.
"""

from __future__ import annotations

import json
import logging
import re
import xml.etree.ElementTree as ET
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from datetime import date
from typing import Any, Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request, urlopen

from app.core.http import JsonRequest, JsonTransport, UrllibJsonTransport
from app.core.store import RecordStore, stable_key
from app.llm.client import LLMClient, LLMRequest

SUPPORTED_FORMS = {"13F-HR", "13F-HR/A", "SC 13D", "SC 13D/A", "4", "4/A"}
LOGGER = logging.getLogger(__name__)


class SecPipelineError(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class FilingReference:
    cik: str
    accession: str
    form: str
    filing_date: str
    primary_document: str


@dataclass(frozen=True, slots=True)
class FilingDocument:
    reference: FilingReference
    content: str
    source_url: str


@dataclass(frozen=True, slots=True)
class TickerResolution:
    ticker: str | None
    provider: str
    confidence: float


class FilingClient(Protocol):
    def list_recent_filings(self, cik: str, forms: set[str]) -> list[FilingReference]: ...
    def download_filing(self, reference: FilingReference) -> FilingDocument: ...


class TickerResolver(Protocol):
    name: str
    def resolve(self, cusip: str, issuer: str) -> str | None: ...


class SectorResolver(Protocol):
    def classify(self, ticker: str, issuer: str) -> tuple[str | None, str | None, str]: ...


class SecEdgarClient:
    """Minimal SEC Fair Access client for recent filings and filing documents."""

    def __init__(self, user_agent: str, *, timeout_seconds: float = 30.0) -> None:
        if "@" not in user_agent and "contact" not in user_agent.lower():
            raise SecPipelineError("SEC_USER_AGENT must identify the application and a contact")
        self.user_agent = user_agent
        self.timeout_seconds = max(2.0, timeout_seconds)

    def _get(self, url: str) -> bytes:
        request = Request(url, headers={"User-Agent": self.user_agent, "Accept-Encoding": "identity"}, method="GET")
        try:
            with urlopen(request, timeout=self.timeout_seconds) as response:
                return bytes(response.read())
        except (HTTPError, URLError, TimeoutError, OSError) as exc:
            raise SecPipelineError(f"SEC request failed for {url}: {exc}") from exc

    def _json(self, url: str) -> dict[str, Any]:
        try:
            value = json.loads(self._get(url).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SecPipelineError(f"SEC returned invalid JSON for {url}") from exc
        if not isinstance(value, dict):
            raise SecPipelineError("SEC JSON payload must be an object")
        return value

    def list_recent_filings(self, cik: str, forms: set[str] | None = None) -> list[FilingReference]:
        normalized = str(int(cik)).zfill(10)
        payload = self._json(f"https://data.sec.gov/submissions/CIK{normalized}.json")
        recent = payload.get("filings", {}).get("recent", {})
        if not isinstance(recent, dict):
            raise SecPipelineError("SEC submissions payload has no recent filing table")
        desired = forms or SUPPORTED_FORMS
        lengths = [len(recent.get(field, [])) for field in ("accessionNumber", "form", "filingDate", "primaryDocument")]
        rows: list[FilingReference] = []
        for index in range(min(lengths, default=0)):
            form = str(recent["form"][index]).upper()
            if form not in desired:
                continue
            rows.append(FilingReference(normalized, str(recent["accessionNumber"][index]), form, str(recent["filingDate"][index]), str(recent["primaryDocument"][index])))
        return rows

    def download_filing(self, reference: FilingReference) -> FilingDocument:
        accession = reference.accession.replace("-", "")
        base = f"https://www.sec.gov/Archives/edgar/data/{int(reference.cik)}/{accession}"
        filename = reference.primary_document
        if reference.form.startswith("13F"):
            index = self._json(base + "/index.json")
            items = index.get("directory", {}).get("item", [])
            xml_names = [str(item.get("name")) for item in items if isinstance(item, dict) and str(item.get("name", "")).lower().endswith(".xml")]
            filename = next((name for name in xml_names if "info" in name.lower()), xml_names[0] if xml_names else filename)
        url = f"{base}/{quote(filename)}"
        return FilingDocument(reference, self._get(url).decode("utf-8", errors="replace"), url)


class TickerResolverChain:
    """Ordered yfinance/OpenFIGI/TradingView/LLM-style resolution chain."""

    def __init__(self, resolvers: Iterable[TickerResolver]) -> None:
        self.resolvers = list(resolvers)

    def resolve(self, cusip: str, issuer: str) -> TickerResolution:
        for index, resolver in enumerate(self.resolvers):
            try:
                ticker = resolver.resolve(cusip, issuer)
            except Exception as exc:  # noqa: BLE001 - fallback chains isolate third-party adapters
                LOGGER.warning("ticker_resolver_failed", extra={"resolver": resolver.name, "error": str(exc)})
                continue
            if ticker and re.fullmatch(r"[A-Z0-9.\-/]{1,24}", ticker.strip().upper()):
                return TickerResolution(ticker.strip().upper(), resolver.name, max(0.5, 0.99 - index * 0.1))
        return TickerResolution(None, "UNRESOLVED", 0.0)


class YahooTickerResolver:
    name = "YAHOO"

    def __init__(self, transport: JsonTransport | None = None) -> None:
        self.transport = transport or UrllibJsonTransport()

    def resolve(self, cusip: str, issuer: str) -> str | None:
        url = "https://query2.finance.yahoo.com/v1/finance/search?" + urlencode({"q": issuer, "quotesCount": 5, "newsCount": 0})
        payload = self.transport.send(JsonRequest("GET", url))
        if not isinstance(payload, dict):
            return None
        for row in payload.get("quotes", []):
            if isinstance(row, dict) and row.get("quoteType") in {"EQUITY", "ETF"} and isinstance(row.get("symbol"), str):
                return str(row["symbol"])
        return None


class OpenFigiTickerResolver:
    name = "OPENFIGI"

    def __init__(self, api_key: str | None = None, transport: JsonTransport | None = None) -> None:
        self.api_key = api_key
        self.transport = transport or UrllibJsonTransport()

    def resolve(self, cusip: str, issuer: str) -> str | None:
        headers = {"X-OPENFIGI-APIKEY": self.api_key} if self.api_key else {}
        payload = self.transport.send(JsonRequest("POST", "https://api.openfigi.com/v3/mapping", headers, [{"idType": "ID_CUSIP", "idValue": cusip}]))
        if isinstance(payload, list) and payload and isinstance(payload[0], dict):
            data = payload[0].get("data")
            if isinstance(data, list) and data and isinstance(data[0], dict) and isinstance(data[0].get("ticker"), str):
                return str(data[0]["ticker"])
        return None


class TradingViewTickerResolver:
    name = "TRADINGVIEW"

    def __init__(self, transport: JsonTransport | None = None) -> None:
        self.transport = transport or UrllibJsonTransport()

    def resolve(self, cusip: str, issuer: str) -> str | None:
        url = "https://symbol-search.tradingview.com/symbol_search/v3/?" + urlencode({"text": issuer, "hl": "1", "exchange": ""})
        payload = self.transport.send(JsonRequest("GET", url))
        rows = payload.get("symbols", []) if isinstance(payload, dict) else payload if isinstance(payload, list) else []
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("symbol"), str):
                return str(row["symbol"])
        return None


class LLMTickerResolver:
    name = "LLM"

    def __init__(self, client: LLMClient, provider: str) -> None:
        self.client = client
        self.provider = provider

    def resolve(self, cusip: str, issuer: str) -> str | None:
        schema = {"type": "object", "properties": {"ticker": {"type": ["string", "null"]}, "confidence": {"type": "number", "minimum": 0, "maximum": 1}}, "required": ["ticker", "confidence"], "additionalProperties": False}
        result = self.client.complete(LLMRequest(self.provider, f"Resolve SEC issuer {issuer!r} with CUSIP {cusip!r}. Use only public identifier knowledge.", schema))
        return result.data["ticker"] if result.data["confidence"] >= 0.7 else None


class NullSectorResolver:
    def classify(self, ticker: str, issuer: str) -> tuple[str | None, str | None, str]:
        return None, None, "UNRESOLVED"


class SectorResolverChain:
    def __init__(self, resolvers: Iterable[SectorResolver]) -> None:
        self.resolvers = list(resolvers)

    def classify(self, ticker: str, issuer: str) -> tuple[str | None, str | None, str]:
        for resolver in self.resolvers:
            try:
                sector, industry, provider = resolver.classify(ticker, issuer)
            except Exception as exc:  # noqa: BLE001 - fallback chains isolate third-party adapters
                LOGGER.warning("sector_resolver_failed", extra={"resolver": type(resolver).__name__, "error": str(exc)})
                continue
            if sector or industry:
                return sector, industry, provider
        return None, None, "UNRESOLVED"


class YahooSectorResolver:
    def __init__(self, transport: JsonTransport | None = None) -> None:
        self.transport = transport or UrllibJsonTransport()

    def classify(self, ticker: str, issuer: str) -> tuple[str | None, str | None, str]:
        url = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{quote(ticker, safe='.-')}?modules=assetProfile"
        payload = self.transport.send(JsonRequest("GET", url))
        summary = payload.get("quoteSummary") if isinstance(payload, dict) else None
        results = summary.get("result") if isinstance(summary, dict) else None
        result = results[0] if isinstance(results, list) and results and isinstance(results[0], dict) else None
        profile = result.get("assetProfile") if isinstance(result, dict) else None
        if not isinstance(profile, dict):
            return None, None, "YAHOO"
        return _optional_text(profile.get("sector")), _optional_text(profile.get("industry")), "YAHOO"


class LLMSectorResolver:
    def __init__(self, client: LLMClient, provider: str) -> None:
        self.client = client
        self.provider = provider

    def classify(self, ticker: str, issuer: str) -> tuple[str | None, str | None, str]:
        schema = {"type": "object", "properties": {"sector": {"type": ["string", "null"]}, "industry": {"type": ["string", "null"]}, "confidence": {"type": "number", "minimum": 0, "maximum": 1}}, "required": ["sector", "industry", "confidence"], "additionalProperties": False}
        result = self.client.complete(LLMRequest(self.provider, f"Classify ticker {ticker!r}, issuer {issuer!r} into a standard equity sector and industry.", schema))
        if result.data["confidence"] < 0.7:
            return None, None, "LLM_LOW_CONFIDENCE"
        return result.data["sector"], result.data["industry"], f"LLM:{self.provider}"


class SecFilingPipeline:
    def __init__(self, client: FilingClient, store: RecordStore, ticker_resolver: TickerResolverChain, sector_resolver: SectorResolver | None = None) -> None:
        self.client = client
        self.store = store
        self.ticker_resolver = ticker_resolver
        self.sector_resolver = sector_resolver or NullSectorResolver()

    def ingest_fund(self, cik: str, fund_name: str, forms: set[str] | None = None) -> dict[str, int]:
        counters = {"filings": 0, "holdings": 0, "insider_transactions": 0, "duplicates": 0, "unresolved": 0}
        fund_key = str(int(cik)).zfill(10)
        self.store.upsert("funds", fund_key, {"cik": fund_key, "name": fund_name})
        for reference in self.client.list_recent_filings(fund_key, forms or SUPPORTED_FORMS):
            document = self.client.download_filing(reference)
            result = self.ingest_document(document, fund_name)
            for key, value in result.items():
                counters[key] += value
        return counters

    def ingest_document(self, document: FilingDocument, fund_name: str) -> dict[str, int]:
        result = {"filings": 0, "holdings": 0, "insider_transactions": 0, "duplicates": 0, "unresolved": 0}
        ref = document.reference
        filing_key = stable_key(ref.cik, ref.accession, ref.form)
        filing_row = {**asdict(ref), "fund_name": fund_name, "source_url": document.source_url}
        filing_upsert = self.store.upsert("filings", filing_key, filing_row)
        result["filings"] += int(filing_upsert.inserted)
        result["duplicates"] += int(not filing_upsert.inserted)
        if ref.form.startswith("13F"):
            holdings = parse_13f(document.content)
        elif ref.form.startswith("SC 13D"):
            holdings = parse_13d(document.content)
        else:
            holdings = []
            for transaction in parse_form4(document.content):
                row = {**transaction, "fund_cik": ref.cik, "accession": ref.accession, "filing_date": ref.filing_date}
                key = stable_key(ref.accession, transaction["owner"], transaction["transaction_date"], transaction["code"], transaction["shares"])
                upsert = self.store.upsert("insider_transactions", key, row)
                result["insider_transactions"] += int(upsert.inserted)
                result["duplicates"] += int(not upsert.inserted)
        total_value = sum(float(row.get("value", 0) or 0) for row in holdings)
        for holding in holdings:
            resolution = self.ticker_resolver.resolve(str(holding.get("cusip", "")), str(holding.get("issuer", "")))
            if resolution.ticker is None:
                result["unresolved"] += 1
            ticker = resolution.ticker
            sector, industry, sector_provider = self.sector_resolver.classify(ticker, str(holding.get("issuer", ""))) if ticker else (None, None, "UNRESOLVED")
            value = float(holding.get("value", 0) or 0)
            row = {
                **holding,
                "fund_cik": ref.cik,
                "fund_name": fund_name,
                "accession": ref.accession,
                "filing_type": ref.form,
                "filing_date": ref.filing_date,
                "quarter": quarter_label(ref.filing_date),
                "ticker": ticker,
                "ticker_resolution": resolution.provider,
                "ticker_confidence": resolution.confidence,
                "sector": sector,
                "industry": industry,
                "sector_resolution": sector_provider,
                "portfolio_pct": round(value / total_value, 8) if total_value > 0 else None,
            }
            key = stable_key(ref.accession, holding.get("cusip"), holding.get("class"), holding.get("shares"), holding.get("transaction_type"))
            upsert = self.store.upsert("holdings", key, row)
            result["holdings"] += int(upsert.inserted)
            result["duplicates"] += int(not upsert.inserted)
        return result


def _local_name(tag: str) -> str:
    return tag.rsplit("}", 1)[-1]


def _children(root: ET.Element, name: str) -> list[ET.Element]:
    return [item for item in root.iter() if _local_name(item.tag).lower() == name.lower()]


def _value(root: ET.Element, name: str) -> str:
    item = next(iter(_children(root, name)), None)
    if item is None:
        return ""
    direct = (item.text or "").strip()
    if direct:
        return direct
    nested = next((child for child in item.iter() if child is not item and _local_name(child.tag).lower() == "value" and (child.text or "").strip()), None)
    return (nested.text or "").strip() if nested is not None else ""


def _number(value: str) -> float:
    cleaned = re.sub(r"[^0-9.\-]", "", value)
    return float(cleaned) if cleaned not in {"", "-", "."} else 0.0


def parse_13f(content: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise SecPipelineError(f"Invalid 13F XML: {exc}") from exc
    rows = []
    for table in _children(root, "infoTable"):
        rows.append({
            "issuer": _value(table, "nameOfIssuer"),
            "class": _value(table, "titleOfClass"),
            "cusip": _value(table, "cusip").upper(),
            "value": _number(_value(table, "value")) * 1_000,
            "shares": _number(_value(table, "sshPrnamt")),
            "share_type": _value(table, "sshPrnamtType"),
            "transaction_type": "HOLDING",
        })
    return rows


def parse_13d(content: str) -> list[dict[str, Any]]:
    text = re.sub(r"<[^>]+>", " ", content)
    issuer = _match(text, r"(?:Name of Issuer|Issuer)\s*[:\-]?\s*([A-Z][A-Za-z0-9 &.,'\-]{2,80})") or "UNKNOWN"
    cusip = (_match(text, r"CUSIP(?: Number)?\s*[:#\-]?\s*([A-Z0-9]{8,9})") or "").upper()
    shares = _number(_match(text, r"(?:Aggregate Amount Beneficially Owned|shares)\s*[:\-]?\s*([0-9,]+)") or "0")
    percent = _number(_match(text, r"Percent of Class[^0-9]*([0-9.]+)%") or "0")
    return [{"issuer": issuer.strip(), "class": "BENEFICIAL_OWNERSHIP", "cusip": cusip, "value": 0.0, "shares": shares, "share_type": "SH", "ownership_pct": percent / 100 if percent else None, "transaction_type": "13D_POSITION"}]


def parse_form4(content: str) -> list[dict[str, Any]]:
    try:
        root = ET.fromstring(content)
    except ET.ParseError as exc:
        raise SecPipelineError(f"Invalid Form 4 XML: {exc}") from exc
    owner = _value(root, "rptOwnerName")
    ticker = _value(root, "issuerTradingSymbol").upper()
    rows = []
    for transaction in _children(root, "nonDerivativeTransaction") + _children(root, "derivativeTransaction"):
        rows.append({
            "owner": owner,
            "ticker": ticker,
            "transaction_date": _value(transaction, "transactionDate") or _value(root, "periodOfReport"),
            "code": _value(transaction, "transactionCode"),
            "shares": _number(_value(transaction, "transactionShares")),
            "price": _number(_value(transaction, "transactionPricePerShare")),
            "acquired_disposed": _value(transaction, "transactionAcquiredDisposedCode"),
            "source": "SEC_FORM4",
        })
    return rows


def _match(text: str, pattern: str) -> str | None:
    found = re.search(pattern, text, flags=re.IGNORECASE)
    return found.group(1) if found else None


def _optional_text(value: Any) -> str | None:
    return value.strip() if isinstance(value, str) and value.strip() else None


def quarter_label(value: str) -> str:
    parsed = date.fromisoformat(value)
    return f"{parsed.year}Q{((parsed.month - 1) // 3) + 1}"


__all__ = [
    "FilingDocument", "FilingReference", "LLMSectorResolver", "LLMTickerResolver", "OpenFigiTickerResolver", "SecEdgarClient",
    "SecFilingPipeline", "SecPipelineError", "SectorResolverChain", "TickerResolution", "TickerResolverChain", "TradingViewTickerResolver",
    "YahooSectorResolver", "YahooTickerResolver", "parse_13d", "parse_13f", "parse_form4", "quarter_label",
]
