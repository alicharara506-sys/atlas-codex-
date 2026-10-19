#!/usr/bin/env python3
"""ATLAS Quantitative Signal Engine v2.0 orchestration core.

This module composes deterministic closed-bar analytics into an institutional
research schema. Local LLMs are optional, advisory reviewers. Market geometry,
confluence math, volatility exposure, and suppression rules remain deterministic.
"""

from __future__ import annotations

import asyncio
import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from audit_log import AuditLogger
from confluence_scorer import ConfluenceInputs, ConfluenceScorer
from data_sources import FreeMarketDataClient
from llm_orchestrator import LLMOrchestrator, routes_from_config
from macro_engine import MacroInputs, MacroRegimeEngine, event_cooldown, parse_calendar_rows
from news_sentiment import Headline, analyze_headlines
from orderflow_engine import build_orderflow_features
from risk_engine import correlation_guard, exposure_units, liquidity_filter, realized_volatility, scenario_stress, structural_levels
from smc_engine import SMCAnalyzer
from wyckoff_engine import WyckoffAnalyzer


class AtlasV2ValidationError(ValueError):
    pass


def load_config(path: str | Path) -> dict[str, Any]:
    """Load config.yaml, whose JSON-compatible YAML avoids a PyYAML dependency."""

    config_path = Path(path).expanduser().resolve()
    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise AtlasV2ValidationError(f"Invalid Atlas config: {exc}") from exc
    if not isinstance(payload, dict):
        raise AtlasV2ValidationError("Atlas config root must be an object")
    return payload


def _atr(bars: list[dict[str, Any]], period: int = 14) -> float | None:
    if len(bars) < period + 1:
        return None
    true_ranges = []
    for index in range(1, len(bars)):
        high, low = float(bars[index]["high"]), float(bars[index]["low"])
        prior = float(bars[index - 1]["close"])
        true_ranges.append(max(high - low, abs(high - prior), abs(low - prior)))
    return sum(true_ranges[-period:]) / period


def _atr_percentile(bars: list[dict[str, Any]], period: int = 14) -> float | None:
    if len(bars) < period * 2:
        return None
    values = []
    for end in range(period + 1, len(bars) + 1):
        value = _atr(bars[:end], period)
        if value is not None:
            values.append(value)
    if not values:
        return None
    current = values[-1]
    return sum(1 for value in values if value <= current) / len(values)


def _bb_width_percentile(bars: list[dict[str, Any]], period: int = 20) -> float | None:
    closes = [float(bar["close"]) for bar in bars]
    if len(closes) < period * 2:
        return None
    widths = []
    for end in range(period, len(closes) + 1):
        window = closes[end - period : end]
        mean = sum(window) / period
        variance = sum((value - mean) ** 2 for value in window) / period
        widths.append((4 * math.sqrt(variance) / mean) if mean else 0.0)
    return sum(1 for value in widths if value <= widths[-1]) / len(widths)


def _expected_hold(timeframe: str, volatility_regime: str) -> str:
    if timeframe.upper() == "15M":
        baseline = "1-4 hours"
    elif timeframe.upper() == "1H":
        baseline = "8-16 hours"
    elif timeframe.upper() == "4H":
        baseline = "1-4 days"
    else:
        baseline = "3-15 days"
    return f"{baseline} ({volatility_regime.lower()} volatility estimate)"


