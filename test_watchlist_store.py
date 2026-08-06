from pathlib import Path
import sqlite3

from watchlist_store import DEFAULT_WATCHLIST, list_watchlist, save_watchlist_entry


def test_new_database_contains_the_versioned_default_watchlist(tmp_path: Path):
    entries = list_watchlist(tmp_path / "watchlist.db")

    assert len(entries) == len(DEFAULT_WATCHLIST)
    etf = next(entry for entry in entries if entry["symbol"] == "588000")
    assert etf["name"] == "科创50ETF"
    assert etf["instrument_type"] == "etf"
    assert all(entry["strategy_id"] == "ma_crossover" for entry in entries)
    assert all(entry["enabled"] == 1 for entry in entries)


def test_watchlist_can_be_changed_without_a_json_file(tmp_path: Path):
    database_path = tmp_path / "watchlist.db"
    entry_id = save_watchlist_entry(
        {
            "symbol": "600519",
            "name": "贵州茅台",
            "instrument_type": "stock",
            "strategy_id": "volume_breakout",
            "enabled": True,
            "breakout_window": 30,
            "volume_window": 20,
            "volume_multiple": 2.0,
            "exit_window": 12,
        },
        database_path,
    )
    entries = list_watchlist(database_path)

    added = next(entry for entry in entries if entry["id"] == entry_id)
    assert added["strategy_id"] == "volume_breakout"
    assert added["volume_multiple"] == 2.0

    added["enabled"] = False
    save_watchlist_entry(added, database_path)
    assert not next(entry for entry in list_watchlist(database_path) if entry["id"] == entry_id)["enabled"]


def test_existing_588000_configuration_is_migrated_to_etf(tmp_path: Path):
    database_path = tmp_path / "watchlist.db"
    with sqlite3.connect(database_path) as connection:
        connection.execute(
            """
            CREATE TABLE watchlist_entries (
                id INTEGER PRIMARY KEY, symbol TEXT, name TEXT, strategy_id TEXT,
                enabled INTEGER, adjust TEXT, short_window INTEGER, long_window INTEGER,
                breakout_window INTEGER, volume_window INTEGER, volume_multiple REAL,
                exit_window INTEGER, fee_bps REAL, created_at TEXT, updated_at TEXT
            )
            """
        )
        connection.execute(
            "INSERT INTO watchlist_entries VALUES (1, '588000', '科创50ETF', "
            "'ma_crossover', 1, 'qfq', 10, 30, 20, 20, 1.5, 10, 3, 'x', 'x')"
        )

    entry = next(row for row in list_watchlist(database_path) if row["id"] == 1)
    assert entry["instrument_type"] == "etf"
