# stocks-claw v2 继续开发计划

> 版本：v2.1-dev
> 日期：基于代码审查后的重构开发路线图
> 状态：执行中

---

## 一、关键决策确认（已拍板）

### 决策 1：打破纯标准库限制，引入金融数据栈

**从：** "Python 标准库 only，零 pip 依赖"
**到：** 引入 `pandas` + `numpy` + `pytest`，作为核心金融数据计算与测试基础设施

**理由：**
- 技术指标（RSI/MACD/ATR/波动率）、相关性矩阵、历史数据处理，用标准库手写是低效的重复劳动
- 这是金融数据领域的 de facto 标准，社区维护成熟，可靠性远高于自研
- 测试是工程底线，`pytest` 不可妥协
- Docker 部署已就绪，依赖安装不增加用户负担

**新增依赖：**
| 包名 | 用途 | 最小版本 |
|------|------|----------|
| `pytest` | 测试框架 | 7.4.0 |
| `pytest-asyncio` | 异步测试支持 | 0.21.0 |
| `pandas` | 时间序列数据、技术指标计算 | 2.0.0 |
| `numpy` | 数值计算、矩阵运算 | 1.24.0 |
| `httpx` | 异步 HTTP 客户端（替代 `urllib.request` 的线程池） | 0.25.0 |
| `pyyaml` | 配置热加载（YAML 解析） | 6.0 |

**保持轻量：** 不引入 FastAPI、SQLAlchemy、Redis 等重型依赖。HTTP 适配器继续用标准库 `http.server`，只加认证层。

### 决策 2：收敛定位——"数据管道 + 分析脚手架"，而非"投资顾问"

**从：** stocks-claw 既做数据获取又做 LLM 深度分析，与 Agent 主脑功能重叠
**到：** stocks-claw 做厚数据层，提供**结构化、深度化的金融数据**；投资决策交给 Agent 主脑

**具体调整：**
- `LLMAnalysis.generate_report()` 标记为 **deprecated**，保留但不再扩展，默认禁用
- `LLMEnhancer` 保留但精简：只做新闻摘要、分级，不做"投资建议"
- 新增核心模块：**技术指标引擎**、**历史数据分析**、**组合风险分析**
- `AnalysisContext` 扩展数据字段：技术指标、历史对比、风险指标

**价值主张：** Agent 调用 `build_context()` 后，拿到的不只是"今天沪深300涨了0.35%"，而是"沪深300 30日均线 3540，当前价 3582，RSI 62，MACD 金叉第3天，近20日波动率 1.2%"——这才是 Agent 做决策需要的原料。

---

## 二、总体架构调整

```
┌──────────────────────────────────────────────────────────────┐
│ 交互适配层 (Adapters)                                        │
│ ┌────────┐ ┌────────────┐ ┌────────────┐ ┌────────────────┐ │
│ │ CLI    │ │ Python API │ │ MCP (标准) │ │ HTTP (带认证)  │ │
│ └────────┘ └────────────┘ └────────────┘ └────────────────┘ │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────────────────────────────────────────┐
│ 核心引擎层 (Engine)                                          │
│                                                              │
│ ┌──────────────────┐  ┌──────────────────┐  ┌──────────┐ │
│ │ Data Fetchers    │  │ Analysis Scaffolds│  │  Context │ │
│ │ - fetch_quotes   │  │ - Portfolio       │  │  Builder │ │
│ │ - fetch_news     │  │ - Market          │  │          │ │
│ │ - fetch_history  │  │ - Drift           │  │          │ │
│ └──────────────────┘  └──────────────────┘  └──────────┘ │
│                                                              │
│ ┌──────────────────┐  ┌──────────────────┐  ┌──────────┐ │
│ │ 技术指标引擎 (NEW) │  │ 组合风险分析 (NEW) │  │ Persistence│ │
│ │ - MA/EMA/RSI     │  │ - 波动率贡献      │  │ - Snapshots│ │
│ │ - MACD/ATR/Boll  │  │ - 相关性矩阵      │  │ - Cache    │ │
│ │ - 历史波动率     │  │ - 分散度指标      │  │ - History  │ │
│ └──────────────────┘  └──────────────────┘  └──────────┘ │
│                                                              │
│ ┌──────────────────┐  ┌──────────────────┐                 │
│ │ LLM Enhancer     │  │ LLM Analysis     │  [deprecated]  │
│ │ - 新闻摘要/分级  │  │ - 报告生成       │  [默认禁用]    │
│ └──────────────────┘  └──────────────────┘                 │
└──────────────────────────┬───────────────────────────────────┘
                           │
┌──────────────────────────────────────────────────────────────┐
│ Provider / Data 层                                         │
│ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐ ┌────────┐     │
│ │Tencent │ │Eastmoney│ │Finnhub │ │ RSS   │ │ History│     │
│ │A-Share │ │ A-Share│ │ US/Crypto│ │ News  │ │ Cache  │     │
│ └────────┘ └────────┘ └────────┘ └────────┘ └────────┘     │
└──────────────────────────────────────────────────────────────┘
```

