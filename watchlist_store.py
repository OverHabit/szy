"""Local persistence for the configurable signal watchlist.

The database deliberately lives outside version control. It is the source of
truth for the local Streamlit page, so users can change a stock or strategy
from the page without editing a configuration file.
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import sqlite3
from typing import Any


DATABASE_PATH = Path(__file__).resolve().parent / ".data" / "watchlist.db"
STRATEGY_IDS = {"ma_crossover", "volume_breakout"}
INSTRUMENT_TYPES = {"stock", "etf"}

DEFAULT_PARAMETERS = {
    "adjust": "qfq",
    "short_window": 10,
    "long_window": 30,
    "breakout_window": 20,
    "volume_window": 20,
    "volume_multiple": 1.5,
    "exit_window": 10,
    "fee_bps": 3.0,
}


def _connect(database_path: Path | str | None = None) -> sqlite3.Connection:
    path = DATABASE_PATH if database_path is None else Path(database_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def initialise_watchlist(database_path: Path | str | None = None) -> None:
    """Create local tables and add the requested initial ETF configuration."""
    with _connect(database_path) as connection:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS watchlist_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol TEXT NOT NULL,
                name TEXT NOT NULL DEFAULT '',
                instrument_type TEXT NOT NULL DEFAULT 'stock',
                strategy_id TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                adjust TEXT NOT NULL DEFAULT 'qfq',
                short_window INTEGER NOT NULL DEFAULT 10,
                long_window INTEGER NOT NULL DEFAULT 30,
                breakout_window INTEGER NOT NULL DEFAULT 20,
                volume_window INTEGER NOT NULL DEFAULT 20,
                volume_multiple REAL NOT NULL DEFAULT 1.5,
                exit_window INTEGER NOT NULL DEFAULT 10,
                fee_bps REAL NOT NULL DEFAULT 3.0,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(symbol, strategy_id)
            );

            """
        )
        columns = {
            row["name"]
            for row in connection.execute("PRAGMA table_info(watchlist_entries)").fetchall()
        }
        if "instrument_type" not in columns:
            connection.execute(
                "ALTER TABLE watchlist_entries "
                "ADD COLUMN instrument_type TEXT NOT NULL DEFAULT 'stock'"
            )
        connection.execute(
            "UPDATE watchlist_entries SET instrument_type = 'etf' WHERE symbol = '588000'"
        )
        existing = connection.execute(
            "SELECT 1 FROM watchlist_entries WHERE symbol = ? AND strategy_id = ?",
            ("588000", "ma_crossover"),
        ).fetchone()
        if existing is None:
            now = _now()
            connection.execute(
                """
                INSERT INTO watchlist_entries (
                    symbol, name, instrument_type, strategy_id, enabled, adjust, short_window,
                    long_window, breakout_window, volume_window, volume_multiple,
                    exit_window, fee_bps, created_at, updated_at
                ) VALUES (?, ?, 'etf', ?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "588000",
                    "科创50ETF",
                    "ma_crossover",
                    DEFAULT_PARAMETERS["adjust"],
                    DEFAULT_PARAMETERS["short_window"],
                    DEFAULT_PARAMETERS["long_window"],
                    DEFAULT_PARAMETERS["breakout_window"],
                    DEFAULT_PARAMETERS["volume_window"],
                    DEFAULT_PARAMETERS["volume_multiple"],
                    DEFAULT_PARAMETERS["exit_window"],
                    DEFAULT_PARAMETERS["fee_bps"],
                    now,
                    now,
                ),
            )


def list_watchlist(
    database_path: Path | str | None = None, *, enabled_only: bool = False
) -> list[dict[str, Any]]:
    initialise_watchlist(database_path)
    query = "SELECT * FROM watchlist_entries"
    if enabled_only:
        query += " WHERE enabled = 1"
    query += " ORDER BY enabled DESC, symbol, strategy_id"
    with _connect(database_path) as connection:
        rows = connection.execute(query).fetchall()
    return [dict(row) for row in rows]


def save_watchlist_entry(
    entry: dict[str, Any], database_path: Path | str | None = None
) -> int:
    """Insert or update one symbol/strategy configuration after validation."""
    initialise_watchlist(database_path)
    values = _validated_entry(entry)
    now = _now()
    entry_id = entry.get("id")
    with _connect(database_path) as connection:
        if entry_id is None:
            cursor = connection.execute(
                """
                INSERT INTO watchlist_entries (
                    symbol, name, instrument_type, strategy_id, enabled, adjust, short_window,
                    long_window, breakout_window, volume_window, volume_multiple,
                    exit_window, fee_bps, created_at, updated_at
                ) VALUES (
                    :symbol, :name, :instrument_type, :strategy_id, :enabled, :adjust, :short_window,
                    :long_window, :breakout_window, :volume_window, :volume_multiple,
                    :exit_window, :fee_bps, :created_at, :updated_at
                )
                """,
                {**values, "created_at": now, "updated_at": now},
            )
            return int(cursor.lastrowid)
        connection.execute(
            """
            UPDATE watchlist_entries SET
                symbol = :symbol, name = :name, instrument_type = :instrument_type,
                strategy_id = :strategy_id,
                enabled = :enabled, adjust = :adjust, short_window = :short_window,
                long_window = :long_window, breakout_window = :breakout_window,
                volume_window = :volume_window, volume_multiple = :volume_multiple,
                exit_window = :exit_window, fee_bps = :fee_bps, updated_at = :updated_at
            WHERE id = :id
            """,
            {**values, "id": int(entry_id), "updated_at": now},
        )
    return int(entry_id)


def delete_watchlist_entry(
    entry_id: int, database_path: Path | str | None = None
) -> None:
    initialise_watchlist(database_path)
    with _connect(database_path) as connection:
        connection.execute("DELETE FROM watchlist_entries WHERE id = ?", (entry_id,))


def _validated_entry(entry: dict[str, Any]) -> dict[str, Any]:
    symbol = str(entry.get("symbol", "")).strip()
    if len(symbol) != 6 or not symbol.isdigit():
        raise ValueError("股票或 ETF 代码必须为 6 位数字，例如 588000")
    strategy_id = str(entry.get("strategy_id", ""))
    if strategy_id not in STRATEGY_IDS:
        raise ValueError("请选择支持的策略")
    values = {**DEFAULT_PARAMETERS, **entry}
    values["symbol"] = symbol
    values["name"] = str(values.get("name", "")).strip()
    values["instrument_type"] = str(values.get("instrument_type", "stock"))
    if values["instrument_type"] not in INSTRUMENT_TYPES:
        raise ValueError("请选择普通 A 股或 ETF")
    values["strategy_id"] = strategy_id
    values["enabled"] = int(bool(values.get("enabled", True)))
    values["adjust"] = str(values["adjust"])
    values["short_window"] = int(values["short_window"])
    values["long_window"] = int(values["long_window"])
    values["breakout_window"] = int(values["breakout_window"])
    values["volume_window"] = int(values["volume_window"])
    values["volume_multiple"] = float(values["volume_multiple"])
    values["exit_window"] = int(values["exit_window"])
    values["fee_bps"] = float(values["fee_bps"])
    if values["short_window"] < 2 or values["short_window"] >= values["long_window"]:
        raise ValueError("双均线的短期均线至少为 2 日且必须小于长期均线")
    if min(values["breakout_window"], values["volume_window"], values["exit_window"]) < 2:
        raise ValueError("放量突破的三个窗口均至少为 2 日")
    if values["volume_multiple"] <= 0:
        raise ValueError("放量倍数必须大于 0")
    if values["fee_bps"] < 0:
        raise ValueError("单边费用不能为负数")
    return {key: values[key] for key in (
        "symbol", "name", "instrument_type", "strategy_id", "enabled", "adjust", "short_window",
        "long_window", "breakout_window", "volume_window", "volume_multiple",
        "exit_window", "fee_bps",
    )}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")
