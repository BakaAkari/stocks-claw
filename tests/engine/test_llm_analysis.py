"""统一个人建议 prompt 接线测试。"""

from unittest.mock import Mock

from stocks.engine.llm_analysis import LLMAnalysis


async def test_generate_report_uses_shared_system_prompt(tmp_path):
    prompt_path = tmp_path / "advice.txt"
    prompt_path.write_text("SHARED ANALYSIS CONSTITUTION", encoding="utf-8")
    analyzer = LLMAnalysis(
        enabled=True,
        api_key="test",
        prompt_path=prompt_path,
    )
    analyzer._call_llm = Mock(return_value="report")
    context = Mock(raw_prompt_input="DESENSITIZED CONTEXT")

    result = await analyzer.generate_report(context)

    assert result == "report"
    analyzer._call_llm.assert_called_once_with(
        "SHARED ANALYSIS CONSTITUTION",
        "DESENSITIZED CONTEXT",
    )
