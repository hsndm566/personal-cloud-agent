"""Isolated private FastAPI application. No brokerage integration or execution route."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

from account import account_state, position_risks, validate_snapshot
from engine import report_state, research_current
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from ledger import portfolio
from market import RIYADH, ExchangeCalendar, timestamp
from notifications import Notifier, format_alert, signal_id
from production import runtime_inputs, scan
from store import Store


def create_app(store=None, access_key=None, session_key=None, origin=None, scheduled=False):
    store = store or Store(os.getenv("DATABASE_URL", ""), os.getenv("INVESTMENT_OWNER", "personal"))
    access_key = access_key or os.getenv("INVESTMENT_ACCESS_KEY", "")
    session_key = session_key or os.getenv("INVESTMENT_SESSION_KEY", "")
    origin = origin or os.getenv("INVESTMENT_ORIGIN", "")
    attempts = defaultdict(deque)
    quiet = tuple(int(x) for x in os.getenv("INVESTMENT_QUIET_HOURS", "23,7").split(","))
    notifier = Notifier(
        store,
        os.getenv("INVESTMENT_TELEGRAM_BOT_TOKEN"),
        os.getenv("INVESTMENT_TELEGRAM_CHAT_ID"),
        quiet_hours=quiet,
    )

    def private_scan():
        now = datetime.now(UTC)
        # Session advisory lock across the complete run, independent of HTTP requests/replicas.
        import psycopg

        with psycopg.connect(store.dsn, autocommit=True) as lock:
            acquired = lock.execute(
                "SELECT pg_try_advisory_lock(hashtextextended(%s,1))", (store.owner,)
            ).fetchone()[0]
            if not acquired:
                return {"status": "already_running"}
            try:
                store.save(
                    "RUN_STARTED", {"at_utc": now.isoformat(), "origin": "private_production"}
                )
                config = json.loads((Path(__file__).parent / "data/config.json").read_text())
                config["exchanges"] = json.loads(os.getenv("INVESTMENT_EXCHANGES", "{}"))
                providers, entitlements = runtime_inputs()
                events = store.events()
                account = account_state(events, now)
                reviews = {}
                for event in events:
                    if event["kind"] == "RESEARCH_REVIEW":
                        for ticker in event["payload"]["tickers"]:
                            reviews[ticker] = event["payload"]
                result = scan(
                    config,
                    production=True,
                    providers=providers,
                    entitlements=entitlements,
                    capital=account["capital"],
                    research=reviews,
                )
                quotes = {q["ticker"]: q for q in result["observations"] if q["eligible"]}
                positions = portfolio(events, {t: q["price"] for t, q in quotes.items()})
                levels = store.latest("RISK_REFERENCES") or {}
                result["positions"] = position_risks(positions, quotes, levels)
                result["account"] = {k: v for k, v in account.items() if k != "capital"}
                store.save("REPORT", result)
                for position in result["positions"]:
                    if position["status"] == "RISK REVIEW":
                        identifier = signal_id("risk", position["ticker"], now.date().isoformat())
                        notifier.enqueue(
                            identifier, "risk:" + position["ticker"], format_alert("risk", now)
                        )
                for candidate in result["stocks"]:
                    if candidate["status"] == "CONDITIONAL ENTRY REVIEW":
                        identifier = signal_id(
                            "entry",
                            candidate["ticker"],
                            str(candidate["technical_entry_above_usd"]) + now.date().isoformat(),
                        )
                        notifier.enqueue(
                            identifier,
                            "entry:" + candidate["ticker"],
                            format_alert("entry", now, candidate),
                            (
                                timestamp(candidate["quote"]["observed_at_utc"])
                                + timedelta(minutes=20)
                            ).isoformat(),
                        )
                        store.save(
                            "PREDICTION",
                            {
                                "signal_id": identifier,
                                "logged_at_utc": now.isoformat(),
                                "candidate": candidate,
                            },
                            identifier,
                        )
                if result["run_status"] != "completed" or not any(
                    q.get("eligible") for q in result["observations"]
                ):
                    identifier = signal_id("system", "data_unavailable", now.date().isoformat())
                    notifier.enqueue(identifier, "system", format_alert("system", now))
                # Exactly one daily concise summary (updated state only, not a fabricated opportunity).
                category = (
                    "entry_summary"
                    if any(s["status"] == "CONDITIONAL ENTRY REVIEW" for s in result["stocks"])
                    else "summary"
                )
                notifier.enqueue(
                    signal_id("summary", "market", now.date().isoformat()),
                    category,
                    format_alert(category, now),
                )
                notifier.flush(now)
                store.save(
                    "HEARTBEAT",
                    {"at_utc": datetime.now(UTC).isoformat(), "run_status": result["run_status"]},
                )
                return {
                    "status": result["run_status"],
                    "completed_at_utc": result["completed_at_utc"],
                }
            except Exception:  # noqa: BLE001 -- fail closed without exposing private runtime errors
                failure = {
                    "origin": "private_production",
                    "run_status": "failed",
                    "status": "SCANNER_UNAVAILABLE",
                    "completed_at_utc": datetime.now(UTC).isoformat(),
                    "error_code": "scanner_failure",
                    "stocks": [],
                    "positions": [],
                }
                store.save("REPORT", failure)
                notifier.enqueue(
                    signal_id("failure", "scanner", now.date().isoformat()),
                    "system",
                    format_alert("system", now),
                )
                notifier.flush(now)
                return {"status": "failed"}

    async def scheduler():
        while True:
            try:
                now, calendar = datetime.now(UTC), ExchangeCalendar()
                latest = store.latest("REPORT")
                if calendar.session(now) == "regular":
                    interval = 3600 if latest and latest.get("run_status") == "completed" else 300
                    age = (
                        (now - timestamp(latest["completed_at_utc"])).total_seconds()
                        if latest
                        else interval
                    )
                    if age >= interval:
                        await asyncio.to_thread(private_scan)
                await asyncio.to_thread(notifier.flush)
                store.save("SCHEDULER_HEARTBEAT", {"at_utc": now.isoformat()})
            except Exception:  # noqa: BLE001 -- fail closed without exposing private runtime errors
                # Log only a fixed operational code, never private exception details.
                logging.getLogger(__name__).error("scheduler_unavailable")
            await asyncio.sleep(60)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(scheduler()) if scheduled else None
        yield
        if task:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass

    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None, lifespan=lifespan)
    app.state.private_scan = private_scan

    def session(request):
        cookie = request.cookies.get("lab_session", "")
        try:
            encoded, signature = cookie.split(".")
            expected = hmac.new(session_key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
            if not session_key or not hmac.compare_digest(expected, signature):
                raise ValueError()
            data = json.loads(base64.urlsafe_b64decode(encoded))
            if data["expires"] < time.time():
                raise ValueError()
            return data
        except Exception:  # noqa: BLE001 -- fail closed without exposing private runtime errors
            raise HTTPException(401, "Authentication required") from None

    @app.middleware("http")
    async def security(request, call_next):
        if request.url.path not in ("/", "/app.js", "/style.css", "/health", "/api/login"):
            try:
                data = session(request)
                if request.method not in ("GET", "HEAD") and (
                    request.headers.get("origin") != origin
                    or not hmac.compare_digest(
                        request.headers.get("x-csrf-token", ""), data["csrf"]
                    )
                ):
                    return JSONResponse({"detail": "CSRF rejected"}, status_code=403)
            except HTTPException:
                return JSONResponse({"detail": "Authentication required"}, status_code=401)
        if request.method not in ("GET", "HEAD"):
            # Single-owner, single-replica limit; never trust attacker-controlled X-Forwarded-For.
            key = request.url.path
            queue = attempts[key]
            now = time.monotonic()
            while queue and queue[0] < now - 60:
                queue.popleft()
            if len(queue) >= (5 if key == "/api/login" else 30):
                return JSONResponse({"detail": "Rate limit"}, status_code=429)
            queue.append(now)
            if int(request.headers.get("content-length", "0") or 0) > 16384:
                return JSONResponse({"detail": "Request too large"}, status_code=413)
        response = await call_next(request)
        response.headers.update(
            {
                "Cache-Control": "no-store",
                "X-Content-Type-Options": "nosniff",
                "X-Frame-Options": "DENY",
                "Referrer-Policy": "no-referrer",
                "Strict-Transport-Security": "max-age=31536000",
                "Content-Security-Policy": "default-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
            }
        )
        return response

    @app.exception_handler(ValueError)
    async def invalid(request, error):
        return JSONResponse({"detail": str(error)}, status_code=400)

    @app.get("/health")
    def health():
        try:
            store.healthy()
            return {"status": "ok", "database": "connected", "execution_enabled": False}
        except Exception:  # noqa: BLE001 -- fail closed without exposing private runtime errors
            return JSONResponse({"status": "unavailable"}, status_code=503)

    @app.post("/api/login")
    async def login(request: Request):
        if not access_key or len(access_key) < 32 or not session_key or not origin:
            raise HTTPException(503, "Authentication not provisioned")
        if request.headers.get("origin") != origin:
            raise HTTPException(403, "Origin rejected")
        data = await request.json()
        if not hmac.compare_digest(str(data.get("key", "")), access_key):
            raise HTTPException(401, "Invalid access key")
        payload = {"expires": int(time.time()) + 8 * 3600, "csrf": secrets.token_urlsafe(32)}
        encoded = base64.urlsafe_b64encode(json.dumps(payload).encode()).decode()
        signed = (
            encoded
            + "."
            + hmac.new(session_key.encode(), encoded.encode(), hashlib.sha256).hexdigest()
        )
        response = JSONResponse({"csrf": payload["csrf"]})
        response.set_cookie(
            "lab_session", signed, httponly=True, secure=True, samesite="strict", max_age=8 * 3600
        )
        return response

    @app.get("/api/session")
    def current_session(request: Request):
        return {"csrf": session(request)["csrf"]}

    @app.post("/api/logout")
    def logout():
        response = JSONResponse({"status": "logged_out"})
        response.delete_cookie("lab_session", secure=True, httponly=True, samesite="strict")
        return response

    @app.get("/api/dashboard")
    def dashboard():
        now = datetime.now(UTC)
        events = store.events()
        latest = store.latest("REPORT")
        state = report_state(latest, now)
        account = account_state(events, now)
        reviews = {}
        for event in events:
            if event["kind"] == "RESEARCH_REVIEW":
                for ticker in event["payload"]["tickers"]:
                    reviews[ticker] = event["payload"]
        # Recompute age at read time; a cached entry must not retain its review status.
        if latest:
            for stock in latest.get("stocks", []):
                quote = stock.get("quote")
                from market import timestamp

                fresh_quote = False
                try:
                    fresh_quote = (
                        quote
                        and 0 <= (now - timestamp(quote["observed_at_utc"])).total_seconds() <= 1200
                        and ExchangeCalendar().session(now) == "regular"
                    )
                except (KeyError, TypeError, ValueError):
                    pass
                if (
                    state != "FRESH"
                    or not fresh_quote
                    or not account["reconciled"]
                    or not research_current(reviews.get(stock["ticker"]), stock["ticker"], now)
                ):
                    stock["status"] = "WAIT"
                    stock["allocation"] = None
        marks = {}
        for quote in (latest or {}).get("observations", []):
            try:
                if (
                    quote.get("eligible")
                    and 0 <= (now - timestamp(quote["observed_at_utc"])).total_seconds() <= 1200
                ):
                    marks[quote["ticker"]] = quote["price"]
            except (KeyError, TypeError, ValueError):
                pass
        return {
            "system_health": state,
            "report": latest,
            "session": ExchangeCalendar().session(now),
            "as_of_riyadh": now.astimezone(RIYADH).isoformat(),
            "positions": portfolio(events, marks),
            "confirmed_cash_sar": account["confirmed_cash_sar"],
            "estimated_cash_sar": account["estimated_cash_sar"],
            "buying_power_sar": account["buying_power_sar"],
            "reconciliation": "Current user-confirmed snapshot"
            if account["reconciled"]
            else "Required; sizing disabled",
            "provider_status": "eligible"
            if latest and any(q.get("eligible") for q in latest.get("observations", []))
            else "unavailable",
            "independent_freshness_check": store.latest("INDEPENDENT_FRESHNESS_CHECK"),
            "predictions": [e for e in events if e["kind"] == "PREDICTION"][-20:],
            "recent_alerts": [e for e in events if e["kind"] == "NOTIFICATION_ATTEMPT"][-20:],
            "submissions": [
                {
                    "id": e["id"],
                    "fill": e["payload"],
                    "state": store.submission_state(e["id"], events),
                }
                for e in events
                if e["kind"] == "DRAFT"
            ][-20:],
            "opening_drafts": [
                {"id": e["id"], "snapshot": e["payload"]}
                for e in events
                if e["kind"] == "OPENING_DRAFT"
                and not any(
                    x["kind"] == "OPENING" and x["payload"].get("submission_id") == e["id"]
                    for x in events
                )
            ],
            "scheduler_heartbeat": store.latest("SCHEDULER_HEARTBEAT"),
        }

    @app.post("/api/opening-holdings")
    async def opening_draft(request: Request):
        return {"id": store.opening_draft(await request.json()), "state": "PENDING_CONFIRMATION"}

    @app.post("/api/opening-holdings/{identifier}/confirm")
    async def opening_confirm(identifier: str, request: Request):
        data = await request.json()
        if data.get("confirm_current_snapshot") is not True:
            raise ValueError("Confirm the actual current holding before importing")
        return {"id": store.opening_confirm(identifier), "state": "CONFIRMED"}

    @app.post("/api/opening-basis")
    async def correct_basis(request: Request):
        data = await request.json()
        if data.get("verified_cost_basis") is not True:
            raise ValueError("Independently verified historical basis required")
        return {
            "id": store.correct_basis(data["ticker"], data["total_cost_usd"], data.get("reason"))
        }

    @app.post("/api/account-snapshot")
    async def account_snapshot(request: Request):
        data = validate_snapshot(await request.json(), datetime.now(UTC))
        return {"id": store.save("ACCOUNT_SNAPSHOT", data), "status": "recorded"}

    @app.post("/api/research/review")
    async def review_research(request: Request):
        data = await request.json()
        now = datetime.now(UTC)
        if not data.get("tickers") or not all(
            research_current(data, t, now) for t in data["tickers"]
        ):
            raise ValueError("Current independently reviewed primary sources and claims required")
        return {"id": store.save("RESEARCH_REVIEW", data)}

    @app.post("/api/research/gather")
    async def gather_research(request: Request):
        from research import ResearchClient

        data = await request.json()
        config = json.loads((Path(__file__).parent / "data/config.json").read_text())
        tickers = data.get("tickers", [])
        if not tickers or len(tickers) > 5 or any(t not in config["watchlist"] for t in tickers):
            raise ValueError("Select one to five watchlist tickers")
        client = ResearchClient(
            store,
            os.getenv("PERPLEXITY_API_KEY", ""),
            os.getenv("PERPLEXITY_MODEL", ""),
            os.getenv("PERPLEXITY_MONTHLY_LIMIT_USD", "0"),
            os.getenv("PERPLEXITY_REQUEST_BOUND_USD"),
            os.getenv("PERPLEXITY_BILLING_CAP_VERIFIED") == "true",
        )
        return await asyncio.to_thread(client.gather, tickers)

    @app.post("/api/risk-references")
    async def risk_references(request: Request):
        from risk import number

        data = await request.json()
        if not isinstance(data, dict) or len(data) > 30:
            raise ValueError("Invalid reference levels")
        for ticker, level in data.items():
            if not ticker.isalnum() or number(level) <= 0:
                raise ValueError("Positive technical reference required")
        return {"id": store.save("RISK_REFERENCES", data), "broker_stop_order_confirmed": False}

    @app.post("/api/scan")
    def run_scan():
        return private_scan()

    @app.post("/api/transactions")
    async def submit(request: Request):
        return store.submit(await request.json())

    @app.post("/api/transactions/{identifier}/{target}")
    def transition(identifier: str, target: str):
        return store.transition(identifier, target)

    @app.post("/api/ledger/{identifier}/reverse")
    async def reverse(identifier: str, request: Request):
        data = await request.json()
        return {"id": store.reverse(identifier, data.get("reason"))}

    @app.post("/api/ledger/{identifier}/correct")
    async def correct(identifier: str, request: Request):
        data = await request.json()
        if data.get("confirm_correction") is not True:
            raise ValueError("Explicit correction confirmation required")
        return {"id": store.correct(identifier, data["fill"], data.get("reason"))}

    @app.post("/api/notifications/test")
    def test_alert():
        now = datetime.now(UTC)
        notifier.enqueue(
            signal_id("test", "setup", now.date().isoformat()),
            "test",
            format_alert(
                "entry", now, {"ticker": "TEST", "status": "CONDITIONAL ENTRY REVIEW"}, test=True
            ),
        )
        return {"state": notifier.flush(now), "read_receipt": "unavailable"}

    @app.get("/", response_class=HTMLResponse)
    def page():
        return (Path(__file__).parent / "ui/index.html").read_text()

    @app.get("/app.js")
    def javascript():
        from fastapi.responses import Response

        return Response(
            (Path(__file__).parent / "ui/app.js").read_text(), media_type="text/javascript"
        )

    @app.get("/style.css")
    def style():
        from fastapi.responses import Response

        return Response((Path(__file__).parent / "ui/style.css").read_text(), media_type="text/css")

    return app


app = create_app(scheduled=os.getenv("INVESTMENT_SCHEDULER_ENABLED") == "true")
