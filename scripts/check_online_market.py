"""Online provider samples and optional real, account-free market analysis.

Uses existing credentials through normal loaders; never prints credentials,
reads private accounts, sends messages or installs schedules. All outputs and
caches live in a new directory outside the repository. Empty samples and source
failures are not called healthy. This is a point-in-time sample, not an SLA.
"""
import argparse
import asyncio
import json
import logging
import os
import shutil
import sys
from dataclasses import asdict, is_dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from stocks.domain.models import Instrument
from stocks.engine import StocksEngine
from stocks.engine.advisory_mainline import build_advisory_outlook, resolve_mainline_llm_clients
from stocks.engine.config_loader import load_engine_config
from stocks.engine.event_calendar import (
    AkShareEarningsProvider,
    FinnhubEarningsCalendarProvider,
    StaticEventCalendarProvider,
)
from stocks.engine.history_provider import (
    BinanceKLineProvider,
    EastmoneyKLineProvider,
    NasdaqKLineProvider,
    TencentKLineProvider,
    YahooKLineProvider,
)
from stocks.engine.intelligence_analyzer import LLMIntelligenceAnalyzer
from stocks.engine.intelligence_harvester import IntelligenceHarvester
from stocks.engine.macro_data import FredMacroProvider, YahooFinanceMacroProvider
from stocks.engine.news_intelligence_store import IntelligenceSnapshot
from stocks.engine.news_sources import WatchlistGoogleNewsProvider
from stocks.providers.binance_quote import BinanceQuoteProvider
from stocks.providers.cn_macro import CnMacroProvider
from stocks.providers.eastmoney_a import EastmoneyAQuoteProvider
from stocks.providers.filings import CninfoFilingsProvider
from stocks.providers.finnhub_quote import FinnhubQuoteProvider
from stocks.providers.fund_nav import FundNavProvider
from stocks.providers.polygon_quote import PolygonQuoteProvider
from stocks.providers.rss_news import RSSNewsProvider
from stocks.providers.tencent_a import TencentAQuoteProvider


def serial(value):
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if is_dataclass(value):
        return asdict(value)
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, default=serial), encoding="utf-8")


def observation_ages(sources, now):
    ages = {}
    for key, meta in sources.items():
        raw = meta.get("as_of") if isinstance(meta, dict) else None
        try:
            day = datetime.fromisoformat(str(raw).replace("Z", "+00:00")).date()
            ages[key] = (now.date() - day).days
        except (ValueError, TypeError):
            ages[key] = None
    return ages


