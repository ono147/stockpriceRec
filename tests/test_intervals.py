import unittest

from stockrec.intervals import plan_window


class PlanWindowTest(unittest.TestCase):
    def test_daily_backfill_starts_at_the_beginning(self):
        window = plan_window("1d", None, 1_700_000_000)
        self.assertEqual(window.period1, 0)
        self.assertFalse(window.clamped)

    def test_minute_backfill_stays_inside_yahoo_window(self):
        now = 1_700_000_000
        window = plan_window("1m", None, now)
        self.assertEqual(window.period1, now + 60 - 12 * 86400)
        self.assertFalse(window.clamped)

    def test_incremental_fetch_refreshes_the_last_bar(self):
        last = 1_700_000_000
        window = plan_window("1d", last, last)
        self.assertEqual(window.period1, last - 86400)
        self.assertFalse(window.clamped)

    def test_stale_minute_history_cannot_fill_the_hole(self):
        now = 1_700_000_000
        last = now - 30 * 86400
        window = plan_window("1m", last, now)
        self.assertTrue(window.clamped)
        self.assertEqual(window.period1, now + 60 - 12 * 86400)

    def test_unknown_interval(self):
        with self.assertRaises(ValueError):
            plan_window("2d", None, 1_700_000_000)


if __name__ == "__main__":
    unittest.main()
