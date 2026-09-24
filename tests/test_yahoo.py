import io
import json
import unittest
import urllib.error
import urllib.parse
from datetime import datetime
from unittest import mock
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from stockrec.yahoo import RangeTooLarge, YahooClient, YahooError, http_get_json, parse_chart

JST = ZoneInfo("Asia/Tokyo")


def payload(timestamps, closes, granularity="1d", timezone="Asia/Tokyo", first_trade=None, symbol="7203.T"):
    body = {
        "chart": {
            "result": [
                {
                    "meta": {
                        "currency": "JPY",
                        "symbol": symbol,
                        "exchangeName": "JPX",
                        "fullExchangeName": "Tokyo",
                        "exchangeTimezoneName": timezone,
                        "longName": "Toyota Motor Corporation",
                        "dataGranularity": granularity,
                    },
                    "timestamp": timestamps,
                    "indicators": {
                        "quote": [
                            {
                                "open": closes,
                                "high": closes,
                                "low": closes,
                                "close": closes,
                                "volume": [1000] * len(closes),
                            }
                        ],
                        "adjclose": [{"adjclose": closes}],
                    },
                }
            ],
            "error": None,
        }
    }
    if first_trade is not None:
        body["chart"]["result"][0]["meta"]["firstTradeDate"] = first_trade
    return body


def periods(url):
    query = parse_qs(urlparse(url).query)
    return int(query["period1"][0]), int(query["period2"][0])


class ParseChartTest(unittest.TestCase):
    def test_live_daily_bar_collapses_onto_the_session_date(self):
        morning = int(datetime(2026, 9, 24, 9, 0, tzinfo=JST).timestamp())
        later = int(datetime(2026, 9, 24, 11, 30, tzinfo=JST).timestamp())
        chart = parse_chart(payload([morning, later], [100.0, 110.0]), "1d", "7203.T")
        self.assertEqual(len(chart.bars), 1)
        self.assertEqual(chart.bars[0].close, 110.0)
        self.assertEqual(chart.bars[0].ts, int(datetime(2026, 9, 24, tzinfo=JST).timestamp()))
        self.assertEqual(chart.name, "Toyota Motor Corporation")
        self.assertEqual(chart.timezone, "Asia/Tokyo")

    def test_intraday_timestamps_stay_on_the_bar(self):
        start = int(datetime(2026, 9, 24, 9, 0, tzinfo=JST).timestamp())
        chart = parse_chart(payload([start, start + 60], [1.0, 2.0], granularity="1m"), "1m", "7203.T")
        self.assertEqual([bar.ts for bar in chart.bars], [start, start + 60])

    def test_missing_close_is_skipped(self):
        raw = payload([100, 160], [1.0, 2.0])
        raw["chart"]["result"][0]["indicators"]["quote"][0]["close"][0] = None
        chart = parse_chart(raw, "1m", "7203.T")
        self.assertEqual(len(chart.bars), 1)
        self.assertEqual(chart.bars[0].close, 2.0)

    def test_current_week_uses_monday(self):
        thursday = int(datetime(2026, 9, 24, 11, 30, tzinfo=JST).timestamp())
        chart = parse_chart(payload([thursday], [5.0], granularity="1wk"), "1wk", "7203.T")
        monday = int(datetime(2026, 9, 21, tzinfo=JST).timestamp())
        self.assertEqual(chart.bars[0].ts, monday)

    def test_error_payload(self):
        raw = {"chart": {"result": None, "error": {"code": "Not Found", "description": "No data found"}}}
        with self.assertRaises(YahooError) as caught:
            parse_chart(raw, "1d", "NOPE")
        self.assertIn("No data found", str(caught.exception))


