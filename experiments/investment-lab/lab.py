"""Conservative investment research scanner. Never places or recommends automatic orders."""

from __future__ import annotations

import argparse
import csv
import json
import math
from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

SAR_PER_USD = 3.75
ET = ZoneInfo("America/New_York")


@dataclass(frozen=True)
class DailyBar:
    date: str
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass(frozen=True)
class Quote:
    price: float
    observed_at_utc: str
    source: str


def quote_is_fresh(quote: Quote | None, now: datetime) -> bool:
    if not quote:
        return False
    ny = now.astimezone(ET)
    if ny.weekday() >= 5 or not ((9, 30) <= (ny.hour, ny.minute) < (16, 0)):
        return False
    try:
        observed = datetime.fromisoformat(quote.observed_at_utc.replace("Z", "+00:00"))
        if observed.tzinfo is None:
            return False
        lag = now - observed.astimezone(UTC)
        return timedelta(0) <= lag <= timedelta(minutes=20)
    except ValueError:
        return False


def load_holdings(path: Path) -> list[dict]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as file:
        rows = list(csv.DictReader(file))
    holdings = []
    for row in rows:
        if not row.get("ticker") or not row.get("quantity"):
            continue
        item = {
            "ticker": row["ticker"].upper().strip(),
            "quantity": float(row["quantity"]),
            "cost_usd": float(row["cost_usd"]),
            "stop_usd": float(row["stop_usd"]),
        }
        if (
            item["quantity"] <= 0
            or item["cost_usd"] <= 0
            or not 0 < item["stop_usd"] < item["cost_usd"]
        ):
            raise ValueError("Invalid holding: quantity, cost and reference stop required")
        holdings.append(item)
    return holdings


def fetch_yahoo(ticker: str) -> tuple[list[DailyBar], Quote | None]:
    """Unofficial, research-grade and potentially delayed price feed."""
    import yfinance as yf

    obj = yf.Ticker(ticker)
    hist = obj.history(period="6mo", interval="1d", auto_adjust=True, prepost=False)
    bars = []
    for index, row in hist.iterrows():
        vals = [float(row[k]) for k in ("Open", "High", "Low", "Close", "Volume")]
        if not all(math.isfinite(v) for v in vals):
            continue
        bars.append(DailyBar(str(index.date()), *vals))
    intraday = obj.history(period="2d", interval="5m", prepost=False, auto_adjust=True)
    quote = None
    if not intraday.empty:
        stamp = intraday.index[-1]
        last = float(intraday["Close"].iloc[-1])
        if stamp.tzinfo is not None and math.isfinite(last) and last > 0:
            quote = Quote(
                last, stamp.to_pydatetime().astimezone(UTC).isoformat(), "Yahoo 5-minute bar"
            )
    return bars, quote


