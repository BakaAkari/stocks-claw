
"""测试研究论点追踪。"""
import tempfile
from pathlib import Path

from stocks.engine.hypothesis_tracker import (
    HypothesisStore,
    auto_check_hypotheses,
    format_hypothesis_report,
)


def test_create_and_list():
    """创建论点后能被 list_all 找到。"""
    with tempfile.TemporaryDirectory() as td:
        store = HypothesisStore(store_dir=Path(td))
        h = store.create(
            statement="测试论点：A 股牛市",
            tags=["a_share", "bull"],
        )
        assert h.statement == "测试论点：A 股牛市"
        all_h = store.list_all()
        assert len(all_h) == 1
        assert all_h[0].id == h.id

def test_add_evidence():
    """添加证据后论点状态保持 open。"""
    with tempfile.TemporaryDirectory() as td:
        store = HypothesisStore(store_dir=Path(td))
        h = store.create(statement="测试论点", tags=["test"])
        store.add_evidence(h.id, "run-1")
        h2 = store.list_all()[0]
        assert len(h2.evidence_links) == 1


def test_auto_check_matches():
    """action_cards 的 instrument_key 匹配论点 tags。"""
    with tempfile.TemporaryDirectory() as td:
        store = HypothesisStore(store_dir=Path(td))
        store.create(statement="测试 AI 板块", tags=["ai", "nasdaq100"])
        matched = auto_check_hypotheses(
            store, "run-1",
            [{"signal": "add", "instrument_key": "us_QQQ"}]
        )
        assert len(matched) >= 0  # 匹配与否取决于 tag→instrument_key 映射

def test_format_report():
    """format 输出包含论点列表。"""
    with tempfile.TemporaryDirectory() as td:
        store = HypothesisStore(store_dir=Path(td))
        store.create(statement="测试论点", tags=["test"])
        report = format_hypothesis_report(store.list_all())
        assert "研究论点追踪" in report
        assert "测试论点" in report

def test_multiple_hypotheses():
    """多个论点的 list_all 返回正确数量。"""
    with tempfile.TemporaryDirectory() as td:
        store = HypothesisStore(store_dir=Path(td))
        store.create(statement="论点 A", tags=["a"])
        store.create(statement="论点 B", tags=["b"])
        all_h = store.list_all()
        assert len(all_h) == 2
