import tempfile
import unittest
from pathlib import Path

from stockrec.store import Store
from stockrec.yahoo import Bar


def bar(ts, close):
    return Bar(ts=ts, open=close, high=close, low=close, close=close, adj_close=close, volume=10)


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "prices.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_upsert_replaces_the_same_bar_and_keeps_one_row(self):
        inserted, updated = self.store.upsert_bars("7203.T", "1d", [bar(100, 2000)])
        self.assertEqual((inserted, updated), (1, 0))
        inserted, updated = self.store.upsert_bars("7203.T", "1d", [bar(100, 2100), bar(200, 2200)])
        self.assertEqual((inserted, updated), (1, 1))
        rows = self.store.bars("7203.T", "1d")
        self.assertEqual([row["close"] for row in rows], [2100, 2200])
        count, oldest, newest = self.store.summary("7203.T", "1d")
        self.assertEqual((count, oldest, newest), (2, 100, 200))

    def test_remove_keeps_prices_unless_purged(self):
        self.store.upsert_symbol("7203.T", name="Toyota", timezone_name="Asia/Tokyo")
        self.store.upsert_bars("7203.T", "1d", [bar(100, 2000)])
        self.assertTrue(self.store.remove_symbol("7203.T"))
        self.assertIsNone(self.store.get_symbol("7203.T"))
        self.assertEqual(len(self.store.bars("7203.T", "1d")), 1)
        self.store.upsert_symbol("7203.T", timezone_name="Asia/Tokyo")
        self.assertTrue(self.store.remove_symbol("7203.T", purge=True))
        self.assertEqual(self.store.bars("7203.T", "1d"), [])

    def test_close_keeps_prices_in_the_main_file_without_the_wal(self):
        self.store.upsert_symbol("7203.T", timezone_name="Asia/Tokyo")
        self.store.upsert_bars("7203.T", "1d", [bar(100, 2000)])
        path = self.store.path
        self.store.close()
        for suffix in ("-wal", "-shm"):
            side = Path(str(path) + suffix)
            if side.exists():
                side.unlink()
        self.store = Store(path)
        self.assertEqual([row["close"] for row in self.store.bars("7203.T", "1d")], [2000])

    def test_limit_returns_the_newest_rows_in_time_order(self):
        self.store.upsert_bars("AAPL", "1d", [bar(1, 1), bar(2, 2), bar(3, 3)])
        rows = self.store.bars("AAPL", "1d", limit=2)
        self.assertEqual([row["ts"] for row in rows], [2, 3])


if __name__ == "__main__":
    unittest.main()
