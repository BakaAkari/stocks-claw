# stocks-claw v2.1 开发计划

> 版本：v2.1-dev
> 当前状态：Phase 2 最小闭环已完成，后续候选按启动条件推进
> 已完成修复与收口见 `EXECUTION_PLAN.md`
> 本文档是当前开发进度与下一步规划的唯一主线文档；旧路线见
> `docs/archive/stocks-ROADMAP.md`，仅作历史参考。

---

## 1. 当前定位

`stocks-claw` 当前定位为 Agent-first 的个人金融数据与分析上下文工具包。

边界：

- 程序负责数据获取、清洗、降级、配置加载、组合映射、偏离检查和上下文组装。
- 程序输出结构化 `AnalysisContext`，由 Agent 主脑做最终投资分析。
- 新闻语义增强归外部 Agent；引擎只保留诚实的 `rules_v1` 事件提取。
- `LLMAnalysis.generate_report()` 保留为兼容能力，默认禁用，不再作为主线扩展。
- 不做自动交易，不执行下单，不把 LLM 输出包装成确定投资建议。

已确认的技术取向：

- 打破旧的 stdlib-only 限制，引入小型金融/数据工程依赖。
- 保持轻量，不引入 FastAPI、SQLAlchemy、Redis、Celery 等重型依赖。
- HTTP/MCP 是适配层，不是产品主线；公开部署前必须完成完整安全审计和限流。

---

## 2. 当前已完成进度

### Phase 1：工程基础 — 已完成

目标：让项目从“能跑”变成“工程化地能跑”。

完成项：

- [x] 创建 pytest 测试骨架：`tests/conftest.py`
- [x] Provider 测试：`tests/providers/test_tencent_a.py`
- [x] Scaffold 测试：`tests/engine/test_scaffolds.py`
- [x] Engine 测试：`tests/engine/test_engine.py`
- [x] Fetcher 降级链测试：`tests/engine/test_fetchers.py`
- [x] 配置加载测试：`tests/engine/test_config_loader.py`
- [x] 日志脱敏测试：`tests/engine/test_logging_utils.py`
- [x] 降级链实现：`stocks/engine/fetchers.py`
- [x] 配置落地：`stocks/engine/config_loader.py` + `stocks/config/engine.yaml`
- [x] 异常体系：`stocks/errors.py`
- [x] 日志脱敏：`stocks/logging_utils.py`
- [x] 项目配置：`pyproject.toml`、`uv.lock`、`requirements.txt`
- [x] 清理坏掉的旧测试入口：删除 `stocks/tests/test_v2.py`
- [x] 文档入口同步：`README.md`、`README.zh.md`、`AGENT_GUIDE.md`

验证证据：

```text
uv run ruff check .
All checks passed

uv run python -m pytest -q
125 passed

uv run python -m compileall -q stocks tests
通过

uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
{'schema_version': 2, 'asset_count': 7, 'news_count': 0, 'quotes': {}}
```

当前 Git 状态基线：

```text
1240f97 chore: stabilize v2 development workflow
```

---

## 3. 当前依赖与运行方式

Python：

```text
>=3.11
```

依赖：

```text
pandas
numpy
httpx
pyyaml
pytest
pytest-asyncio
ruff
```

推荐验证命令：

