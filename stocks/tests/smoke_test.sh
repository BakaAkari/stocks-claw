#!/bin/sh
set -eu

python3 stocks/tests/test_quote_guard.py
python3 stocks/tests/test_markdown_renderer.py
python3 stocks/tests/test_research_context_service.py
python3 stocks/tests/test_llm_report_service.py
python3 stocks/tests/test_build_report_fallback.py
python3 stocks/tests/test_provider_fallback.py
python3 stocks/tests/test_command_service.py
python3 stocks/tests/test_chat_router_service.py
python3 stocks/tests/test_validate_config.py
python3 stocks/tests/test_financial_memory_service.py
python3 stocks/tests/test_asset_memory_chat_service.py
python3 stocks/tests/test_news_input_service.py
python3 stocks/tests/test_news_fetch_service.py
python3 stocks/tests/test_finnhub_quote_provider.py
python3 stocks/tests/test_personal_insight_service.py
python3 stocks/tests/test_theme_analysis_service.py
python3 stocks/tests/test_report_assembly_service.py
python3 stocks/tests/test_personal_llm_report_service.py
python3 stocks/scripts/build_report.py A股 >/tmp/stocks_build_report.out || true
python3 stocks/scripts/query_market.py A股 紫金矿业 >/tmp/stocks_query_market.out || true
python3 stocks/scripts/query_market.py 美股 AAPL >/tmp/stocks_query_us_market.out || true
python3 stocks/scripts/financial_memory.py list >/tmp/stocks_financial_memory.out
python3 stocks/scripts/personal_insight_context.py --format text >/tmp/stocks_personal_insight.out
python3 stocks/scripts/build_personal_report.py >/tmp/stocks_personal_report.out
python3 stocks/scripts/send_llm_report.py >/tmp/stocks_send_llm_report.out

grep -q 'A股简报' /tmp/stocks_build_report.out
grep -Eq '摘要|状态' /tmp/stocks_build_report.out
grep -q '紫金矿业' /tmp/stocks_query_market.out
grep -q 'schema_version' /tmp/stocks_financial_memory.out
grep -q '金融记忆' /tmp/stocks_personal_insight.out
grep -q '新闻输入' /tmp/stocks_personal_insight.out
grep -q '今日概览' /tmp/stocks_personal_report.out
grep -q '风险偏好' /tmp/stocks_personal_report.out
grep -q '美股观察' /tmp/stocks_personal_report.out
grep -q '与你资产相关' /tmp/stocks_personal_report.out
grep -q '今日概览' /tmp/stocks_send_llm_report.out
grep -q '一句话结论' /tmp/stocks_send_llm_report.out

echo 'smoke test ok'
