from pathlib import Path

from watchlist_store import list_watchlist, save_watchlist_entry


def test_new_database_contains_requested_588000_configuration(tmp_path: Path):
    entries = list_watchlist(tmp_path / "watchlist.db")

    assert len(entries) == 1
    assert entries[0]["symbol"] == "588000"
    assert entries[0]["name"] == "科创50ETF"
    assert entries[0]["strategy_id"] == "ma_crossover"
    assert entries[0]["enabled"] == 1


def test_watchlist_can_be_changed_without_a_json_file(tmp_path: Path):
    database_path = tmp_path / "watchlist.db"
    entry_id = save_watchlist_entry(
        {
            "symbol": "600519",
            "name": "贵州茅台",
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