```bash
uv sync --dev
uv run ruff check .
uv run python -m pytest -q
uv run python -m compileall -q stocks tests
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

真实 CLI 入口：

```bash
uv run python -m stocks.adapters.cli [options]
```

旧入口已废弃：

```text
python -m stocks.cli.stocks ...
python -m stocks.tests.test_v2
```

---

## 4. 下一步：Phase 2 最小闭环

Phase 2 不要一次性展开所有数据源和分析层。当前优先做“历史数据缓存 + 技术指标 + AnalysisContext 集成”这一条闭环。

### Phase 2A：历史数据缓存

目标：为技术指标提供稳定的本地历史序列，断网时仍可降级使用。

建议文件：

```text
stocks/engine/history_cache.py
tests/engine/test_history_cache.py
```

最小能力：

- [ ] 保存某个标的的日线历史序列到本地 JSON。
- [ ] 读取某个标的的历史序列。
- [ ] 支持 TTL / stale 标记。
- [ ] 网络失败或无 Provider 历史接口时，能返回 cached/stale 状态，而不是抛穿主流程。
- [ ] 缓存路径必须 gitignore，默认位于运行态目录，不写入源码目录的 tracked 文件。

建议数据结构：

```json
{
  "instrument": {"market": "a", "code": "000300", "name": "沪深300"},
  "updated_at": "2026-07-01T00:00:00+08:00",
  "source": "cache|provider|fixture",
  "stale": false,
  "bars": [
    {"date": "2026-06-01", "open": 0, "high": 0, "low": 0, "close": 0, "volume": 0}
  ]
}
```

验收：

- [ ] `test_history_cache.py` 覆盖 save/load/missing/stale/corrupt JSON。
- [ ] 无网络或无历史 Provider 时，主流程仍可运行。

### Phase 2B：技术指标引擎

目标：对历史 K 线计算 Agent 真正有用的基础指标。

建议文件：

```text
stocks/engine/indicators.py
tests/engine/test_indicators.py
```

最小指标集：

- [ ] `ma_5`, `ma_10`, `ma_20`, `ma_30`
- [ ] `ema_12`, `ema_26`
- [ ] `rsi_14`
- [ ] `macd`, `macd_signal`, `macd_histogram`
- [ ] `atr_14`
- [ ] `boll_upper`, `boll_middle`, `boll_lower`
- [ ] `volatility_20`（20 日收益率标准差 × √252 的年化历史波动率）

设计要求：

- 输入为 pandas DataFrame 或 list[dict]，但外部接口保持简单。
- 数据不足时返回 `None` 或带 `status=insufficient_data`，不能制造假指标。
- 单测使用固定样本，不依赖外部网络。
- 不在 Quote dataclass 里硬塞大量字段，优先用独立 `technical_indicators` 映射。

验收：

- [ ] 固定样本计算结果稳定。
- [ ] 空数据、短序列、缺字段、非数字值都有测试。
- [ ] ruff / pytest / compileall 全过。

### Phase 2C：AnalysisContext 集成

目标：让 Agent 在一次 `build_context()` 中拿到行情 + 技术指标状态。

建议修改：

```text
stocks/domain/models.py
stocks/engine/context_builder.py
stocks/engine/__init__.py
tests/engine/test_context_builder.py
```

建议 schema：

```text
AnalysisContext.schema_version = 3
technical_indicators: dict[str, dict]
```

key 建议：

```text
{market}:{code}
```

例如：

```json
{
  "a:000300": {
    "status": "ok",
    "source": "cache",
    "stale": false,
    "ma_20": 3582.1,
    "rsi_14": 62.4
  }
}
```

验收：

- [ ] `build_context()` 返回 `technical_indicators` 字段。
- [ ] 无历史数据时字段存在，但标记 `missing` / `insufficient_data`。
- [ ] CLI smoke 输出可解析。
- [ ] schema version 升级有测试覆盖。

---

## 5. 暂缓事项

以下内容不要在 Phase 2A/2B/2C 之前展开：

- 多源新闻聚合：GNews/Juhe 等实际接入。
- 新闻-标的关联。
- 宏观数据抓取：VIX、Yahoo 等。
- Provider 全面 httpx 改造。
- 组合风险分析、相关性矩阵、回测框架。
- HTTP 认证、限速、CORS。
- 标准 MCP SDK 重写。

理由：这些都需要更清晰的数据契约。先把历史数据与技术指标闭环跑稳。

---

## 6. Phase 3：分析深度（Phase 2 完成后再启动）

目标：组合分析从一维分桶升级为多维风险分析。

候选任务：

- [ ] 美股第二行情源（替代当前 Finnhub 单点；未接入前仅使用显式 stale 历史兜底）
- [ ] 组合风险分析：`stocks/engine/risk_analysis.py`
- [ ] 相关性矩阵：`stocks/engine/correlation.py`
- [ ] 分散度指标：`stocks/engine/diversification.py`
- [ ] 风险预算约束：扩展 drift check
- [ ] 轻量回测：`stocks/engine/backtest.py`

最小风险指标：

- 组合波动率
- 最大回撤
- 近似夏普比率
- HHI 集中度
- 风险贡献

启动条件：

- Phase 2 的 `technical_indicators` 已稳定输出。
- 至少有可复用的本地历史序列。
- 默认测试、lint、CLI smoke 均稳定通过。

---

## 7. Phase 4：交付硬化（需要 HTTP/MCP 真实使用场景时再启动）

候选任务：

- [ ] 在现有 Bearer Token 基础上补齐密钥轮换与权限分级
- [ ] 简单内存级限速
- [ ] CORS 配置
- [ ] 标准 MCP SDK 实现
- [ ] Provider 延迟、缓存状态、降级状态暴露到 health check
- [ ] 性能基准：并发 10 次 build_context

启动条件：

- 用户明确需要 NAS/HTTP/MCP 作为稳定接口。
- 内网安全边界明确。
- 有部署验证路径。

---

## 8. 禁止事项

- 不新增与当前阶段无关的文档文件。
- 新增 .md 文件前必须先证明现有文档无法承载；分析/调研类文档一律进
  `docs/archive/`，不进根目录。
- 不继续扩展 `llm_analysis.py` 的决策能力。
- 不引入重型依赖。
- 不把技术指标做成投资建议。
- 不在缺数据时伪造指标。
- 不提交 `.local/`、`.secret/`、缓存、快照、虚拟环境。
- 不让默认 `pytest` 失败。

---

## 9. 每次开发完成的验收清单

每次提交前必须至少运行：

```bash
uv run ruff check .
uv run python -m pytest -q
uv run python -m compileall -q stocks tests
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

提交说明需包含：

- 改了什么。
- 验证命令结果。
- 是否影响 `AnalysisContext` schema。
- 是否涉及 `.local/` 或 `.secret/`。
