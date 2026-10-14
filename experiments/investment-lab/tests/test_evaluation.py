import sys
import unittest
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from evaluation import evaluate, metrics
from lab import DailyBar


def candles(n=100):
    return [
        DailyBar(
            str(date(2020, 1, 1) + timedelta(days=i)),
            100 + i * 0.2,
            100.1 + i * 0.2,
            99.9 + i * 0.2,
            100 + i * 0.2,
            1e6,
        )
        for i in range(n)
    ]


class EvaluationTests(unittest.TestCase):
    def test_future_never_passed_to_signal_and_nonoverlap(self):
        rows = candles()
        seen = []

        def predictor(ticker, history, benchmark):
            seen.append(history[-1].date)
            self.assertEqual(history, rows[: len(history)])
            self.assertEqual(benchmark, rows[: len(history)])
            return {"signal_date": history[-1].date, "trigger": history[-1].close + 0.2}

        with patch("evaluation.prediction", side_effect=predictor):
            r = evaluate("SYNTH", rows, rows)
        self.assertGreater(len(r["trades"]), 0)
        for a, b in zip(r["trades"], r["trades"][1:]):
            self.assertLess(a["exit_date"], b["entry_date"])
        for t in r["trades"]:
            self.assertLess(t["signal_date"], t["entry_date"])
        self.assertEqual(seen[0], rows[54].date)

    def test_costs_reduce_returns(self):
        rows = candles()

        def predict(_, history, __):
            return {"signal_date": history[-1].date, "trigger": history[-1].close + 0.2}

        with patch("evaluation.prediction", side_effect=predict):
            cheap = evaluate("SYNTH", rows, rows, 5)
            costly = evaluate("SYNTH", rows, rows, 30)
        self.assertEqual(cheap["metrics"]["sample_count"], costly["metrics"]["sample_count"])
        self.assertGreater(cheap["metrics"]["net_return"], costly["metrics"]["net_return"])

    def test_missing_session_rejected(self):
        rows = candles()
        with self.assertRaises(ValueError):
            evaluate("SYNTH", rows[:70] + rows[71:], rows)

    def test_daily_drawdown_and_undefined_statistics(self):
        m = metrics([], [1, 1.2, 0.9, 1])
        self.assertAlmostEqual(m["maximum_daily_drawdown"], -0.25)
        self.assertIsNone(m["win_rate"])
        self.assertIsNone(m["profit_factor"])
        self.assertEqual(metrics([0.1, -0.05], [1, 1.1, 1.045])["profit_factor"], 2)

    def test_future_changes_do_not_change_earlier_predictions(self):
        rows = candles(200)
        before = evaluate("SYNTH", rows, rows, fold_sessions=60)
        changed = rows[:150] + [
            DailyBar(b.date, b.open * 0.5, b.high * 0.5, b.low * 0.5, b.close * 0.5, b.volume)
            for b in rows[150:]
        ]
        after = evaluate("SYNTH", changed, rows, fold_sessions=60)
        cutoff = rows[140].date
        self.assertEqual(
            [s for s in before["signals"] if s["signal_date"] < cutoff],
            [s for s in after["signals"] if s["signal_date"] < cutoff],
        )