async def check_sources(output):
    output.mkdir()
    now = datetime.now(timezone.utc)
    semaphore = asyncio.Semaphore(6)
    results = []

    async def probe(name, factory, owner=None, *, configured=True, note=None):
        row = {"name": name, "checked_at": datetime.now(timezone.utc).isoformat(), "note": note}
        if not configured:
            row["status"] = "not_configured"
            results.append(row)
            return
        async with semaphore:
            try:
                value = await asyncio.wait_for(factory(), timeout=80)
                if hasattr(value, "empty"):
                    data, count = value.to_dict(orient="records"), len(value)
                elif isinstance(value, list):
                    data, count = value, len(value)
                else:
                    data, count = value, int(value is not None)
                errors = getattr(owner, "last_errors", {}) or getattr(value, "errors", {})
                fields = getattr(value, "field_sources", {})
                if hasattr(value, "field_sources"):
                    row["fields"] = fields
                    row["observation_age_days"] = observation_ages(fields, now)
                    count = len(fields)
                row.update({"status": "partial" if count and errors else "error" if errors else "sample_returned" if count else "empty_sample",
                            "count": count, "error_fields": list(errors), "as_of": getattr(value, "as_of", None)})
                # Keep public observations; avoid error strings that could contain credential-bearing URLs.
                payload = json.loads(json.dumps(data, default=serial))
                if isinstance(payload, dict) and "errors" in payload:
                    payload["errors"] = {key: "provider_error" for key in payload["errors"]}
                write(output / (name + ".json"), payload)
            except Exception as exc:
                row.update({"status": "error", "error_type": type(exc).__name__, "http_status": getattr(exc, "code", None)})
            results.append(row)
            write(output / "progress.json", results)

    a = Instrument("518880", "黄金ETF", "a", exchange="sh")
    us = Instrument("AAPL", "苹果", "us")
    cn = Instrument("000001", "平安银行", "a", exchange="sz")
    crypto = Instrument("BTCUSDT", "Bitcoin", "crypto")
    finnhub, polygon = FinnhubQuoteProvider(), PolygonQuoteProvider()
    tasks = []
    for name, provider, inst in [("quote_tencent", TencentAQuoteProvider(), a), ("quote_eastmoney", EastmoneyAQuoteProvider(), a),
                                 ("quote_finnhub", finnhub, us), ("quote_polygon", polygon, us), ("quote_binance", BinanceQuoteProvider(), crypto)]:
        configured = bool(provider.api_key) if hasattr(provider, "api_key") else True
        tasks.append(probe(name, lambda p=provider, i=inst: p.fetch(i), provider, configured=configured))
    fund = FundNavProvider()
    tasks.append(probe("fund_nav", lambda: fund.fetch("000001"), fund))
    for cls, inst in [(EastmoneyKLineProvider, a), (TencentKLineProvider, a), (YahooKLineProvider, us), (NasdaqKLineProvider, us), (BinanceKLineProvider, crypto)]:
        provider = cls()
        tasks.append(probe("history_" + cls.__name__, lambda p=provider, i=inst: p.fetch(i, 60), provider))
    config = json.loads((REPO / "stocks/config/news_sources.json").read_text())
    for index, item in enumerate(config["sources"]):
        if not item.get("enabled", True):
            results.append({"name": f"rss_{index}", "label": item["name"], "status": "disabled", "note": item.get("note")})
            continue
        provider = RSSNewsProvider(item["url"], source_name=item["name"])
        tasks.append(probe(f"rss_{index}", lambda p=provider: p.fetch(5), provider, note=item["name"]))
    watch = WatchlistGoogleNewsProvider(lambda: [us, cn])
    tasks.append(probe("watchlist_news", lambda: watch.fetch(6), watch))
    for cls in (FredMacroProvider, YahooFinanceMacroProvider, CnMacroProvider):
        provider = cls()
        tasks.append(probe(cls.__name__, provider.fetch, provider))
    # User explicitly chose to skip SEC rather than invent a contact identity.
    results.append({"name": "sec_edgar", "status": "skipped_user", "note": "Contact-bearing SEC User-Agent not configured; user requested skip."})
    filings = CninfoFilingsProvider(lambda: [cn], {"000001": "gssz0000001"})
    tasks.append(probe("cninfo_filings", lambda: filings.fetch(5), filings))
    for provider in [StaticEventCalendarProvider(json.loads((REPO / "stocks/config/event_calendar.json").read_text())),
                     FinnhubEarningsCalendarProvider(client=finnhub), AkShareEarningsProvider(cache_dir=output / "calendar-cache")]:
        tasks.append(probe("calendar_" + provider.name,
                           lambda p=provider: p.fetch(start=now.date(), end=(now + timedelta(days=45)).date(), watchlist=[us, cn]), provider))
    harvester = IntelligenceHarvester(finnhub_client=finnhub, keywords=["federal reserve"], max_items_per_source=3,
                                      fred_cache_dir=output / "fred-cache")
    tasks.append(probe("gnews", lambda: harvester._fetch_gnews_keyword("federal reserve", 3), configured=bool(harvester._gnews_api_key)))
    tasks.append(probe("intelligence_google_rss", harvester._fetch_google_rss_all))
    tasks.append(probe("intelligence_finnhub", harvester._fetch_finnhub_market_news, configured=bool(finnhub.api_key)))
    await asyncio.gather(*tasks)
    clients = resolve_mainline_llm_clients(load_engine_config())
    summary = {"checked_at": now.isoformat(), "scope": "representative_public_samples_not_all_symbols_or_uptime",
               "sources": sorted(results, key=lambda r: r["name"]),
               "llm_config": [{"model": c.model, "configured": bool(c.api_key), "endpoint_host": urlsplit(c.base_url).hostname} for c in clients],
               "real_accounts_used": False, "messages_sent": False}
    write(output / "health.json", summary)
    return summary


