# Investment Lab — USD stock scanner for SAR 409

This is a **research-only, isolated** module. It does not execute or authorize trades and does not alter the existing LangGraph/FastAPI application. Outputs are watchlist observations, not guaranteed returns.

## Objective
- Research capital: 409 SAR = about USD 109.07 at the reference exchange rate of 3.75 SAR/USD.
- Aspirational same-day gain: 50 SAR (~12.22% of the full balance). Not a realistic daily expectation.
- Theoretical risk per position: at most 1% of capital (4.09 SAR) before gaps/slippage; at most 50% of capital allocated to a candidate. Execution risk may exceed these amounts.
- Default watchlist: SPY, HL, XLF, AAPL, SOFI. All entries start with zero verified positions.

## Run

Requires Python 3.11+, with only yfinance installed for external data:

    pip install 'yfinance>=0.2.58,<2'
    python experiments/investment-lab/lab.py
    python -m unittest discover -s experiments/investment-lab/tests -v

Reports are written to experiments/investment-lab/reports/report.json, report.md and alerts.json.

## Core checks

The scanner reads 6 months of Yahoo historical candles and recent 5-minute bars, both **unofficial/delayed**. It excludes today's possibly incomplete daily candle. It rejects missing/older-than-20-minute quotes and nonregular US market hours. It checks benchmark SPY above its 50-session average, positive 10/20/50-session moving-average alignment, liquidity, five-day momentum and whether a stock looks overextended.

The only special state is **ENTRY_REVIEW**: a technical setup whose trigger may have been reached. It requires independent confirmation of broker bid/ask, spread, order size, and actual risk. It never means automatically BUY. It can emit STOP_REVIEW only for user-confirmed local positions. No short selling, leverage, options or market orders are supported.

## Private positions

**The GitHub repository is public. Never commit actual holdings here.** Copy data/holdings.example.csv to data/holdings.csv locally. That private file is .gitignored. Do not run GitHub public Actions with actual holdings or upload a private holdings report to a public artifact.

Format: ticker,quantity,cost_usd,stop_usd

The stop field is a reference threshold; it does NOT place a protective order. The user's old HL and VOO positions are unverified and are not preloaded.

## GitHub notifications

After a PR is merged, the workflow runs hourly on US weekdays at 14–21 UTC (best-effort GitHub scheduling). It also supports manual runs. Tests run before research. Research reports become GitHub Actions artifacts. On the default branch, only a NEW action-review state creates a dated GitHub issue. Optional Telegram delivery requires secrets INVESTMENT_TELEGRAM_BOT_TOKEN and INVESTMENT_TELEGRAM_CHAT_ID; no alerts are pushed without both configured. GitHub issues are not guaranteed push notifications.

## Source projects, licensing and roadmap

- ZhuLinsen/daily_stock_analysis (MIT): research-analysis inspiration; an import adapter for its structured output requires a subsequent schema-verification step. No external code is bundled.
- QuantConnect/Lean (Apache 2.0): proposed paper-trading/backtest validation phase; not currently integrated.
- Open-Dev-Society/OpenStock (AGPL-3.0): interface inspiration only; no AGPL source copied.
- StockSharp/StockSharp: restricted/proprietary source; no code copied.

This first phase is intentionally small and cost-conscious. Its Yahoo source can become unavailable or stale; this is handled by NO_TRADE instead of inventing prices. A full strategy needs timestamped historical prediction storage, out-of-sample testing, benchmark and slippage evaluation, and a licensed broker/data provider before considering live execution.

**Verification status:** static scanner and offline tests supplied. Actual external price access, GitHub production schedule and Telegram delivery remain unverified until runs succeed and secrets are configured.