class YahooClientTest(unittest.TestCase):
    def test_range_request_keeps_bars_older_than_a_plain_seven_day_period(self):
        now = 1_790_000_000
        older = now - 12 * 86400
        calls = []

        def transport(url):
            calls.append(url)
            if "range=7d" in url:
                return payload([older, now - 60], [1.0, 2.0], granularity="1m")
            raise AssertionError(url)

        chart = YahooClient(transport=transport, sleeper=lambda _: None).fetch(
            "7203.T",
            "1m",
            now - 7 * 86400,
            now,
            range_token="7d",
        )
        self.assertEqual(len(calls), 1)
        self.assertIn("range=7d", calls[0])
        self.assertNotIn("period1=", calls[0])
        self.assertEqual([bar.close for bar in chart.bars], [1.0, 2.0])

    def test_downsampled_range_falls_back_to_period(self):
        def transport(url):
            if "range=" in url:
                return payload([1_700_000_000], [9.0], granularity="1mo")
            period1, _period2 = periods(url)
            return payload([period1 + 60], [4.0], granularity="1m")

        chart = YahooClient(transport=transport, sleeper=lambda _: None).fetch(
            "7203.T",
            "1m",
            1_700_000_000,
            1_700_003_600,
            range_token="7d",
        )
        self.assertEqual([bar.close for bar in chart.bars], [4.0])

    def test_url_uses_period_not_downsampling_range(self):
        url = YahooClient._url("7203.T", "1d", 0, 100)
        self.assertNotIn("range=", url)
        self.assertIn("period1=0", url)
        self.assertIn("period2=100", url)
        self.assertIn(urllib.parse.quote("7203.T", safe=""), url)

    def test_bars_outside_the_requested_window_are_dropped(self):
        start = 1_700_000_000

        def transport(url):
            del url
            return payload([start + 60, start - 30 * 86400], [3.0, 9.0], granularity="1m")

        chart = YahooClient(transport=transport, sleeper=lambda _: None).fetch("7203.T", "1m", start, start + 3600)
        self.assertEqual([bar.close for bar in chart.bars], [3.0])

    def test_downsampled_response_is_split_until_the_interval_matches(self):
        calls = []

        def transport(url):
            period1, period2 = periods(url)
            calls.append(period2 - period1)
            granularity = "3mo" if period2 - period1 > 400 * 86400 else "1d"
            return payload([period1 + 86400], [1.0], granularity=granularity)

        end = 800 * 86400
        chart = YahooClient(transport=transport, sleeper=lambda _: None).fetch("7203.T", "1d", 0, end)
        self.assertGreater(len(calls), 1)
        self.assertTrue(all(span <= 400 * 86400 for span in calls[1:]))
        self.assertGreaterEqual(len(chart.bars), 1)
        self.assertTrue(all(bar.close == 1.0 for bar in chart.bars))

    def test_late_response_is_split_so_earlier_history_is_requested(self):
        calls = []
        start = 1_000_000_000
        end = start + 800 * 86400

        def transport(url):
            period1, period2 = periods(url)
            calls.append((period1, period2))
            if period2 - period1 > 500 * 86400:
                ts = period2 - 86400
            else:
                ts = period1 + 3600
            return payload([ts], [4.0], granularity="1h", first_trade=start)

        chart = YahooClient(transport=transport, sleeper=lambda _: None).fetch("AAPL", "1h", start, end)
        self.assertGreaterEqual(len(calls), 3)
        self.assertEqual(len(chart.bars), 2)
        self.assertLess(chart.bars[0].ts, start + 400 * 86400)

    def test_range_too_large_is_split(self):
        def transport(url):
            period1, period2 = periods(url)
            if period2 - period1 > 400 * 86400:
                raise RangeTooLarge("too big")
            return payload([period1 + 3600], [7.0], granularity="1h")

        chart = YahooClient(transport=transport, sleeper=lambda _: None).fetch(
            "AAPL", "1h", 1_000_000_000, 1_000_000_000 + 800 * 86400
        )
        self.assertGreaterEqual(len(chart.bars), 2)


class HttpGetJsonTest(unittest.TestCase):
    def test_unprocessable_range_is_not_retried(self):
        calls = []

        def fake_urlopen(request, timeout=30):
            del timeout
            calls.append(request.full_url)
            raise urllib.error.HTTPError(request.full_url, 422, "Unprocessable Entity", {}, io.BytesIO(b""))

        with mock.patch("stockrec.yahoo.urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(RangeTooLarge):
                http_get_json("https://example.test", sleeper=lambda _: None)
        self.assertEqual(len(calls), 1)

    def test_not_found(self):
        def fake_urlopen(request, timeout=30):
            del timeout
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b""))

        with mock.patch("stockrec.yahoo.urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(YahooError) as caught:
                http_get_json("https://example.test", sleeper=lambda _: None)
        self.assertIn("銘柄が見つかりません", str(caught.exception))

    def test_server_error_is_retried(self):
        calls = []

        def fake_urlopen(request, timeout=30):
            del timeout
            calls.append(1)
            if len(calls) < 3:
                raise urllib.error.HTTPError(request.full_url, 503, "unavailable", {}, io.BytesIO(b""))

            class Response:
                def read(self):
                    return json.dumps({"chart": {"result": [], "error": None}}).encode()

                def __enter__(self):
                    return self

                def __exit__(self, *args):
                    return False

            return Response()

        with mock.patch("stockrec.yahoo.urllib.request.urlopen", fake_urlopen):
            payload_body = http_get_json("https://example.test", sleeper=lambda _: None)
        self.assertEqual(len(calls), 3)
        self.assertIn("chart", payload_body)

    def test_network_failure_message(self):
        def fake_urlopen(request, timeout=30):
            del request, timeout
            raise urllib.error.URLError("offline")

        with mock.patch("stockrec.yahoo.urllib.request.urlopen", fake_urlopen):
            with self.assertRaises(YahooError) as caught:
                http_get_json("https://example.test", attempts=1, sleeper=lambda _: None)
        self.assertIn("ネットワーク", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