def analyze(
    ticker: str,
    bars: list[DailyBar],
    quote: Quote | None,
    benchmark: list[DailyBar],
    budget_sar: float,
    risk_pct: float,
    max_position_pct: float,
    now: datetime,
) -> dict:
    out = {"ticker": ticker, "status": "NO_TRADE", "quote": asdict(quote) if quote else None}
    # Exclude the unfinished daily candle. Intraday quote is evaluated separately.
    if bars and bars[-1].date == now.astimezone(ET).date().isoformat():
        bars = bars[:-1]
    if benchmark and benchmark[-1].date == now.astimezone(ET).date().isoformat():
        benchmark = benchmark[:-1]
    if len(bars) < 55 or len(benchmark) < 55 or any(x.close <= 0 for x in bars):
        return {**out, "reason": "Insufficient valid historical candles"}
    closes = [x.close for x in bars]
    bc = [x.close for x in benchmark]
    ma10 = sum(closes[-10:]) / 10
    ma20 = sum(closes[-20:]) / 20
    ma50 = sum(closes[-50:]) / 50
    dollar_liquidity = sum(x.close * x.volume for x in bars[-20:]) / 20
    trend = closes[-1] > ma20 and ma10 > ma20 > ma50
    liquid = dollar_liquidity >= 5_000_000
    not_extended = 0 <= (closes[-1] / ma20 - 1) <= 0.06
    momentum5 = closes[-1] / closes[-6] - 1
    momentum_ok = 0 < momentum5 < 0.09
    benchmark_ok = bc[-1] > sum(bc[-50:]) / 50
    score = sum((trend, liquid, not_extended, momentum_ok, benchmark_ok))
    entry = round(max(x.high for x in bars[-5:]) * 1.001, 2)
    stop = round(min(ma20, entry * 0.97), 2)
    per_share_risk = max(0.01, entry - stop)
    max_risk_usd = budget_sar * risk_pct / SAR_PER_USD
    max_spend_usd = budget_sar * max_position_pct / SAR_PER_USD
    shares = max(0, math.floor(min(max_risk_usd / per_share_risk, max_spend_usd / entry)))
    # Awaed supports user-entered SAR amounts (fractional shares). Round DOWN
    # to the nearest 0.001 share so this calculation cannot exceed the risk cap.
    fractional_shares = math.floor(
        min(max_risk_usd / per_share_risk, max_spend_usd / entry) * 1000
    ) / 1000
    # Entry-trigger size is only hypothetical; today's paid price can be higher.
    # Recompute risk at the observed price. The Awaed order preview may differ,
    # so the final quantity must be independently recalculated before a trade.
    if quote and quote.price > stop:
        quote_risk = quote.price - stop
        quote_fraction = math.floor(
            min(max_risk_usd / quote_risk, max_spend_usd / quote.price) * 1000
        ) / 1000
    else:
        quote_fraction = 0.0
    out.update(
        {
            "score_0_to_5": score,
            "ma10": round(ma10, 3),
            "ma20": round(ma20, 3),
            "ma50": round(ma50, 3),
            "momentum5_pct": round(momentum5 * 100, 2),
            "average_dollar_volume": round(dollar_liquidity, 2),
            "technical_entry_above_usd": entry,
            "reference_stop_usd": stop,
            "reference_2R_target_usd": round(entry + 2 * per_share_risk, 2),
            "whole_shares_within_risk": shares,
            "fractional_shares_within_risk": fractional_shares,
            "estimated_fractional_spend_sar": round(fractional_shares * entry * SAR_PER_USD, 2),
            "estimated_fractional_stop_risk_sar": round(fractional_shares * per_share_risk * SAR_PER_USD, 2),
            "quote_risk_capped_fractional_shares": quote_fraction,
            "quote_max_spend_sar": round(quote_fraction * quote.price * SAR_PER_USD, 2) if quote else 0,
            "quote_estimated_stop_risk_sar": round(quote_fraction * max(quote.price - stop, 0) * SAR_PER_USD, 2) if quote else 0,
            "maximum_theoretical_stop_loss_sar": round(max_risk_usd * SAR_PER_USD, 2),
            "quote_fresh": quote_is_fresh(quote, now),
            "history_through": bars[-1].date,
            "benchmark_risk_on": benchmark_ok,
        }
    )
    if not quote_is_fresh(quote, now):
        out.update(
            status="NO_TRADE",
            reason="Regular US session not open or current quote stale/unavailable",
        )
    elif not benchmark_ok:
        out.update(status="NO_TRADE", reason="Benchmark risk-on condition failed")
    elif score != 5 or quote_fraction < 0.001:
        out.update(status="WATCH", reason="All five checks and current-price risk-capped fractional affordability required")
    elif quote.price < entry:
        out.update(status="WATCH", reason="Technicals promising but breakout not confirmed")
    elif quote.price > entry * 1.015:
        out.update(status="WATCH", reason="Delayed quote is over 1.5% above entry; avoid chasing")
    else:
        out.update(
            status="ENTRY_REVIEW",
            reason="Manual broker quote/bid-ask and trade-risk review required",
        )
    return out


