"""样本质量标注测试: scorecard 胜率标注可信度, 防止把噪声胜率当信任依据。"""
import json, sys
sys.path.insert(0, "/mnt/user/code-project/stocks-claw")


def test_assess_sample_quality_trustable_when_distributed():
    from stocks.engine.context_builder import ContextBuilder
    # 30天、每只标的多天、日均样本均匀 -> 应 trustable
    recs = []
    for day_idx in range(10):
        for sym in ("a:AAA", "a:BBB", "a:CCC"):
            recs.append({"correct": True, "day": f"2026-08-0{day_idx}", "symbol": sym})
    q = ContextBuilder._assess_sample_quality(recs, min_sample=30, min_day_span=5,
                                              max_day_share=0.6, min_symbols=3)
    assert q["trustable"] is True
    assert q["day_span"] == 10
    assert q["distinct_symbols"] == 3


def test_assess_sample_quality_single_day_dominant_not_trustable():
    from stocks.engine.context_builder import ContextBuilder
    # 大量样本但全部集中在同一天、同标的 -> 必须 trustable=False
    recs = [{"correct": True, "day": "2026-08-13", "symbol": "a:AAA"} for _ in range(200)]
    q = ContextBuilder._assess_sample_quality(recs, min_sample=50, min_day_span=5,
                                              max_day_share=0.6, min_symbols=3)
    assert q["trustable"] is False
    assert any("时间跨度短" in i for i in q["issues"])
    assert any("单日集中" in i for i in q["issues"])
    assert any("标的过于集中" in i for i in q["issues"])


def test_assess_sample_quality_insufficient_sample_not_trustable():
    from stocks.engine.context_builder import ContextBuilder
    recs = [{"correct": True, "day": f"2026-08-0{i}", "symbol": f"a:S{i}"} for i in range(3)]
    q = ContextBuilder._assess_sample_quality(recs, min_sample=50, min_day_span=5,
                                              max_day_share=0.6, min_symbols=3)
    assert q["trustable"] is False
    assert any("样本不足" in i for i in q["issues"])
