"""監視銘柄の足を取得し、手元のデータベースへ重ねて保存する。"""

from __future__ import annotations

import time
from dataclasses import dataclass

from stockrec.intervals import get_interval, plan_window
from stockrec.store import Store
from stockrec.yahoo import YahooError


@dataclass(frozen=True)
class SyncResult:
    symbol: str
    interval: str
    fetched: int = 0
    inserted: int = 0
    updated: int = 0
    total: int = 0
    oldest: int | None = None
    newest: int | None = None
    timezone: str | None = None
    gap: bool = False
    error: str | None = None


def sync_symbol(store: Store, client, symbol: str, interval: str, now: int | None = None) -> SyncResult:
    spec = get_interval(interval)
    moment = int(time.time()) if now is None else now
    previous = store.latest_ts(symbol, interval)
    window = plan_window(interval, previous, moment)
    # 初回と、上限を超えて空いたあとは range 指定で Yahoo が返す最大幅を取る。
    # 続きの取得は period で最後の足だけを取り直す。
    range_token = spec.yahoo_range if previous is None or window.clamped else None
    chart = client.fetch(
        symbol,
        spec.name,
        window.period1,
        window.period2,
        range_token=range_token,
    )
    store.upsert_symbol(
        symbol,
        name=chart.name,
        currency=chart.currency,
        exchange=chart.exchange,
        timezone_name=chart.timezone,
    )
    inserted, updated = store.upsert_bars(symbol, interval, chart.bars)
    total, oldest, newest = store.summary(symbol, interval)
    gap = False
    if window.clamped and previous is not None and chart.bars:
        gap = chart.bars[0].ts > previous + spec.bar_seconds
    return SyncResult(
        symbol=symbol,
        interval=interval,
        fetched=len(chart.bars),
        inserted=inserted,
        updated=updated,
        total=total,
        oldest=oldest,
        newest=newest,
        timezone=chart.timezone,
        gap=gap,
    )


def sync_many(
    store: Store,
    client,
    symbols: list[str],
    intervals: list[str],
    now: int | None = None,
    sleeper=None,
) -> list[SyncResult]:
    sleep = sleeper or time.sleep
    results: list[SyncResult] = []
    first = True
    for symbol in symbols:
        for interval in intervals:
            if not first:
                sleep(0.4)
            first = False
            try:
                results.append(sync_symbol(store, client, symbol, interval, now=now))
            except (YahooError, ValueError) as exc:
                results.append(SyncResult(symbol=symbol, interval=interval, error=str(exc)))
    return results
