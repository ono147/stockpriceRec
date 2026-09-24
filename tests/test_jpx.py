import io
import unittest
import zipfile
from unittest import mock
from datetime import datetime
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from stockrec.cli import main
from stockrec.jpxarchive import merge_daily_bars, read_symbol_daily, update_market
from stockrec.jpxarchive import DailyBar
from stockrec.jpxlist import Listing, parse_listings, yahoo_symbol
from stockrec.store import Store
from stockrec.yahoo import Bar, Chart

JST = ZoneInfo("Asia/Tokyo")


def xlsx_bytes(rows: list[list[object]]) -> bytes:
    strings: list[str] = []
    index: dict[str, int] = {}

    def shared(value: str) -> int:
        if value not in index:
            index[value] = len(strings)
            strings.append(value)
        return index[value]

    sheet_rows: list[str] = []
    for row_number, row in enumerate(rows, start=1):
        cells: list[str] = []
        for column, value in enumerate(row):
            letter = chr(ord("A") + column)
            if isinstance(value, int):
                cells.append(f'<c r="{letter}{row_number}"><v>{value}</v></c>')
            else:
                cells.append(
                    f'<c r="{letter}{row_number}" t="s"><v>{shared(str(value))}</v></c>'
                )
        sheet_rows.append(f'<row r="{row_number}">{"".join(cells)}</row>')
    shared_xml = "".join(f"<si><t>{escape(value)}</t></si>" for value in strings)
    sheet = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"<sheetData>{''.join(sheet_rows)}</sheetData></worksheet>"
    )
    shared_doc = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        f"{shared_xml}</sst>"
    )
    content_types = """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>
<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>
<Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/>
</Types>"""
    rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>
</Relationships>"""
    workbook = """<?xml version="1.0" encoding="UTF-8"?>
<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">
<sheets><sheet name="Sheet1" sheetId="1" r:id="rId1"/></sheets>
</workbook>"""
    workbook_rels = """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>
</Relationships>"""
    blob = io.BytesIO()
    with zipfile.ZipFile(blob, "w") as book:
        book.writestr("[Content_Types].xml", content_types)
        book.writestr("_rels/.rels", rels)
        book.writestr("xl/workbook.xml", workbook)
        book.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        book.writestr("xl/worksheets/sheet1.xml", sheet)
        book.writestr("xl/sharedStrings.xml", shared_doc)
    return blob.getvalue()


def chart_for(symbol: str, day: datetime, close: float) -> Chart:
    ts = int(day.timestamp())
    bar = Bar(ts=ts, open=close, high=close + 1, low=close - 1, close=close, adj_close=None, volume=100)
    return Chart(
        symbol=symbol,
        name=symbol,
        currency="JPY",
        exchange="Tokyo",
        timezone="Asia/Tokyo",
        interval="1d",
        granularity="1d",
        first_trade=None,
        bars=(bar,),
    )


class ListingParseTest(unittest.TestCase):
    def test_reads_numeric_codes_preferred_shares_and_letter_codes(self):
        payload = xlsx_bytes(
            [
                ["日付", "コード", "銘柄名", "市場・商品区分"],
                [20260831, 1301, "極洋", "プライム（内国株式）"],
                ["20260831", "130A", "ベリタス", "グロース（内国株式）"],
                ["20260831", "25935", "伊藤園優先", "プライム（内国株式）"],
                ["20260831", "", "空", "プライム（内国株式）"],
            ]
        )
        listings = parse_listings(payload)
        self.assertEqual(
            [(item.symbol, item.name, item.as_of) for item in listings],
            [
                ("1301.T", "極洋", "2026-08-31"),
                ("130A.T", "ベリタス", "2026-08-31"),
                ("25935.T", "伊藤園優先", "2026-08-31"),
            ],
        )

    def test_yahoo_symbol_rejects_unknown_shapes(self):
        self.assertEqual(yahoo_symbol("7203"), "7203.T")
        self.assertIsNone(yahoo_symbol("AAPL"))
        self.assertIsNone(yahoo_symbol(""))


class ArchiveTest(unittest.TestCase):
    def test_merge_keeps_symbols_already_in_the_file(self):
        with TemporaryDirectory() as tmp:
            archive = Path(tmp)
            merge_daily_bars(
                archive,
                [DailyBar("2026-09-18", "1301.T", 1, 2, 1, 2, 10)],
            )
            merge_daily_bars(
                archive,
                [DailyBar("2026-09-18", "7203.T", 3, 4, 3, 9, 11)],
            )
            rows = read_symbol_daily(archive, "1301.T")
            self.assertEqual(rows[0]["close"], "2")
            self.assertEqual(read_symbol_daily(archive, "7203.T")[0]["close"], "9")

    def test_update_requests_recent_days_only_and_writes_sqlite(self):
        day = datetime(2026, 9, 18, tzinfo=JST)
        listing = Listing("7203", "7203.T", "トヨタ", "プライム（内国株式）", "2026-08-31")
        calls = []

        class Client:
            def fetch(self, symbol, interval, period1, period2, range_token=None):
                calls.append((symbol, interval, period1, period2, range_token))
                return chart_for(symbol, day, 3025)

        with TemporaryDirectory() as tmp:
            root = Path(tmp)
            store = Store(root / "prices.db")
            try:
                result = update_market(
                    [listing],
                    Client(),
                    root / "jpx",
                    store=store,
                    days=20,
                    workers=1,
                    now=int(day.timestamp()) + 86400,
                )
            finally:
                names = store.get_symbol("7203.T")
                bars = store.bars("7203.T", "1d")
                store.close()
        self.assertEqual(result.fetched, 1)
        self.assertEqual(result.failed, 0)
        self.assertGreater(calls[0][2], 0)
        self.assertIsNone(calls[0][4])
        self.assertEqual(names["name"], "トヨタ")
        self.assertEqual(bars[0]["close"], 3025)


class JpxCommandTest(unittest.TestCase):
    def test_command_uses_the_listing_and_writes_a_daily_file(self):
        day = datetime(2026, 9, 18, tzinfo=JST)
        listing = Listing("1301", "1301.T", "極洋", "プライム（内国株式）", "2026-08-31")

        class Client:
            def fetch(self, symbol, interval, period1, period2, range_token=None):
                del interval, period1, period2, range_token
                return chart_for(symbol, day, 4680)

        with TemporaryDirectory() as tmp:
            out = str(Path(tmp) / "jpx")
            db = str(Path(tmp) / "prices.db")
            with mock.patch("stockrec.cli.download_listings", return_value=[listing]):
                from io import StringIO
                from contextlib import redirect_stderr, redirect_stdout

                stdout, stderr = StringIO(), StringIO()
                with redirect_stdout(stdout), redirect_stderr(stderr):
                    code = main(
                        ["--db", db, "jpx", "--out", out, "--workers", "1", "--days", "5"],
                        client=Client(),
                    )
            self.assertEqual(code, 0, stderr.getvalue())
            text = Path(out, "daily", "2026-09-18.csv").read_text(encoding="utf-8")
            self.assertIn("1301.T", text)
            self.assertIn("4680", text)
            self.assertIn("極洋", Path(out, "listings.csv").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
