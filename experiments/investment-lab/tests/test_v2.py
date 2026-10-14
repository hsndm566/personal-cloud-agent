import io
import json
import sys
import unittest
import urllib.error
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from engine import evaluate, report_state
from lab import DailyBar
from ledger import fingerprint, portfolio, validate_fill
from market import Entitlement, ExchangeCalendar, Finnhub, MarketQuote, ProviderError, select_quote
from notifications import format_alert, signal_id
from production import scan
from risk import size_position

NOW = datetime(2026, 10, 13, 15, 10, tzinfo=UTC)
NOW_ISO = NOW.isoformat()


def entitlement(provider="finnhub"):
    return Entitlement(
        provider,
        ("XNYS",),
        True,
        True,
        True,
        "https://example.org/license",
        "2026-10-01T00:00:00+00:00",
        "2027-01-01T00:00:00+00:00",
        0,
    )


def research():
    return {
        "status": "verified",
        "tickers": ["TEST"],
        "claims": ["Synthetic test claim"],
        "retrieved_at_utc": NOW.isoformat(),
        "independent_review": True,
        "negative_news_checked": True,
        "technical_upside_reference": 190,
        "upside_reference_source": "https://example.org/research",
        "sources": [
            {
                "primary": True,
                "url": "https://example.org/release",
                "published_at_utc": (NOW - timedelta(hours=1)).isoformat(),
            }
        ],
    }


@unittest.skipUnless(
    find_spec("exchange_calendars"), "Run the dedicated Investment Lab dependency environment"
)
class MarketTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.calendar = ExchangeCalendar()

    def quote(self, stamp=NOW_ISO, price=171.5):
        return MarketQuote("TEST", "XNYS", price, "USD", "finnhub", stamp, 0, "real-time")

    def test_holiday_dst_early_close(self):
        self.assertEqual(self.calendar.session(datetime(2026, 12, 25, 16, tzinfo=UTC)), "closed")
        self.assertEqual(
            self.calendar.session(datetime(2026, 11, 27, 18, 1, tzinfo=UTC)), "extended"
        )
        self.assertEqual(self.calendar.session(datetime(2026, 11, 3, 14, tzinfo=UTC)), "extended")
        self.assertEqual(self.calendar.session(datetime(2026, 10, 13, 14, tzinfo=UTC)), "regular")

    def test_quote_boundary_and_timezones(self):
        result = self.quote((NOW - timedelta(minutes=20)).isoformat()).record(
            NOW, self.calendar, entitlement()
        )
        self.assertTrue(result["eligible"])
        self.assertTrue(result["observed_at_riyadh"].endswith("+03:00"))
        for stamp in [
            None,
            "",
            "2026-10-13T15:00:00",
            (NOW - timedelta(minutes=20, seconds=1)).isoformat(),
            (NOW + timedelta(seconds=1)).isoformat(),
        ]:
            self.assertFalse(
                self.quote(stamp).record(NOW, self.calendar, entitlement())["eligible"]
            )
        for price in [None, 0, -1, float("nan"), float("inf")]:
            self.assertFalse(
                self.quote(price=price).record(NOW, self.calendar, entitlement())["eligible"]
            )

    def test_license_and_delay_fail_closed(self):
        self.assertFalse(
            self.quote().record(NOW, self.calendar, Entitlement("finnhub"))["eligible"]
        )
        self.assertFalse(entitlement("twelvedata").eligible("XNYS", NOW))
        self.assertFalse(
            MarketQuote("TEST", "XNYS", 1, "USD", "finnhub", NOW.isoformat()).record(
                NOW, self.calendar, entitlement()
            )["eligible"]
        )

    def test_provider_uses_observation_not_receipt(self):
        provider = Finnhub(
            "not-a-real-key",
            entitlement(),
            lambda *a, **kw: io.BytesIO(
                json.dumps({"c": 12, "t": (NOW - timedelta(hours=1)).timestamp()}).encode()
            ),
        )
        self.assertFalse(
            provider.quote("TEST", "XNYS", NOW).record(NOW, self.calendar, entitlement())[
                "eligible"
            ]
        )

    def test_provider_failures(self):
        for data in [
            {},
            {"c": 12, "t": 0},
            {"c": 0, "t": NOW.timestamp()},
            [],
            {"c": 12, "t": "bad"},
        ]:
            provider = Finnhub(
                "synthetic",
                entitlement(),
                lambda *a, data=data, **kw: io.BytesIO(json.dumps(data).encode()),
            )
            with self.assertRaises(ProviderError):
                provider.quote("TEST", "XNYS", NOW)
        for code in (401, 403, 429):

            def transport(*a, code=code, **kw):
                raise urllib.error.HTTPError("redacted", code, "error", {}, None)

            with self.assertRaisesRegex(ProviderError, f"http_{code}"):
                Finnhub("synthetic", entitlement(), transport).quote("TEST", "XNYS", NOW)

        def timeout(*a, **kw):
            raise TimeoutError()

        with self.assertRaisesRegex(ProviderError, "network"):
            Finnhub("synthetic", entitlement(), timeout).quote("TEST", "XNYS", NOW)

    def test_all_providers_fail_or_disagree(self):
        class Provider:
            name = "finnhub"

            def quote(inner, *args):
                return self.quote()

        class Other:
            name = "independent"

            def quote(inner, *args):
                return MarketQuote(
                    "TEST", "XNYS", 180, "USD", inner.name, NOW.isoformat(), 0, "real-time"
                )

        q, _, _ = select_quote([Provider()], "TEST", "XNYS", {}, NOW, self.calendar)
        self.assertIsNone(q)
        q, _, failures = select_quote(
            [Provider(), Other()],
            "TEST",
            "XNYS",
            {"finnhub": entitlement(), "independent": entitlement("independent")},
            NOW,
            self.calendar,
        )
        self.assertIsNone(q)
        self.assertEqual(failures[0]["code"], "price_discrepancy_over_1_percent")