---

## 三、四阶段开发计划

### Phase 1：工程基础（Day 1-3）

**目标：** 让项目从"能跑"变成"工程化的能跑"——测试、降级、配置落地。

#### 任务清单

| 编号 | 任务 | 文件 | 说明 |
|------|------|------|------|
| 1.1 | 创建测试骨架 | `tests/conftest.py` | pytest 配置、通用 fixtures |
| 1.2 | Provider 测试 | `tests/providers/test_tencent_a.py` | Mock urllib，覆盖正常/乱码/超时 |
| 1.3 | Scaffold 测试 | `tests/engine/test_scaffolds.py` | Portfolio + Market scaffold 边界测试 |
| 1.4 | Engine 测试 | `tests/engine/test_engine.py` | StocksEngine 初始化、配置加载、健康检查 |
| 1.5 | 降级链实现 | `stocks/engine/fetchers.py` | 实时 → 备用 Provider → 降级标记 |
| 1.6 | 配置落地 | `stocks/engine/config_loader.py` | 读取 `engine.yaml`，替换硬编码 |
| 1.7 | 异常体系 | `stocks/errors.py` | 分层异常：ProviderError → RetryableError → FatalError |
| 1.8 | 日志脱敏 | `stocks/logging_utils.py` | 过滤 API Key、资产金额的日志输出 |

#### 验收标准

- [ ] `pytest` 运行通过 ≥ 20 个测试用例
- [ ] `pytest --cov` 覆盖率 ≥ 60%（Provider + Scaffold 层）
- [ ] 断开网络时，CLI 仍能返回结果（降级标记 + 空数据）
- [ ] `engine.yaml` 存在且被读取，配置项可覆盖默认值
- [ ] 日志中不出现任何 API Key 或具体资产金额

---

### Phase 2：数据质量（Day 4-7）

**目标：** 做厚数据层，让 `AnalysisContext` 真正有价值。

#### 任务清单

| 编号 | 任务 | 文件 | 说明 |
|------|------|------|------|
| 2.1 | 历史数据缓存 | `stocks/engine/history_cache.py` | 基于 JSON 的本地历史缓存，保存 30 日行情 |
| 2.2 | 技术指标引擎 | `stocks/engine/indicators.py` | MA/EMA/RSI/MACD/ATR/Bollinger Band |
| 2.3 | 指标集成 | `stocks/engine/context_builder.py` | `AnalysisContext` 新增 `technical_indicators` 字段 |
| 2.4 | 多源新闻聚合 | `stocks/engine/fetchers.py` | 接入 GNews/Juhe（实际调用），RSS 去重 |
| 2.5 | 新闻-标的关联 | `stocks/engine/news_mapper.py` | 关键词匹配，标记新闻涉及哪些 watchlist 标的 |
| 2.6 | 宏观数据抓取 | `stocks/engine/macro_fetcher.py` | USD/CNY 汇率、VIX（Yahoo Finance 免费端点） |
| 2.7 | Provider 性能 | `stocks/providers/` | 引入 `httpx` 替代 `urllib`，支持异步连接池 |
| 2.8 | 快照持久化 | `stocks/engine/persistence.py` | 每次 build_context 自动保存，支持历史回溯 |

#### 技术指标需求（最小集）

对每个 watchlist 标的，计算：
- `ma_5`, `ma_10`, `ma_20`, `ma_30` — 简单移动平均线
- `ema_12`, `ema_26` — 指数移动平均（MACD 前置）
- `rsi_14` — 相对强弱指数
- `macd`, `macd_signal`, `macd_histogram` — MACD 三值
- `atr_14` — 平均真实波幅（波动率）
- `boll_upper`, `boll_lower`, `boll_middle` — 布林带
- `volatility_20` — 20 日年化波动率（%）

#### 验收标准

- [ ] `build_context()` 返回的每个 Quote 包含上述技术指标
- [ ] 断网后重新运行，使用缓存历史数据仍能计算指标（滞后但可用）
- [ ] 新闻列表中 ≥ 30% 有 `related_instruments` 字段标记关联标的
- [ ] 运行时间：从当前 ~30s 压缩到 ~10s（异步并行优化）

