# 个人持仓数据入口：细粒度分析与数据结构优化重构文档

> ARCHIVED / SUPERSEDED:本文是 S2 立项前设计稿。S2 工程实现后,现行 schema 以
> `stocks/DATA_MODEL.md` 为准,任务状态以根目录 `EXECUTION_PLAN.md` 为准。
> 本文只作为设计来源和历史依据。

> 生成日期：2026-07-03
> 性质：调研/设计稿（对应 PLAN §6 规则，建议放入 `docs/archive/` 并在决策日志登记）
> 关联文档：`docs/archive/ASSET_DATA_MODEL_REFACTOR_20260704.md`（已有英文初稿，本文是对它的中文化、核验与修订，二者结论大方向一致，差异点见 §8）

---

## 1. 现状：持仓入口的真实能力边界（逐行核验）

系统对个人资产的唯一入口是 `FinancialAsset`（`stocks/domain/models.py`）：

```
name / platform / amount / asset_type / notes / confirmed
currency / instrument_key / quantity / tradable
amount_cny / conversion_status / conversion_source / conversion_rate  ← 运行时派生，不落盘
```

那个 Agent 给你的回复**基本准确**，逐条核验如下：

| Agent 的说法 | 核验结果 |
|---|---|
| 市场只支持 a/us/crypto | ✅ `_SUPPORTED_INSTRUMENT_MARKETS = {"a","us","crypto"}`，港股 `hk:xxxx` 会直接抛 ValueError |
| 没有子账户层级，扁平列表 | ✅ `financial_assets.json` 就是一个 list，`platform` 只是字符串标签 |
| 锁定资产只能写 notes | ✅ `tradable` 有，但 `PortfolioMapping` 的 `locked_assets_present` 走的是 `asset_type` 关键词映射（scaffolds.py 只认 "保险"/"锁定"/"locked"），`tradable=false` 并不参与任何护栏逻辑 |
| 没有成本价、盈亏、历史交易 | ✅ 完全没有。`recent_advice.performance` 只对比"建议日 vs 最新收盘"，与你的持仓成本无关 |
| 汇率非实时抓取所有币种 | ✅ `exchange_rate.py` 只支持 USD→CNY 一条链路（fixed > cache 6h > 免费API > 过期cache > 硬编码7.2），HKD 等直接 `failed`，该资产不计入 CNY 合计 |

还有两个 Agent 没说透、但对你影响更大的问题：

**问题 A：`asset_type` 是自由文本 + 关键词映射，分桶极脆弱。**
`scaffolds.py` 用一张关键词表把 `asset_type` 映射到 权益/固收/现金/黄金/锁定 五个桶。你的样本里会踩的坑：
- "黄金ETF" → 黄金，但"贵金属"（建行黄金）不在表里 → 落入未知桶；
- "QDII"、"固收+"、"混合基金" 都不在表里；
- 华安黄金ETF联接如果被写成 "基金" → 会被归入**权益**而不是黄金；
- SGOV 若写成 "ETF" → 权益（它实际是类现金）。
分桶错了，下游 `DriftCheck`（对照 `portfolio_constraints.json` 的 权益25-65%/固收15-50%/现金5-30%/黄金0-15%）全部失真，而这正是系统给出"加仓/减仓"建议的锚。

**问题 B：包装形式（wrapper）和风险暴露（exposure）被一个字段承担。**
你的黄金暴露横跨三种包装：建行贵金属（银行资产）、华安黄金ETF联接（场外基金）、NEM（美股股票）。一个 `asset_type` 字符串只能记录其中一个维度，所以系统**结构上不可能**算出"黄金总暴露 16-17%、已贴近约束上限 15%"这种对你最有价值的结论。纳指暴露（两只QDII联接 + NVDA + 支付宝渠道）同理。

结论：那个 Agent 说"它是投资顾问上下文工具，不是资产记账软件"在 VISION v1.0 时代是对的；但 VISION 已升级为**个人投资分析师系统**（2026-07-03 用户裁决），六个能力域里"调仓与操作指导"明确要求落到"动什么、动多少、钱从哪来、账户/币种约束"——现有入口模型撑不住这个愿景，重构是愿景拉动的，不是过度设计。

