# stocks-claw

服务单一用户的个人投资分析师系统。它保存用户确认的账户、持仓和投资偏好，定时采集行情、历史价格、新闻、公告、宏观和事件，构建可追踪的数据证据，并由 LLM 投资分析师综合形成持仓建议、市场研判和机会候选。用户始终是唯一决策人，系统不自动下单。

当前生产仍使用确定性规则动作和受限 Outlook；下一阶段正在按 `EXECUTION_PLAN.md` 迁移到“统一证据快照 → LLM InvestmentAdvisory → 确定性可执行性验证 → 推送”的目标架构。迁移采用 shadow-first，不会一次性替换现有生产路径。

[English](README.md) · [愿景](stocks/VISION.md) · [计划](PLAN.md) · [执行清单](EXECUTION_PLAN.md) · [架构](ARCHITECTURE.md) · [Agent 指南](AGENT_GUIDE.md)

## 文档权威

- `stocks/VISION.md`：产品北极星；
- `PLAN.md`：当前状态、裁决和路线；
- `EXECUTION_PLAN.md`：唯一实施清单；
- `ARCHITECTURE.md`：当前与目标架构；
- `stocks/DATA_MODEL.md`：当前和计划 schema；
- `AGENT_GUIDE.md`：操作与开发规则；
- `docs/archive/`：历史证据，无现行效力。

## 当前能力

- Account / Position v2、成本、币种、流动性、分类和投资画像；
- A 股、美股、基金和加密行情及历史数据；
- 新闻、RSS、GNews、SEC EDGAR、巨潮、宏观和事件；
- 技术指标、轮动、组合暴露、数据异常和质量边界；
- LLM 新闻聚类、受限中长期 Outlook；
- 定时 A 股/美股 session、Cron、Feishu 推送；
- Advice、Execution、Forecast、Shadow Account 和信号结算基础。

## 目标能力

- 自然语言资产输入 → diff → 用户确认 → 原子写入；
- 同一 `as_of` 的 UnifiedAnalysisSnapshot；
- LLM 综合用户持仓、风格、价格、历史、新闻和宏观生成 InvestmentAdvisory；
- 规则作为证据，Validator 只做证据与可执行性检查；
- 持仓动作、组合影响、未来情景和板块/品类机会统一报告；
- 执行、拒绝和预测结果形成反馈闭环。

## 环境

- Python 3.11+
- `uv` 或项目现有 `.venv`

```bash
uv venv --python 3.11 .venv
uv pip install -r requirements.txt
```

## 快速开始

```bash
.venv/bin/python -m stocks.adapters.cli --output json --no-news --no-quotes
.venv/bin/python -m stocks.adapters.cli --output json
.venv/bin/python -m stocks.adapters.cli --scheduled-run-due
.venv/bin/python -m stocks.adapters.cli --scheduled-run-latest cn_pre_close
```

旧 `--llm-analysis` 仅为兼容入口，固定禁用，不代表目标 Advisory 路径。

## 金融记忆

读取：

```bash
.venv/bin/python -m stocks.adapters.cli --assets-list
.venv/bin/python -m stocks.adapters.cli --profile-get
```

任何资产、画像、建议、执行和预测写入必须用户确认。私有数据位于 `.local/`，凭证位于 `.secret/` 软链接指向的 `/opt/data/.secret/`，均不得提交。

## 主要数据

```text
.local/financial_assets.json
.local/investor_profile.json
.local/computed_profile.json
.local/history/
.local/news_intelligence/
.local/scheduled_runs/
.local/advice/
.local/executions/
.local/forecasts/
stocks/config/
```

SEC EDGAR 需要带联系邮箱的 `SEC_USER_AGENT` 或 `.secret/sec-user-agent.md`。

## 验证

```bash
.venv/bin/ruff check .
.venv/bin/pytest -q -o 'addopts='
.venv/bin/python -m compileall -q stocks tests
.venv/bin/python -m stocks.adapters.cli --output json --no-news --no-quotes
```

2026-07-22 新鲜基线：ruff 通过，pytest `1124 passed`，Finnhub、Polygon 和 SEC EDGAR 真实 smoke 通过。

本项目用于分析、研究和辅助决策，不构成收益承诺或自动交易服务。
