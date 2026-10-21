"""Exercise the app entry point without allowing initial market-provider imports."""
from pathlib import Path
import subprocess
import sys
import textwrap


def test_initial_pages_are_lightweight_and_backtest_still_runs(tmp_path):
    script = textwrap.dedent("""
        import importlib.abc
        import sys
        import types
        from pathlib import Path
        from streamlit.testing.v1 import AppTest
        import watchlist_store

        watchlist_store.DATABASE_PATH = Path(sys.argv[1])

        class BlockMarketImports(importlib.abc.MetaPathFinder):
            def find_spec(self, fullname, path=None, target=None):
                if fullname.split('.')[0] in {'akshare', 'market_data', 'daily_signal', 'plotly', 'quant_core'}:
                    raise AssertionError('Unexpected initial import: ' + fullname)

        blocker = BlockMarketImports()
        sys.meta_path.insert(0, blocker)
        app = AppTest.from_file('app.py').run()
        assert not app.exception
        assert any('2026.10.21.1' in item.value for item in app.caption)
        app.radio[0].set_value('自选信号').run()
        assert not app.exception
        target = next(item for item in app.expander if '001283' in item.label)
        next(button for button in target.button if button.label == '删除此项').click().run()
        assert not app.exception
        assert any('已删除 豪鹏科技' in item.value for item in app.success)
        assert all('001283' not in item.label for item in app.expander)
        app.radio[0].set_value('单股回测').run()
        assert not app.exception
        sys.meta_path.remove(blocker)

        import pandas as pd
        market = types.ModuleType('market_data')
        market.resolve_a_share = lambda query: ('600519', '贵州茅台')
        prices = pd.DataFrame({
            'date': pd.date_range('2026-01-01', periods=100),
            'open': [100 + i for i in range(100)],
            'high': [102 + i for i in range(100)],
            'low': [99 + i for i in range(100)],
            'close': [101 + i for i in range(100)],
            'volume': [1000] * 100,
        })
        market.fetch_a_share_history = lambda *args: prices.copy()
        sys.modules['market_data'] = market
        next(button for button in app.button if button.label == '运行回测').click().run()
        assert not app.exception
        assert len(app.metric) == 7
    """)
    result = subprocess.run(
        [sys.executable, '-c', script, str(tmp_path / 'watchlist.db')],
        cwd=Path(__file__).resolve().parent, capture_output=True, text=True, timeout=45,
    )
    assert result.returncode == 0, result.stdout + result.stderr
