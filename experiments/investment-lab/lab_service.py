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
from datetime import UTC, datetime
from pathlib import Path

from engine import report_state
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from ledger import portfolio
from market import RIYADH, ExchangeCalendar
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
                # Until reconciliation is implemented, account balances never enter entry sizing.
                result = scan(
                    config, production=True, providers=providers, entitlements=entitlements
                )
                result["positions"] = portfolio(store.events())
                store.save("REPORT", result)
                for candidate in result["stocks"]:
                    if candidate["status"] == "CONDITIONAL ENTRY REVIEW":
                        identifier = signal_id(
                            "entry",
                            candidate["ticker"],
                            str(candidate["technical_entry_above_usd"]) + now.date().isoformat(),
                        )
                        notifier.enqueue(identifier, "entry", format_alert("entry", now, candidate))
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
                if calendar.session(now) == "regular" and report_state(latest, now) != "FRESH":
                    await asyncio.to_thread(private_scan)
                elif calendar.session(now) == "regular" and latest:
                    from market import timestamp

                    if (now - timestamp(latest["completed_at_utc"])).total_seconds() >= 3600:
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
                if state != "FRESH" or not fresh_quote:
                    stock["status"] = "WAIT"
                    stock["allocation"] = None
        return {
            "system_health": state,
            "report": latest,
            "session": ExchangeCalendar().session(now),
            "as_of_riyadh": now.astimezone(RIYADH).isoformat(),
            "positions": portfolio(events),
            "confirmed_cash_sar": None,
            "estimated_cash_sar": None,
            "buying_power_sar": None,
            "reconciliation": "Required; no historical snapshot seeded",
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
            "scheduler_heartbeat": store.latest("SCHEDULER_HEARTBEAT"),
        }

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
