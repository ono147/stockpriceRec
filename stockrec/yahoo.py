"""Yahooファイナンス chart API から OHLCV を取る。

公開の相場ページと同じ非公式エンドポイントを使う。個人の記録用であり、
Yahoo の仕様変更で取れなくなることがある。
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from stockrec.intervals import get_interval, granularity_ok

CHART_URL = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


class YahooError(Exception):
    """取得に失敗したとき。メッセージはそのまま利用者に見せられる。"""


class RangeTooLarge(YahooError):
    """指定した期間が長すぎて Yahoo が拒否したとき。分割して取り直す。"""


@dataclass(frozen=True)
class Bar:
    ts: int
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    adj_close: float | None
    volume: int | None


@dataclass(frozen=True)
class Chart:
    symbol: str
    name: str | None
    currency: str | None
    exchange: str | None
    timezone: str
    interval: str
    granularity: str | None
    first_trade: int | None
    bars: tuple[Bar, ...]


def canonical_timestamp(ts: int, interval: str, tz_name: str) -> int:
    """日足・週足・月足の「今ついている未確定の足」を、期間の先頭へ揃える。

    確定済みの日足は取引所の現地日付に固定されているが、当日の足だけ
    現在時刻が入る。時刻のまま保存すると、同じ日が何本にも分かれてしまう。
    """
    if interval not in {"1d", "1wk", "1mo"}:
        return ts
    try:
        tz = ZoneInfo(tz_name or "UTC")
    except Exception:
        tz = ZoneInfo("UTC")
    moment = datetime.fromtimestamp(ts, tz)
    if interval == "1d":
        start = moment.replace(hour=0, minute=0, second=0, microsecond=0)
    elif interval == "1wk":
        start = (moment - timedelta(days=moment.weekday())).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
    else:
        start = moment.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    return int(start.timestamp())


def _number(value) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _volume(value) -> int | None:
    number = _number(value)
    if number is None:
        return None
    return int(number)


def _at(values, index):
    if not values or index >= len(values):
        return None
    return values[index]


def parse_chart(payload: dict, interval: str, requested_symbol: str) -> Chart:
    chart = payload.get("chart") or {}
    error = chart.get("error")
    if error:
        description = error.get("description") or error.get("code") or "取得に失敗しました"
        raise YahooError(str(description))
    results = chart.get("result") or []
    if not results or results[0] is None:
        raise YahooError(f"データがありません: {requested_symbol}")
    result = results[0]
    meta = result.get("meta") or {}
    timezone = meta.get("exchangeTimezoneName") or "UTC"
    timestamps = result.get("timestamp") or []
    indicators = result.get("indicators") or {}
    quote = (indicators.get("quote") or [{}])[0] or {}
    adj_series = (indicators.get("adjclose") or [{}])[0] or {}
    adj_values = adj_series.get("adjclose")
    by_ts: dict[int, Bar] = {}
    for index, raw_ts in enumerate(timestamps):
        if raw_ts is None:
            continue
        close = _number(_at(quote.get("close"), index))
        if close is None:
            continue
        ts = canonical_timestamp(int(raw_ts), interval, timezone)
        by_ts[ts] = Bar(
            ts=ts,
            open=_number(_at(quote.get("open"), index)),
            high=_number(_at(quote.get("high"), index)),
            low=_number(_at(quote.get("low"), index)),
            close=close,
            adj_close=_number(_at(adj_values, index)),
            volume=_volume(_at(quote.get("volume"), index)),
        )
    bars = tuple(by_ts[ts] for ts in sorted(by_ts))
    name = meta.get("longName") or meta.get("shortName")
    return Chart(
        symbol=requested_symbol,
        name=name,
        currency=meta.get("currency"),
        exchange=meta.get("fullExchangeName") or meta.get("exchangeName"),
        timezone=timezone,
        interval=interval,
        granularity=meta.get("dataGranularity"),
        first_trade=_volume(meta.get("firstTradeDate")),
        bars=bars,
    )


def _clip_bars(chart: Chart, interval: str, period1: int, period2: int) -> Chart:
    """問い合わせた期間の外にある足は捨てる。

    期間を分割したとき、Yahoo が期間を無視して直近だけ返すと、
    関係ない足が別の期間に混ざる。
    """
    slack = get_interval(interval).bar_seconds
    lower = period1 - slack
    upper = period2 + slack
    bars = tuple(bar for bar in chart.bars if lower <= bar.ts <= upper)
    if bars == chart.bars:
        return chart
    return replace(chart, bars=bars)


def _looks_truncated(chart: Chart, period1: int, period2: int) -> bool:
    """長い期間なのに、最初の取引日よりずっと後からしか返っていない。

    1回の応答に入りきらず、Yahoo が先頭を落としているときに分割して取り直す。
    """
    if not chart.bars or chart.first_trade is None:
        return False
    if period2 - period1 <= 400 * 86400:
        return False
    asked_from = max(period1, chart.first_trade)
    oldest = chart.bars[0].ts
    return oldest > asked_from + 14 * 86400


def merge_charts(charts: list[Chart], requested_symbol: str, interval: str) -> Chart:
    if not charts:
        raise YahooError(f"データがありません: {requested_symbol}")
    by_ts: dict[int, Bar] = {}
    for chart in charts:
        for bar in chart.bars:
            by_ts[bar.ts] = bar
    latest = charts[-1]
    bars = tuple(by_ts[ts] for ts in sorted(by_ts))
    first_trade = next((chart.first_trade for chart in charts if chart.first_trade), None)
    return Chart(
        symbol=requested_symbol,
        name=latest.name,
        currency=latest.currency,
        exchange=latest.exchange,
        timezone=latest.timezone,
        interval=interval,
        granularity=latest.granularity,
        first_trade=first_trade,
        bars=bars,
    )


class YahooClient:
    def __init__(self, transport=None, sleeper=None):
        self._transport = transport or http_get_json
        self._sleep = sleeper or time.sleep

    def fetch(
        self,
        symbol: str,
        interval: str,
        period1: int,
        period2: int,
        depth: int = 0,
        range_token: str | None = None,
    ) -> Chart:
        get_interval(interval)
        if range_token and depth == 0:
            ranged = self._try_range(symbol, interval, range_token)
            if ranged is not None:
                return ranged
        try:
            payload = self._transport(self._url(symbol, interval, period1, period2))
            chart = parse_chart(payload, interval, symbol)
        except RangeTooLarge:
            return self._split(symbol, interval, period1, period2, depth)
        chart = _clip_bars(chart, interval, period1, period2)
        if not granularity_ok(interval, chart.granularity, bool(chart.bars)):
            return self._split(symbol, interval, period1, period2, depth)
        if _looks_truncated(chart, period1, period2):
            return self._split(symbol, interval, period1, period2, depth)
        return chart

    def _try_range(self, symbol: str, interval: str, range_token: str) -> Chart | None:
        """1分足などは range=7d の方が、同じ日数を period で頼むより長く返る。"""
        try:
            payload = self._transport(self._range_url(symbol, interval, range_token))
            chart = parse_chart(payload, interval, symbol)
        except RangeTooLarge:
            return None
        if not granularity_ok(interval, chart.granularity, bool(chart.bars)):
            return None
        return chart

    def _split(self, symbol: str, interval: str, period1: int, period2: int, depth: int) -> Chart:
        spec = get_interval(interval)
        span = period2 - period1
        if depth >= 8 or span <= spec.bar_seconds * 2:
            raise YahooError(
                f"{symbol} の{spec.label}は、この期間だとYahooが足を間引いて返すため保存しませんでした"
            )
        mid = period1 + span // 2
        self._sleep(0.25)
        left = self.fetch(symbol, interval, period1, mid, depth + 1)
        self._sleep(0.25)
        right = self.fetch(symbol, interval, mid, period2, depth + 1)
        return merge_charts([left, right], symbol, interval)

    @staticmethod
    def _url(symbol: str, interval: str, period1: int, period2: int) -> str:
        query = urllib.parse.urlencode(
            {
                "interval": interval,
                "period1": period1,
                "period2": period2,
                "includeAdjustedClose": "true",
            }
        )
        encoded = urllib.parse.quote(symbol, safe="")
        return f"{CHART_URL.format(symbol=encoded)}?{query}"

    @staticmethod
    def _range_url(symbol: str, interval: str, range_token: str) -> str:
        query = urllib.parse.urlencode(
            {
                "interval": interval,
                "range": range_token,
                "includeAdjustedClose": "true",
            }
        )
        encoded = urllib.parse.quote(symbol, safe="")
        return f"{CHART_URL.format(symbol=encoded)}?{query}"


def http_get_json(url: str, timeout: int = 30, attempts: int = 3, sleeper=None) -> dict:
    sleep = sleeper or time.sleep
    last_error: Exception | None = None
    for attempt in range(attempts):
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
            return _decode_json(body)
        except RangeTooLarge:
            raise
        except YahooError:
            raise
        except urllib.error.HTTPError as exc:
            if exc.code == 422:
                raise RangeTooLarge("指定した期間が長すぎます") from exc
            if exc.code == 404:
                raise YahooError("銘柄が見つかりません") from exc
            if exc.code == 429:
                last_error = YahooError("Yahooへのアクセスが多すぎます。しばらく待って再実行してください")
            elif 500 <= exc.code < 600:
                last_error = YahooError(f"Yahooが一時的に応答しませんでした（HTTP {exc.code}）")
            else:
                detail = _error_detail(exc)
                raise YahooError(detail) from exc
        except urllib.error.URLError as exc:
            last_error = YahooError("ネットワークに接続できません")
            last_error.__cause__ = exc
        except TimeoutError as exc:
            last_error = YahooError("Yahooからの応答がタイムアウトしました")
            last_error.__cause__ = exc
        if attempt + 1 < attempts:
            sleep(float(2**attempt))
    assert last_error is not None
    raise last_error


def _decode_json(body: bytes) -> dict:
    try:
        payload = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise YahooError("Yahooの応答を読み取れませんでした") from exc
    if not isinstance(payload, dict):
        raise YahooError("Yahooの応答を読み取れませんでした")
    return payload


def _error_detail(exc: urllib.error.HTTPError) -> str:
    try:
        payload = _decode_json(exc.read())
    except Exception:
        return f"Yahooからの取得に失敗しました（HTTP {exc.code}）"
    error = (payload.get("chart") or {}).get("error") or {}
    description = error.get("description") or error.get("code")
    if description:
        return str(description)
    return f"Yahooからの取得に失敗しました（HTTP {exc.code}）"