def _deterministic_components(features: dict[str, Any], news: dict[str, Any], macro: dict[str, Any], wyckoff: dict[str, Any], smc: dict[str, Any]) -> ConfluenceInputs:
    profile = features["volumeProfile"]
    last_close = features["footprintProxy"]["bars"][-1] if features["footprintProxy"]["bars"] else None
    avwap = features["avwap"]
    price = features.get("lastPrice")
    avwap_score = 0.0
    if price is not None and avwap.get("value") is not None:
        avwap_score = 2.0 if price > avwap["value"] else -2.0 if price < avwap["value"] else 0.0
    profile_score = 2.0 if price is not None and price > profile["valueAreaHigh"] else -2.0 if price is not None and price < profile["valueAreaLow"] else 0.0
    cvd_state = features["cvd"]["divergence"]
    cvd_score = 2.0 if cvd_state in {"BULLISH_DIVERGENCE_ACCUMULATION", "BULLISH_TREND_CONFIRMED"} else -2.0 if cvd_state in {"BEARISH_DIVERGENCE_DISTRIBUTION", "BEARISH_TREND_CONFIRMED"} else 0.0
    bos = features["marketStructure"]["bos"]
    structure_score = 2.0 if bos == "BULLISH_BOS" else -2.0 if bos == "BEARISH_BOS" else 0.0
    patterns = last_close["patterns"] if last_close else []
    footprint_score = 1.0 if any(item in patterns for item in ("SWEEP_LOW", "INITIATIVE_BUYING")) else -1.0 if any(item in patterns for item in ("SWEEP_HIGH", "INITIATIVE_SELLING")) else 0.0
    latest_ofii = features["ofii"]["latest"]
    ofii_score = 1.0 if latest_ofii and latest_ofii["value"] > 0.70 else -1.0 if latest_ofii and latest_ofii["value"] < 0.30 else 0.0
    sentiment = news.get("impactScore")
    news_score = max(-2.0, min(2.0, float(sentiment) * 2)) if isinstance(sentiment, (int, float)) else 0.0
    macro_regime = macro.get("regime")
    macro_score = 0.0 if macro_regime in {"UNAVAILABLE", "MIXED"} else 1.0 if macro_regime in {"EXPANSION", "RECOVERY"} else -1.0
    base_score = avwap_score + profile_score + cvd_score + structure_score + footprint_score + ofii_score + news_score + macro_score
    lean = 1 if base_score > 0 else -1 if base_score < 0 else 0
    phase = wyckoff.get("phase")
    wyckoff_score = 1.0 if phase in {"ACCUMULATION", "MARKUP"} else -1.0 if phase in {"DISTRIBUTION", "MARKDOWN"} else 0.0
    nearest_ob = smc.get("nearest_ob_to_price")
    order_block_score = 0.0
    if lean and isinstance(nearest_ob, dict) and nearest_ob.get("status") == "untested":
        block_sign = 1 if nearest_ob.get("type") == "bullish" else -1 if nearest_ob.get("type") == "bearish" else 0
        order_block_score = float(lean) if block_sign == lean else 0.0
    fvg_score = 0.0
    if lean:
        desired = "bullish" if lean > 0 else "bearish"
        if any(gap.get("type") == desired and not gap.get("mitigated") for gap in smc.get("fvgs", [])):
            fvg_score = float(lean)
    sweep_score = 0.0
    if lean:
        opposing = "bearish" if lean > 0 else "bullish"
        if any(sweep.get("direction") == opposing and sweep.get("confirmed") for sweep in smc.get("liquidity_sweeps", [])):
            sweep_score = float(-2 * lean)
    return ConfluenceInputs(
        avwap=avwap_score,
        volume_profile=profile_score,
        cvd=cvd_score,
        market_structure=structure_score,
        footprint=footprint_score,
        ofii=ofii_score,
        news_sentiment=news_score,
        macro_narrative=macro_score,
        strategist_review=0.0,
        wyckoff_phase=wyckoff_score,
        smc_order_block=order_block_score,
        smc_fvg=fvg_score,
        smc_liquidity_sweep=sweep_score,
    )


