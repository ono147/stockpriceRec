import csv
import io
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from stockrec.cli import main
from stockrec.yahoo import Bar, Chart

JST = ZoneInfo("Asia/Tokyo")


def chart_with(ts, close):
    bar = Bar(ts=ts, open=close, high=close + 5, low=close - 5, close=close, adj_close=close, volume=1500)
    return Chart(
        symbol="7203.T",
        name="Toyota Motor Corporation",
        currency="JPY",
        exchange="Tokyo",
        timezone="Asia/Tokyo",
        interval="1d",
        granularity="1d",
        first_trade=None,
        bars=(bar,),
    )


class FakeClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = []

    def fetch(self, symbol, interval, period1, period2, range_token=None):
        self.calls.append((symbol, interval, period1, period2, range_token))
        return self.payload


class CliTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self.tmp.name) / "prices.db")

    def tearDown(self):
        self.tmp.cleanup()

    def run_cli(self, *args, client=None):
        stdout = io.StringIO()
        stderr = io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = main(["--db", self.db, *args], client=client)
        return code, stdout.getvalue(), stderr.getvalue()

    def test_usage_without_a_command(self):
        with self.assertRaises(SystemExit) as caught:
            main(["--db", self.db])
        self.assertEqual(caught.exception.code, 2)

    def test_add_update_show_export_round_trip(self):
        day = int(datetime(2026, 9, 18, tzinfo=JST).timestamp())
        code, out, _err = self.run_cli("add", "7203")
        self.assertEqual(code, 0)
        self.assertIn("7203.T", out)

        code, out, _err = self.run_cli("add", "7203")
        self.assertIn("すでに登録済みです", out)

        client = FakeClient(chart_with(day, 3025))
        code, out, err = self.run_cli("update", "--interval", "1d", client=client)
        self.assertEqual(code, 0, err)
        self.assertIn("新規 1", out)
        self.assertEqual(client.calls[0][0], "7203.T")
        self.assertEqual(client.calls[0][2], 0)

        code, out, err = self.run_cli("show", "7203", "--all")
        self.assertEqual(code, 0, err)
        self.assertIn("2026-09-18", out)
        self.assertIn("3025", out)

        destination = str(Path(self.tmp.name) / "toyota.csv")
        code, out, err = self.run_cli("export", "7203", "-o", destination)
        self.assertEqual(code, 0, err)
        raw = Path(destination).read_bytes()
        self.assertTrue(raw.startswith(b"\xef\xbb\xbf"))
        with Path(destination).open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.reader(handle))
        self.assertEqual(rows[0][0], "銘柄")
        self.assertEqual(rows[1][0], "7203.T")
        self.assertEqual(rows[1][2][:10], "2026-09-18")
        self.assertEqual(rows[1][6], "3025")

        code, out, err = self.run_cli("list")
        self.assertEqual(code, 0, err)
        self.assertIn("7203.T", out)
        self.assertIn("1d", out)

    def test_remove_without_purge_leaves_history(self):
        day = int(datetime(2026, 9, 18, tzinfo=JST).timestamp())
        self.run_cli("update", "7203", client=FakeClient(chart_with(day, 100)))
        code, out, err = self.run_cli("remove", "7203")
        self.assertEqual(code, 0, err)
        self.assertIn("価格データは残しています", out)
        code, out, err = self.run_cli("show", "7203", "--all")
        self.assertEqual(code, 0, err)
        self.assertIn("100", out)

    def test_watch_rejects_a_too_short_interval(self):
        code, _out, err = self.run_cli("watch", "--every", "1")
        self.assertEqual(code, 1)
        self.assertIn("10", err)

    def test_show_reports_when_nothing_is_stored(self):
        code, _out, err = self.run_cli("show", "AAPL")
        self.assertEqual(code, 1)
        self.assertIn("保存された足がありません", err)


if __name__ == "__main__":
    unittest.main()
