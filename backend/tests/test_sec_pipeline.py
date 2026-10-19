from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

BACKEND = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.core.http import JsonRequest
from app.core.store import MemoryRecordStore
from app.data.sec.pipeline import (
    FilingDocument,
    FilingReference,
    LLMSectorResolver,
    LLMTickerResolver,
    OpenFigiTickerResolver,
    SecEdgarClient,
    SecFilingPipeline,
    SecPipelineError,
    SectorResolverChain,
    TickerResolverChain,
    TradingViewTickerResolver,
    YahooSectorResolver,
    YahooTickerResolver,
    parse_13d,
    parse_13f,
    parse_form4,
)
from app.llm.client import LLMClient

INFO_TABLE = """<?xml version="1.0" encoding="UTF-8"?>
<informationTable xmlns="http://www.sec.gov/edgar/document/thirteenf/informationtable">
  <infoTable><nameOfIssuer>ALPHA INC</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>000000AA1</cusip><value>5000</value><shrsOrPrnAmt><sshPrnamt>100</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
  <infoTable><nameOfIssuer>BETA CORP</nameOfIssuer><titleOfClass>COM</titleOfClass><cusip>000000BB2</cusip><value>3000</value><shrsOrPrnAmt><sshPrnamt>75</sshPrnamt><sshPrnamtType>SH</sshPrnamtType></shrsOrPrnAmt></infoTable>
</informationTable>"""

FORM4 = """<ownershipDocument>
<periodOfReport>2026-08-01</periodOfReport><issuer><issuerTradingSymbol>ALPH</issuerTradingSymbol></issuer>
<reportingOwner><reportingOwnerId><rptOwnerName>DOE JANE</rptOwnerName></reportingOwnerId></reportingOwner>
<nonDerivativeTable><nonDerivativeTransaction><transactionDate><value>2026-08-01</value></transactionDate>
<transactionCoding><transactionCode>P</transactionCode></transactionCoding>
<transactionAmounts><transactionShares><value>50</value></transactionShares><transactionPricePerShare><value>12.50</value></transactionPricePerShare><transactionAcquiredDisposedCode><value>A</value></transactionAcquiredDisposedCode></transactionAmounts>
</nonDerivativeTransaction></nonDerivativeTable></ownershipDocument>"""

SCHEDULE_13D = """<html><body>
Name of Issuer: GAMMA CORPORATION<br>
CUSIP Number: 000000CC3<br>
Aggregate Amount Beneficially Owned: 12,500<br>
Percent of Class: 7.5%
</body></html>"""


class StaticTransport:
    def __init__(self, payload: dict[str, Any] | list[Any]) -> None:
        self.payload = payload
        self.requests: list[JsonRequest] = []

    def send(self, request: JsonRequest) -> dict[str, Any] | list[Any]:
        self.requests.append(request)
        return self.payload


class FixtureEdgarClient(SecEdgarClient):
    def __init__(self, responses: dict[str, bytes]) -> None:
        super().__init__("Atlas tests contact@example.com", timeout_seconds=0.1)
        self.responses = responses

    def _get(self, url: str) -> bytes:
        for suffix, payload in self.responses.items():
            if url.endswith(suffix):
                return payload
        raise AssertionError(f"Unexpected SEC URL: {url}")


class StaticClient:
    def __init__(self, document: FilingDocument) -> None:
        self.document = document

    def list_recent_filings(self, cik: str, forms: set[str]) -> list[FilingReference]:
        return [self.document.reference]

    def download_filing(self, reference: FilingReference) -> FilingDocument:
        return self.document


class NoneResolver:
    name = "YAHOO"
    def resolve(self, cusip: str, issuer: str) -> str | None:
        return None


class FixtureResolver:
    name = "OPENFIGI"
    def resolve(self, cusip: str, issuer: str) -> str | None:
        return {"000000AA1": "ALPH", "000000BB2": "BETA"}.get(cusip)


class FixtureSector:
    def classify(self, ticker: str, issuer: str) -> tuple[str, str, str]:
        return "Technology", "Software", "FIXTURE"