def run(config_path: Path, holdings_path: Path, report_folder: Path) -> dict:
    config = json.loads(config_path.read_text(encoding="utf-8"))
    budget = float(config["capital_sar"])
    risk = float(config["risk_pct_per_trade"])
    allocation = float(config["max_position_pct"])
    if budget <= 0 or not 0 < risk <= 0.03 or not 0 < allocation <= 0.5:
        raise ValueError("Invalid capital or risk cap")
    holdings = load_holdings(holdings_path)
    watchlist = list(dict.fromkeys(x.strip().upper() for x in config["watchlist"]))
    tickers = list(dict.fromkeys(watchlist + [x["ticker"] for x in holdings] + ["SPY"]))
    quotes = {}
    errors = []
    for ticker in tickers:
        try:
            quotes[ticker] = fetch_yahoo(ticker)
        except Exception as e:
            errors.append(f"{ticker}: market provider failed ({type(e).__name__})")
            quotes[ticker] = ([], None)
    now = datetime.now(UTC)
    benchmark = quotes["SPY"][0]
    stocks = [
        analyze(t, quotes[t][0], quotes[t][1], benchmark, budget, risk, allocation, now)
        for t in watchlist
    ]
    positions = []
    for h in holdings:
        q = quotes[h["ticker"]][1]
        fresh = quote_is_fresh(q, now)
        status = (
            "STOP_REVIEW"
            if fresh and q.price <= h["stop_usd"]
            else ("MONITOR" if fresh else "HOLD_UNVERIFIED")
        )
        positions.append(
            {
                **h,
                "quote": asdict(q) if q else None,
                "status": status,
                "pnl_sar": round((q.price - h["cost_usd"]) * h["quantity"] * SAR_PER_USD, 2)
                if fresh
                else None,
            }
        )
    payload = {
        "as_of_utc": now.isoformat(),
        "capital_sar": budget,
        "aspirational_goal_sar": config["aspirational_goal_sar"],
        "stocks": stocks,
        "positions": positions,
        "provider_errors": errors,
        "warning": "Research-only. This service never places or authorizes trades.",
    }
    report_folder.mkdir(parents=True, exist_ok=True)
    (report_folder / "report.json").write_text(json.dumps(payload, indent=2), encoding="utf-8")
    lines = [
        "# Investment Lab — research only",
        "",
        f"Generated {payload['as_of_utc']}",
        f"Available research capital: {budget} SAR; aspirational gain: {payload['aspirational_goal_sar']} SAR",
        "No stock order has been placed. Broker quote and risk verification required.",
        "",
        "|Ticker|Status|Score / 5|Quote USD|Trigger USD|Whole shares|Fractional shares at quote|Max spend SAR at quote|Explanation|",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for s in stocks:
        q = s["quote"] or {}
        lines.append(
            f"|{s['ticker']}|{s['status']}|{s.get('score_0_to_5', '—')}|{q.get('price', '—')}|"
            f"{s.get('technical_entry_above_usd', '—')}|{s.get('whole_shares_within_risk', '—')}|"
            f"{s.get('quote_risk_capped_fractional_shares', '—')}|{s.get('quote_max_spend_sar', '—')}|{s.get('reason', '')}|"
        )
    lines += ["", "## Confirmed positions (private CSV only)", ""]
    for h in positions:
        lines.append(f"{h['ticker']} — {h['status']} — P/L SAR: {h['pnl_sar']}")
    if errors:
        lines.extend(["", "Provider failures:", *errors])
    (report_folder / "report.md").write_text("\n".join(lines) + "\n", encoding="utf-8")
    alerts = [s for s in stocks if s["status"] == "ENTRY_REVIEW"] + [
        h for h in positions if h["status"] == "STOP_REVIEW"
    ]
    (report_folder / "alerts.json").write_text(json.dumps(alerts, indent=2), encoding="utf-8")
    return payload


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    here = Path(__file__).resolve().parent
    parser.add_argument("--config", type=Path, default=here / "data" / "config.json")
    parser.add_argument("--holdings", type=Path, default=here / "data" / "holdings.csv")
    parser.add_argument("--reports", type=Path, default=here / "reports")
    args = parser.parse_args()
    summary = run(args.config, args.holdings, args.reports)
    print((args.reports / "report.md").read_text(encoding="utf-8"))
