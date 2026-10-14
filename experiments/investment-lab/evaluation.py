"""Frozen-rule walk-forward research; never an execution or accuracy guarantee."""

import argparse
import hashlib
import json
import math
from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from pathlib import Path

from lab import DailyBar, analyze


def validate(bars):
    dates = [b.date for b in bars]
    if not bars or dates != sorted(set(dates)):
        raise ValueError("Empty, duplicate or unordered history")
    for b in bars:
        if (
            not all(math.isfinite(v) for v in (b.open, b.high, b.low, b.close, b.volume))
            or b.volume < 0
            or min(b.low, b.open, b.close, b.high) <= 0
            or b.low > min(b.open, b.close) + 1e-10 * max(b.open, b.close)
            or b.high < max(b.open, b.close) - 1e-10 * max(b.open, b.close)
            or b.low > b.high
        ):
            raise ValueError("Invalid OHLCV")


def prediction(ticker, bars, spy):
    now = datetime.fromisoformat(bars[-1].date).replace(tzinfo=UTC) + timedelta(days=1, hours=12)
    a = analyze(ticker, bars, None, spy, 100, 0.0409, 1, now)
    if a.get("score_0_to_5") != 5:
        return None
    return {
        "ticker": ticker,
        "signal_date": bars[-1].date,
        "trigger": a["technical_entry_above_usd"],
        "invalidation": a["reference_stop_usd"],
        "hypothesis": "positive five-session net return after qualifying next open",
    }


def metrics(returns, curve):
    n = len(returns)
    wins = [r for r in returns if r > 0]
    losses = [r for r in returns if r < 0]
    peak = curve[0]
    dd = 0
    for v in curve:
        peak = max(peak, v)
        dd = min(dd, v / peak - 1)
    interval = None
    if n:
        p = len(wins) / n
        z = 1.96
        denom = 1 + z * z / n
        center = (p + z * z / (2 * n)) / denom
        half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denom
        interval = [center - half, center + half]
    return {
        "sample_count": n,
        "win_rate": len(wins) / n if n else None,
        "win_rate_wilson_95": interval,
        "profit_factor": sum(wins) / abs(sum(losses)) if losses else None,
        "average_win": sum(wins) / len(wins) if wins else None,
        "average_loss": sum(losses) / len(losses) if losses else None,
        "expectancy": sum(returns) / n if n else None,
        "maximum_daily_drawdown": dd,
        "net_return": curve[-1] / curve[0] - 1,
    }


