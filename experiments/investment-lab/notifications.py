"""Durable outbox; API submission is not delivery/read proof. Ambiguous sends stop retries."""

import hashlib
import json
import urllib.error
import urllib.request
from datetime import UTC, datetime

from market import RIYADH
from psycopg.types.json import Jsonb


def signal_id(category, identity, revision):
    return hashlib.sha256(f"{category}:{identity}:{revision}".encode()).hexdigest()


def format_alert(category, now, candidate=None, test=False):
    prefix = "TEST — " if test else ""
    stamp = now.astimezone(RIYADH).strftime("%Y-%m-%d %I:%M %p Saudi")
    # Lock-screen default deliberately excludes holdings, quantities and account capital.
    if category == "entry" and candidate and candidate["status"] == "CONDITIONAL ENTRY REVIEW":
        body = f"{candidate['ticker']} — ENTRY REVIEW\nScanner: 5/5\nOpen private dashboard for provisional allocation.\nAwaed quote verification required."
    elif category == "risk":
        body = "RISK REVIEW\nReview private dashboard. No broker stop order confirmed."
    elif category == "summary":
        body = "Market check — NO TRADE\nNo validated entries. Next check follows the exchange schedule."
    elif category == "entry_summary":
        body = "Market check — REVIEW\nValidated research candidates in private dashboard. Awaed preview required."
    else:
        body = "System status — NO TRADE\nScanner or eligible data unavailable. Check private dashboard."
    return prefix + body + "\nUpdated: " + stamp


class Notifier:
    def __init__(self, store, token=None, chat=None, transport=None, quiet_hours=(23, 7)):
        self.store, self.token, self.chat = store, token, chat
        self.transport = transport or urllib.request.urlopen
        self.quiet_hours = quiet_hours

    def enqueue(self, identifier, category, text):
        with self.store.transaction() as c:
            c.execute(
                "INSERT INTO lab_notifications(owner,signal_id,category,payload) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (self.store.owner, identifier, category, Jsonb({"text": text})),
            )

    def flush(self, now=None):
        now = now or datetime.now(UTC)
        start, end = self.quiet_hours
        hour = now.astimezone(RIYADH).hour
        if (start < end and start <= hour < end) or (start > end and (hour >= start or hour < end)):
            return "quiet_hours"
        if not self.token or not self.chat:
            return "not_configured"
        # Claim and commit BEFORE networking. If the process dies after submission,
        # state stays 'sending' (outcome unknown), so a restart cannot duplicate it.
        for _ in range(5):
            with self.store.transaction() as c:
                row = c.execute(
                    "SELECT * FROM lab_notifications n WHERE owner=%s AND state='pending' AND next_attempt<=%s AND NOT EXISTS (SELECT 1 FROM lab_notifications recent WHERE recent.owner=n.owner AND recent.category=n.category AND (recent.state='sending' OR recent.submitted_at>%s - interval '60 minutes')) ORDER BY next_attempt LIMIT 1 FOR UPDATE",
                    (self.store.owner, now, now),
                ).fetchone()
                if not row:
                    break
                c.execute(
                    "UPDATE lab_notifications SET state='sending',attempts=attempts+1 WHERE owner=%s AND signal_id=%s",
                    (self.store.owner, row["signal_id"]),
                )
            state, code = "submitted", None
            try:
                request = urllib.request.Request(
                    "https://api.telegram.org/bot" + self.token + "/sendMessage",
                    data=json.dumps(
                        {
                            "chat_id": self.chat,
                            "text": row["payload"]["text"],
                            "protect_content": True,
                        }
                    ).encode(),
                    headers={"Content-Type": "application/json"},
                )
                with self.transport(request, timeout=15) as response:
                    data = json.load(response)
                if data.get("ok") is not True:
                    state, code = "failed", "telegram_rejected"
            except urllib.error.HTTPError as error:
                code = f"http_{error.code}"
                state = "pending" if error.code == 429 and row["attempts"] < 3 else "failed"
            except (TimeoutError, OSError, ValueError):
                state, code = "unknown", "submission_outcome_unknown"
            with self.store.transaction() as c:
                c.execute(
                    "UPDATE lab_notifications SET state=%s,error_code=%s,next_attempt=%s + interval '5 minutes',submitted_at=CASE WHEN %s='submitted' THEN %s ELSE NULL END WHERE owner=%s AND signal_id=%s",
                    (state, code, now, state, now, self.store.owner, row["signal_id"]),
                )
                self.store.append(
                    c,
                    "NOTIFICATION_ATTEMPT",
                    {
                        "signal_id": row["signal_id"],
                        "state": state,
                        "error_code": code,
                        "at_utc": now.isoformat(),
                    },
                )
        return "processed"