关于"简化成大类"的那段风险分析，我认同它的每一条，且可以用代码佐证：大类录入后 `instrument_key` 为空 → `context_builder._holding_text` 无法把行情和持仓关联 → `action_signals` 只对 watchlist 发信号而与你的仓位脱钩 → 建议退化为市场简报，这恰恰是 VISION 成功标准 2 明确否定的形态。**大类录入等于主动放弃切片 1（建议闭环）的全部价值。**

---

## 2. 数据三分法：脱敏材料逐项裁决

原则（与现有 `to_storage_dict` 的"派生不落盘"纪律一致）：

1. **只持久化事实源**：用户确认的、市场数据推不出来的事实；
2. **能算的一律派生**：任何"最新价 × 数量"能得到的数字，落盘即腐烂；
3. **叙述性结论既不落盘也不录入**：它们应是系统的输出，不是输入。

### 2.1 必须结构化保留（事实源，市场数据无法恢复）

| 脱敏材料中的事实 | 为什么必须保留 | 去处 |
|---|---|---|
| 账户归属（A股券商/IBKR/支付宝/建行/香港保险） | 决定资金调拨路径、币种、执行场所；"钱从哪来"依赖它 | `accounts[]` + `position.account_id` |
| 币种（CNY/USD） | CNY 基准报表与汇率风险 | `position.currency` |
| 标的代码+市场（510300、ITA…） | 行情/历史/新闻/事件/信号关联的唯一钥匙 | `instrument.instrument_key`（沿用现格式） |
| 持有数量（2100股、18股、30股…） | 市值与盈亏计算的乘数 | `holding.quantity` |
| **成本价/成本金额**（4.796、228.23、100.50…） | 未实现盈亏、亏损源分析（XLE）、税务/纪律参考；行情推不出 | `holding.cost_basis`（新增，现模型完全缺失） |
| 产品类型（ETF/股票/场外基金/理财/保险/贵金属/货基） | 决定估值方法与数据源路由 | `classification.product_type`（受控枚举） |
| 经济资产类别（权益/固收/现金等价/商品/保险） | 驱动分桶与 DriftCheck，替代关键词猜测 | `classification.asset_class`（受控枚举） |
| **暴露标签**（gold、nasdaq100、energy、defense、gold_miner…） | 跨包装聚合集中度的唯一途径（黄金三处、纳指四处） | `classification.exposure_tags[]` |
| 可交易性/可调仓性（保险锁5年、建行黄金想锁仓） | 护栏：不能建议动实际动不了的钱 | `liquidity.tradable` + `liquidity.rebalance_eligible`（两者语义不同：支付宝黄金可交易也可调仓；建行黄金可交易但你**不想**让它进调仓建议） |
| 流动性档位与赎回规则（T+0现金 / T+1基金 / 持有期产品 / 保险锁定） | "可动用现金"计算与危机预案 | `liquidity.tier` + `redemption_rule` + `lockup_until` |
| 无法计算时的**渠道口径金额**（余额宝≈7.5万、理财≈20.2万、保险5万USD） | 缺份额/净值时的唯一估值来源 | `valuation_input.method=manual_amount` + `as_of`（必须带时间，过期降级 data_quality） |
| 渠道报告的收益（理财+1.03万、纳指基金+1.88万、建行黄金-6.5万） | 在补齐份额/成本前是外部快照，用于对账，不冒充计算值 | `reported_performance {pnl, as_of, source}` |
| 缺口清单（材料第十一节整节） | 让 Agent 能解释"为什么这条建议置信度低/被阻断" | `data_completeness.missing_fields[]`（机器可读） |

### 2.2 可以完全省略——市场数据 + 上述事实即可派生

材料里大量"需行情查询""需根据现价计算"的占位行，全部不入库：