def evaluate(ticker, bars, spy, cost_bps=15, fold_sessions=126):
    """Expanding-prefix frozen rules. Next-open entry, fifth-session close exit.

    Non-overlapping trades within each held-out fold; no parameter fitting.
    No intrabar stop/target fills inferred. Single-symbol normalized capital.
    """
    validate(bars)
    validate(spy)
    if not math.isfinite(cost_bps) or not 0 <= cost_bps < 1000 or fold_sessions < 5:
        raise ValueError("Invalid assumptions")
    common = sorted({b.date for b in bars} & {b.date for b in spy})
    if not common:
        raise ValueError("No aligned history")
    for rows in (bars, spy):
        if [b.date for b in rows if common[0] <= b.date <= common[-1]] != common:
            raise ValueError("Missing sessions; cannot compress forecast horizon")
    stock = {b.date: b for b in bars}
    market = {b.date: b for b in spy}
    bars = [stock[d] for d in common]
    spy = [market[d] for d in common]
    if len(bars) < 65:
        raise ValueError("Insufficient history")
    cost = cost_bps / 10000
    balance = 1.0
    curve = [balance]
    signals = []
    trades = []
    folds = []
    for start in range(55, len(bars), fold_sessions):
        end = min(start + fold_sessions, len(bars))
        returns = []
        fold_curve = [balance]
        i = start
        while i < end:
            # Construct the prediction before reading any future candle.
            p = prediction(ticker, bars[:i], spy[:i])
            if p:
                signals.append(p)
            if p and i + 4 < end and p["trigger"] <= bars[i].open <= p["trigger"] * 1.015:
                entry = bars[i].open * (1 + cost)
                initial = balance
                for j in range(i, i + 5):
                    value = initial * bars[j].close / entry * (1 - cost if j == i + 4 else 1)
                    curve.append(value)
                    fold_curve.append(value)
                balance = curve[-1]
                net = balance / initial - 1
                returns.append(net)
                trades.append(
                    {
                        "signal_date": p["signal_date"],
                        "entry_date": bars[i].date,
                        "exit_date": bars[i + 4].date,
                        "net_return": net,
                        "spy_same_period_return": spy[i + 4].close
                        * (1 - cost)
                        / (spy[i].open * (1 + cost))
                        - 1,
                    }
                )
                i += 5
            else:
                curve.append(balance)
                fold_curve.append(balance)
                i += 1
        folds.append(
            {
                "start": bars[start].date,
                "end": bars[end - 1].date,
                "metrics": metrics(returns, fold_curve),
            }
        )
    return {
        "ticker": ticker,
        "cost_bps_each_side": cost_bps,
        "start": bars[55].date,
        "end": bars[-1].date,
        "metrics": metrics([t["net_return"] for t in trades], curve),
        "spy_buy_hold_return": spy[-1].close * (1 - cost) / (spy[55].open * (1 + cost)) - 1,
        "cash_return": 0,
        "folds": folds,
        "signals": signals,
        "trades": trades,
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--start", default="2016-01-01")
    parser.add_argument("--end", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--cache", type=Path, required=True)
    args = parser.parse_args()
    if args.end > datetime.now(UTC).date().isoformat():
        raise SystemExit("Exclude unfinished/future candles")
    import yfinance as yf

    symbols = json.loads((Path(__file__).parent / "data/config.json").read_text())["watchlist"]
    args.cache.mkdir(parents=True, exist_ok=True)
    histories = {}
    hashes = {}
    failures = {}
    for ticker in symbols:
        path = args.cache / f"{ticker}-{args.start}-{args.end}.json"
        try:
            if path.exists():
                payload = json.loads(path.read_text())
                if payload["adjusted"] is not True or payload["source"] != "yfinance":
                    raise ValueError("Invalid provenance")
            else:
                df = yf.Ticker(ticker).history(
                    start=args.start,
                    end=args.end,
                    auto_adjust=True,
                    actions=True,
                    raise_errors=True,
                )
                rows = [
                    DailyBar(
                        str(idx.date()),
                        *[float(r[k]) for k in ("Open", "High", "Low", "Close", "Volume")],
                    )
                    for idx, r in df.iterrows()
                ]
                payload = {
                    "source": "yfinance",
                    "adjusted": True,
                    "retrieved_at_utc": datetime.now(UTC).isoformat(),
                    "bars": [asdict(b) for b in rows],
                }
                path.write_text(json.dumps(payload, sort_keys=True))
            histories[ticker] = [DailyBar(**b) for b in payload["bars"]]
            hashes[ticker] = hashlib.sha256(path.read_bytes()).hexdigest()
        except Exception as exc:  # noqa: BLE001 -- record unavailable provider data, never fabricate
            failures[ticker] = type(exc).__name__
    results = []
    for ticker, bars in histories.items():
        try:
            results.append(
                {
                    "ticker": ticker,
                    "sensitivity": [
                        evaluate(ticker, bars, histories["SPY"], c) for c in (5, 15, 30)
                    ],
                }
            )
        except (ValueError, KeyError) as exc:
            failures[ticker] = type(exc).__name__
    output = {
        "generated_at_utc": datetime.now(UTC).isoformat(),
        "method": "frozen-five-factor-next-open-five-session-v1",
        "assumptions": {
            "adjusted_prices": True,
            "constant_sar_per_usd": 3.75,
            "cash_yield": 0,
            "cost_bps_each_side": [5, 15, 30],
            "costs": "combined proportional fees, half-spread and slippage; fixed broker fees not modeled",
        },
        "limitations": [
            "Current watchlist creates survivorship and selection bias.",
            "Rules not preregistered historically; walk-forward cannot remove researcher selection bias.",
            "Single-symbol normalized portfolios are not combined account returns.",
            "News, quote rights, cash and broker-preview gates not reconstructed.",
            "Wilson intervals assume independent trials; serial dependence limits interpretation.",
            "Adjusted Yahoo data can be revised; retain hashed snapshots.",
            "Historical simulation is not prospective verified predictive accuracy.",
        ],
        "input_sha256": hashes,
        "failures": failures,
        "results": results,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2, allow_nan=False))
    print(
        json.dumps(
            {"evaluated_symbols": len(results), "failures": failures, "output": str(args.output)}
        )
    )


if __name__ == "__main__":
    main()
