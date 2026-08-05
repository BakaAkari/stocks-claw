# TASK-INFO-01 — 人民币侧宏观与估值数据基础设施

> 触发：`docs/analysis/user-requirements-analysis-2026-08-04.md` §5 差距矩阵中
> D1a（国内池建仓）和 D4（A股战术）被评为 ❌ 严重缺失 / ❌ 完全缺失。
> 本任务为"双引擎信息面专项"第一阶段，聚焦**人民币侧数据基础设施**：
> 中国宏观数据 + A股主要指数估值分位。

## Objective

让系统首次具备中国宏观与A股估值的机器可读数据能力，使 `AnalysisContext`
能携带以下新事实进入裁决器和报告：

1. **中国宏观关键指标**：官方 PMI、CPI 同比、PPI 同比、M2 同比、社融增量、
   LPR 1Y/5Y、社会消费品零售总额同比、工业增加值同比。
2. **A股主要指数估值分位**：沪深300、中证500、创业板指、科创50的
   PE-TTM / PB / 股息率，以及各自在近5年/近10年的历史分位。

这些数据首次进入 `AnalysisContext` 的 `macro` 段和新增 `cn_valuation` 段，
并通过 `data_notes` 诚实标注数据来源与新鲜度。

## Scope

### 1. 中国宏观数据 Provider（`stocks/providers/cn_macro.py`）

- 使用 **AKShare** 作为数据源（免费、活跃维护、无需 API key）。
- 实现 `CnMacroProvider` 类，提供 `fetch() -> CnMacroSnapshot`：
  - PMI（官方制造业 PMI）
  - CPI 同比
  - PPI 同比
  - M2 同比
  - 社会融资规模增量（当月）
  - LPR 1年期 / 5年期
  - 社会消费品零售总额同比
  - 工业增加值同比
  - 人民币兑美元中间价（作为 USD/CNY 的辅助验证）
- 每个字段携带 `source` 和 `as_of` 时间戳。
- 失败字段记录 `errors`，不阻断整体流程。
- 可选本地磁盘缓存（JSON，24h TTL），避免每次运行重复拉取。

### 2. A股指数估值 Provider（`stocks/providers/cn_index_valuation.py`）

- 使用 AKShare 的指数估值接口（如 `ak.index_value_hist_funddb` 或
  `ak.index_analysis` 等，以实际可用接口为准）。
- 实现 `CnIndexValuationProvider` 类，覆盖：
  - 沪深300 (000300)
  - 中证500 (000905)
  - 创业板指 (399006)
  - 科创50 (000688)
- 每个指数返回：PE-TTM、PB、股息率，以及近5年/近10年分位。
- 若 AKShare 的估值接口不可用或不稳定，降级方案：
  从指数历史行情自行计算 PE/PB 近似（使用 AKShare 的指数成分股盈利数据）。
- 同样携带 `source`、`as_of`、`errors`。

### 3. 集成到宏观数据管线

- 在 `stocks/engine/macro_data.py` 中：
  - 导入 `CnMacroProvider`，在 `CompositeMacroProvider` 降级链中加入
    `CnMacroProvider`（中国市场指标由 CnMacroProvider 提供，
    不与 Yahoo/FRED 的 USD 指标冲突）。
  - 或更清晰的做法：将宏观数据分为 `global_macro` 和 `cn_macro` 两段，
    在 `AnalysisContext` 中分别存放。本任务选择后者（更清晰）。
- 新增 `CnMacroSnapshot` dataclass，字段命名与 `MacroSnapshot` 风格一致。

### 4. 接入 `AnalysisContext`

- 在 `stocks/engine/scheduled_analysis.py` 的 `build_context` 流程中：
  - 新增 `cn_macro` 和 `cn_valuation` 采集步骤。
  - 这些字段进入 `AnalysisContext` 的 `macro.cn` 和 `valuation.cn` 段。
- 在 `stocks/engine/context_builder.py` 中：
  - 确保 `_build_macro_section` 或新增方法能正确组装中国宏观数据。
  - 数据新鲜度检查：宏观数据通常月度发布，设定合理的新鲜度阈值
    （如 CPI/PMI 超过 45 天标为 stale）。

### 5. 报告呈现

- 在 `stocks/engine/presentation.py` 中：
  - 新增"中国宏观"与"A股估值"的呈现段落（简洁，不超过 5 行）。
  - 数据不可用时降级为"数据暂缺"，不伪造数字。
- 在 `build_push_payload.py` 中：
  - 确保新数据段能进入 push payload 的 `data_notes` 或独立小节。

### 6. 配置与文档

- `DEFAULT_ENGINE_CONFIG` 中新增 `providers.cn_macro.enabled = True`。
- 在 `docs/contracts/README.md` 中更新数据契约状态（新 Provider 为
  `[PRODUCTION]` 候选）。