- 当前价格、当前市值、CNY 折算值（quote/NAV × quantity × FX，FX 沿用现有 `conversion_*` 溯源纪律）；
- 未实现盈亏、盈亏比例（有 quantity + cost_basis 后纯派生；上市持仓 9 只当天即可全部点亮）；
- 账户总额、账户内现金比例（"账户总额约7-8万"这类数字**不要落盘**——持仓求和即得，落盘必与明细漂移）；
- 账户占比、总资产占比、组合权重；
- **材料第七/八/九/十节的全部聚合段**（黄金汇总、纳指汇总、能源军工汇总、类现金汇总）——这正是 `exposure_tags` 派生视图，人工维护必然过期；
- "主要盈利来源 ITA / 主要亏损来源 XLE"（PnL 排名派生）；
- "A股账户现金比例较高 / 美元现金极低"（派生标签）；
- 材料第十二/十三节的监控优先级清单（由 `valuation_input.method` 自然导出：market_quote→高频，fund_nav→日频，manual_amount→低频）。

### 2.3 直接舍弃——不入库、也不作为字段设计目标

- 所有叙述性定性（"不是大仓位""只是小底仓""偏防御""与黄金相关性较高"）：要么是派生结论，要么属于 investor_profile 的偏好陈述，不是资产事实；
- 交易所字段作为必填（NYSEARCA/NYSE/上交所）：`instrument_key` 已含市场，交易所可选保留即可，不值得成为完备性要求；
- 逐笔历史交易、税务批次（tax lots）：切片纪律下明确 out of scope，等复盘能力域被用户裁决"要加厚"再说；
- 券商实际账户号：安全边界，不存。

---

## 3. 目标模型：`financial_assets.json` schema v2

单文件 JSON，不引入数据库（PLAN §6 禁止无裁决的重依赖）。骨架：

```json
{
  "schema_version": 2,
  "base_currency": "CNY",
  "accounts": [ ... ],
  "positions": [ ... ]
}
```

### 3.1 Account（新增层级，解决"扁平列表"）

```json
{
  "account_id": "cn_broker_a",
  "display_name": "A股证券账户",
  "institution_type": "brokerage | fund_platform | bank | insurance | manual",
  "market_scope": ["a"],
  "base_currency": "CNY",
  "default_liquidity_tier": "t0",
  "notes": null
}
```

你的五个账户：`cn_broker_a`（A股券商）、`us_broker`（IBKR）、`cn_fund_platform`（支付宝）、`cn_bank`（建行）、`hk_insurance`（中银人寿）。

### 3.2 Position（统一表达持仓、现金、手工资产）

```json
{
  "position_id": "us_broker_XLE",
  "account_id": "us_broker",
  "display_name": "Energy Select Sector SPDR",
  "currency": "USD",
  "classification": {
    "asset_class": "equity",
    "product_type": "exchange_traded_fund",
    "subtype": "sector_etf",
    "exposure_tags": ["us", "usd", "us_equity", "energy"]
  },
  "instrument": {
    "instrument_key": "us:XLE",
    "exchange": "NYSEARCA",
    "quote_kind": "exchange_quote"
  },
  "holding": {
    "quantity": 90,
    "unit": "share",
    "cost_basis": { "method": "average", "unit_cost": 57.11, "currency": "USD" }
  },
  "valuation_input": { "method": "market_quote" },
  "liquidity": { "tradable": true, "rebalance_eligible": true, "tier": "t1" },
  "reported_performance": { "unrealized_pnl": -353.80, "as_of": "2026-07-02", "source": "broker" },
  "data_completeness": { "missing_fields": [] },
  "confirmed": true,
  "notes": null
}
```

### 3.3 受控词表

`asset_class`：`cash / cash_equivalent / fixed_income / equity / commodity / insurance / alternative / unknown`

`product_type`：`cash / money_market_fund / bank_wealth_management / fixed_income_plus_fund / mixed_fund / qdii_fund / feeder_fund / exchange_traded_fund / stock / short_treasury_etf / precious_metal_account / insurance_policy / manual_asset`

`liquidity.tier`：`cash / t0 / t1 / t2_plus / periodic_open / locked / unknown`

`valuation_input.method`（估值路由，是本次重构的枢轴字段）：

