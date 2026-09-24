"""東証全銘柄の日足を、日付ごとのCSVとSQLiteへ保存する。"""

from __future__ import annotations

import csv
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from stockrec.jpxlist import Listing
from stockrec.store import Store
from stockrec.yahoo import YahooError

JST = ZoneInfo("Asia/Tokyo")
DAILY_FIELDS = ("symbol", "open", "high", "low", "close", "volume")
LISTING_FIELDS = ("code", "symbol", "name", "market", "as_of")


@dataclass(frozen=True)
class DailyBar:
    date: str
    symbol: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: int | None


@dataclass(frozen=True)
class JpxResult:
    listings: int
    fetched: int
    failed: int
    files: int
    errors: tuple[tuple[str, str], ...]


def default_archive_dir() -> Path:
    return Path.cwd() / "data" / "jpx"


def save_listings(archive: Path, listings: list[Listing]) -> Path:
    path = archive / "listings.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    _write_csv(path, LISTING_FIELDS, [_listing_row(item) for item in listings])
    return path


def load_listings(archive: Path) -> list[Listing]:
    path = archive / "listings.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as handle:
        return [
            Listing(
                code=row["code"],
                symbol=row["symbol"],
                name=row["name"],
                market=row["market"],
                as_of=row["as_of"],
            )
            for row in csv.DictReader(handle)
        ]


def update_market(
    listings: list[Listing],
    client,
    archive: Path,
    store: Store | None = None,
    days: int = 20,
    workers: int = 4,
    now: int | None = None,
) -> JpxResult:
    if days < 1:
        raise ValueError("--days は 1 以上にしてください")
    if workers < 1:
        raise ValueError("--workers は 1 以上にしてください")
    moment = int(time.time()) if now is None else now
    period1 = moment - days * 86400
    period2 = moment + 60
    total = len(listings)
    successes: list[tuple[Listing, object]] = []
    errors: list[tuple[str, str]] = []

    def fetch_one(listing: Listing):
        try:
            chart = client.fetch(listing.symbol, "1d", period1, period2)
            return listing, chart, None
        except (YahooError, ValueError) as exc:
            return listing, None, str(exc)

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = [pool.submit(fetch_one, listing) for listing in listings]
        for done, future in enumerate(as_completed(futures), start=1):
            listing, chart, error = future.result()
            if error is None and chart is not None:
                successes.append((listing, chart))
            else:
                errors.append((listing.symbol, error or "取得に失敗しました"))
            if done == total or done % 200 == 0:
                print(f"取得中 {done}/{total}", file=sys.stderr)

    bars: list[DailyBar] = []
    for listing, chart in successes:
        bars.extend(_daily_bars(listing.symbol, chart.bars))
        if store is not None:
            store.upsert_symbol(
                listing.symbol,
                name=listing.name,
                currency="JPY",
                exchange=listing.market,
                timezone_name="Asia/Tokyo",
            )
            store.upsert_bars(listing.symbol, "1d", chart.bars)
    files = merge_daily_bars(archive, bars)
    return JpxResult(
        listings=total,
        fetched=len(successes),
        failed=len(errors),
        files=len(files),
        errors=tuple(errors),
    )


def merge_daily_bars(archive: Path, bars: list[DailyBar]) -> list[Path]:
    grouped: dict[str, dict[str, DailyBar]] = {}
    for bar in bars:
        grouped.setdefault(bar.date, {})[bar.symbol] = bar
    written: list[Path] = []
    for date in sorted(grouped):
        path = archive / "daily" / f"{date}.csv"
        existing = _read_daily(path)
        for symbol, bar in grouped[date].items():
            existing[symbol] = bar
        ordered = [existing[symbol] for symbol in sorted(existing)]
        path.parent.mkdir(parents=True, exist_ok=True)
        _write_csv(path, DAILY_FIELDS, [_daily_row(bar) for bar in ordered])
        written.append(path)
    return written


def latest_daily_path(archive: Path) -> Path | None:
    daily = archive / "daily"
    if not daily.exists():
        return None
    files = sorted(daily.glob("*.csv"))
    if not files:
        return None
    return files[-1]


def count_data_rows(path: Path) -> int:
    with path.open(encoding="utf-8", newline="") as handle:
        return max(0, sum(1 for _ in handle) - 1)


def read_symbol_daily(archive: Path, symbol: str) -> list[dict[str, str]]:
    daily = archive / "daily"
    if not daily.exists():
        return []
    found: list[dict[str, str]] = []
    for path in sorted(daily.glob("*.csv")):
        with path.open(encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                if row.get("symbol") == symbol:
                    found.append({"date": path.stem, **row})
                    break
    return found


def _daily_bars(symbol: str, bars) -> list[DailyBar]:
    rows: list[DailyBar] = []
    for bar in bars:
        if bar.close is None:
            continue
        rows.append(
            DailyBar(
                date=datetime.fromtimestamp(bar.ts, JST).strftime("%Y-%m-%d"),
                symbol=symbol,
                open=bar.open,
                high=bar.high,
                low=bar.low,
                close=bar.close,
                volume=bar.volume,
            )
        )
    return rows


def _read_daily(path: Path) -> dict[str, DailyBar]:
    if not path.exists():
        return {}
    found: dict[str, DailyBar] = {}
    with path.open(encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            symbol = row.get("symbol") or ""
            if not symbol:
                continue
            found[symbol] = DailyBar(
                date=path.stem,
                symbol=symbol,
                open=_optional_float(row.get("open")),
                high=_optional_float(row.get("high")),
                low=_optional_float(row.get("low")),
                close=_optional_float(row.get("close")),
                volume=_optional_int(row.get("volume")),
            )
    return found


def _listing_row(item: Listing) -> dict[str, str]:
    return {
        "code": item.code,
        "symbol": item.symbol,
        "name": item.name,
        "market": item.market,
        "as_of": item.as_of,
    }


def _daily_row(bar: DailyBar) -> dict[str, str]:
    return {
        "symbol": bar.symbol,
        "open": _format_number(bar.open),
        "high": _format_number(bar.high),
        "low": _format_number(bar.low),
        "close": _format_number(bar.close),
        "volume": "" if bar.volume is None else str(bar.volume),
    }


def _write_csv(path: Path, fields: tuple[str, ...], rows: list[dict[str, str]]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _format_number(value: float | None) -> str:
    if value is None:
        return ""
    return f"{float(value):.4f}".rstrip("0").rstrip(".")


def _optional_float(value: str | None) -> float | None:
    if value is None or value == "":
        return None
    return float(value)


def _optional_int(value: str | None) -> int | None:
    if value is None or value == "":
        return None
    return int(float(value))
