import io
import json
import sys
import unittest
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from account import account_state, position_risks, validate_snapshot
from research import ResearchClient

NOW = datetime(2026, 10, 13, 15, tzinfo=UTC)


class MemoryEvents:
    def __init__(self):
        self.rows = []

    @contextmanager
    def transaction(self):
        yield self

    def events(self, c=None):
        return self.rows

    def append(self, c, kind, payload, dedup=None):
        identifier = str(len(self.rows))
        self.rows.append({"id": identifier, "kind": kind, "payload": payload, "created_at": NOW})
        return identifier

    def save(self, kind, payload):
        return self.append(self, kind, payload)


class AccountResearchTests(unittest.TestCase):
    def test_estimates_never_promoted_and_fills_invalidate_capital(self):
        data = validate_snapshot(
            {
                "user_verified": True,
                "observed_at_utc": NOW.isoformat(),
                "estimated_cash_sar": "123",
                "share_precision": 3,
            },
            NOW,
        )
        events = [{"id": "a", "kind": "ACCOUNT_SNAPSHOT", "payload": data, "created_at": NOW}]
        result = account_state(events, NOW)
        self.assertIsNone(result["capital"]["confirmed_cash_sar"])
        self.assertEqual(result["estimated_cash_sar"], "123")
        events.append(
            {
                "id": "b",
                "kind": "CONFIRMED",
                "payload": {},
                "created_at": NOW + timedelta(seconds=1),
            }
        )
        self.assertFalse(account_state(events, NOW + timedelta(seconds=2))["reconciled"])
        self.assertFalse(account_state(events[:1], NOW + timedelta(days=2))["reconciled"])

    def test_snapshot_requires_confirmation_and_timestamp(self):
        with self.assertRaises(ValueError):
            validate_snapshot({}, NOW)
        with self.assertRaises(ValueError):
            validate_snapshot(
                {
                    "user_verified": True,
                    "observed_at_utc": (NOW + timedelta(seconds=1)).isoformat(),
                },
                NOW,
            )

    def test_risk_references_are_not_stop_orders(self):
        positions = [{"ticker": "TEST", "quantity": "2"}]
        result = position_risks(
            positions, {"TEST": {"eligible": True, "price": 99}}, {"TEST": 100}
        )[0]
        self.assertEqual(result["status"], "RISK REVIEW")
        self.assertFalse(result["broker_stop_order_confirmed"])
        self.assertEqual(position_risks(positions, {}, {"TEST": 100})[0]["status"], "WAIT")

    def client(self, store, limit="10", bound="1", transport=None):
        return ResearchClient(
            store,
            "synthetic",
            "test-model",
            limit,
            bound,
            True,
            transport
            or (
                lambda *a, **kw: io.BytesIO(
                    json.dumps(
                        {"output": [], "usage": {"cost": {"currency": "USD", "total_cost": 0.1}}}
                    ).encode()
                )
            ),
        )

    def test_three_call_limit_and_documented_tools(self):
        store = MemoryEvents()
        requests = []

        def transport(request, **kwargs):
            requests.append(json.loads(request.data))
            return io.BytesIO(b'{"output":[],"usage":{"cost":{"currency":"USD","total_cost":0.1}}}')

        client = self.client(store, transport=transport)
        for _ in range(3):
            self.assertEqual(client.gather(["TEST"], NOW)["status"], "draft")
        self.assertEqual(client.gather(["TEST"], NOW)["status"], "unavailable")
        self.assertEqual(len(requests), 3)
        self.assertEqual(requests[0]["max_steps"], 4)
        self.assertEqual(requests[0]["tools"], [{"type": "web_search"}, {"type": "finance_search"}])

    def test_monthly_limit_missing_usage_and_provider_failure(self):
        store = MemoryEvents()

        def timeout(*a, **kwargs):
            raise TimeoutError()

        client = self.client(store, limit="1", transport=timeout)
        self.assertEqual(client.gather(["TEST"], NOW)["status"], "unavailable")
        self.assertEqual(client.gather(["TEST"], NOW + timedelta(days=1))["status"], "unavailable")
        self.assertEqual(len([e for e in store.rows if e["kind"] == "RESEARCH_RESERVATION"]), 1)
        self.assertEqual(
            ResearchClient(store).gather(["TEST"], NOW)["provider"], "zero_cost_fallback"
        )

    def test_model_results_never_self_certify(self):
        store = MemoryEvents()
        result = self.client(store).gather(["TEST"], NOW)
        self.assertFalse(result["independent_review"])
        self.assertEqual(result["status"], "draft")
        self.assertEqual(result["sources"], [])


if __name__ == "__main__":
    unittest.main()