| method | 适用 | 需要的事实 | 升级路径 |
|---|---|---|---|
| `market_quote` | 9只上市持仓 | instrument_key + quantity | 已就绪，接现有行情层 |
| `fund_nav` | 5只场外基金 | 基金代码 + 份额 + 成本净值 | 补齐代码份额后从 manual_amount 升级；需新增基金净值 Provider（切片候选，勿抢跑） |
| `manual_amount` | 现金/理财/货基/暂缺数据的基金 | 金额 + **as_of** | 永久合法的兜底 |
| `precious_metal_quote` | 建行黄金（补齐克数+成本价后） | 品种/克数/成本价 | 未补齐前留在 manual_amount |
| `insurance_value` | 保险 | 现金价值或退保价值 + as_of | 保单面额≠可投资价值，未补齐前估值质量标记 limited |

`exposure_tags` 初始词表（可扩展，需归一化）：`cn / us / usd / cny`、`cn_equity / us_equity / fixed_income / cash_like`、`csi300 / dividend_low_vol / star50 / nasdaq100`、`energy / defense_aerospace / ai / semiconductor / tech_growth / utilities_power`、`gold / gold_miner / precious_metals`、`short_treasury / money_market / bank_wmp`、`locked / qdii_delayed_nav`。

关键设计：**wrapper 与 exposure 正交**。NEM = `product_type: stock` + `tags: [gold, gold_miner, us_equity]`；华安黄金联接 = `product_type: feeder_fund` + `tags: [gold]`；建行黄金 = `precious_metal_account` + `tags: [gold]`。黄金集中度 = 按 tag 求和的派生视图。

### 3.4 运行时派生（进 AnalysisContext，不写回资产文件）

沿用 `amount_cny` 的既有纪律，扩展为每持仓快照：最新价/净值、价源、as_of、原币市值、CNY 市值、FX 率与来源、未实现盈亏、盈亏比、账户权重、组合权重、stale/manual/degraded 标记；组合级新增三个派生节点：

- `exposure_summary`：按 tag 聚合的暴露占比（直接回答"黄金 16-17% 是否贴近 15% 上限"）；
- `liquidity_summary`：可动用现金 / 类现金但受限 / 锁定资产 三档合计（替代现在只看"现金桶占比"的 `liquidity_status`）；
- `completeness_summary`：缺字段持仓数与被阻断的分析能力清单。

`DriftCheck` 的分桶改为读 `asset_class`（确定性映射到 权益/固收/现金/黄金），彻底废除 `scaffolds.py` 的关键词猜测表。

---

## 4. 建议护栏（重构的"为什么"，写进 decision 层约束）

1. `rebalance_eligible=false` 的持仓不得出现在任何建议的资金来源里（保险 5 万 USD、建行黄金若你标记锁仓）；
2. `tradable=false` 不得收到 `reduce/exit` action；
3. `valuation_input.method=manual_amount` 且 `as_of` 过期（建议阈值 30 天）→ 计入净值但降级/阻断精确调仓建议；
4. 上市持仓无行情 → `no_data`，禁止按"不变"处理（与现有"先诚实再博学"红线一致）;
5. 无代码/份额的场外基金只能收到大类配置建议，不得生成价格触发器；
6. 集中度检查必须走 `exposure_tags` 聚合，不得只看单一持仓占比。

---

## 5. 你的资产映射结果（重构后即时收益）

| 资产群 | v2 表达 | 立即点亮的能力 |
|---|---|---|
| 4 只 A 股 ETF | market_quote + quantity + cost_basis | 逐标的市值/盈亏/触发器/action_signals 关联，现有行情层零改动 |
| 5 只美股持仓 | 同上 + USD→CNY 派生 | XLE 亏损源、ITA 盈利源自动排名；SGOV 正确归入 cash_equivalent 而非权益 |
| 5 只场外基金 | manual_amount + tags（补齐代码份额后升 fund_nav） | 纳指/黄金暴露即刻可聚合；净值精算等 Provider 切片 |
| 建行活期/理财/货基 | manual_amount + 各自 liquidity.tier | "可动用资金"不再把持有期理财当活钱 |
| 建行黄金 | manual_amount + gold tag + reported_performance(-6.5万) | 浮亏进对账视图；补克数/成本价后可升级 |
| 保险 | insurance_value(缺口显式) + locked + rebalance_eligible=false | 永不再被当作可调配资金 |

