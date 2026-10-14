"""Fail-closed production entry gate over the retained five-factor scanner."""

import math
from datetime import timedelta
from urllib.parse import urlparse

from lab import analyze
from market import timestamp
from risk import size_position


def report_state(report, now):
    if not report or report.get("origin") != "private_production":
        return "UNAVAILABLE"
    if report.get("run_status") != "completed":
        return "UNAVAILABLE"
    try:
        age = now - timestamp(report["completed_at_utc"])
        return "FRESH" if timedelta(0) <= age <= timedelta(minutes=90) else "STALE"
    except (KeyError, TypeError, ValueError, AttributeError):
        return "UNAVAILABLE"


def research_current(research, ticker, now):
    try:
        if (
            ticker not in research["tickers"]
            or research["status"] != "verified"
            or not research["claims"]
        ):
            return False
        retrieved = timestamp(research["retrieved_at_utc"])
        if not timedelta(0) <= now - retrieved <= timedelta(hours=24):
            return False
        sources = research["sources"]
        if not sources or not any(s["primary"] for s in sources):
            return False
        for source in sources:
            if urlparse(source["url"]).scheme != "https":
                return False
            published = timestamp(source["published_at_utc"])
            if not timedelta(0) <= now - published <= timedelta(hours=72) or published > retrieved:
                return False
        return research["independent_review"] is True and research["negative_news_checked"] is True
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def evaluate(ticker, bars, benchmark, quote, research, capital, now, calendar, production=False):
    # Legacy calculations are reusable historical research, never production authorization.
    technical = analyze(ticker, bars, None, benchmark, 1, 0.01, 0.1, now)
    keep = (
        "score_0_to_5",
        "checks",
        "technical_entry_above_usd",
        "reference_stop_usd",
        "history_through",
        "ma20",
        "momentum5_pct",
        "average_dollar_volume",
    )
    result = {k: technical[k] for k in keep if k in technical}
    result.update(
        ticker=ticker,
        status="WAIT",
        quote=quote,
        allocation=None,
        broker_preview_required=True,
        executable=False,
    )
    reasons = []
    if not production:
        reasons.append("Research/PR run is not a production signal")
    expected = calendar.last_completed(now)
    for name, candles in [("stock", bars), ("SPY", benchmark)]:
        completed = [b for b in candles if b.date <= expected]
        if (
            len(completed) < 55
            or not candles
            or completed[-1].date != expected
            or any(b.date > now.date().isoformat() for b in candles)
            or len({b.date for b in candles}) != len(candles)
            or [b.date for b in candles] != sorted(b.date for b in candles)
            or any(
                not all(math.isfinite(v) and v > 0 for v in (b.open, b.high, b.low, b.close))
                or not math.isfinite(b.volume)
                or b.volume < 0
                or b.low > min(b.open, b.close)
                or b.high < max(b.open, b.close)
                for b in candles
            )
        ):
            reasons.append(f"{name} history stale or unavailable")
    if result.get("score_0_to_5") != 5:
        reasons.append("All five technical conditions required")
    if not quote or not quote.get("eligible"):
        reasons.append("Fresh licensed independent quote unavailable")
    else:
        try:
            if (
                not timedelta(0)
                <= now - timestamp(quote["observed_at_utc"])
                <= timedelta(minutes=20)
            ):
                reasons.append("Quote stale or future")
        except (TypeError, ValueError, KeyError):
            reasons.append("Invalid quote timestamp")
        entry, stop = result.get("technical_entry_above_usd"), result.get("reference_stop_usd")
        if entry and stop:
            if quote["price"] < entry:
                reasons.append("Breakout not confirmed")
            if quote["price"] > entry * 1.015 or quote["price"] > result["ma20"] * 1.06:
                reasons.append("Entry excessively extended")
            sizing = size_position(entry=quote["price"], invalidation=stop, **capital)
            result["allocation"] = sizing
            if not sizing["eligible"]:
                reasons.append(sizing["reason"])
            # A theoretical 2R level is not evidence of attainable upside.
            target = research.get("technical_upside_reference") if research else None
            if (
                not isinstance(target, (float, int))
                or not research.get("upside_reference_source")
                or not math.isfinite(target)
                or quote["price"] <= stop
                or (target - quote["price"]) / (quote["price"] - stop) < 2
            ):
                reasons.append("Independent theoretical reward/risk evidence insufficient")
            elif sizing["eligible"]:
                shares = float(sizing["shares"])
                net_upside = shares * (target - quote["price"]) * float(capital["fx_sar_per_usd"])
                net_upside -= (
                    float(capital["fee_sar"])
                    + shares
                    * quote["price"]
                    * float(capital["fx_sar_per_usd"])
                    * (float(capital["spread_bps"]) + float(capital["slippage_bps"]))
                    / 10000
                )
                if net_upside < 2 * float(sizing["planned_loss_sar"]):
                    reasons.append("Reward/risk insufficient after estimated costs")
    if not research_current(research, ticker, now):
        reasons.append("Recent independent news review unavailable")
    if not reasons:
        result["status"] = "CONDITIONAL ENTRY REVIEW"
    result["reasons"] = reasons or ["Inspect setup and Awaed preview; never an order authorization"]
    return result
