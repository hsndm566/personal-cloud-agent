import json
import sys
import tempfile
import unittest
from datetime import UTC, datetime, timedelta
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

    @patch("lab.fetch_yahoo", return_value=([], None))
    def test_empty_private_holdings_do_not_get_invented(self, _):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "config.json").write_text(
                json.dumps(
                    dict(
                        capital_sar=409,
                        aspirational_goal_sar=50,
                        risk_pct_per_trade=0.01,
                        max_position_pct=0.5,
                        watchlist=["HL"],
                    )
                )
            )
            data = run(root / "config.json", root / "holdings.csv", root / "reports")
            self.assertEqual(data["positions"], [])
            self.assertEqual(data["stocks"][0]["status"], "NO_TRADE")
            self.assertTrue((root / "reports/report.json").exists())


if __name__ == "__main__":
    unittest.main()