async def market_trial(output):
    output.mkdir()
    config_dir = output / "config"
    config_dir.mkdir()
    for name in ("news_sources.json", "event_calendar.json", "filing_symbols.json"):
        shutil.copyfile(REPO / "stocks/config" / name, config_dir / name)
    watch = [dict(code="000300", name="沪深300", market="a", exchange="sh_index", category="equity_cn"),
             dict(code="518880", name="黄金ETF", market="a", exchange="sh", category="gold"),
             dict(code="AAPL", name="苹果", market="us", category="tech"),
             dict(code="SPY", name="标普500ETF", market="us", category="equity_us")]
    for name, value in [("watchlist.json", watch), ("sector_scan.json", []), ("investor_profile.json", {}), ("portfolio_constraints.json", {})]:
        write(config_dir / name, value)
    config = load_engine_config()
    config["cache"]["history_dir"] = str(output / "history")
    config["llm"]["outlook"]["cache_dir"] = str(output / "outlook-cache")
    config["intelligence_dir"] = str(output / "intelligence")
    config["llm"]["outlook"].update({"retry_attempts": 0, "fallback_models": []})
    config["filings"]["sec"]["enabled"] = False
    engine = StocksEngine(config_dir=str(config_dir), local_data_dir=str(output / "local"),
                          data_dir=str(output / "data"), engine_config=config, llm_analysis_enabled=False)
    context = await engine.build_context()
    if context.assets or context.position_valuations:
        raise RuntimeError("market-only run must have no user account positions")
    write(output / "context.json", context.to_dict())
    outcomes = {}
    for market, session in (("cn", "cn_after_close"), ("us", "us_after_close")):
        result = await asyncio.to_thread(build_advisory_outlook, context, session_id=session, market=market,
                                        config=config, now=datetime.now(timezone.utc).isoformat())
        write(output / (market + "-outlook.json"), result)
        lineage = result.get("advisory_lineage") or {}
        receipt = (lineage.get("validation_receipt") or {}).get("contract_receipt") or {}
        proposal = lineage.get("advisory") or {}
        actions = [a for a in proposal.get("actions", []) if a.get("action") in {"buy", "add", "sell", "reduce"}]
        outcomes[market] = {"outlook_status": result.get("status"), "contract_status": receipt.get("status"),
                            "contract_errors": receipt.get("errors", []), "action_proposals_without_accounts": len(actions),
                            "parsed_live_response": any(t.get("status") == "parsed" for t in lineage.get("attempts", [])),
                            "scope": "format_and_source_links_only_manual_factual_review_required"}
    write(output / "acceptance.json", outcomes)
    return outcomes


async def intelligence_trial(output, source_dir):
    output.mkdir()
    articles = []
    for name in ("intelligence_google_rss", "intelligence_finnhub"):
        path = source_dir / (name + ".json")
        if path.exists():
            articles.extend(json.loads(path.read_text()))
    snapshot = IntelligenceSnapshot(
        collected_at=datetime.now(timezone.utc), sources={}, articles=articles,
        macro={}, quotes={}, data_quality={"scope": "public_news_samples"},
    )
    analyzer = LLMIntelligenceAnalyzer(
        holdings=[], model=load_engine_config()["llm"]["analysis_model"],
        timeout=90, max_input_articles=25, fallback_to_rules=False,
    )
    try:
        result = await asyncio.to_thread(analyzer.analyze, [snapshot])
        write(output / "analysis.json", result)
        status = {"analysis_mode": result.metadata.get("analysis_mode"),
                  "raw_article_count": len(articles), "articles_input": result.metadata.get("articles_input", 0),
                  "excluded_articles": result.metadata.get("excluded_articles", 0), "cluster_count": len(result.clusters),
                  "signal_count": len(result.signals),
                  "scope": "research_signals_not_approved_trades"}
    except Exception as exc:
        status = {"status": "error", "error_type": type(exc).__name__}
    write(output / "acceptance.json", status)
    return status


async def run(output, with_llm):
    health = await check_sources(output / "sources")
    result = {"health_path": str(output / "sources/health.json"), "source_count": len(health["sources"])}
    if with_llm:
        result["market_trials"] = await market_trial(output / "market")
        result["intelligence_trial"] = await intelligence_trial(output / "intelligence", output / "sources")
    write(output / "summary.json", result)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--with-llm", action="store_true", help="Uses configured paid model API for two market-only trials and one news analysis")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists() or output == REPO or REPO in output.parents:
        parser.error("use a new output directory outside the repository")
    output.mkdir(parents=True)
    os.chdir(REPO)
    logging.disable(logging.CRITICAL)
    result = asyncio.run(run(output, args.with_llm))
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
