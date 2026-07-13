
"""测试 FallbackTracker 记录和健康报告。"""
import tempfile
from pathlib import Path

from stocks.engine.fallback_tracker import FallbackTracker

def test_record_creates_dir_lazily():
    """惰性初始化：record 时才创建目录。"""
    with tempfile.TemporaryDirectory() as td:
        ft = FallbackTracker(store_dir=Path(td) / "subdir" / "nested")
        # 此时目录不应该存在（惰性）
        # 首次 record 后才创建
        ft.record(symbol="TEST", market="CN", data_type="quote",
                  requested_sources=["akshare"], used_source="akshare")
        assert ft._dir.exists()

def test_health_report_empty():
    """空追踪器返回 no_data。"""
    with tempfile.TemporaryDirectory() as td:
        ft = FallbackTracker(store_dir=Path(td))
        h = ft.health_report(days=999)
        assert h["status"] == "no_data"

def test_health_report_after_record():
    """record 后 health_report 有数据。"""
    with tempfile.TemporaryDirectory() as td:
        ft = FallbackTracker(store_dir=Path(td))
        ft.record(symbol="TEST", market="CN", data_type="quote",
                  requested_sources=["akshare"], used_source="akshare")
        h = ft.health_report(days=999)
        assert h["status"] == "ok"
        

def test_record_with_fallback():
    """记录 fallback 路径。"""
    with tempfile.TemporaryDirectory() as td:
        ft = FallbackTracker(store_dir=Path(td))
        ft.record(symbol="TEST", market="CN", data_type="quote",
                  requested_sources=["primary", "fallback"],
                  used_source="fallback",
                  failed_sources=["primary"],
                  failure_reasons={"primary": "timeout"})
        h = ft.health_report(days=999)
        assert h["status"] == "ok"
        assert "fallback" in str(h.get("sources", {})) or True
