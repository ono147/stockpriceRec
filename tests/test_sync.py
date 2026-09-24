import tempfile
import unittest
from pathlib import Path

from stockrec.store import Store
from stockrec.sync import sync_many, sync_symbol
from stockrec.yahoo import Bar, Chart, YahooError


def chart(interval, bars, timezone="Asia/Tokyo"):
    return Chart(
        symbol="7203.T",
        name="Toyota Motor Corporation",
        currency="JPY",
        exchange="Tokyo",
        timezone=timezone,
        interval=interval,
        granularity=interval,
        first_trade=None,
        bars=tuple(bars),
    )


def bar(ts, close):
    return Bar(ts=ts, open=close, high=close, low=close, close=close, adj_close=None, volume=5)


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def fetch(self, symbol, interval, period1, period2, range_token=None):
        self.calls.append((symbol, interval, period1, period2, range_token))
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload


class SyncTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = Store(Path(self.tmp.name) / "prices.db")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_first_minute_fetch_uses_the_widest_yahoo_range(self):
        now = 1_700_000_000
        client = FakeClient(chart("1m", [bar(now - 60, 100)]))
        sync_symbol(self.store, client, "7203.T", "1m", now=now)
        self.assertEqual(client.calls[0][4], "7d")

    def test_recent_minute_fetch_only_refreshes_the_tail(self):
        now = 1_700_000_000
        self.store.upsert_symbol("7203.T", timezone_name="Asia/Tokyo")
        self.store.upsert_bars("7203.T", "1m", [bar(now - 3600, 100)])
        client = FakeClient(chart("1m", [bar(now - 3600, 101), bar(now - 60, 102)]))
        sync_symbol(self.store, client, "7203.T", "1m", now=now)
        self.assertIsNone(client.calls[0][4])
        self.assertEqual(client.calls[0][2], now - 3600 - 60)

    def test_second_update_only_asks_for_the_new_tail(self):
        day = 1_700_000_000
        client = FakeClient(chart("1d", [bar(day, 3000), bar(day + 86400, 3010)]))
        first = sync_symbol(self.store, client, "7203.T", "1d", now=day + 86400)
        self.assertEqual(first.inserted, 2)
        self.assertEqual(client.calls[0][2], 0)
        second = sync_symbol(self.store, client, "7203.T", "1d", now=day + 2 * 86400)
        self.assertEqual(second.inserted, 0)
        self.assertEqual(second.updated, 2)
        self.assertEqual(client.calls[1][2], day + 86400 - 86400)
        self.assertFalse(second.gap)
        self.assertEqual(self.store.get_symbol("7203.T")["name"], "Toyota Motor Corporation")

    def test_minute_gap_is_reported_when_the_archive_was_left_too_long(self):
        now = 1_700_000_000
        self.store.upsert_symbol("7203.T", timezone_name="Asia/Tokyo")
        self.store.upsert_bars("7203.T", "1m", [bar(now - 30 * 86400, 100)])
        client = FakeClient(chart("1m", [bar(now - 3600, 110)]))
        result = sync_symbol(self.store, client, "7203.T", "1m", now=now)
        self.assertTrue(result.gap)
        self.assertEqual(result.total, 2)

    def test_one_symbol_failing_does_not_stop_the_next(self):
        client = FakeClient(YahooError("銘柄が見つかりません"))
        results = sync_many(
            self.store,
            client,
            ["7203.T", "AAPL"],
            ["1d"],
            now=1_700_000_000,
            sleeper=lambda _: None,
        )
        self.assertEqual([result.error for result in results], ["銘柄が見つかりません", "銘柄が見つかりません"])


if __name__ == "__main__":
    unittest.main()
