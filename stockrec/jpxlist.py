"""JPXが公表する上場銘柄一覧を読む。"""

from __future__ import annotations

import re
import urllib.request
import zipfile
from dataclasses import dataclass
from io import BytesIO
from xml.etree import ElementTree as ET

from stockrec.yahoo import USER_AGENT

JPX_LIST_URL = "https://www.jpx.co.jp/markets/statistics-equities/misc/tvdivq0000001vg2-att/data_j.xlsx"
_NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"


@dataclass(frozen=True)
class Listing:
    code: str
    symbol: str
    name: str
    market: str
    as_of: str


def download_listings(timeout: int = 60) -> list[Listing]:
    request = urllib.request.Request(JPX_LIST_URL, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        payload = response.read()
    return parse_listings(payload)


def parse_listings(payload: bytes) -> list[Listing]:
    rows = _read_sheet_rows(payload)
    if not rows:
        raise ValueError("JPXの上場一覧が空です")
    header = rows[0]
    columns = {name: index for index, name in enumerate(header)}
    for required in ("コード", "銘柄名", "市場・商品区分"):
        if required not in columns:
            raise ValueError(f"JPXの上場一覧に「{required}」列がありません")
    date_index = columns.get("日付")
    listings: list[Listing] = []
    seen: set[str] = set()
    for row in rows[1:]:
        code = _cell(row, columns["コード"])
        symbol = yahoo_symbol(code)
        if symbol is None or symbol in seen:
            continue
        seen.add(symbol)
        as_of = _as_of(_cell(row, date_index) if date_index is not None else "")
        listings.append(
            Listing(
                code=code.strip().upper(),
                symbol=symbol,
                name=_cell(row, columns["銘柄名"]),
                market=_cell(row, columns["市場・商品区分"]),
                as_of=as_of,
            )
        )
    listings.sort(key=lambda item: item.symbol)
    if not listings:
        raise ValueError("JPXの上場一覧から銘柄を読み取れませんでした")
    return listings


def yahoo_symbol(code: str) -> str | None:
    """JPXのコードを Yahoo の東証シンボルにする。対応できないコードは None。"""
    text = code.strip().upper()
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".", 1)[0]
    if re.fullmatch(r"\d{4,5}", text):
        return f"{text}.T"
    if re.fullmatch(r"[0-9A-Z]{4}", text) and any(ch.isdigit() for ch in text):
        return f"{text}.T"
    return None


def _as_of(value: str) -> str:
    text = value.strip()
    if re.fullmatch(r"\d+\.0+", text):
        text = text.split(".", 1)[0]
    if re.fullmatch(r"\d{8}", text):
        return f"{text[:4]}-{text[4:6]}-{text[6:8]}"
    return text


def _cell(row: list[str], index: int) -> str:
    if index >= len(row):
        return ""
    return row[index]


def _read_sheet_rows(payload: bytes) -> list[list[str]]:
    with zipfile.ZipFile(BytesIO(payload)) as book:
        shared = _shared_strings(book)
        sheet_name = _first_sheet_path(book)
        root = ET.fromstring(book.read(sheet_name))
    rows: list[list[str]] = []
    for row in root.findall(f"{_NS}sheetData/{_NS}row"):
        values: dict[int, str] = {}
        for cell in row.findall(f"{_NS}c"):
            ref = cell.get("r") or ""
            values[_column_index(ref)] = _cell_text(cell, shared)
        if not values:
            continue
        width = max(values) + 1
        rows.append([values.get(index, "") for index in range(width)])
    return rows


def _first_sheet_path(book: zipfile.ZipFile) -> str:
    for name in book.namelist():
        if name.startswith("xl/worksheets/sheet"):
            return name
    raise ValueError("JPXの上場一覧にシートがありません")


def _shared_strings(book: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in book.namelist():
        return []
    root = ET.fromstring(book.read("xl/sharedStrings.xml"))
    strings: list[str] = []
    for item in root.findall(f"{_NS}si"):
        strings.append("".join(node.text or "" for node in item.findall(f".//{_NS}t")))
    return strings


def _cell_text(cell: ET.Element, shared: list[str]) -> str:
    kind = cell.get("t")
    if kind == "inlineStr":
        return "".join(node.text or "" for node in cell.findall(f".//{_NS}t"))
    value = cell.find(f"{_NS}v")
    if value is None or value.text is None:
        return ""
    if kind == "s":
        return shared[int(value.text)]
    return value.text


def _column_index(ref: str) -> int:
    letters = "".join(ch for ch in ref if ch.isalpha())
    index = 0
    for char in letters:
        index = index * 26 + (ord(char.upper()) - 64)
    return index - 1