class SecPipelineTests(unittest.TestCase):
    def test_13f_ingestion_resolves_tickers_weights_portfolio_and_is_idempotent(self) -> None:
        reference = FilingReference("0000123456", "0000123456-26-000001", "13F-HR", "2026-06-30", "primary.xml")
        document = FilingDocument(reference, INFO_TABLE, "fixture://13f")
        store = MemoryRecordStore()
        pipeline = SecFilingPipeline(StaticClient(document), store, TickerResolverChain([NoneResolver(), FixtureResolver()]), FixtureSector())
        first = pipeline.ingest_fund(reference.cik, "Fixture Fund", {"13F-HR"})
        second = pipeline.ingest_fund(reference.cik, "Fixture Fund", {"13F-HR"})
        self.assertEqual(first["holdings"], 2)
        self.assertEqual(first["unresolved"], 0)
        self.assertEqual(second["holdings"], 0)
        holdings = store.values("holdings")
        self.assertEqual({row["ticker"] for row in holdings}, {"ALPH", "BETA"})
        self.assertAlmostEqual(sum(row["portfolio_pct"] for row in holdings), 1.0)
        self.assertTrue(all(row["quarter"] == "2026Q2" for row in holdings))

    def test_form4_parser_extracts_insider_transaction(self) -> None:
        rows = parse_form4(FORM4)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["ticker"], "ALPH")
        self.assertEqual(rows[0]["code"], "P")
        self.assertEqual(rows[0]["shares"], 50)
        self.assertEqual(rows[0]["price"], 12.5)

    def test_sec_client_lists_and_downloads_information_table(self) -> None:
        submissions = {
            "filings": {"recent": {
                "accessionNumber": ["0000123456-26-000001", "ignored"],
                "form": ["13F-HR", "10-K"],
                "filingDate": ["2026-06-30", "2026-01-01"],
                "primaryDocument": ["primary.xml", "annual.htm"],
            }}
        }
        client = FixtureEdgarClient({
            "CIK0000123456.json": __import__("json").dumps(submissions).encode(),
            "/index.json": b'{"directory":{"item":[{"name":"primary.xml"},{"name":"informationTable.xml"}]}}',
            "/informationTable.xml": INFO_TABLE.encode(),
        })
        references = client.list_recent_filings("123456", {"13F-HR"})
        self.assertEqual(len(references), 1)
        document = client.download_filing(references[0])
        self.assertIn("informationTable", document.source_url)
        self.assertEqual(len(parse_13f(document.content)), 2)

        with self.assertRaises(SecPipelineError):
            SecEdgarClient("anonymous")
        invalid = FixtureEdgarClient({"CIK0000123456.json": b"not-json"})
        with self.assertRaisesRegex(SecPipelineError, "invalid JSON"):
            invalid.list_recent_filings("123456")

    def test_public_ticker_and_sector_fallback_adapters(self) -> None:
        yahoo = YahooTickerResolver(StaticTransport({"quotes": [
            {"quoteType": "CRYPTOCURRENCY", "symbol": "NO"},
            {"quoteType": "EQUITY", "symbol": "ALPH"},
        ]}))
        self.assertEqual(yahoo.resolve("", "Alpha"), "ALPH")
        self.assertIsNone(YahooTickerResolver(StaticTransport([])).resolve("", "Alpha"))

        figi_transport = StaticTransport([{"data": [{"ticker": "BETA"}]}])
        figi = OpenFigiTickerResolver("secret", figi_transport)
        self.assertEqual(figi.resolve("000000BB2", "Beta"), "BETA")
        self.assertEqual(figi_transport.requests[0].headers["X-OPENFIGI-APIKEY"], "secret")
        self.assertIsNone(OpenFigiTickerResolver(transport=StaticTransport([])).resolve("x", "x"))

        trading_view = TradingViewTickerResolver(StaticTransport({"symbols": [{"symbol": "GAMMA"}]}))
        self.assertEqual(trading_view.resolve("", "Gamma"), "GAMMA")
        self.assertIsNone(TradingViewTickerResolver(StaticTransport({})).resolve("", "Gamma"))

        sector = YahooSectorResolver(StaticTransport({"quoteSummary": {"result": [{"assetProfile": {
            "sector": " Technology ", "industry": "Software",
        }}]}}))
        self.assertEqual(sector.classify("ALPH", "Alpha"), ("Technology", "Software", "YAHOO"))
        self.assertEqual(YahooSectorResolver(StaticTransport({})).classify("ALPH", "Alpha"), (None, None, "YAHOO"))

    def test_chains_and_optional_llm_fallbacks_fail_closed(self) -> None:
        class BrokenTicker:
            name = "BROKEN"
            def resolve(self, cusip: str, issuer: str) -> str | None:
                raise RuntimeError("offline")

        unresolved = TickerResolverChain([BrokenTicker(), NoneResolver()]).resolve("x", "x")
        self.assertIsNone(unresolved.ticker)

        class BrokenSector:
            def classify(self, ticker: str, issuer: str) -> tuple[str | None, str | None, str]:
                raise RuntimeError("offline")

        self.assertEqual(SectorResolverChain([BrokenSector()]).classify("ABC", "Alpha"), (None, None, "UNRESOLVED"))

        class FakeLlm:
            def __init__(self, data: dict[str, Any]) -> None:
                self.data = data
            def complete(self, request: object) -> SimpleNamespace:
                return SimpleNamespace(data=self.data)

        ticker_client = cast(LLMClient, FakeLlm({"ticker": "ALPH", "confidence": 0.8}))
        self.assertEqual(LLMTickerResolver(ticker_client, "ollama").resolve("x", "Alpha"), "ALPH")
        low_client = cast(LLMClient, FakeLlm({"ticker": "ALPH", "confidence": 0.2}))
        self.assertIsNone(LLMTickerResolver(low_client, "ollama").resolve("x", "Alpha"))

        sector_client = cast(LLMClient, FakeLlm({"sector": "Technology", "industry": "Software", "confidence": 0.9}))
        self.assertEqual(LLMSectorResolver(sector_client, "ollama").classify("ALPH", "Alpha"), ("Technology", "Software", "LLM:ollama"))
        low_sector_client = cast(LLMClient, FakeLlm({"sector": None, "industry": None, "confidence": 0.1}))
        self.assertEqual(LLMSectorResolver(low_sector_client, "ollama").classify("ALPH", "Alpha"), (None, None, "LLM_LOW_CONFIDENCE"))

    def test_13d_and_form4_documents_ingest_idempotently(self) -> None:
        store = MemoryRecordStore()
        resolver = TickerResolverChain([FixtureResolver()])
        pipeline = SecFilingPipeline(
            StaticClient(FilingDocument(FilingReference("1", "a", "SC 13D", "2026-08-01", "a.htm"), SCHEDULE_13D, "fixture://13d")),
            store,
            resolver,
        )
        document = pipeline.client.download_filing(FilingReference("1", "a", "SC 13D", "2026-08-01", "a.htm"))
        first = pipeline.ingest_document(document, "Gamma Fund")
        second = pipeline.ingest_document(document, "Gamma Fund")
        self.assertEqual(first["holdings"], 1)
        self.assertEqual(second["holdings"], 0)
        parsed = parse_13d(SCHEDULE_13D)[0]
        self.assertEqual(parsed["ownership_pct"], 0.075)

        form4_document = FilingDocument(FilingReference("1", "b", "4", "2026-08-01", "b.xml"), FORM4, "fixture://form4")
        form4_result = pipeline.ingest_document(form4_document, "Gamma Fund")
        self.assertEqual(form4_result["insider_transactions"], 1)
        self.assertEqual(len(store.values("insider_transactions")), 1)

    def test_invalid_xml_is_rejected(self) -> None:
        with self.assertRaises(SecPipelineError):
            parse_13f("<broken")
        with self.assertRaises(SecPipelineError):
            parse_form4("<broken")


if __name__ == "__main__":
    unittest.main()
