"""Gitに残す監視銘柄リスト。1行に1銘柄。"""

from __future__ import annotations

import os
from pathlib import Path

from stockrec.symbols import normalize_symbol


def resolve_watchlist(explicit: str | None = None) -> Path:
    if explicit:
        return Path(explicit)
    env = os.environ.get("STOCKREC_WATCHLIST")
    if env:
        return Path(env)
    return Path.cwd() / "watchlist.txt"


def read_watchlist(path: Path) -> list[str]:
    if not path.exists():
        return []
    symbols: list[str] = []
    seen: set[str] = set()
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        body = raw_line.split("#", 1)[0].strip()
        if not body:
            continue
        symbol = normalize_symbol(body)
        if symbol in seen:
            continue
        seen.add(symbol)
        symbols.append(symbol)
    return symbols


def ensure_in_watchlist(path: Path, symbol: str) -> bool:
    """銘柄が無ければ末尾に足す。ファイルを変えたとき True。"""
    if symbol in read_watchlist(path):
        return False
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = ""
    if path.exists() and path.stat().st_size > 0:
        if not path.read_bytes().endswith(b"\n"):
            prefix = "\n"
    with path.open("a", encoding="utf-8") as handle:
        handle.write(f"{prefix}{symbol}\n")
    return True


def remove_from_watchlist(path: Path, symbol: str) -> bool:
    if not path.exists():
        return False
    kept: list[str] = []
    removed = False
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        body = raw_line.split("#", 1)[0].strip()
        if body and normalize_symbol(body) == symbol:
            removed = True
            continue
        kept.append(raw_line)
    if not removed:
        return False
    text = "\n".join(kept)
    if text:
        text += "\n"
    path.write_text(text, encoding="utf-8")
    return True
