"""株価を手元に貯めて見返すコマンド。"""

from __future__ import annotations

import argparse
import csv
import os
import sys
import time
import unicodedata
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from stockrec.intervals import INTERVALS, INTERVAL_NAMES, get_interval
from stockrec.store import Store
from stockrec.symbols import guess_timezone, normalize_symbol
from stockrec.sync import SyncResult, sync_many
from stockrec.yahoo import YahooClient, YahooError

CSV_HEADERS = ("銘柄", "足", "日時", "始値", "高値", "安値", "終値", "調整後終値", "出来高")


def default_db_path() -> Path:
    env = os.environ.get("STOCKREC_DB")
    if env:
        return Path(env)
    return Path.cwd() / "data" / "prices.db"


def main(argv: list[str] | None = None, client=None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    db_path = Path(args.db) if args.db else default_db_path()
    store = Store(db_path)
    try:
        return args.func(args, store, client or YahooClient())
    except (YahooError, ValueError) as exc:
        print(f"エラー: {exc}", file=sys.stderr)
        return 1
    finally:
        store.close()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stockrec",
        description=(
            "Yahooファイナンスから株価を取得し、手元のSQLiteに蓄積します。"
            "分足はYahooが直近しか返さないため、定期的に取得して自分の履歴を作ります。"
        ),
    )
    parser.add_argument(
        "--db",
        help="保存先のSQLiteファイル。省略時は ./data/prices.db（環境変数 STOCKREC_DB でも指定できます）",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="監視する銘柄を登録する")
    add.add_argument("symbols", nargs="+", help="例: 7203 9984 AAPL")
    add.set_defaults(func=cmd_add)

    remove = sub.add_parser("remove", help="監視をやめる")
    remove.add_argument("symbols", nargs="+")
    remove.add_argument("--purge", action="store_true", help="保存済みの価格データも削除する")
    remove.set_defaults(func=cmd_remove)

    listing = sub.add_parser("list", help="監視銘柄と、保存できている期間を表示する")
    listing.set_defaults(func=cmd_list)

    update = sub.add_parser("update", help="未保存の期間を取得して追記する")
    update.add_argument("symbols", nargs="*", help="省略すると登録済みの全銘柄")
    update.add_argument(
        "--interval",
        action="append",
        choices=INTERVAL_NAMES,
        dest="intervals",
        help="足の種類。繰り返すと複数取れる。省略時は日足（1d）",
    )
    update.set_defaults(func=cmd_update)

    show = sub.add_parser("show", help="保存済みの足を表示する")
    show.add_argument("symbol")
    show.add_argument("--interval", default="1d", choices=INTERVAL_NAMES)
    show.add_argument("--last", type=int, default=30, help="新しい方から何本表示するか")
    show.add_argument("--all", action="store_true", help="保存済みをすべて表示する")
    show.add_argument("--from", dest="start", help="開始日（YYYY-MM-DD）")
    show.add_argument("--to", dest="end", help="終了日（YYYY-MM-DD）")
    show.set_defaults(func=cmd_show)

    export = sub.add_parser("export", help="保存済みの足をCSVにする")
    export.add_argument("symbol", nargs="?")
    export.add_argument("--all", action="store_true", help="登録済みの全銘柄")
    export.add_argument("--interval", default="1d", choices=INTERVAL_NAMES)
    export.add_argument("--from", dest="start", help="開始日（YYYY-MM-DD）")
    export.add_argument("--to", dest="end", help="終了日（YYYY-MM-DD）")
    export.add_argument("-o", "--output", help="出力ファイル。省略すると標準出力")
    export.set_defaults(func=cmd_export)

    watch = sub.add_parser("watch", help="市場時間中に繰り返し取得する")
    watch.add_argument("symbols", nargs="*", help="省略すると登録済みの全銘柄")
    watch.add_argument("--interval", default="1m", choices=INTERVAL_NAMES)
    watch.add_argument("--every", type=int, default=60, help="何秒ごとに取得するか（最短10秒）")
    watch.set_defaults(func=cmd_watch)
    return parser


def cmd_add(args, store: Store, client) -> int:
    del client
    for raw in args.symbols:
        symbol = normalize_symbol(raw)
        already = store.get_symbol(symbol) is not None
        store.upsert_symbol(symbol, timezone_name=guess_timezone(symbol))
        if already:
            print(f"すでに登録済みです: {symbol}")
        else:
            print(f"登録しました: {symbol}")
    print("取得するには: python -m stockrec update")
    return 0


def cmd_remove(args, store: Store, client) -> int:
    del client
    code = 0
    for raw in args.symbols:
        symbol = normalize_symbol(raw)
        if not store.remove_symbol(symbol, purge=args.purge):
            print(f"登録されていません: {symbol}", file=sys.stderr)
            code = 1
            continue
        if args.purge:
            print(f"削除しました: {symbol}（価格データも削除しました）")
        else:
            print(f"監視を外しました: {symbol}（価格データは残しています）")
    return code


