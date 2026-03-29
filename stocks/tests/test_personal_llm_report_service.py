from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.services.personal_llm_report_service import PersonalLLMReportService


if __name__ == '__main__':
    service = PersonalLLMReportService(model='kimi-k2.5')
    prompt = service.build_prompt()
    assert '你是这个系统的主分析引擎' in prompt
    assert '原始上下文' in prompt
    assert '结构化分析信号' in prompt
    assert '用户金融记忆' in prompt
    assert '与你资产相关' in prompt

    normalized = service.normalize_output('''**今日概览**\n今天没啥\n\n风险偏好\n- 中性\n''')
    assert '今日概览' in normalized
    assert '- 今天没啥' in normalized
    assert '美股观察' in normalized
    assert '一句话结论' in normalized

    feishu = service.format_for_feishu('''今日概览\n- 今天没啥\n\n风险偏好\n- 中性\n\n一句话结论\n- QQQ 更弱，159915 没跟跌\n\n风险提示\n- 波动在放大\n''')
    assert '**一句话结论**' in feishu
    assert '---' in feishu
    assert '> `QQQ` 更弱，`159915` 没跟跌' in feishu
    assert '**今日概览**' in feishu
    assert '- 今天没啥' in feishu
    print('personal llm report prompt ok')