---

### Phase 3：分析深度（Day 8-10）

**目标：** 组合分析从一维分桶升级为多维风险分析。

#### 任务清单

| 编号 | 任务 | 文件 | 说明 |
|------|------|------|------|
| 3.1 | 组合风险分析 | `stocks/engine/risk_analysis.py` | 波动率贡献、夏普比率、最大回撤（基于历史） |
| 3.2 | 相关性矩阵 | `stocks/engine/correlation.py` | 跨 bucket 标的收益率相关性 |
| 3.3 | 分散度指标 | `stocks/engine/diversification.py` | 赫芬达尔指数、有效前沿近似 |
| 3.4 | 增强 Prompt | `stocks/engine/context_builder.py` | raw_prompt 包含指标解读和风险分析 |
| 3.5 | 约束引擎升级 | `stocks/engine/scaffolds.py` | 支持风险预算约束（不只是比例区间） |
| 3.6 | 回测框架（轻量） | `stocks/engine/backtest.py` | 基于历史快照的"如果之前按建议调仓会怎样" |
| 3.7 | 偏离检查升级 | `stocks/engine/scaffolds.py` | drift 包含风险贡献偏离，不只是比例偏离 |

#### 风险分析最小集

- **组合波动率**：加权平均 + 协方差修正
- **最大回撤（30日）**：基于缓存历史
- **夏普比率（近似）**：基于 30 日收益和波动率
- **分散度**：HHI（赫芬达尔-赫希曼指数）衡量集中度
- **风险贡献**：每个 bucket 对总波动率的边际贡献

#### 验收标准

- [ ] `AnalysisContext` 包含 `risk_metrics` 字典，有上述 5 个指标
- [ ] drift check 区分"比例偏离"和"风险贡献偏离"
- [ ] 回测框架能运行至少 3 次历史快照的"模拟调仓"
- [ ] Prompt 中新增"风险分析"段落，LLM 能基于此给出更专业的建议

---

### Phase 4：交付硬化（Day 11-13）

**目标：** 让 HTTP/MCP 接口从"演示级"变成"可用级"。

#### 任务清单

| 编号 | 任务 | 文件 | 说明 |
|------|------|------|------|
| 4.1 | HTTP 认证 | `stocks/adapters/http.py` | Bearer Token 或 API Key 认证 |
| 4.2 | 速率限制 | `stocks/adapters/http.py` | 简单内存级限速（每 IP 每分钟 N 次） |
| 4.3 | CORS 支持 | `stocks/adapters/http.py` | 允许前端跨域调用 |
| 4.4 | 标准 MCP | `stocks/adapters/mcp.py` | 使用 `mcp` Python SDK 实现标准协议 |
| 4.5 | 健康检查升级 | `stocks/engine/__init__.py` | 检查 Provider 延迟、数据新鲜度、缓存状态 |
| 4.6 | 指标暴露 | `stocks/engine/metrics.py` | 运行时指标：API 调用成功率、延迟、缓存命中率 |
| 4.7 | 文档同步 | `README.md` | 更新所有使用示例，删除过时文档 |
| 4.8 | 性能基准 | `tests/benchmarks/` | 性能测试：并发 10 次 build_context 耗时 |

#### 验收标准

- [ ] 无 Token 调用 HTTP API 返回 401
- [ ] 超过速率限制返回 429，带 Retry-After 头
- [ ] MCP 客户端（Claude Desktop）能正确发现 tools 并调用
- [ ] `health_check()` 返回每个 Provider 的延迟和可用性
- [ ] 并发 10 次 build_context 总耗时 ≤ 15 秒

---

## 四、具体技术决策

### 4.1 异步策略

- `httpx` 替代 `urllib.request` 的 `asyncio.to_thread` 模式
- Provider 内部使用 `httpx.AsyncClient` 连接池
- `fetch_quotes()` 并行获取所有市场，不再串行
- `fetch_news()` 并行获取所有源，不再串行
- 指标计算用 `asyncio.to_thread`  offload 到线程池（pandas 计算非异步安全）

### 4.2 缓存策略

```yaml
# engine.yaml 新增 cache 配置
cache:
  enabled: true
  backend: file              # 只支持 file，不引入 Redis
  quote_ttl: 1800            # 30 分钟
  news_ttl: 7200             # 2 小时
  history_ttl: 86400         # 24 小时（历史数据变动慢）
  max_snapshots: 30          # 保留 30 天快照
```

- 快照文件：`./data/snapshots/YYYY-MM-DD_HHMMSS.json`
- 历史缓存：`./data/history/{market}/{code}.json`
- 缓存失效：TTL 到期后重新 fetch，失败时继续用旧数据（graceful degradation）