def cmd_list(args, store: Store, client) -> int:
    del args, client
    rows = store.symbols()
    print(f"データベース: {store.path}")
    if not rows:
        print("監視銘柄がありません。例: python -m stockrec add 7203 9984")
        return 0
    now = int(time.time())
    for row in rows:
        name = row["name"] or "（名称未取得）"
        currency = row["currency"] or ""
        print(f"{row['symbol']}  {name}  {currency}".rstrip())
        intervals = store.intervals_for(row["symbol"])
        if not intervals:
            print("  まだ価格がありません。python -m stockrec update で取得します")
            continue
        for interval in _sorted_intervals(intervals):
            count, oldest, newest = store.summary(row["symbol"], interval)
            span = f"{_format_ts(oldest, interval, row['timezone'])} .. {_format_ts(newest, interval, row['timezone'])}"
            note = _retention_note(interval, oldest, now)
            suffix = f"  ※{note}" if note else ""
            print(f"  {interval:<4} {count:>6}本  {span}{suffix}")
    return 0


def cmd_update(args, store: Store, client) -> int:
    symbols = _symbols_from_args(args, store)
    intervals = args.intervals or ["1d"]
    print(f"データベース: {store.path}")
    results = sync_many(store, client, symbols, intervals)
    return _print_results(results)


def cmd_show(args, store: Store, client) -> int:
    del client
    symbol = normalize_symbol(args.symbol)
    meta = store.get_symbol(symbol)
    timezone_name = (meta["timezone"] if meta else None) or guess_timezone(symbol) or "UTC"
    start, end = _range_bounds(args.start, args.end, timezone_name)
    limit = None if args.all else args.last
    if limit is not None and limit < 1:
        raise ValueError("--last は 1 以上にしてください")
    rows = store.bars(symbol, args.interval, start=start, end=end, limit=limit)
    if not rows:
        print(f"保存された足がありません: {symbol} {args.interval}", file=sys.stderr)
        return 1
    spec = get_interval(args.interval)
    print(f"{symbol}  {spec.label}  {len(rows)}本")
    print(
        f"{_pad('日時', 16)}  {_pad('始値', 10, 'right')}  {_pad('高値', 10, 'right')}  "
        f"{_pad('安値', 10, 'right')}  {_pad('終値', 10, 'right')}  {_pad('出来高', 14, 'right')}"
    )
    for row in rows:
        when = _format_ts(int(row["ts"]), args.interval, timezone_name)
        print(
            f"{_pad(when, 16)}  {_pad(_fmt_price(row['open']), 10, 'right')}  "
            f"{_pad(_fmt_price(row['high']), 10, 'right')}  {_pad(_fmt_price(row['low']), 10, 'right')}  "
            f"{_pad(_fmt_price(row['close']), 10, 'right')}  {_pad(_fmt_volume(row['volume']), 14, 'right')}"
        )
    return 0


def cmd_export(args, store: Store, client) -> int:
    del client
    if args.all:
        symbols = [row["symbol"] for row in store.symbols()]
        if not symbols:
            raise ValueError("監視銘柄がありません。先に add してください")
    elif args.symbol:
        symbols = [normalize_symbol(args.symbol)]
    else:
        raise ValueError("銘柄を指定するか、--all を付けてください")
    lines: list[list[str]] = []
    for symbol in symbols:
        meta = store.get_symbol(symbol)
        timezone_name = (meta["timezone"] if meta else None) or guess_timezone(symbol) or "UTC"
        start, end = _range_bounds(args.start, args.end, timezone_name)
        for row in store.bars(symbol, args.interval, start=start, end=end):
            moment = datetime.fromtimestamp(int(row["ts"]), _zone(timezone_name))
            lines.append(
                [
                    symbol,
                    args.interval,
                    moment.isoformat(timespec="minutes"),
                    _fmt_price(row["open"]),
                    _fmt_price(row["high"]),
                    _fmt_price(row["low"]),
                    _fmt_price(row["close"]),
                    _fmt_price(row["adj_close"]),
                    "" if row["volume"] is None else str(int(row["volume"])),
                ]
            )
    if args.output:
        path = Path(args.output)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8-sig", newline="") as handle:
            writer = csv.writer(handle, lineterminator="\r\n")
            writer.writerow(CSV_HEADERS)
            writer.writerows(lines)
        print(f"書き出しました: {path}（{len(lines)}本）")
        return 0
    writer = csv.writer(sys.stdout, lineterminator="\n")
    writer.writerow(CSV_HEADERS)
    writer.writerows(lines)
    return 0


