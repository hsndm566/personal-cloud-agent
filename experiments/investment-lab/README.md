# Investment Lab V2

Isolated, long-only research software. It neither places nor authorizes brokerage orders.
The broader FastAPI/LangGraph application is unchanged. No brokerage password is needed.

## Public and private boundaries

This public directory contains reusable algorithms, provider interfaces, a private-service
implementation, and synthetic tests. Production scheduling, deployment configuration,
migrations, reports, accounting records and secrets belong in private infrastructure.
Public CI runs tests only: it never uploads reports, creates alert issues, accesses
production credentials, or generates production signals. Do not pass financial records
to public workflows, logs, screenshots, or artifacts.

Historical configuration is **not an account balance**. Confirmed cash, estimated cash,
buying power and FX are unavailable unless independently supplied and reconciled.
No historical personal holdings are seeded. Unknown historical basis stays unknown.

## Local development

Requires Python 3.12 or newer:

```sh
python -m venv .venv
. .venv/bin/activate
pip install -r experiments/investment-lab/requirements.txt
python -m unittest discover -s experiments/investment-lab/tests -v
python experiments/investment-lab/lab.py
```

The last command performs public **historical research only**. It requires Yahoo access
and writes ignored local reports. Yahoo is never an entry-validation quote source.
The retained `analyze` function exposes historical five-factor and sizing calculations;
it can only return historical watch/no-trade states, never a production entry review.

To run the private service after private migrations and credentials are provisioned:

```sh
cd experiments/investment-lab
uvicorn lab_service:app --host 0.0.0.0 --port 8000 --no-access-log
```

Required private runtime settings: `DATABASE_URL` (least-privilege application role),
`INVESTMENT_OWNER`, `INVESTMENT_ACCESS_KEY` (32+ random characters),
`INVESTMENT_SESSION_KEY`, and `INVESTMENT_ORIGIN` (HTTPS origin).
The service refuses authenticated access if authentication is not provisioned.
Never use the database administrator role for application requests.

## Research gates

Five factors are retained: trend alignment, dollar liquidity, extension control,
five-session momentum and SPY above its 50-session average. The trigger is 0.1% above
the preceding five completed sessions' high; entries over 1.5% above it are rejected.
Exchange calendars handle New York DST, holidays and early closes. User times use Riyadh.

`CONDITIONAL ENTRY REVIEW` additionally requires current valid stock/SPY history, an
eligible quote observed within 20 minutes, independently reviewed recent primary-source
news, evidenced upside with at least 2:1 theoretical reward/risk after cost assumptions,
and conservative fractional sizing. It always requires an Awaed preview and never
represents executable pricing. Broker-supported precision must be explicitly supplied.
Planned risk is capped at 4.09 SAR and allocation at 100 SAR. Fees, FX, spread and slippage
assumptions cannot be silently invented. Gaps and slippage can exceed planned loss.

Missing production reports are `UNAVAILABLE`, reports older than 90 minutes are `STALE`,
and failed scans are `SCANNER_UNAVAILABLE`. Research/PR runs never qualify. Read-time
freshness checks remove expired entry-review status. `WAIT` means no supported new entry.

## Market-data entitlements

Finnhub is conditional and disabled for production entry use until its exchange coverage,
internal-use rights, Saudi-resident availability and real-time entitlement are documented.
A private entitlement file or `INVESTMENT_ENTITLEMENTS_JSON` supplies evidence URL,
verification/expiration dates, exchanges (MIC identifiers), and documented delay. Set
`INVESTMENT_ENTITLEMENTS_FILE` when using a file, and
`FINNHUB_API_KEY` through secure runtime configuration. `INVESTMENT_EXCHANGES` is a
private JSON ticker-to-MIC mapping verified against the provider's instrument metadata.

```sh
python experiments/investment-lab/production.py --entitlement-smoke
```

The smoke test uses the provider observation timestamp, never receipt time, and does not
establish licensing rights by itself. Quote records contain source, UTC/Riyadh/market time,
age, session, delay, classification and eligibility. Missing values remain null.
Finnhub quotes are indicative; consolidated/executable status is not assumed.
Twelve Data is explicitly blocked. A second licensed provider is not yet implemented;
the interface and discrepancy rejection tests are provided. Yahoo is history-only.

## Private operations and mobile records

