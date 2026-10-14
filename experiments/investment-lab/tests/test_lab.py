import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
from importlib.util import find_spec
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lab import DailyBar, Quote, analyze, quote_is_fresh, run


class ScannerTests(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026, 10, 13, 15, 10, tzinfo=UTC)
        self.bars = [
            DailyBar(
                str(i), 10 + i * 0.12, 10.2 + i * 0.12, 9.9 + i * 0.12, 10.1 + i * 0.12, 2_000_000
            )
            for i in range(70)
        ]

    def test_stale_data_blocks_entries(self):
        stale = Quote(25, (self.now - timedelta(hours=3)).isoformat(), "test")
        self.assertFalse(quote_is_fresh(stale, self.now))
        result = analyze("HL", self.bars, stale, self.bars, 409, 0.01, 0.5, self.now)
        self.assertEqual(result["status"], "NO_TRADE")

    def test_no_quote_blocks_entries(self):
        result = analyze("HL", self.bars, None, self.bars, 409, 0.01, 0.5, self.now)
        self.assertEqual(result["status"], "NO_TRADE")

    def test_risk_never_exceeds_budget(self):
        q = Quote(100, self.now.isoformat(), "test")
        result = analyze("HL", self.bars, q, self.bars, 409, 0.01, 0.5, self.now)
        shares = result["whole_shares_within_risk"]
        self.assertLessEqual(
            shares * (result["technical_entry_above_usd"] - result["reference_stop_usd"]) * 3.75,
            4.10,
        )
        self.assertLessEqual(shares * result["technical_entry_above_usd"] * 3.75, 204.51)

    @unittest.skipUnless(
        find_spec("exchange_calendars"), "Run the dedicated Investment Lab dependency environment"
    )
    @patch("production.fetch_yahoo", return_value=([], None))
    def test_empty_private_holdings_do_not_get_invented(self, _):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(
                json.dumps(
                    {
                        "capital_sar": 409,
                        "aspirational_goal_sar": 50,
                        "risk_pct_per_trade": 0.01,
                        "max_position_pct": 0.5,
                        "watchlist": ["HL"],
                    }
                )
            )
            data = run(root / "config.json", root / "holdings.csv", root / "reports")
            self.assertEqual(data["positions"], [])
            self.assertEqual(data["stocks"][0]["status"], "WAIT")
            self.assertTrue((root / "reports/report.json").exists())

    def test_fractional_expensive_share_sizing_and_entry_review(self):
        bars = [
            DailyBar(
                str(i), 149.8 + i * 0.3, 150.2 + i * 0.3, 149.7 + i * 0.3, 150 + i * 0.3, 2_000_000
            )
            for i in range(70)
        ]
        at_quote = Quote(171.5, self.now.isoformat(), "test")
        result = analyze("UNH", bars, at_quote, bars, 409, 0.01, 100 / 409, self.now)
        self.assertEqual(result["score_0_to_5"], 5)
        self.assertEqual(result["whole_shares_within_risk"], 0)
        self.assertGreater(result["fractional_shares_within_risk"], 0)
        self.assertLessEqual(result["estimated_fractional_spend_sar"], 100.01)
        self.assertLessEqual(result["estimated_fractional_stop_risk_sar"], 4.10)
        self.assertLessEqual(result["quote_estimated_stop_risk_sar"], 4.10)
        self.assertLessEqual(result["quote_max_spend_sar"], 100.01)
        self.assertGreater(result["quote_risk_capped_fractional_shares"], 0)
        self.assertEqual(result["status"], "WATCH")

    def test_delayed_breakout_chase_does_not_trigger_entry(self):
        bars = [
            DailyBar(
                str(i), 149.8 + i * 0.3, 150.2 + i * 0.3, 149.7 + i * 0.3, 150 + i * 0.3, 2_000_000
            )
            for i in range(70)
        ]
        quote = Quote(185, self.now.isoformat(), "test")
        result = analyze("GS", bars, quote, bars, 409, 0.01, 100 / 409, self.now)
        self.assertEqual(result["status"], "WATCH")
        self.assertIn("chasing", result["reason"])


if __name__ == "__main__":
    unittest.main()
