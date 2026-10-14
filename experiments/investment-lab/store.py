"""Private PostgreSQL event store; operations are serialized per account, with RLS."""

from contextlib import contextmanager
from uuid import uuid4

import psycopg
from ledger import fingerprint, portfolio, validate_fill
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


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
            "SELECT id, kind, payload, created_at FROM lab_events WHERE owner=%s ORDER BY created_at, id",
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