def cmd_watch(args, store: Store, client) -> int:
    if args.every < 10:
        raise ValueError("--every は 10 秒以上にしてください")
    symbols = _symbols_from_args(args, store)
    print(f"データベース: {store.path}")
    print(f"{', '.join(symbols)} の{get_interval(args.interval).label}を {args.every} 秒ごとに保存します。止めるときは Ctrl+C")
    try:
        while True:
            started = time.monotonic()
            results = sync_many(store, client, symbols, [args.interval])
            _print_results(results)
            elapsed = time.monotonic() - started
            time.sleep(max(0.0, args.every - elapsed))
    except KeyboardInterrupt:
        print("\n停止しました")
        return 0


def _symbols_from_args(args, store: Store) -> list[str]:
    if args.symbols:
        symbols = []
        for raw in args.symbols:
            symbol = normalize_symbol(raw)
            if store.get_symbol(symbol) is None:
                store.upsert_symbol(symbol, timezone_name=guess_timezone(symbol))
            symbols.append(symbol)
        return symbols
    symbols = [row["symbol"] for row in store.symbols()]
    if not symbols:
        raise ValueError("監視銘柄がありません。例: python -m stockrec add 7203")
    return symbols


def _print_results(results: list[SyncResult]) -> int:
    failed = 0
    for result in results:
        spec = INTERVALS.get(result.interval)
        label = spec.label if spec else result.interval
        if result.error:
            failed += 1
            print(f"{result.symbol} {label}: 失敗 - {result.error}", file=sys.stderr)
            continue
        span = ""
        if result.oldest is not None and result.newest is not None:
            span = (
                f"（{_format_ts(result.oldest, result.interval, result.timezone)}"
                f" .. {_format_ts(result.newest, result.interval, result.timezone)}）"
            )
        print(
            f"{result.symbol} {label}: 取得 {result.fetched} 本 / 新規 {result.inserted} / "
            f"更新 {result.updated} / 保存合計 {result.total}{span}"
        )
        if result.gap:
            window = spec.yahoo_window_seconds if spec else None
            days = (window or 0) // 86400
            print(
                f"  注意: 前回の保存から間が空いています。Yahooの{label}は直近約{days}日しか返さないため、"
                "空いた期間は埋まりません。",
                file=sys.stderr,
            )
    return 1 if failed else 0


def _retention_note(interval: str, oldest: int | None, now: int) -> str | None:
    if oldest is None:
        return None
    spec = get_interval(interval)
    if spec.yahoo_window_seconds is None:
        return None
    if oldest < now - spec.yahoo_window_seconds - spec.bar_seconds:
        days = spec.yahoo_window_seconds // 86400
        return f"Yahooは直近約{days}日しか返さないため、それより前は手元の保存分です"
    return None


def _format_ts(ts: int | None, interval: str, timezone_name: str | None) -> str:
    if ts is None:
        return "-"
    moment = datetime.fromtimestamp(ts, _zone(timezone_name))
    if interval in {"1d", "1wk", "1mo"}:
        return moment.strftime("%Y-%m-%d")
    return moment.strftime("%Y-%m-%d %H:%M")


def _zone(timezone_name: str | None) -> ZoneInfo:
    try:
        return ZoneInfo(timezone_name or "UTC")
    except Exception:
        return ZoneInfo("UTC")


def _range_bounds(start: str | None, end: str | None, timezone_name: str) -> tuple[int | None, int | None]:
    return (
        _parse_user_time(start, timezone_name, end_of_day=False) if start else None,
        _parse_user_time(end, timezone_name, end_of_day=True) if end else None,
    )


def _parse_user_time(text: str, timezone_name: str, end_of_day: bool) -> int:
    raw = text.strip()
    parsed = None
    used = ""
    for fmt in ("%Y-%m-%d", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M"):
        try:
            parsed = datetime.strptime(raw, fmt)
            used = fmt
            break
        except ValueError:
            continue
    if parsed is None:
        raise ValueError(f"日付の形式が正しくありません: {text}（例: 2024-01-15）")
    if used == "%Y-%m-%d" and end_of_day:
        parsed = parsed + timedelta(days=1) - timedelta(seconds=1)
    return int(parsed.replace(tzinfo=_zone(timezone_name)).timestamp())


def _fmt_price(value) -> str:
    if value is None:
        return ""
    return f"{float(value):.4f}".rstrip("0").rstrip(".")


def _fmt_volume(value) -> str:
    if value is None:
        return ""
    return f"{int(value):,}"


def _sorted_intervals(names: list[str]) -> list[str]:
    order = {name: index for index, name in enumerate(INTERVAL_NAMES)}
    return sorted(names, key=lambda name: order.get(name, len(order)))


def _display_width(text: str) -> int:
    width = 0
    for char in text:
        width += 2 if unicodedata.east_asian_width(char) in {"F", "W"} else 1
    return width


def _pad(text: str, width: int, align: str = "left") -> str:
    gap = width - _display_width(text)
    if gap <= 0:
        return text
    if align == "right":
        return " " * gap + text
    return text + " " * gap