The service offers secure-cookie authentication, same-origin CSRF checks, rate limits,
private reports and an actual-fill form. Every fill passes DRAFT → PENDING_CONFIRMATION
→ CONFIRMED. Confirmations are append-only ledger events; atomic PostgreSQL transactions,
account locks and unique fingerprints prevent repeat purchases. Corrections reverse a
confirmed record with an audit reason, then submit the corrected fill. Reversals that
would create a negative position are rejected. Accounting uses weighted-average cost,
includes fees, and never infers realized gains from an unsold position.

An initial holdings snapshot requires a current broker check and a second confirmation
in the private mobile interface. Acquisition time is not inferred. Unknown basis remains
unknown until the user supplies verified historical cost; corrections preserve an audit
trail and recompute subsequent weighted-average accounting. Fills predating an opening
snapshot are rejected to avoid double-counting. No personal snapshot is preloaded.

Explicit cash snapshots are supported; estimated and confirmed amounts are separate.
Snapshots expire after 24 hours and a later confirmed fill or correction disables sizing
until another broker reconciliation. Atomic fill correction and reversal endpoints are
provided; correcting a buy after a confirmed sale validates the complete replacement
ledger in one transaction. Broker-preview import, screenshot extraction and iPhone
Shortcut integration are not enabled.

## Notifications and scheduling

Telegram uses private runtime secrets `INVESTMENT_TELEGRAM_BOT_TOKEN` and
`INVESTMENT_TELEGRAM_CHAT_ID`. Only provision an authorized private chat. Lock-screen
messages omit allocation, holdings and balances; those belong behind authentication.
No Telegram webhook is exposed. `INVESTMENT_QUIET_HOURS` defaults to `23,7` in Riyadh;
`0,0` disables quiet hours. The authenticated test button sends an explicitly labeled
synthetic test, not a market opportunity.

The PostgreSQL outbox deduplicates signals, applies category cooldowns, and records
submission attempts. HTTP 429 is retried with bounded attempts. Timeouts are `unknown`,
not automatically resent: Telegram has no idempotency key, so resending could duplicate
a message. A process interrupted during submission leaves `sending`, requiring operator
review. API `submitted` never means delivered to the phone or read.

`INVESTMENT_SCHEDULER_ENABLED=true` enables the exchange-aware service scheduler.
It checks at one-minute intervals and scans hourly during regular U.S. sessions.
Advisory locks serialize scans across replicas. Scheduler and scanner heartbeats are
separate. An independent freshness monitor should run from the private companion;
without its deployment, external outage detection remains unverified.
The separate ChatGPT monitoring automation is not modified.

## Research and evaluation limits

The Perplexity adapter uses the documented `POST /v1/agent` request with web and finance
search, four tool steps and an output-token bound. It is disabled by default: configure
`PERPLEXITY_API_KEY`, `PERPLEXITY_MODEL`, `PERPLEXITY_MONTHLY_LIMIT_USD`, and a verified
`PERPLEXITY_REQUEST_BOUND_USD` only after confirming a provider-side billing cap, then set
`PERPLEXITY_BILLING_CAP_VERIFIED=true`. Local budget reservations serialize concurrent calls,
limit calls to three per UTC day, and retain reservations after ambiguous failures.
A response exceeding the verified per-request bound disables subsequent calls. Local
accounting cannot substitute for a provider-enforced billing cap.

Authenticated `/api/research/gather` accepts one to five watchlist tickers. It stores
sources, reported publication dates, retrieval times and cited claims as **drafts**.
It never turns model-written dates or primary-source claims into independent verification.
`/api/research/review` accepts a source-backed review following the gate contract, after
an operator independently checks publication times and primary sources. The zero-dollar
fallback marks news unavailable and blocks entry review. No recent catalysts are invented.
The live Perplexity request and billing entitlement remain unverified without credentials.

Walk-forward backtesting and statistical prediction calibration are not implemented.
No historical performance or accuracy claim is made. `backtesting.py` is not imported
or copied: its AGPL-3.0 obligations require review, and subprocess isolation does not
remove those obligations. Prediction events are logged before subsequent outcomes;
automatic outcome evaluation remains pending.

## Verification

Run the public tests above. Private PostgreSQL acceptance tests additionally exercise
RLS, append-only grants, concurrent deduplication, ledger state transitions, authentication,
CSRF rejection, synthetic Telegram transport submission and retry behavior. A mocked
transport test does not establish a real Telegram delivery or provider entitlement.
