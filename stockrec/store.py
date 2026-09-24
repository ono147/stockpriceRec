"""株価の蓄積先。1つの SQLite ファイルに銘柄と足を保存する。"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from stockrec.yahoo import Bar


SCHEMA = """
CREATE TABLE IF NOT EXISTS symbols (
    symbol TEXT PRIMARY KEY,
    name TEXT,
    currency TEXT,
    exchange TEXT,
    timezone TEXT,
    added_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS bars (
    symbol TEXT NOT NULL,
    interval TEXT NOT NULL,
    ts INTEGER NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    adj_close REAL,
    volume INTEGER,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (symbol, interval, ts)
);
"""


def utc_now_text() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


class Store:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self.path)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA busy_timeout=5000")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        # WAL の内容を本体ファイルへ書き戻す。Git には本体だけを残す。
        try:
            self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            self._conn.close()

    def upsert_symbol(
        self,
        symbol: str,
        name: str | None = None,
        currency: str | None = None,
        exchange: str | None = None,
        timezone_name: str | None = None,
    ) -> None:
        existing = self.get_symbol(symbol)
        added_at = existing["added_at"] if existing else utc_now_text()
        if existing:
            name = name or existing["name"]
            currency = currency or existing["currency"]
            exchange = exchange or existing["exchange"]
            timezone_name = timezone_name or existing["timezone"]
        self._conn.execute(
            """
            INSERT INTO symbols (symbol, name, currency, exchange, timezone, added_at)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(symbol) DO UPDATE SET
                name=excluded.name,
                currency=excluded.currency,
                exchange=excluded.exchange,
                timezone=excluded.timezone
            """,
            (symbol, name, currency, exchange, timezone_name, added_at),
        )
        self._conn.commit()

    def get_symbol(self, symbol: str) -> sqlite3.Row | None:
        return self._conn.execute("SELECT * FROM symbols WHERE symbol = ?", (symbol,)).fetchone()

    def symbols(self) -> list[sqlite3.Row]:
        return list(self._conn.execute("SELECT * FROM symbols ORDER BY symbol"))

    def remove_symbol(self, symbol: str, purge: bool = False) -> bool:
        row = self.get_symbol(symbol)
        if row is None:
            return False
        self._conn.execute("DELETE FROM symbols WHERE symbol = ?", (symbol,))
        if purge:
            self._conn.execute("DELETE FROM bars WHERE symbol = ?", (symbol,))
        self._conn.commit()
        return True

    def latest_ts(self, symbol: str, interval: str) -> int | None:
        row = self._conn.execute(
            "SELECT MAX(ts) AS ts FROM bars WHERE symbol = ? AND interval = ?",
            (symbol, interval),
        ).fetchone()
        if row is None or row["ts"] is None:
            return None
        return int(row["ts"])

    def upsert_bars(self, symbol: str, interval: str, bars: list[Bar] | tuple[Bar, ...]) -> tuple[int, int]:
        if not bars:
            return 0, 0
        ordered = sorted(bars, key=lambda bar: bar.ts)
        existing_rows = self._conn.execute(
            """
            SELECT ts FROM bars
            WHERE symbol = ? AND interval = ? AND ts BETWEEN ? AND ?
            """,
            (symbol, interval, ordered[0].ts, ordered[-1].ts),
        ).fetchall()
        existing = {int(row["ts"]) for row in existing_rows}
        incoming = {bar.ts for bar in ordered}
        inserted = len(incoming - existing)
        updated = len(incoming & existing)
        updated_at = utc_now_text()
        with self._conn:
            self._conn.executemany(
                """
                INSERT INTO bars (
                    symbol, interval, ts, open, high, low, close, adj_close, volume, updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(symbol, interval, ts) DO UPDATE SET
                    open=excluded.open,
                    high=excluded.high,
                    low=excluded.low,
                    close=excluded.close,
                    adj_close=excluded.adj_close,
                    volume=excluded.volume,
                    updated_at=excluded.updated_at
                """,
                [
                    (
                        symbol,
                        interval,
                        bar.ts,
                        bar.open,
                        bar.high,
                        bar.low,
                        bar.close,
                        bar.adj_close,
                        bar.volume,
                        updated_at,
                    )
                    for bar in ordered
                ],
            )
        return inserted, updated

    def summary(self, symbol: str, interval: str) -> tuple[int, int | None, int | None]:
        row = self._conn.execute(
            """
            SELECT COUNT(*) AS n, MIN(ts) AS oldest, MAX(ts) AS newest
            FROM bars WHERE symbol = ? AND interval = ?
            """,
            (symbol, interval),
        ).fetchone()
        oldest = None if row["oldest"] is None else int(row["oldest"])
        newest = None if row["newest"] is None else int(row["newest"])
        return int(row["n"]), oldest, newest

    def intervals_for(self, symbol: str) -> list[str]:
        rows = self._conn.execute(
            "SELECT DISTINCT interval FROM bars WHERE symbol = ? ORDER BY interval",
            (symbol,),
        ).fetchall()
        return [row["interval"] for row in rows]

    def bars(
        self,
        symbol: str,
        interval: str,
        start: int | None = None,
        end: int | None = None,
        limit: int | None = None,
    ) -> list[sqlite3.Row]:
        clauses = ["symbol = ?", "interval = ?"]
        params: list[object] = [symbol, interval]
        if start is not None:
            clauses.append("ts >= ?")
            params.append(start)
        if end is not None:
            clauses.append("ts <= ?")
            params.append(end)
        where = " AND ".join(clauses)
        if limit is not None:
            sql = f"""
                SELECT * FROM (
                    SELECT * FROM bars WHERE {where} ORDER BY ts DESC LIMIT ?
                ) ORDER BY ts ASC
            """
            params.append(limit)
        else:
            sql = f"SELECT * FROM bars WHERE {where} ORDER BY ts ASC"
        return list(self._conn.execute(sql, params))