### 4.3 测试策略

```
tests/
├── conftest.py                    # fixtures：mock engine、mock provider、sample assets
├── providers/
│   ├── test_tencent_a.py          # 3 场景：正常、乱码、超时
│   ├── test_eastmoney_a.py
│   └── test_finnhub_quote.py
├── engine/
│   ├── test_fetchers.py           # 降级链、并发获取
│   ├── test_scaffolds.py          # 边界：空资产、极端比例
│   ├── test_indicators.py         # 指标计算正确性（对比已知数据）
│   ├── test_risk_analysis.py      # 风险指标
│   ├── test_context_builder.py    # 组装完整上下文
│   └── test_persistence.py        # 快照保存/加载
├── adapters/
│   ├── test_cli.py                # 参数解析、输出格式
│   └── test_http.py               # 认证、速率限制
└── integration/
    └── test_end_to_end.py         # 端到端：engine.build_context() 完整链路
```

- 所有外部 API 调用必须 Mock（`unittest.mock.patch` 或 `respx`）
- 每个测试独立，不依赖网络、不依赖文件系统状态
- CI 目标：pytest 通过 + 覆盖率 ≥ 80%（engine 层）+ 无安全告警

### 4.4 数据隐私加固

- 资产数据文件（`financial_assets.json`）可选加密（AES-256-GCM，密钥从 `.secret/data-key.md` 读取）
- 日志脱敏：金额显示为 `***` 或范围（如 `> 100000`）
- HTTP 传输：本地使用 HTTP 即可，NAS 部署建议加反向代理 TLS
- 缓存文件不加密，但路径在 `.local/` 下，已 gitignore

### 4.5 版本管理

- `AnalysisContext.schema_version` 从 2 升级到 3
- 新增字段：
  - `technical_indicators: dict[str, dict]` — 每个标的的技术指标
  - `risk_metrics: dict` — 组合风险指标
  - `news_with_mapping: list[dict]` — 带关联标的的新闻
  - `macro_data: dict` — 宏观数据（汇率、VIX 等）
- 向后兼容：`from_dict()` 支持 schema_version 2 → 3 的字段默认值填充

---

## 五、里程碑定义

| 里程碑 | 日期 | 可交付物 | 验收标准 |
|--------|------|----------|----------|
| **M1** | Day 3 | Phase 1 完成 | pytest 通过 ≥ 20 个测试，降级链工作，配置落地 |
| **M2** | Day 7 | Phase 2 完成 | 每个标的带 7 个技术指标，新闻关联率 ≥ 30%，运行时间 ≤ 10s |
| **M3** | Day 10 | Phase 3 完成 | 风险指标齐全，回测框架可运行，Prompt 含风险分析 |
| **M4** | Day 13 | Phase 4 完成 | HTTP 认证 + 限速，标准 MCP，健康检查有指标，并发性能达标 |
| **M5** | Day 14 | 发布 v2.1 | 文档更新、GitHub Release、性能基准报告 |

---

## 六、禁止事项

本计划执行期间，以下行为**严格禁止**：

1. **不新增任何文档文件**（README 更新除外）—— 所有设计决策直接体现在代码和测试里
2. **不扩展 LLM 分析层**—— `llm_analysis.py` 只修 bug，不新增功能
3. **不引入重型依赖**—— 只允许 pandas/numpy/pytest/httpx/pyyaml，禁止 FastAPI/SQLAlchemy/Redis/Celery
4. **不追求 100% 覆盖率**—— 先保证核心链路（Provider → Engine → Scaffold）覆盖 ≥ 80%
5. **不提前设计 Phase 3+ 的代码**—— 每个阶段只规划当前阶段，下一阶段开始前重新评估

---

## 七、立即开始（今天）

今天（Day 1）的具体任务：

1. **更新 `requirements.txt`** — 添加 pandas/numpy/pytest/httpx/pyyaml
2. **更新 `Dockerfile`** — 安装 pip 依赖
3. **创建 `tests/` 骨架** — 目录结构、conftest.py、fixtures
4. **写第一个测试** — `tests/providers/test_tencent_a.py`（Mock 正常返回）
5. **写第二个测试** — `tests/engine/test_scaffolds.py`（PortfolioScaffold 边界）
6. **运行 `pytest`** — 确保环境正确，至少 2 个测试通过

**今天结束时的标准：能在终端看到 `pytest` 输出绿色通过。**

---

*本计划一经确立，所有后续开发工作严格按此文档执行。如需调整，需在此文档中修改并记录变更理由。*