## Data source details

| 指标 | AKShare 函数（候选） | 频率 | 备注 |
|---|---|---|---|
| PMI | `ak.macroeconomics_china_pmi` 或 `ak.macro_china_pmi` | 月度 | 官方制造业 PMI |
| CPI | `ak.macro_china_cpi` | 月度 | 同比 |
| PPI | `ak.macro_china_ppi` | 月度 | 同比 |
| M2 | `ak.macro_china_m2` | 月度 | 同比 |
| 社融 | `ak.macro_china_shrz` 或 `ak.macro_china_society_financing` | 月度 | 当月增量 |
| LPR | `ak.macro_china_lpr` | 月度 | 1Y/5Y |
| 社零 | `ak.macro_china_retail_sales` | 月度 | 同比 |
| 工业增加值 | `ak.macro_china_industrial_production` | 月度 | 同比 |
| 估值分位 | `ak.index_value_hist_funddb` / `ak.index_analysis` | 日频 | 以实际接口为准 |

> ⚠️ AKShare 的接口命名和可用性会随版本变化。实现时以当前安装版本的
> 实际可用接口为准，优先使用稳定接口，降级 gracefully。

## Non-goals (must not do)

- **不做中文新闻源接入**：那是 `TASK-INFO-02`（中文财经新闻）。
- **不做美元侧估值锚**：那是 `TASK-INFO-03`（Shiller CAPE / 纳指分位 / QDII 溢价）。
- **不修改 LLM prompt**：本任务只把数据送进 `AnalysisContext`，
  让 LLM 在现有研判中自然引用新数据即可；prompt 级别的结构化引用
  优化后续再做。
- **不做 A股情绪指标**（成交额/北向/涨跌停）：那是 `TASK-INFO-02` 或
  `TASK-INFO-04` 的范畴。
- **不接入实时盘中数据**：本任务只覆盖盘后/定时分析所需的日频/月频数据。
- **不写金融记忆**：宏观和估值数据属于市场数据，非用户确认事实，
  按现有缓存策略处理即可。

## Acceptance criteria

1. `pip install akshare` 成功，Provider 能正常初始化。
2. `CnMacroProvider.fetch()` 返回至少 5 个指标（PMI/CPI/PPI/M2/社融），
   每个含 `value`、`source`、`as_of`；失败字段在 `errors` 中。
3. `CnIndexValuationProvider.fetch()` 返回至少 3 个指数的 PE/PB/分位数据。
4. `AnalysisContext` 包含 `macro.cn` 和 `valuation.cn` 段，
   `scheduled_analysis.py` 的定时运行能正确组装。
5. 报告呈现段出现"中国宏观"和"A股估值"小节（数据可用时），
   或"数据暂缺"降级（数据不可用时）。
6. 新 Provider 有独立单元测试（mock AKShare 返回，不依赖网络）。
7. Full pytest 回归通过（现有 1351 tests + 新增 tests）。
8. ruff clean，compileall clean，diff-check clean。

## Smoke check

```bash
# 1. 安装依赖
pip install akshare

# 2. 手动验证 Provider
.venv/bin/python -c "
from stocks.providers.cn_macro import CnMacroProvider
import asyncio
async def test():
    p = CnMacroProvider()
    snap = await p.fetch()
    print('PMI:', snap.pmi, 'as_of:', snap.pmi_as_of)
    print('CPI:', snap.cpi_yoy, 'as_of:', snap.cpi_as_of)
    print('Errors:', snap.errors)
asyncio.run(test())
"

# 3. 运行 cn_after_close 会话，检查 AnalysisContext 中是否包含新数据段
.venv/bin/python -m stocks.adapters.cli \
  --scheduled-run-session cn_after_close \
  --now '2026-08-04T15:00:00+08:00' --force --output json \
  | jq '.context.macro.cn, .context.valuation.cn'
```

## Files likely to touch

- `stocks/providers/cn_macro.py`（新）
- `stocks/providers/cn_index_valuation.py`（新）
- `stocks/engine/macro_data.py`（集成 CnMacroProvider）
- `stocks/engine/scheduled_analysis.py`（采集步骤）
- `stocks/engine/context_builder.py`（数据组装）
- `stocks/engine/presentation.py`（呈现）
- `stocks/engine/config_loader.py`（DEFAULT_ENGINE_CONFIG 扩展）
- `tests/providers/test_cn_macro.py`（新）
- `tests/providers/test_cn_index_valuation.py`（新）
- `docs/contracts/README.md`（契约状态更新）

## Stop criteria

- 以上 8 条验收标准全部满足。
- 手动 smoke 验证通过（能看到真实中国宏观数据进入 AnalysisContext）。
- `STATUS.md` 更新（记录本任务完成及下步计划）。
