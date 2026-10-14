"""Private production scanner. Entry gates fail closed; records never go to stdout."""

from __future__ import annotations

import argparse
import json
import os
import uuid
from datetime import UTC, datetime
from pathlib import Path

from engine import evaluate
from lab import fetch_yahoo
from market import Entitlement, ExchangeCalendar, Finnhub, select_quote


def scan(
    config,
    production=False,
    capital=None,
    research=None,
    providers=None,
    entitlements=None,
    history=None,
    now=None,
    calendar=None,
):
    now = now or datetime.now(UTC)
    calendar = calendar or ExchangeCalendar()
    history = history or fetch_yahoo
    providers, entitlements, research = providers or [], entitlements or {}, research or {}
    capital = capital or {"fx_sar_per_usd": None}
    watchlist = list(dict.fromkeys(config["watchlist"] + ["SPY"]))
    candles, errors = {}, []
    for ticker in watchlist:
        try:
            candles[ticker] = history(ticker)[0]
        except Exception:  # noqa: BLE001 -- isolate third-party provider failures
            candles[ticker] = []
            errors.append({"ticker": ticker, "provider": "yfinance", "code": "historical_failure"})
    stocks, observations = [], []
    for ticker in watchlist:
        # Exchange metadata is explicit operational configuration, never inferred from price.
        exchange = config.get("exchanges", {}).get(ticker)
        quote, seen, failed = select_quote(providers, ticker, exchange, entitlements, now, calendar)
        observations.extend(seen)
        errors.extend({"ticker": ticker, **failure} for failure in failed)
        stocks.append(
            evaluate(
                ticker,
                candles[ticker],
                candles["SPY"],
                quote,
                research.get(ticker),
                capital,
                now,
                calendar,
                production,
            )
        )
    stocks.sort(
        key=lambda s: (
            -(s.get("score_0_to_5") or 0),
            s["ticker"] not in ("HL", "GS", "UNH"),
            s["ticker"],
        )
    )
    failed = not candles.get("SPY") or all(not c for c in candles.values())
    return {
        "schema_version": 2,
        "signal_id": str(uuid.uuid4()),
        "origin": "private_production" if production else "public_research",
        "started_at_utc": now.isoformat(),
        "completed_at_utc": datetime.now(UTC).isoformat(),
        "run_status": "failed" if failed else "completed",
        "status": "SCANNER_UNAVAILABLE" if failed else "WAIT",
        "market_session": calendar.session(now),
        "next_market_open_utc": calendar.next_open(now),
        "stocks": stocks,
        "positions": [],
        "observations": observations,
        "provider_errors": errors,
        "news_status": "unavailable" if not research else "review_required",
        "warning": "Research only. Broker preview required. No automatic orders.",
    }


def runtime_inputs():
    path = os.getenv("INVESTMENT_ENTITLEMENTS_FILE")
    data = json.loads(Path(path).read_text()) if path else {}
    entitlements = {
        name: Entitlement(provider=name, **entry)
        for name, entry in data.items()
        if name == "finnhub"
    }
    entitlement = entitlements.get("finnhub", Entitlement("finnhub"))
    providers = [Finnhub(os.getenv("FINNHUB_API_KEY", ""), entitlement)]
    return providers, entitlements


def smoke():
    now, calendar = datetime.now(UTC), ExchangeCalendar()
    providers, entitlements = runtime_inputs()
    provider = providers[0]
    from market import ProviderError

    try:
        quote = provider.quote("SPY", "ARCX", now)
        return quote.record(now, calendar, entitlements.get("finnhub", Entitlement("finnhub")))
    except ProviderError as error:
        return {"eligible": False, "provider": "finnhub", "failure": str(error)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--entitlement-smoke", action="store_true")
    args = parser.parse_args()
    if args.entitlement_smoke:
        print(json.dumps(smoke(), indent=2))
    else:
        raise SystemExit(
            "Use authenticated private service /api/scan to persist production reports."
        )
