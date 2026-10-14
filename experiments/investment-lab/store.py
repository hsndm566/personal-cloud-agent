"""Private PostgreSQL event store; operations are serialized per account, with RLS."""

from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import psycopg
from ledger import fingerprint, portfolio, validate_fill
from market import timestamp
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb
from risk import number


class Store:
    def __init__(self, dsn, owner):
        if not owner:
            raise ValueError("Private owner identity required")
        self.dsn, self.owner = dsn, owner

    @contextmanager
    def transaction(self):
        with psycopg.connect(self.dsn, row_factory=dict_row, connect_timeout=10) as connection:
            connection.execute("SELECT set_config('app.owner', %s, true)", (self.owner,))
            connection.execute(
                "SELECT pg_advisory_xact_lock(hashtextextended(%s, 0))", (self.owner,)
            )
            yield connection

    def events(self, connection=None):
        if connection is None:
            with self.transaction() as c:
                return self.events(c)
        return connection.execute(
            "SELECT id, kind, payload, created_at FROM lab_events WHERE owner=%s AND kind NOT IN ('REPORT','HEARTBEAT','SCHEDULER_HEARTBEAT','RUN_STARTED','INDEPENDENT_FRESHNESS_CHECK') ORDER BY created_at, id",
            (self.owner,),
        ).fetchall()

    def append(self, c, kind, payload, dedup=None):
        identifier = str(uuid4())
        row = c.execute(
            "INSERT INTO lab_events (id,owner,kind,payload,dedup) VALUES (%s,%s,%s,%s,%s) ON CONFLICT (owner,dedup) DO NOTHING RETURNING id",
            (identifier, self.owner, kind, Jsonb(payload), dedup),
        ).fetchone()
        if row:
            return row["id"]
        return c.execute(
            "SELECT id FROM lab_events WHERE owner=%s AND dedup=%s", (self.owner, dedup)
        ).fetchone()["id"]

    def submit(self, data):
        fill = validate_fill(data)
        with self.transaction() as c:
            identifier = self.append(c, "DRAFT", fill, "fill:" + fingerprint(fill))
            return {"id": identifier, "state": self.submission_state(identifier, self.events(c))}

    def submission_state(self, identifier, events):
        kinds = [e["kind"] for e in events if e["payload"].get("submission_id") == identifier]
        return (
            "CONFIRMED"
            if "CONFIRMED" in kinds
            else ("PENDING_CONFIRMATION" if "PENDING_CONFIRMATION" in kinds else "DRAFT")
        )

    def transition(self, identifier, target):
        with self.transaction() as c:
            events = self.events(c)
            draft = next(
                (e for e in events if e["id"] == identifier and e["kind"] == "DRAFT"), None
            )
            if not draft:
                raise ValueError("Submission not found")
            state = self.submission_state(identifier, events)
            if state == target:
                return {"id": identifier, "state": state}
            if (state, target) not in (
                ("DRAFT", "PENDING_CONFIRMATION"),
                ("PENDING_CONFIRMATION", "CONFIRMED"),
            ):
                raise ValueError("Invalid state transition")
            payload = {**draft["payload"], "submission_id": identifier}
            if target == "CONFIRMED":
                openings = [
                    e
                    for e in events
                    if e["kind"] == "OPENING" and e["payload"]["ticker"] == payload["ticker"]
                ]
                if any(
                    timestamp(payload["executed_at_utc"])
                    <= timestamp(e["payload"].get("as_of_utc", e["payload"].get("executed_at_utc")))
                    for e in openings
                ):
                    raise ValueError(
                        "Fill predates initial snapshot; reconcile rather than double-count it"
                    )
                portfolio(events + [{"id": str(uuid4()), "kind": target, "payload": payload}])
            self.append(c, target, payload, target + ":" + identifier)
            return {"id": identifier, "state": target}

    def reverse(self, identifier, reason):
        if not reason or len(reason) > 500:
            raise ValueError("Correction reason required")
        with self.transaction() as c:
            events = self.events(c)
            if not any(
                e["id"] == identifier and e["kind"] in ("CONFIRMED", "OPENING") for e in events
            ):
                raise ValueError("Confirmed ledger record required")
            reversal = {
                "id": str(uuid4()),
                "kind": "REVERSAL",
                "payload": {"reverses": identifier, "reason": reason},
            }
            portfolio(events + [reversal])  # Reject corrections that create negative holdings.
            return self.append(c, "REVERSAL", reversal["payload"], "reverse:" + identifier)

    def correct(self, identifier, replacement, reason):
        fill = validate_fill(replacement)
        if not reason or len(reason) > 500:
            raise ValueError("Correction reason required")
        dedup = "correction:" + identifier + ":" + fingerprint(fill)
        with self.transaction() as c:
            events = self.events(c)
            existing = next(
                (
                    e
                    for e in events
                    if e["kind"] == "CONFIRMED"
                    and e["payload"].get("correction_of") == identifier
                    and fingerprint(
                        {
                            k: v
                            for k, v in e["payload"].items()
                            if k not in ("correction_of", "correction_reason")
                        }
                    )
                    == fingerprint(fill)
                ),
                None,
            )
            if existing:
                return existing["id"]
            if not any(e["id"] == identifier and e["kind"] == "CONFIRMED" for e in events):
                raise ValueError("Confirmed fill required")
            if any(
                e["kind"] == "REVERSAL" and e["payload"]["reverses"] == identifier for e in events
            ):
                raise ValueError("Original fill already reversed; correct its replacement")
            reverse = {
                "id": str(uuid4()),
                "kind": "REVERSAL",
                "payload": {"reverses": identifier, "reason": reason},
            }
            corrected = {**fill, "correction_of": identifier, "correction_reason": reason}
            portfolio(
                events + [reverse, {"id": str(uuid4()), "kind": "CONFIRMED", "payload": corrected}]
            )
            self.append(c, "REVERSAL", reverse["payload"], "reverse:" + identifier)
            return self.append(c, "CONFIRMED", corrected, dedup)

    def opening_draft(self, data):
        if data.get("broker_reconciled") is not True:
            raise ValueError("Current broker reconciliation confirmation required")
        observed = timestamp(data.get("as_of_utc", ""))
        if not timedelta(0) <= datetime.now(UTC) - observed <= timedelta(hours=24):
            raise ValueError("Snapshot must describe current holdings checked within 24 hours")
        ticker = str(data.get("ticker", "")).strip().upper()
        import re

        if not re.fullmatch(r"[A-Z][A-Z0-9.\-]{0,11}", ticker):
            raise ValueError("Valid ticker required")
        quantity = number(data.get("quantity"))
        raw_basis = data.get("total_cost_usd")
        basis = number(raw_basis) if raw_basis not in (None, "") else None
        if quantity <= 0 or (basis is not None and basis < 0):
            raise ValueError("Positive quantity and nonnegative known basis required")
        payload = {
            "ticker": ticker,
            "quantity": str(quantity),
            "total_cost_usd": str(basis) if basis is not None else None,
            "as_of_utc": observed.isoformat(),
            "source": "user_reconciled_current_snapshot",
            "acquisition_time": None,
        }
        with self.transaction() as c:
            events = self.events(c)
            if any(
                e["kind"] in ("OPENING", "CONFIRMED") and e["payload"].get("ticker") == ticker
                for e in events
            ):
                raise ValueError(
                    "Ticker already has accounting records; use an auditable correction"
                )
            return self.append(
                c, "OPENING_DRAFT", payload, "opening:" + ticker + ":" + fingerprint(payload)
            )

    def opening_confirm(self, identifier):
        with self.transaction() as c:
            events = self.events(c)
            completed = next(
                (
                    e
                    for e in events
                    if e["kind"] == "OPENING" and e["payload"].get("submission_id") == identifier
                ),
                None,
            )
            if completed:
                return completed["id"]
            draft = next(
                (e for e in events if e["id"] == identifier and e["kind"] == "OPENING_DRAFT"), None
            )
            if not draft:
                raise ValueError("Opening draft not found")
            ticker = draft["payload"]["ticker"]
            if any(
                e["kind"] in ("OPENING", "CONFIRMED") and e["payload"].get("ticker") == ticker
                for e in events
            ):
                raise ValueError("Later records supersede this snapshot")
            if datetime.now(UTC) - timestamp(draft["payload"]["as_of_utc"]) > timedelta(hours=24):
                raise ValueError("Snapshot expired; reconcile again")
            return self.append(
                c,
                "OPENING",
                {**draft["payload"], "submission_id": identifier},
                "opening-confirm:" + identifier,
            )

    def correct_basis(self, ticker, value, reason):
        basis = number(value)
        if basis < 0 or not reason or len(reason) > 500:
            raise ValueError("Verified nonnegative historical basis and audit reason required")
        with self.transaction() as c:
            events = self.events(c)
            reversed_ids = {e["payload"]["reverses"] for e in events if e["kind"] == "REVERSAL"}
            opening = next(
                (
                    e
                    for e in events
                    if e["kind"] == "OPENING"
                    and e["payload"]["ticker"] == ticker
                    and e["id"] not in reversed_ids
                ),
                None,
            )
            if not opening:
                raise ValueError("Active historical opening snapshot required")
            corrections = [
                e
                for e in events
                if e["kind"] == "BASIS_CORRECTION" and e["payload"]["opening_id"] == opening["id"]
            ]
            if corrections and number(corrections[-1]["payload"]["total_cost_usd"]) == basis:
                return corrections[-1]["id"]
            return self.append(
                c,
                "BASIS_CORRECTION",
                {
                    "opening_id": opening["id"],
                    "total_cost_usd": str(basis),
                    "reason": reason,
                    "confirmed_at_utc": datetime.now(UTC).isoformat(),
                },
            )

    def save(self, kind, payload, dedup=None):
        with self.transaction() as c:
            return self.append(c, kind, payload, dedup)

    def latest(self, kind):
        with self.transaction() as c:
            row = c.execute(
                "SELECT payload FROM lab_events WHERE owner=%s AND kind=%s ORDER BY created_at DESC LIMIT 1",
                (self.owner, kind),
            ).fetchone()
            return row["payload"] if row else None

    def healthy(self):
        with self.transaction() as c:
            role = c.execute(
                "SELECT rolsuper, rolbypassrls FROM pg_roles WHERE rolname=current_user"
            ).fetchone()
            if role["rolsuper"] or role["rolbypassrls"]:
                raise ValueError("Application role must not bypass RLS")
            c.execute("SELECT id FROM lab_events LIMIT 1")
        return True