录入成本评估：9 只上市持仓每只 4 个事实（key/数量/成本/账户），5 分钟级；其余按 manual_amount 抄现值。这直接回答你最初的问题——**不需要在"精细录入"与"大类简化"之间二选一**：v2 允许同一文件里 market_quote 精细持仓与 manual_amount 大类资产共存，各自带完备性标记，缺什么、损失什么分析能力，系统显式告诉你。

---

## 6. 迁移方案（保持 v1 兼容，符合切片纪律）

**Step 1 — schema v2 与加载兼容**：`persistence.py` 识别 list（v1）与 `{schema_version:2}`（v2）两种形态；v1 字段确定性映射（`name→display_name`、`platform→account.display_name`、`asset_type→classification`（映射表）、`amount→manual_amount`、其余同名迁移）；禁止从 notes 自动解析成本/代码（沿用"长期记忆只在用户确认后写入"红线）。

**Step 2 — 完备性与行情宇宙检查**：确定性告警——已映射持仓不在 quote 宇宙、上市持仓缺 quantity、有 quantity 缺 cost、manual_amount 缺 as_of、不支持的币种、锁定资产混入资金来源。

**Step 3 — 派生视图接入 AnalysisContext**（v11→v12，三处同步：DATA_MODEL.md + models/builder + schema 断言测试，schema 版本只增不减）：exposure_summary / liquidity_summary / 每持仓估值快照 / data_quality 新节点。

**Step 4 —（独立切片，需用户裁决后开工）**：中国基金净值 Provider、贵金属报价 Provider、USD 之外的 FX。**在用户补齐基金代码/份额之前不要建 Provider**——为没人能用的数据建数据源违反价值拉动规则。

明确 out of scope：交易流水台账、税务批次、券商 API 同步、自动记账对账、notes 自动解析、数据库化。

---

## 7. 数据缺口的补齐优先级（给你自己的行动清单）

1. **零成本高收益**：9 只上市持仓已具备全部字段，录入即得完整盈亏监控；
2. **高收益低成本**：5 只场外基金的基金代码 + 份额 + 成本净值（支付宝页面均可查），解锁纳指/黄金暴露的精确计算；
3. **中收益**：建行黄金的克数 + 成本金价（解释 -6.5 万浮亏、监控黄金总暴露 vs 15% 约束上限）；
4. **低频但关键**：保险的现金价值/退保价值/可提取日（决定它在净值里怎么算，以及危机预案里能不能碰）；
5. 理财产品的赎回规则/开放日（影响"可动用资金"精度）。

---

## 8. 与已有英文初稿（ASSET_DATA_MODEL_REFACTOR_20260704.md）的关系

本文与该稿结论一致处直接采纳（accounts/positions 分层、classification 三元组、valuation_input 路由、liquidity 一等公民、completeness 机器可读、派生不落盘）。修订/补充点：

1. 初稿未指出 `scaffolds.py` 关键词分桶的具体失效路径（"贵金属"/"QDII"/"固收+"落桶错误 → DriftCheck 失真），本文 §1 问题 A 给出代码级依据，这是重构最强的"现在就疼"证据；
2. 初稿把 `reported_performance` 一笔带过，本文明确它是**对账快照**语义（含 as_of/source），并规定上市持仓的 computed PnL 永远优先；
3. 明确 `rebalance_eligible` 与 `tradable` 的语义分离（建行黄金：可交易但用户意愿锁仓）——这是你与 Agent 对话里"建议卖黄金但我锁仓"冲突的直接解法；
4. 补充 DriftCheck/PortfolioMapping 的接线改造（初稿只改了输入端，没改消费端）；
5. 给出 AnalysisContext v11→v12 的三处同步要求与迁移的切片边界，使其可直接转为 EXECUTION_PLAN 任务卡。

---

## 9. 一句话结论

现有 `FinancialAsset` 是"上下文工具包"时代的合理最薄版本，但它的三个结构缺陷——自由文本分桶、包装与暴露不分、流动性不建模——使系统无法兑现已裁决的"个人投资分析师"愿景中最值钱的部分（跨包装集中度、可动用资金、可问责盈亏）。重构不加重系统：仍是单 JSON 文件、仍只持久化用户确认的事实、派生仍不落盘；它只是把你资产里**市场数据永远推不出来的那 12 类事实**给了一个受控的家。
