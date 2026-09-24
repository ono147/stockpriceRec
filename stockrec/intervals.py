"""足の種類と、Yahooへ問い合わせる期間の計算。"""

from dataclasses import dataclass


@dataclass(frozen=True)
class IntervalSpec:
    name: str
    bar_seconds: int
    # None は「期間の上限がなく、period1=0 で上場来を取る」という意味。
    yahoo_window_seconds: int | None
    # その足で Yahoo がまとめて返すときの range。日足で max を使うと月足に間引かれる。
    yahoo_range: str | None
    label: str


# 上限は chart API で確認した実測。range=7d の1分足は暦の7日より長く、約13日返る。
# 5分足の range=60d は約90日、1時間足の range=2y は約2年。
# 日足・週足・月足は period1=0 で上場来が返り、range=max は使わない。
INTERVALS: dict[str, IntervalSpec] = {
    "1m": IntervalSpec("1m", 60, 12 * 86400, "7d", "1分足"),
    "2m": IntervalSpec("2m", 120, 45 * 86400, "60d", "2分足"),
    "5m": IntervalSpec("5m", 300, 90 * 86400, "60d", "5分足"),
    "15m": IntervalSpec("15m", 900, 90 * 86400, "60d", "15分足"),
    "30m": IntervalSpec("30m", 1800, 90 * 86400, "60d", "30分足"),
    "60m": IntervalSpec("60m", 3600, 720 * 86400, "2y", "60分足"),
    "1h": IntervalSpec("1h", 3600, 720 * 86400, "2y", "1時間足"),
    "90m": IntervalSpec("90m", 5400, 90 * 86400, "60d", "90分足"),
    "1d": IntervalSpec("1d", 86400, None, None, "日足"),
    "1wk": IntervalSpec("1wk", 7 * 86400, None, None, "週足"),
    "1mo": IntervalSpec("1mo", 31 * 86400, None, None, "月足"),
}

INTERVAL_NAMES = tuple(INTERVALS)


@dataclass(frozen=True)
class Window:
    period1: int
    period2: int
    clamped: bool


def get_interval(name: str) -> IntervalSpec:
    try:
        return INTERVALS[name]
    except KeyError as exc:
        known = ", ".join(INTERVAL_NAMES)
        raise ValueError(f"未対応の足です: {name}（使える足: {known}）") from exc


def plan_window(interval: str, last_ts: int | None, now: int) -> Window:
    """次に取るべき period1/period2 を決める。

    すでに保存済みの足があるときは、最後の足を1本分さかのぼって取り直す。
    これで、取引時間中の未確定の足をあとから確定値で上書きできる。

    分足のように Yahoo が直近しか返さない種類では、さかのぼりすぎると
    エラーになるため、上限より古い開始時刻は切り上げる。その場合 clamped=True。
    """
    spec = get_interval(interval)
    period2 = now + 60
    clamped = False
    if last_ts is None:
        if spec.yahoo_window_seconds is None:
            period1 = 0
        else:
            period1 = max(0, period2 - spec.yahoo_window_seconds)
    else:
        period1 = max(0, last_ts - spec.bar_seconds)
        if spec.yahoo_window_seconds is not None:
            earliest = period2 - spec.yahoo_window_seconds
            if period1 < earliest:
                period1 = earliest
                clamped = True
    if period1 >= period2:
        period1 = max(0, period2 - spec.bar_seconds)
    return Window(period1=period1, period2=period2, clamped=clamped)


def granularity_ok(requested: str, actual: str | None, has_bars: bool) -> bool:
    """range=max のように指定すると、Yahoo は日足のつもりが月足で返すことがある。"""
    if not has_bars or actual is None:
        return True
    aliases = {
        "1h": {"1h", "60m"},
        "60m": {"1h", "60m"},
    }
    return actual in aliases.get(requested, {requested})