@unittest.skipUnless(
    find_spec("exchange_calendars"), "Run the dedicated Investment Lab dependency environment"
)
class GateTests(unittest.TestCase):
    def setUp(self):
        self.calendar = ExchangeCalendar()
        dates = self.calendar.calendar.sessions_in_range("2026-07-01", "2026-10-12")[-70:]
        self.bars = [
            DailyBar(
                str(d.date()),
                149.8 + i * 0.3,
                150.2 + i * 0.3,
                149.7 + i * 0.3,
                150 + i * 0.3,
                2_000_000,
            )
            for i, d in enumerate(dates)
        ]
        trigger = round(max(b.high for b in self.bars[-5:]) * 1.001, 2)
        self.quote = MarketQuote(
            "TEST", "XNYS", trigger + 0.1, "USD", "finnhub", NOW.isoformat(), 0, "real-time"
        ).record(NOW, self.calendar, entitlement())
        self.capital = {
            "fx_sar_per_usd": 3.75,
            "confirmed_cash_sar": 200,
            "buying_power_sar": 200,
            "share_precision": 3,
            "fee_sar": 0.1,
            "spread_bps": 10,
            "slippage_bps": 10,
        }

    def evaluate(self, **kw):
        args = {
            "ticker": "TEST",
            "bars": self.bars,
            "benchmark": self.bars,
            "quote": self.quote,
            "research": research(),
            "capital": self.capital,
            "now": NOW,
            "calendar": self.calendar,
            "production": True,
        }
        args.update(kw)
        return evaluate(**args)

    def test_synthetic_eligible_setup_still_requires_broker(self):
        result = self.evaluate()
        self.assertEqual(result["status"], "CONDITIONAL ENTRY REVIEW")
        self.assertTrue(result["broker_preview_required"])
        self.assertFalse(result["executable"])
        self.assertEqual(result["score_0_to_5"], 5)
        self.assertTrue(all(result["checks"].values()))
        self.assertLessEqual(float(result["allocation"]["allocation_sar"]), 100)
        self.assertLessEqual(float(result["allocation"]["planned_loss_sar"]), 4.09)

    def test_each_missing_gate_prevents_entry(self):
        for args in [
            {"production": False},
            {"quote": None},
            {"research": None},
            {"bars": self.bars[:-1]},
            {"benchmark": self.bars[:-1]},
            {"capital": {"fx_sar_per_usd": None}},
            {"quote": {**self.quote, "price": 200}},
            {"quote": {**self.quote, "price": 1}},
        ]:
            self.assertEqual(self.evaluate(**args)["status"], "WAIT")
        stale = research()
        stale["retrieved_at_utc"] = "2026-10-01T00:00:00+00:00"
        self.assertEqual(self.evaluate(research=stale)["status"], "WAIT")

    def test_reports_stale_unavailable_failed_and_pr(self):
        report = {
            "origin": "private_production",
            "run_status": "completed",
            "completed_at_utc": NOW.isoformat(),
        }
        self.assertEqual(report_state(None, NOW), "UNAVAILABLE")
        self.assertEqual(report_state(report, NOW + timedelta(minutes=90)), "FRESH")
        self.assertEqual(report_state(report, NOW + timedelta(minutes=91)), "STALE")
        self.assertEqual(report_state({**report, "origin": "pull_request"}, NOW), "UNAVAILABLE")
        self.assertEqual(report_state({**report, "run_status": "failed"}, NOW), "UNAVAILABLE")

    def test_failed_scanner_is_not_success(self):
        result = scan(
            {"watchlist": ["TEST"]},
            production=True,
            history=lambda _: ([], None),
            now=NOW,
            calendar=self.calendar,
        )
        self.assertEqual(result["status"], "SCANNER_UNAVAILABLE")
        self.assertEqual(result["run_status"], "failed")
        self.assertTrue(all(s["status"] == "WAIT" for s in result["stocks"]))