class ATLASQuantEngine:
    def __init__(self, config_path: str | Path = "config.yaml"):
        self.config_path = Path(config_path).expanduser().resolve()
        self.config = load_config(self.config_path)
        prompt_directory = Path(self.config.get("llm", {}).get("prompt_directory", "prompts"))
        if not prompt_directory.is_absolute():
            prompt_directory = self.config_path.parent / prompt_directory
        self.orchestrator = LLMOrchestrator(
            timeout_seconds=float(self.config.get("llm", {}).get("timeout_seconds", 300)),
            cache_ttl_seconds=float(self.config.get("llm", {}).get("cache_ttl_seconds", 300)),
            routes=routes_from_config(self.config),
            prompt_directory=prompt_directory,
        )
        audit_path = Path(self.config.get("engine", {}).get("audit_database", "data/atlas_audit.sqlite3"))
        if not audit_path.is_absolute():
            audit_path = self.config_path.parent / audit_path
        self.audit = AuditLogger(audit_path)
        structure_config = self.config.get("structure", {})
        self.wyckoff_analyzer = WyckoffAnalyzer(int(structure_config.get("wyckoff_minimum_bars", 20)))
        self.smc_analyzer = SMCAnalyzer(float(structure_config.get("smc_impulse_atr", 1.5)))

    async def generate_signal(
        self,
        instrument: str,
        timeframe: str,
        lookback_bars: int = 200,
        *,
        run_llm: bool = False,
    ) -> dict[str, Any]:
        """Optional zero-cost Yahoo path matching the documented v2 interface.

        The production Atlas UI uses MT5 and calls ``generate_signal_from_data``.
        This convenience path raises a visible error if yfinance/network data is
        unavailable; it never substitutes generated bars.
        """

        client = FreeMarketDataClient()
        bars = await client.fetch_bars_async(instrument, timeframe, lookback_bars)
        return await self.generate_signal_from_data(
            instrument=instrument,
            timeframe=timeframe,
            bars=bars,
            run_llm=run_llm,
        )

    async def generate_signal_from_data(
        self,
        *,
        instrument: str,
        timeframe: str,
        bars: list[dict[str, Any]],
        ticks: list[dict[str, Any]] | None = None,
        quote: dict[str, Any] | None = None,
        macro_inputs: dict[str, Any] | None = None,
        headlines: list[dict[str, Any]] | None = None,
        calendar_events: list[dict[str, Any]] | None = None,
        active_signals: list[dict[str, Any]] | None = None,
        run_llm: bool = False,
        write_audit: bool = True,
    ) -> dict[str, Any]:
        supported = set(self.config.get("instruments", []))
        normalized = instrument.upper().replace("/", "")
        if normalized not in supported:
            raise AtlasV2ValidationError(f"Unsupported instrument '{instrument}'")
        if len(bars) < 50:
            raise AtlasV2ValidationError("At least 50 closed bars are required for v2 analysis")
        now = int(datetime.now(timezone.utc).timestamp())
        atr = _atr(bars)
        if atr is None or atr <= 0:
            raise AtlasV2ValidationError("ATR is unavailable or non-positive")
        atr_pct = _atr_percentile(bars)
        bb_pct = _bb_width_percentile(bars)
        features, wyckoff, smc = await asyncio.gather(
            asyncio.to_thread(build_orderflow_features, bars, ticks, timeframe=timeframe, atr_percentile=atr_pct, bb_width_percentile=bb_pct),
            asyncio.to_thread(self.wyckoff_analyzer.analyze, bars),
            asyncio.to_thread(self.smc_analyzer.analyze, bars),
        )
        features["lastPrice"] = float(bars[-1]["close"])

        macro_fields = {key: value for key, value in (macro_inputs or {}).items() if key in MacroInputs.__dataclass_fields__}
        macro = MacroRegimeEngine.classify(MacroInputs(**macro_fields))
        normalized_headlines = [Headline(str(row.get("title", "")), str(row.get("summary", "")), str(row.get("source", "local")), str(row.get("url", "")), row.get("published_at")) for row in (headlines or []) if row.get("title")]
        news = analyze_headlines(normalized_headlines, normalized)
        cooldown = event_cooldown(parse_calendar_rows(calendar_events or []), now=now, instrument=normalized)

        current_spread = None
        if quote and isinstance(quote.get("bid"), (int, float)) and isinstance(quote.get("ask"), (int, float)):
            current_spread = float(quote["ask"]) - float(quote["bid"])
        liquidity = liquidity_filter(current_spread=current_spread, average_spread=None, atr_percentile=atr_pct, timestamp_utc=now, tier1_cooldown=cooldown["suppressNewSignals"])
        score_inputs = _deterministic_components(features, news, macro, wyckoff, smc)
        scored = ConfluenceScorer().score(score_inputs, micro_regime=features["microRegime"], liquidity_suppressed=liquidity["suppressed"], news_suppressed=cooldown["suppressNewSignals"])

        closes = [float(bar["close"]) for bar in bars[-21:]]
        periods = {"15M": 252 * 24 * 4, "1H": 252 * 24, "4H": 252 * 6, "D1": 252}.get(timeframe.upper(), 252)
        realized = realized_volatility(closes, periods)
        correlated = correlation_guard({"direction": scored["direction"], "conviction": scored["conviction"]}, active_signals or [], float(self.config.get("risk", {}).get("correlation_threshold", 0.75)))
        exposure = exposure_units(correlated["adjustedConviction"], realized, float(self.config.get("risk", {}).get("annual_vol_target", 0.10)))

        structure = features["marketStructure"]
        swing_low = structure["swingLows"][-1]["price"] if structure["swingLows"] else None
        swing_high = structure["swingHighs"][-1]["price"] if structure["swingHighs"] else None
        profile = features["volumeProfile"]
        entry = float(bars[-1]["close"])
        geometry = structural_levels(scored["direction"], entry=entry, atr=atr, value_area_low=profile["valueAreaLow"], value_area_high=profile["valueAreaHigh"], swing_low=swing_low, swing_high=swing_high, low_volume_nodes=profile["lowVolumeNodes"], high_volume_nodes=profile["highVolumeNodes"])
        stress = scenario_stress(scored["direction"], entry=entry, stop=geometry["stopLoss"], atr=atr, value_area_low=profile["valueAreaLow"], value_area_high=profile["valueAreaHigh"]) if geometry["valid"] else {"stressPassed": False, "confidenceAdjustment": 0.0, "scenarios": []}

        volatility_regime = "EXTREME" if realized is not None and realized > 0.30 else "HIGH" if atr_pct is not None and atr_pct > 0.80 else "LOW" if atr_pct is not None and atr_pct < 0.20 else "NORMAL"
        targets = geometry.get("takeProfit", [])
        llm_results: list[dict[str, Any]] = []
        if run_llm:
            gated_requests = [("micro_regime", {"deterministicFeatures": features})]
            if features["microRegime"] not in {"BALANCED", "VOL_COMPRESSION"}:
                gated_requests.extend([("confluence_thesis", {"features": features, "wyckoff": wyckoff, "smc": smc, "macro": macro, "news": news, "score": scored}), ("avwap_quality", {"avwap": features["avwap"], "lastBar": bars[-1]})])
            if abs(scored["score"]) >= 4:
                display_action = "LONG" if scored["direction"].endswith("LONG") else "SHORT" if scored["direction"].endswith("SHORT") else "NO_TRADE"
                deterministic_signal = {
                    "action": display_action,
                    "symbol": normalized,
                    "timeframe": timeframe.upper(),
                    "entry": entry if geometry["valid"] else None,
                    "stopLoss": geometry.get("stopLoss"),
                    "takeProfits": targets,
                    "grade": scored["direction"],
                    "evidence": [f"Deterministic confluence score {scored['score']:.1f}/10.", f"Micro regime {features['microRegime']}.", f"Liquidity status {liquidity['status']}."],
                }
                gated_requests.extend([
                    ("signal_json", {"deterministicSignal": deterministic_signal}),
                    ("risk_validation", {"deterministicStop": geometry.get("stopLoss"), "deterministicRiskReward": geometry.get("riskReward", []), "minimumRewardRisk": self.config.get("risk", {}).get("minimum_reward_risk", 1.5)}),
                    ("confluence_math_review", {"score": scored}),
                    ("scenario_stress", {"deterministicScenarios": stress}),
                ])
            llm_results = await self.orchestrator.run_parallel(gated_requests)

        reasoning = " ".join(
            [
                f"{features['microRegime']} micro regime.",
                f"AVWAP anchor {features['avwap']['anchor']['type']}.",
                f"CVD state {features['cvd']['divergence']}.",
                f"Market structure {structure['bos']} / {structure['choch']}.",
                f"Wyckoff phase {wyckoff['phase']} at {wyckoff['confidence']:.0%} deterministic confidence.",
                f"SMC found {len(smc['order_blocks'])} order blocks, {len(smc['fvgs'])} fair-value gaps, and {len(smc['liquidity_sweeps'])} liquidity sweeps.",
                f"Liquidity {liquidity['status']}.",
                f"Macro evidence {macro['regime']} and news evidence {news['status']}.",
            ]
        )
        signal = {
            "metadata": {"engine": "ATLAS", "version": "2.0", "timestamp": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), "instrument": normalized, "timeframe": timeframe.upper()},
            "signal": {
                "direction": scored["direction"], "conviction": correlated["adjustedConviction"], "confluence_score": scored["score"],
                "entry_zone": {"min": entry, "max": entry, "type": "MARKET_REFERENCE"} if geometry["valid"] else {"min": None, "max": None, "type": "NONE"},
                "stop_loss": geometry.get("stopLoss"),
                "take_profit": {"tp1": targets[0] if len(targets) > 0 else None, "tp2": targets[1] if len(targets) > 1 else None, "tp3": targets[2] if len(targets) > 2 else None},
                "risk_reward": {f"tp{index + 1}": value for index, value in enumerate(geometry.get("riskReward", []))},
                "exposure_units": exposure["units"], "volatility_regime": volatility_regime, "expected_hold": _expected_hold(timeframe, volatility_regime),
            },
            "regime": {"micro": features["microRegime"], "macro": macro["regime"], "macro_narrative": macro["evidence"][0], "dxy_bias": macro["inputs"]["dxy_direction"], "vix_regime": macro["inputs"]["vix_level"]},
            "confluence": scored,
            "wyckoff": wyckoff,
            "smc": smc,
            "indicators": {
                "avwap": features["avwap"], "volume_profile": profile,
                "cvd": {"trend": features["cvd"]["divergence"], "divergence": features["cvd"]["divergence"], "slope_10": features["cvd"]["latest"] - features["cvd"]["series"][max(0, len(features["cvd"]["series"]) - 10)]["value"] if features["cvd"]["series"] else 0},
                "footprint": features["footprintProxy"]["latest"], "orderflow": features["ofii"]["latest"], "market_structure": structure,
            },
            "news": {"headlines_analyzed": news["headlinesAnalyzed"], "dominant_sentiment": news["dominantSentiment"], "tier1_countdown_min": cooldown["tier1CountdownMin"], "impact_score": news["impactScore"], "status": news["status"]},
            "risk": {"scenario_test_passed": stress["stressPassed"], "correlation_warning": bool(correlated["warnings"]), "liquidity_status": liquidity["status"], "max_adverse_excursion": abs(entry - geometry["stopLoss"]) if geometry["valid"] else None, "sl_atr_multiple": geometry.get("slAtrMultiple"), "exposure": exposure, "stress": stress, "correlation": correlated, "suppression": scored["suppressionReasons"]},
            "llm_attribution": {
                "llama_1b_tasks": [row["task"] for row in llm_results if row.get("model") == "llama3.2:1b-instruct-q3_K_L"],
                "mistral_3b_tasks": [row["task"] for row in llm_results if row.get("model") == "ministral-3:3b"],
                "qwen_3b_tasks": [row["task"] for row in llm_results if row.get("model") == "qwen2.5:3b"],
                "phi4_mini_tasks": [row["task"] for row in llm_results if row.get("model") == "phi4-mini"],
                "total_latency_ms": sum(float(row.get("latencyMs", 0)) for row in llm_results), "results": llm_results,
            },
            "reasoning": reasoning,
            "data_health": {"market": "PROVIDED_CLOSED_BARS", "orderflow": features["preferredOrderFlowSource"], "wyckoff": "DETERMINISTIC", "smc": "DETERMINISTIC", "macro": "LIVE" if macro["regime"] != "UNAVAILABLE" else "UNAVAILABLE", "news": news["status"], "calendar": "LIVE" if calendar_events else "UNAVAILABLE", "cloudFallback": False},
            "safety": {"readOnly": True, "orderSubmission": False, "deterministicEngineAuthoritative": True},
        }
        if write_audit:
            signal["audit"] = self.audit.write_signal(signal)
        return signal


__all__ = ["ATLASQuantEngine", "AtlasV2ValidationError", "load_config"]
