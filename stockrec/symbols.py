"""銘柄コードの表記をYahooファイナンスのシンボルに揃える。"""

import re

_ALLOWED = re.compile(r"^[A-Z0-9.^=-]{1,32}$")


def normalize_symbol(raw: str) -> str:
    """ユーザー入力を保存用のシンボルにする。

    4桁の数字（7203）や、数字を含む4文字（285A）は東証コードとみなして .T を付ける。
    AAPL のような英字ティッカーや、7203.T / ^N225 / 0700.HK のように
    市場まで書かれたシンボルはそのまま大文字化する。
    """
    symbol = raw.strip().upper()
    if not symbol:
        raise ValueError("銘柄コードが空です")
    if re.fullmatch(r"\d{4}", symbol):
        symbol = f"{symbol}.T"
    elif re.fullmatch(r"[0-9A-Z]{4}", symbol) and any(ch.isdigit() for ch in symbol):
        symbol = f"{symbol}.T"
    if not _ALLOWED.fullmatch(symbol):
        raise ValueError(f"銘柄コードの形式が正しくありません: {raw}")
    return symbol


def guess_timezone(symbol: str) -> str | None:
    if symbol.endswith(".T"):
        return "Asia/Tokyo"
    return None