class RiskLedgerTests(unittest.TestCase):
    def test_sizing_downward_precision_costs_and_missing_inputs(self):
        args = {
            "entry": 150,
            "invalidation": 145,
            "fx_sar_per_usd": 3.75,
            "confirmed_cash_sar": 80,
            "buying_power_sar": 60,
            "share_precision": 3,
            "fee_sar": 0.2,
            "spread_bps": 20,
            "slippage_bps": 30,
        }
        result = size_position(**args)
        self.assertTrue(result["eligible"])
        self.assertLessEqual(float(result["allocation_sar"]), 60)
        self.assertLessEqual(float(result["planned_loss_sar"]), 4.09)
        for key in [
            "confirmed_cash_sar",
            "buying_power_sar",
            "fx_sar_per_usd",
            "share_precision",
            "fee_sar",
        ]:
            self.assertFalse(size_position(**{**args, key: None})["eligible"])
        self.assertFalse(size_position(**{**args, "entry": float("nan")})["eligible"])

    def fill(self, side="buy", quantity="2", price="10", fees="1"):
        return validate_fill(
            {
                "ticker": "TEST",
                "side": side,
                "quantity": quantity,
                "price": price,
                "fees": fees,
                "currency": "USD",
                "executed_at_utc": "2026-01-01T12:00:00+00:00",
            }
        )

    def test_dedupe_canonical_values(self):
        self.assertEqual(fingerprint(self.fill(quantity="2.0")), fingerprint(self.fill()))
        self.assertEqual(
            fingerprint({**self.fill(), "reference": "changed"}), fingerprint(self.fill())
        )

    def test_realized_unrealized_and_reversal(self):
        events = [
            {"id": "a", "kind": "CONFIRMED", "payload": self.fill()},
            {"id": "b", "kind": "CONFIRMED", "payload": self.fill("sell", "1", "15", "1")},
        ]
        result = portfolio(events, {"TEST": "20"})[0]
        self.assertEqual(float(result["cost_basis_usd"]), 10.5)
        self.assertEqual(float(result["realized_pnl_usd"]), 3.5)
        self.assertEqual(float(result["unrealized_pnl_usd"]), 9.5)
        result = portfolio(
            events + [{"id": "c", "kind": "REVERSAL", "payload": {"reverses": "b"}}]
        )[0]
        self.assertEqual(float(result["quantity"]), 2)
        with self.assertRaises(ValueError):
            portfolio(events + [{"id": "c", "kind": "REVERSAL", "payload": {"reverses": "a"}}])

    def test_unknown_cost_basis_never_invented(self):
        events = [
            {
                "id": "a",
                "kind": "OPENING",
                "payload": {
                    "ticker": "TEST",
                    "quantity": "3",
                    "total_cost_usd": None,
                    "executed_at_utc": "2025-01-01T00:00:00+00:00",
                },
            }
        ]
        result = portfolio(events, {"TEST": "20"})[0]
        self.assertIsNone(result["cost_basis_usd"])
        self.assertIsNone(result["unrealized_pnl_usd"])
        self.assertEqual(float(result["market_value_usd"]), 60)
        events.append(
            {"id": "b", "kind": "CONFIRMED", "payload": self.fill("sell", "1", "15", "0")}
        )
        self.assertIsNone(portfolio(events)[0]["realized_pnl_usd"])

    def test_alert_format_no_private_capital(self):
        message = format_alert(
            "entry", NOW, {"ticker": "TEST", "status": "CONDITIONAL ENTRY REVIEW"}, test=True
        )
        self.assertIn("TEST —", message)
        self.assertIn("Awaed", message)
        self.assertIn("Saudi", message)
        self.assertEqual(signal_id("a", "b", "c"), signal_id("a", "b", "c"))


if __name__ == "__main__":
    unittest.main()
