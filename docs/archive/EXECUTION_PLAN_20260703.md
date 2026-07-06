# EXECUTION_PLAN.md — 修复与收口执行清单

> ARCHIVED / SUPERSEDED:本文是 2026-07-03 归档的旧执行清单和证据留存,无现行行动效力。
> 现行任务入口以根目录 `EXECUTION_PLAN.md` 为准;本文只用于追溯历史完成证据。

> 生成时间:2026-07-02
> 最近修订:2026-07-03(Phase D 完成 + Phase F 重新验收 + Phase G 开工)
> 来源:对全部文档(约 9,600 行)与全部核心代码(约 5,900 行)的交叉审计
> 原文用途:曾供执行 Agent 按顺序落地;归档后不再是行动清单。
> 执行原则:每个任务动手前先用 grep/读码验证前提(标注为【验证前提】的行),防止基于过时快照误改。

---

## 状态核验附记(2026-07-03,基于对工作区代码的逐文件独立核验与后续证据收口)

本清单曾被标记为全部完成并打 tag `v2.1-phase2-complete`,后续独立核验发现 P2 组存在虚报完成记录。当前已完成 P2 证据收口与 Phase M 建议闭环实现。已核验的真实状态:

- **核验通过,维持完成**:P0 全部、P1 全部(文件修改与任务描述吻合)、P3 全部(`llm_enhancer.py`/`llm_utils.py` 已物理删除、engine.yaml 已清理)、P4 全部、P5 全部(docs/archive 17 份、根目录 6 份文档)。
- **P2 已重新核验完成**:P2-1、P2-2 剩余项、P2-3、P2-4、P2-5 均已补齐代码行号、测试名与全局验收证据。
- **Phase M 已完成**:M-1~M-5 均已带证据完成,并通过 `tests/engine/test_advice_loop.py` 端到端守门测试。
- **Phase D 已完成**:D0-4、D1、D2 与 Phase D 出口已逐段提交并经真实链路验收；外部接口合法空结果保持为空，不以伪造数据满足数量条件。
- **Phase F 已重新验收**:F0 修复后默认测试恢复；D1/D2 完成后真实 37/37 标的均有 ≥40 bars，rotation 顶层采用最旧 as_of，accumulate 强度门槛排除微小正收益，触发复盘与未来事件语义均通过。
- **新增 Phase G**:按用户裁决规划内部 LLM 默认开启 + 用户 Agent delegate 双路径、结构化 DecisionPlan、持仓映射/仓位计算、机会评分、事件生命周期、触发监控与轻量效果校准;G 的硬前置是 F0 修复及 Phase D 出口通过。
- tag `v2.1-phase2-complete` 曾打在未完成状态上,不作为完成依据;是否删除/重打由用户决定。
- 对执行 Agent 的新增硬性要求:**完成记录必须附可复现的验证证据**(如 `grep -n` 的命中行号、测试名),只写 commit hash 不再被接受;虚报完成视为最严重违规。

---

## 使用说明(执行 Agent 必读)

1. **当前执行队列(2026-07-03 更新)**:P0/P1/P2/P3/P4/P5/M、F0、D0~D2 与 Phase F 已核验完成,勿重做。当前严格执行 **G0→G7**，把证据层产品化为个人 DecisionPlan。任一阶段出口未通过不得越级。
2. 每完成一个任务,运行全局验收(见文末),全部通过后才能进入下一任务。
3. 勾选格式:完成后把 `- [ ]` 改为 `- [x]`,并在任务末尾追加一行 `> 完成:<commit hash> <一句话说明>`。
4. 遵守 `PLAN.md` 第 4 节禁止事项:不引入重型依赖、不伪造指标、不提交 `.local/`、`.secret/`、缓存。
5. **LLM/Agent 双路径不得绕过结构化校验**:internal_llm 与 agent_delegate 必须生成同一 DecisionPlan schema;URL/key 缺失返回 setup_required 或显式选择 agent_delegate,禁止静默返回普通摘要。
6. 任何任务如果发现前提不成立(代码已改过、文件不存在等),不要硬改——在任务下记录 `> 跳过:<原因>` 并继续。

---

## P0 — 安全(立即执行,优先级高于一切)

### P0-1 处理 `stocks/DATA_SOURCES.md` 中泄露的明文 API Key

**问题**:该文件(git 追踪)明文包含 GNews / Juhe / Finnhub 的真实 API Key。

- [x] 确认泄露范围：检索已知泄露 key 及文件中所有形如 key 的字符串；用 `git log --all -p -- stocks/DATA_SOURCES.md` 确认是否已进入历史（执行输出必须脱敏）。
- [x] 从 `stocks/DATA_SOURCES.md` 中删除所有真实 key,替换为 `<从 .secret/ 读取>` 占位说明。
- [x] 若仓库曾推送到任何远端(GitHub 等):**在文档中显著标注这些 key 必须作废轮换**,并在完成报告中提醒用户手动到各服务商控制台轮换(Agent 无法代做)。
- [x] 检查 `.gitignore` 覆盖 `.secret/`、`secret/`、`.local/`;注意仓库根目录存在 `secret/finnhub-key.md`(无点前缀),确认它是否被 git 追踪:`git ls-files secret/`,若被追踪则移入 `.secret/` 并从索引移除。

> 完成:78a9e5e 清除文档与被追踪文件中的真实凭据，补齐忽略规则并标注远端历史凭据必须轮换。

**验收**:`git grep -iE "(apikey|api_key|token)" -- "*.md"` 无真实凭据;真实 key 只存在于 `.secret/` 下未追踪文件。

### P0-2 汇率缓存移出 `.secret/`

**问题**:`stocks/engine/exchange_rate.py` 把非敏感的汇率缓存写入 `.secret/exchange-rate-cache.json`,密钥目录被当通用存储,且路径硬编码相对源码树。

- [x] 缓存路径改为运行态数据目录(建议 `data/cache/exchange-rate-cache.json` 或与 `HistoryCache` 同级目录),确保 gitignore。
- [x] `.secret/` 目录只允许存放凭据类文件。

> 完成:7f0e538 汇率缓存迁移至忽略的 `data/cache/`，并将现有运行态缓存移出 `.secret/`。

**验收**:运行 CLI 后 `.secret/` 无新增非凭据文件;新缓存路径未被 git 追踪。

---

## P1 — 修复静默错误信号(这些 bug 在给 LLM 喂错误骨架,危害大于缺数据)

### P1-1 `check_drift` 漏报空 bucket 违规

**文件**:`stocks/engine/scaffolds.py`
**问题**:只遍历 `mapping.ratios` 中存在的 bucket;某类资产占比为 0(如现金归零)时 bucket 缺失,`min` 约束违规永远不报——恰恰是最需要报警的场景。

- [x] 【验证前提】读 `check_drift`,确认遍历基准是 mapping 现有 bucket 而非约束文件的全部 bucket。
- [x] 改为以 `portfolio_constraints.json` 中声明的所有 bucket 为遍历基准,缺失 bucket 按 ratio=0 参与 `min` 检查。
- [x] 新增测试:现金 bucket 为空 + `cash min 5%` 约束 → 必须产出 below_min 违规。

> 完成:f237c51 约束声明成为 drift 遍历基准，缺失资产桶按 0% 报告最低占比违规。

**验收**:新测试通过;既有 drift 测试不回归。

### P1-2 `MarketScaffold` 死分支与错误分类

**文件**:`stocks/engine/scaffolds.py`
**问题**:四个独立缺陷——
(a) `safe_haven_changes`/`rates_changes` 仅在 `market in ("gold","commodity")/("bond","rates")` 时填充,而系统实际 market 只有 `a/us/crypto`,故 `safe_haven_state`/`rates_state` 恒为 `"unknown"`;
(b) 黄金 ETF(如 518880)按 market="a" 被算进"中国权益";
(c) BTC/ETH 涨跌完全被忽略(crypto 不匹配任何分支);
(d) 科技股靠名字子串匹配("nvda"/"aapl"…),中文名标的(如"高通"QCOM)匹配不上。

- [x] 【验证前提】逐条在代码中确认上述四点。
- [x] 引入标的分类配置:在 `stocks/config/watchlist.json` 每项增加可选 `category` 字段(如 `equity_cn / equity_us / tech / gold / bond / crypto`),或新建轻量映射表;分类判断一律走配置,**删除名字子串匹配**。
- [x] 基于 category 修复:黄金 ETF 归入 safe_haven 分支;crypto 涨跌纳入独立 `crypto_state`;无对应类别数据时输出 `"no_data"` 而非伪装的 `"unknown"`。
- [x] 新增测试:518880 不进中国权益、进 safe_haven;BTCUSDT 波动反映到 crypto_state;QCOM 按配置归类。

> 完成:c2470d1 市场状态改为 category 驱动，新增 crypto_state，并统一缺类数据为 no_data。

**验收**:用当前 watchlist 跑 `build_context`,`market_state` 中不再出现恒定 `"unknown"`(除非确实无该类标的,此时为 `"no_data"`)。

### P1-3 删除无意义的 `volatility` 指标

**文件**:`stocks/engine/indicators.py`
**问题**:现实现为"收益率标准差 / 收益率均值",不是任何标准波动率定义;均值近零时爆炸,靠 `1e-12` 阈值掩盖,输出是噪声。

- [x] 改为标准定义:`volatility_20 = 收益率20日标准差 × sqrt(252)`(年化历史波动率),数据不足返回 None。
- [x] 更新对应测试的期望值;确认 `PLAN.md` Phase 2B 指标清单描述一致。

> 完成:e982304 使用 20 日收益率样本标准差计算年化历史波动率，并覆盖零均值稳定性。

**验收**:固定样本下数值稳定可解释;近零均值序列不再产生极端值。

### P1-4 资产币种腐蚀 bug(USD 被永久换算为 CNY)

**文件**:`stocks/engine/__init__.py`(`_load_assets_from_file` / `_save_assets`)
**问题**:加载时把外币按当日汇率换算并将 `currency` 写死为 `"CNY"`;任何一次保存都会用换算后金额覆盖原文件,原始币种金额丢失(只剩 notes 文本)。

- [x] 数据模型分离两个概念:`amount` + `currency` 保持**原始录入值不变**;换算结果放派生字段(如 `amount_cny`,仅存在于内存/上下文输出,不写回文件)。
- [x] `_save_assets` 只写原始值。
- [x] 新增测试:加载 USD 资产 → save → 重新加载,原始 USD 金额与 currency 不变。

> 完成:53c438d 原始币种金额与 CNY 派生估值分离，保存不再覆盖原值，并兼容恢复旧版腐蚀数据。

**验收**:round-trip 测试通过;`AnalysisContext` 中仍能拿到 CNY 口径合计。

### P1-5 汇率兜底与 HKD 1:1 换算

**文件**:`stocks/engine/exchange_rate.py`
**问题**:USD/CNY 失败时硬编码 7.2 且不标注;HKD 等币种按 1:1 换算仅打 warning——估值层面的实质错误。

- [x] 兜底策略改为:优先用本地缓存的上次成功汇率(带时间戳),并在结果中携带 `source: "stale_cache"|"hardcoded_fallback"` 标记,透传进 `data_quality`。
- [x] HKD 等未支持币种:禁止静默 1:1。要么接入该币种汇率,要么该资产标记 `conversion: "failed"`、不计入 CNY 合计,并在 `data_quality` 报告。
- [x] 新增测试:网络失败时返回 stale 标记;HKD 资产不再按 1:1 汇入合计。

> 完成:992978e 汇率结果携带来源与状态，失败换算不计入合计，所有降级在 data_quality 可见。

**验收**:任何非精确换算在 `data_quality` 中可见,LLM 上下文不再收到无标注的错误估值。

### P1-6 `HistoryCache` 同日双计

**文件**:`stocks/engine/history_cache.py`
**问题**:`_merge_and_deduplicate` 按精确 timestamp 去重;provider 日 K(00:00 UTC)与实时 `record()`(当前时刻)可能同一交易日双计,污染指标计算。

- [x] 去重粒度改为"交易日"(按标的市场时区取日期);同日多条时保留 provider 日 K 优先、实时记录次之。
- [x] 新增测试:同日一条日 K + 一条实时 record → 合并后仅一根 bar。

> 完成:d923254 HistoryCache 按市场时区交易日去重，同日优先 provider 日 K，并兼容旧缓存。

**验收**:指标输入序列无同日重复。

---

## P2 — 打通记忆回路(advisor 与 context toolkit 的分界线,VISION 核心)

### P2-1 暴露资产写入接口

**文件**:`stocks/adapters/cli.py`、`stocks/adapters/mcp.py`(HTTP 暂缓,见 P2-5)
**问题**:`StocksEngine.add_asset/remove_asset/update_asset` 已实现但三个 adapter 零暴露;MEMORY_RULES.md 的"对话维护资产"没有任何入口。

- [x] 【验证前提】grep 确认三个 adapter 中无 asset 写入调用。
- [x] MCP adapter 新增方法:`assets_list` / `asset_add` / `asset_update` / `asset_remove`,参数与 `FinancialAsset` 字段对齐;写操作要求显式 `confirmed: true` 参数(呼应 VISION"系统只在用户确认后更新记忆")。
- [x] CLI 新增对应 flag 或子命令(与现有单命令风格一致即可,不强行引入子命令框架)。
- [x] 新增测试:通过 adapter 走完 add → update → remove 全流程,落盘文件正确(依赖 P1-4 先完成)。

> 完成:b6bc27b CLI/MCP 暴露确认式资产 CRUD,并以 adapter 全流程测试验证落盘。| 证据:`stocks/adapters/mcp.py:51-58` 路由 asset 方法,`stocks/adapters/cli.py:161-203` 处理 asset flags,`tests/test_asset_adapters.py:27`/`:69` 覆盖 MCP/CLI CRUD round-trip。

**验收**:外部 Agent 能通过 MCP/CLI 完成资产维护,无需手改 JSON。

### P2-2 落地 `investor_profile.json`(偏好记忆)

**文件**:`stocks/engine/__init__.py`、新增 `stocks/data/investor_profile.example.json`
**问题**:代码读取 `investor_profile.json` 但该文件在仓库中不存在,连示例都没有,用户画像恒为空。

- [x] 定义最小 schema(建议字段:`risk_tolerance`、`investment_horizon`、`preferences`(自由文本数组)、`constraints`(禁投/上限类)、`updated_at`)。
- [x] 提交 example 文件;真实文件路径为 `.local/investor_profile.json`(gitignore),加载优先级与资产文件一致。
- [x] MCP/CLI 暴露 `profile_get` / `profile_update`(同样要求显式确认参数)。
- [x] `ContextBuilder` 确认 profile 注入 `raw_prompt_input` 的【用户画像】段。
- [x] 新增测试:有/无 profile 文件两种情况下 build_context 均正常,有文件时画像段非空。

> 完成:6cc4024 新增本地画像 schema 与 example,CLI/MCP 确认式更新,并验证 prompt 注入。| 证据:`stocks/adapters/cli.py:85`/`:179` 暴露并处理 profile flags,`stocks/adapters/mcp.py:61`/`:149` 路由并校验 `profile_update`,`tests/test_asset_adapters.py:98` 覆盖未确认拒写与落盘。

**验收**:CLI smoke 输出的 raw_prompt 含画像内容。

### P2-3 闭合历史快照回路

**文件**:`stocks/engine/__init__.py`、`stocks/engine/persistence.py`
**问题**:`build_context` 读 `load_recent(5)` 但 `save_context()` 全库零调用,`recent_snapshots` 永远为空,系统不记得上次说过什么。

- [x] 【验证前提】grep 确认 `save_context` 零调用。
- [x] `build_context` 成功后按配置写入快照(`engine.yaml` 的 `save_to_file` / `max_snapshots` 真正生效——同时消灭这两个僵尸配置);快照目录 gitignore。
- [x] 快照内容最小化:时间戳、组合概要、market_state、drift 结果,不必存全量新闻。
- [x] 实现 `max_snapshots` 滚动清理。
- [x] 新增测试:连续两次 build_context,第二次的 `recent_snapshots` 非空且能对照差异。

> 完成:c6e5c87 build_context 保存最小滚动快照,第二次构建注入上次组合状态用于对照。| 证据:`stocks/engine/__init__.py:543-559` 先读近期快照后写入新快照,`stocks/engine/persistence.py:27`/`:77` 保存最小快照并滚动清理,`tests/engine/test_end_to_end.py:180` 覆盖第二次构建含上次快照。

**验收**:第二次运行的上下文中包含"上次快照"信息,LLM 可做前后对照。

### P2-4 把 `personal_advice_prompt.txt` 接进主链路

**文件**:`stocks/engine/llm_analysis.py`、`stocks/prompts/`、`AGENT_GUIDE.md`
**问题**:全库质量最高的 prompt(反幻觉、金额脱敏、格式规范)没有任何代码加载;`llm_analysis` 用的是内联弱 prompt,且两者对"是否暴露金额"要求直接矛盾。

- [x] `LLMAnalysis.generate_report()` 改为从 `stocks/prompts/personal_advice_prompt.txt` 加载系统 prompt,内联 prompt 删除。
- [x] 在 `AGENT_GUIDE.md` 增加一节:外部 Agent 作为主脑时,应读取该 prompt 文件作为分析指引(明确它是给内部 LLM 和外部 Agent 共用的"分析宪法")。
- [x] 解决金额矛盾:`raw_prompt_input` 中资产金额改为**占比 + 量级区间**表达(遵循 prompt 的脱敏要求);精确金额仅保留在结构化 `to_dict()` 中,由调用方决定是否使用。
- [x] 删除死代码 `extract_constraints()`(其职能由 P2-2 的 profile_update 承接)或接线到 profile 更新流程——二选一,倾向删除。

> 完成:6cf7688 内外部分析统一加载 advice prompt,删除死提取器,并将 raw prompt 金额改为占比与量级。| 证据:`stocks/engine/llm_analysis.py:26`/`:61` 加载 shared prompt,`AGENT_GUIDE.md:13-14` 明确外部 Agent 读取约束,`stocks/engine/context_builder.py:647`/`:655` 输出总量级与逐项占比量级,`tests/engine/test_llm_analysis.py:8` 和 `tests/engine/test_context_builder.py:130-132` 覆盖 prompt 接线与 raw_prompt 脱敏;`rg "extract_constraints|_build_analysis_prompt" stocks/engine/llm_analysis.py` 无命中。

**验收**:`--llm-analysis` 输出遵循 advice prompt 的格式约束;raw_prompt 不再出现逐笔精确金额。

### P2-5 HTTP adapter 安全声明与最小防护

**文件**:`stocks/adapters/http.py`
**问题**:无鉴权接口全量输出资产精确金额;500 响应直接 `str(exc)` 外泄内部错误。

- [x] 默认绑定 `127.0.0.1` 强制校验(非 127.0.0.1 启动时要求显式 `--allow-remote` 并打印告警)。
- [x] 增加最简 Bearer Token 校验(从 `.secret/http-token` 读,文件不存在则拒绝非 localhost 请求)——不引入框架,标准库实现。
- [x] 500 响应改为通用错误消息 + 内部日志记录详情。
- [x] 资产金额输出遵循 P2-4 的脱敏口径,提供 `?include_amounts=true` 显式开关。

> 完成:671ee12 HTTP 默认本机绑定,远程强制 Bearer 鉴权,错误收口且金额默认脱敏。| 证据:`stocks/adapters/http.py:36-55` 校验远程绑定与 token,`:121` 校验 Bearer,`:202-216` 支持 include_amounts 并隐藏 500 详情,`:240` 递归移除金额字段;`tests/test_http_security.py:7`/`:23`/`:34`/`:51` 覆盖远程 token、Bearer、脱敏和通用错误。

**验收**:无 token 的远程请求被拒;错误响应不含堆栈/内部路径。

---

## P3 — 删减与止损(减法也是交付)

### P3-1 删除 LLM Enhancer 主链路

**文件**:`stocks/engine/llm_enhancer.py`、`stocks/engine/llm_utils.py`、`stocks/engine/__init__.py`
**问题**:逐条新闻串行 1-2 次 LLM 调用(20 条 = 最多 40 次请求,单次超时 360s),产出仅 importance/sentiment 标签,下游只作可选覆盖;违反 v2"语义处理归 Agent"的边界哲学,投入产出比全库最差。

- [x] 【验证前提】确认 enhancer 输出在下游仅被 `MarketEventExtractor` 可选消费。
- [x] 删除 `llm_enhancer.py` 及其装配、后台模型校验线程(`_validate_in_background`,同时消除其无锁竞态)、`_ENHANCER_FALLBACK_CHAIN`;CLI 的 `--llm-enhancer` flag 移除或改为报错提示已移除。
- [x] `MarketEventExtractor` 固定走 `rules_v1` 路径(它对自己"关键词启发式"的定位是诚实的,保留)。
- [x] 若不愿删除:降级方案为"单次批量调用"(一次请求处理全部新闻)+ 30s 超时,但**默认仍禁用**。倾向直接删。
- [x] 更新 README / AGENT_GUIDE 中相关段落。

> 完成:022132c 删除 Enhancer 主链路与后台模型校验，事件提取固定为诚实的 rules_v1。

**验收**:全量 build_context(含新闻)耗时回到秒级;pytest 全过。

### P3-2 清理死代码与僵尸配置

**文件**:`stocks/engine/fetchers.py`、`stocks/engine/__init__.py`、`stocks/config/engine.yaml`、`stocks/engine/config_loader.py`
**问题清单**(动手前逐条 grep 验证):

- [x] `DataFetcher.fetch_news()`:与 `NewsAggregator` 重复、忽略入参、new 无 URL 的 provider——删除。
- [x] `_replace_context_field`:换用 `dataclasses.replace`,删除手工实现。
- [x] `engine.yaml` 僵尸项逐一处理(实现或删除,不留假配置):
  - [x] `providers.fallback`(降级顺序)→ 让 `_pick_fallback_provider` 真正读取它,顺带消除对 `registry._providers` 私有属性的访问;
  - [x] `providers.*.timeout` → 传给 provider 或删;
  - [x] `cache.quote_ttl` / `news_ttl` → 实现或删;
  - [x] `save_to_file` / `max_snapshots` → 已由 P2-3 接线;
  - [x] `logging.desensitize` → 见 P3-3。
- [x] `HistoryCache.prune()`:在 engine 启动或每次 build 后调用,磁盘缓存获得清理路径。

> 完成:290be6e 删除重复入口与手工替换器，配置驱动 fallback，清除假配置并接通缓存清理。

**验收**:`engine.yaml` 中每个键都有消费者;grep 无剩余死路径。

### P3-3 日志脱敏:实现或删除配置

**文件**:`stocks/logging_utils.py`、`stocks/engine/config_loader.py`
**问题**:`desensitize: True` 是全库唯一出现"desensitize"的地方,无任何实现消费它——空头支票。

- [x] 实现最小脱敏 filter:日志消息中金额模式(`\d{4,}(\.\d+)?` 邻近资产上下文)与 key 模式(长十六进制/`sk-` 前缀)打码;挂到根 logger。
- [x] 或者:删除该配置项并在文档中如实说明"日志不脱敏,勿在共享环境开 DEBUG"。二选一,不允许保留假配置。（已选择实现配置行为）

> 完成:caa40b4 engine 初始化应用 logging.level/desensitize，日志参数格式化后统一脱敏。

**验收**:配置行为与实际一致。

---

## P4 — 数据底盘(输入质量决定一切上层价值)

### P4-1 更换/补充财经新闻源

**文件**:`stocks/config/engine.yaml`、`stocks/config/news_sources.json`、`stocks/engine/news_sources.py`
**问题**:默认唯一 RSS 源是 36kr(科技创业媒体,非财经媒体),market_events 输入质量从源头不成立。

- [x] 【验证前提】确认 `news_sources.json` 当前内容与 engine.yaml 实际加载哪一个。
- [x] 调研并接入至少 2 个可用的中文财经 RSS(候选:东方财富、新浪财经 RSS、华尔街见闻;逐一验证 RSS 可用性后再写入配置)+ 1 个英文源(如 Yahoo Finance RSS / Reuters 可用 feed)。
- [x] 36kr 降为可选源而非默认。
- [x] 新增测试:多源聚合去重正常。

> 完成:26a4418 配置中新网财经、Google News 中文财经和 Yahoo Finance，36kr 改为禁用可选源并实抓验收。

**验收**:默认配置抓到的新闻以财经内容为主。

### P4-2 Finnhub 异常分类失效

**文件**:`stocks/providers/finnhub_quote.py`
**问题**:`_fetch_sync` 以 `except Exception: return None` 吞掉所有异常,降级链的 typed error 分类对该 provider 失效,错误退化为"0 条行情"。

- [x] 按 `stocks/errors.py` 的异常体系分类抛出(网络超时→可重试,401/403→不可重试,429→限流);由降级链统一处理。
- [x] `fetch_batch` 串行逐个请求 + 免费档 60 次/分钟限流:加简单节流或至少文档标注上限。
- [x] 新增测试:mock 各类失败,确认 DegradationRecord 分类正确。

> 完成:af221e7 Finnhub 抛出 typed provider errors，免费档请求节流，降级记录保留错误分类。

**验收**:Finnhub 故障时 `data_quality` 能区分"限流/网络/鉴权",不再统一表现为空结果。

### P4-3 美股行情单点失败标注

**问题**:美股 quote 只有 Finnhub 单源,`_pick_fallback_provider` 找不到第二个 us provider。完整第二源(如接入免费额度的其他 API)成本较高,本阶段只做诚实标注:

- [x] 美股行情失败时,`data_quality` 明确标注 `us_quotes: single_source_failed`,并尝试用 `HistoryCache` 最近收盘价作 stale 兜底(带 `stale: true` 标记)。
- [x] 第二数据源列为 Phase 3 候选,写入 `PLAN.md` 暂缓区,本清单不展开。

> 完成:4084a00 美股单源失败显式标注并回填 stale 历史收盘价，第二源列入 Phase 3。

**验收**:Finnhub 挂掉时上下文仍有带 stale 标记的美股参考价。

---

## P5 — 文档收口(一次性做完,之后冻结)

### P5-1 归档过时文档

**问题**:`stocks/` 目录下整套 v1 文档与当前系统矛盾却自称"当前主线";v2 五份文档大量重复且部分结论已被互相推翻。

- [x] 新建 `docs/archive/`,移入:`stocks/README.md`(v1 版)、`stocks/ARCHITECTURE.md`、`stocks/LLM_DRIVEN_DESIGN.md`、`stocks/ANALYSIS_RULES.md`、`stocks/NEWS_INPUT_RULES.md`、`stocks/DATA_SOURCES.md`(P0-1 清理 key 之后)、`stocks/REFACTOR_PRINCIPLES.md`、`stocks/ROADMAP.md`、`ARCHITECTURE_BOUNDARY_ANALYSIS.md`、`DESIGN_GAP_ANALYSIS.md`、`LLM_ENHANCER_ANALYSIS.md`、三份 `LLM_QUANT_*.md`。每份文件头部加一行:`> ARCHIVED 2026-07:结论已吸收或废弃,勿作为开发依据。`
- [x] `MEMORY_RULES.md` 不归档——它是 P2 的需求规格,移到根目录或并入 AGENT_GUIDE。
- [x] `stocks/DATA_MODEL.md`:删除其中 v1 遗留段(AdvisoryPlan/AdvisorContext),保留现行 AnalysisContext schema 部分,作为现行 schema 文档。
- [x] `DESIGN.md` 与 `ARCHITECTURE_V2.md` 合并为一份 ≤300 行的 `ARCHITECTURE.md`(根目录),只描述**当前实际实现**,删除所有未实现的设计段(三级粒度、子命令 CLI 等);其余内容进 archive。
- [x] 修正 README.md / README.zh.md 中与实际不符的描述。

> 完成:fcc5811 归档旧规格，合并金融记忆规则，建立 201 行现行架构并重写双语 README。

**验收**:根目录活跃文档 ≤ 6 份(README ×2、AGENT_GUIDE、PLAN、ARCHITECTURE、EXECUTION_PLAN);任何一份活跃文档中引用的文件/命令/模块必须真实存在。

### P5-2 文档冻结规则写入 PLAN.md

- [x] 在 `PLAN.md` 禁止事项中追加:"新增 .md 文件前必须先证明现有文档无法承载;分析/调研类文档一律进 `docs/archive/`,不进根目录。"

> 完成:fcc5811 文档冻结规则已写入 PLAN.md。

---

## M — 建议闭环(Phase M,紧接 P2 返工之后,按编号顺序执行)

**目标**:不再堆行情和指标,让系统进入"个人投顾闭环"——建议可留痕、可引用、可回看,并守住"建议/事实/推断"边界与确认式写入、金额脱敏规则。

**硬前置**:P2-1 / P2-2(剩余项)/ P2-3 / P2-4 / P2-5 全部带证据完成后,M 组才能开工。M-2 复用 P2-1 的确认式写入模式,M-3 依赖 P2-3 的快照回路,M-5 依赖 P2-4 的金额脱敏——跳过 P2 直接做 M 是在断掉的记忆层上盖楼,禁止。

**开工基线**(M-1 动工前跑一次并留存):

```bash
uv run ruff check .
uv run python -m pytest -q
uv run python -m compileall -q stocks tests
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes --save /tmp/stocks-claw-baseline.json
```

### M-1 `AdviceRecord` 最小数据结构

**文件**:`stocks/domain/models.py`、`stocks/engine/persistence.py`、`stocks/DATA_MODEL.md`

- [x] 定义 `AdviceRecord`:`created_at`、`instruments`(list[{market, code, name}])、`direction`(每标的 buy/sell/watch/hold)、`rationale_summary`(≤500 字摘要,**不存 LLM 长文**)、`based_on`(引用的事实类别:quotes/news/indicators/macro/portfolio/profile)、`boundary`(每条理由标注 fact / inference,守住"建议、事实、推断"三者边界)。
- [x] 存储于 `.local/advice/`(gitignore),滚动上限 30 条,超限删最旧。
- [x] `AnalysisContext` 新增 `recent_advice` 字段 → `schema_version` 升 **v6**,同步 `stocks/DATA_MODEL.md` + 测试(红线:三处缺一即回滚)。

> 完成:a8fe792 定义 AdviceRecord、advice 滚动持久化与 AnalysisContext v6。| 证据:`stocks/domain/models.py:274`/`:402`/`:405` 定义 AdviceRecord、recent_advice 与 schema v6,`stocks/engine/persistence.py:86`/`:101` 保存和读取 advice,`stocks/DATA_MODEL.md:4`/`:100` 同步 v6 文档,`tests/engine/test_persistence.py:60`/`:78`/`:90` 覆盖 round-trip、滚动与校验,`tests/engine/test_context_builder.py:529-534` 覆盖 schema v6 与 recent_advice。说明:同步 schema 还必须更新 `context_builder.py`、`ARCHITECTURE.md` 和 schema 断言测试。

**验收**:AdviceRecord 落盘/读回 round-trip 测试;schema v6 三处同步有测试覆盖。

### M-2 确认式"保存建议摘要"接口

**文件**:`stocks/adapters/cli.py`、`stocks/adapters/mcp.py`
**依赖**:P2-1(复用其确认式写入模式)。

- [x] MCP 新增 `advice_save`(要求显式 `confirmed: true`,缺失即拒绝)与 `advice_list`。
- [x] CLI 新增对应入口(与现有单命令风格一致)。
- [x] 新增测试:确认式保存成功落盘;未确认拒写。

> 完成:cee3492 暴露 CLI/MCP 确认式建议保存与列表接口。| 证据:`stocks/adapters/mcp.py:63-66`/`:166`/`:369` 路由、保存与工具声明,`stocks/adapters/cli.py:95-100`/`:197` 暴露并处理 flags,`tests/test_asset_adapters.py:140`/`:161` 覆盖 MCP/CLI 未确认拒写、确认保存和列表读取。说明:为 adapters 提供最小调用面,同步新增 `stocks/engine/__init__.py:461`/`:485` 的 `save_advice`/`list_advice`。

**验收**:外部 Agent 能通过 MCP/CLI 保存建议摘要,未确认的写入被拒绝。

### M-3 `build_context()` 注入最近建议摘要

**文件**:`stocks/engine/__init__.py`、`stocks/engine/context_builder.py`
**依赖**:P2-3(快照回路)、M-1。

- [x] 注入最近 N 条(默认 3)建议摘要到 `AnalysisContext.recent_advice` 与 `raw_prompt_input` 的【上次建议】段。
- [x] 无建议记录时字段存在但为空,不伪造、不报错。

> 完成:64ac9fe build_context 注入最近 3 条建议摘要到结构化字段与 raw_prompt。| 证据:`stocks/engine/__init__.py:573-585` 加载并传入 recent_advice,`stocks/engine/context_builder.py:57`/`:125`/`:682` 写入 `AnalysisContext` 与【上次建议】段,`tests/engine/test_end_to_end.py:189` 覆盖无建议为空、保存后第二次上下文含上次建议。

**验收**:保存建议后第二次 build_context 的上下文含上次建议;无记录时 CLI smoke 正常。

### M-4 建议表现回看

**文件**:`stocks/engine/context_builder.py`(若超 100 行则独立为 `advice_review.py` 并在任务下注明)
**依赖**:M-1、已有 `HistoryCache`。

- [x] 对上次建议提及且在 watchlist 内的标的,用 HistoryCache 收盘价计算**自建议日至今的涨跌幅**;历史不足时输出 `status: "no_data"`,禁止伪造。
- [x] 结果并入【上次建议】段:每条建议附"当时方向 vs 此后实际表现"。**只并列事实,不打分、不下结论**——"说对了没有"的判断留给 Agent 主脑。

> 完成:d555aab 用 HistoryCache 为最近建议附加 watchlist 标的表现事实。| 证据:`stocks/engine/advice_review.py:18` 计算派生 performance,`stocks/engine/context_builder.py:114`/`:713` 接入并输出【上次建议】表现事实,`tests/engine/test_advice_review.py:66`/`:85` 覆盖涨跌幅与 no_data。说明:`advice_review.py` 为 115 行,按本任务约定从 `context_builder.py` 拆出。

**验收**:固定 fixture 测试(建议日 + 若干日 K → 涨跌幅正确;缺历史 → no_data)。

### M-5 建议闭环端到端测试

**文件**:`tests/engine/test_advice_loop.py`

- [x] 全链路单测(不依赖网络):生成上下文 → 确认式保存建议 → 第二次上下文包含上次建议与表现回看 → raw_prompt 金额脱敏仍成立(回归 P2-4)。

> 完成:a64c83e 新增建议闭环端到端守门测试。| 证据:`tests/engine/test_advice_loop.py:16` 覆盖生成上下文、确认保存建议、第二次上下文含建议与表现回看,`:91-94` 覆盖 raw_prompt 表现回看与金额脱敏回归。

**验收**:pytest 全过;该测试成为 Phase M 的出口守门测试。

**Phase M 出口标准**:M-1~M-5 全部带可复现证据完成;对照 VISION 成功标准,第 3 条(结合资产给出个人建议)与第 5 条(对决策真有帮助)从"一次性输出"升级为"可追踪、可回看"。

> 完成:a64c83e Phase M 出口标准通过。| 证据:M-1 `a8fe792`、M-2 `cee3492`、M-3 `64ac9fe`、M-4 `d555aab`、M-5 `a64c83e` 均带测试证据;本轮最终全局验收 `ruff`/`compileall`/`pytest 312 passed`/CLI smoke 全通过。

---

## F0 — Phase F 基线修复与完成状态纠偏(当前第一任务)

**背景**:2026-07-03 对提交 `b54dc3d` 独立复核。`ruff`/`compileall` 通过,但默认 pytest 收集 383 项后已出现至少 10 failures;Phase F 定向 40 项为 34 passed / 6 failed。F-3 文档声称存在的 `_review_trigger` 在 `stocks/engine/advice_review.py` 中不存在;旧 schema/提示词断言未同步;部分 Engine 测试因真实 `sector_scan.json` 被加载而触发外网历史回填,破坏测试隔离。此阶段只恢复诚实基线,不扩功能。

### F0-1 恢复默认测试与 schema 契约

- [x] 【验证前提】重跑 `uv run python -m pytest -q --maxfail=20`,逐个列出失败测试;已核对 trigger_review 6 项、schema/提示词旧断言与网络隔离问题。
- [x] 更新所有被 schema 与决策导向 prompt **预期改变**的旧断言;未删除或放宽测试。新增 schema 三处一致性测试:`models/context_builder/DATA_MODEL`。事件字段是破坏性变更,依契约升至 AnalysisContext v8/data_quality v5。
- [x] Engine/端到端测试显式注入空扫描池或禁用自动历史预热;全局 autouse 守门将未 mock 的 `urllib.request.urlopen` 直接判为测试失败,integration 标记除外。
- [x] 修复后跑四道闸并记录准确总数;下列原 F 组“完成”记录仍只代表组件落盘,Phase F 继续受 D1 出口约束。

### F0-2 补齐真实 trigger_review

- [x] 在 `stocks/engine/advice_review.py` 实现 `_review_trigger` 与批量附加:price_above/price_below 按“建议后首次跨越”判 fired;百分比触发以建议后首个有效收盘为基准;不在 watchlist、历史缺失、时间非法均返回 no_data + reason。
- [x] `attach_advice_performance` 对每条建议始终附 `trigger_review`(无 triggers 时 `[]`);仅修改深拷贝,不写回 advice 源文件。
- [x] `tests/engine/test_advice_triggers.py` 全部通过,并覆盖“建议时已在阈值上方但未发生上穿”不误触发。

### F0-3 修正事件“未来”语义与配置事实

- [x] `UpcomingEvent` 引入可比较的完整事件时点(`scheduled_at` 含 timezone/UTC);只有日期没有时间时保留 date-only 并显式 `time_precision=date`。
- [x] EventCalendar 以构建时刻过滤已发生事件;同日 `time_utc` 已过不再进入 upcoming/event_watch;生命周期支持 scheduled/imminent/released_or_expired,已过事件过滤并计入 `expired_count`。
- [x] 时区测试覆盖 Asia/Shanghai 已跨日、美国 UTC 事件仍在前一日、事件发生前后 1 分钟;实现不再只按 UTC `date()` 判断。
- [x] `engine.yaml` 与 `DEFAULT_ENGINE_CONFIG` 均补齐 calendar enabled/lookahead_days/earnings.enabled,并有嵌套默认值一致性测试。

> F0 完成(2026-07-03):`ruff check .`、`compileall`、`pytest 389 passed` 均通过;默认测试由 autouse 网络守门保证未 mock HTTP 立即失败。schema 因 UpcomingEvent/data_quality 字段变化从 v7/v4 单调升级为 v8/v5,DATA_MODEL 与 ARCHITECTURE 同步。Phase F 仍未验收,继续等待 D1 真实历史覆盖出口。

**F0 出口验收**:`uv run python -m pytest -q` 全过且不访问真实网络;6 个 trigger_review 失败清零;事件发生后一秒不再出现在 upcoming;文档不再把 Phase F 标为已完成。

---

## D — 数据可信底盘与冗余(Phase D,2026-07-02 数据可靠性审计导出,F0 后继续)

**目标**:让系统先"诚实"再"博学"——任何数据源失败必须在 `data_quality` 显性可见,任何时间戳必须来自数据源本身,缺数据的指标绝不报 `ok`;在此之上为关键数据源建立真实冗余。对照 VISION 成功标准第 4 条(区分市场信息/用户记忆/LLM 推断边界)与第 5 条(对决策真有帮助)。

**审计证据**(全文见 `docs/archive/DATA_RELIABILITY_AUDIT_20260702.md`,下列任务卡内只引用与该任务直接相关的证据):外部 GPT 审计 5 条结论经行号级独立核验全部成立;2026-07-02 现场网络诊断(`.local/verify_data_sources.py`)实测:eastmoney 日 K 0/6(RemoteDisconnected)、腾讯日 K 6/6(60-61 bars)、新浪日 K 窗口异常(每支 2 bars)、Yahoo 日 K 与宏观全线 HTTP 429(0/5、0/6)、三个新闻源均有产出(30/100/42 条)、Finnhub quote 与财报日历可用、Binance/SEC EDGAR/巨潮/上交所/FRED 全部可达。

**硬前置**:F0 出口通过(先恢复默认测试与真实事件/触发语义)。组内顺序是硬约束:D0 全部完成并通过 D0 出口验收后才能开工 D1;D1-1/D1-2/D1-3 完成后才能开工 D2。理由:D1 的降级链上报依赖 D0-3 的 `history_backfill` 节点;D2 的事件质量依赖 D0-2 的真实时间戳。

**开工基线**(D0-1 动工前跑一次并留存输出):

```bash
uv run ruff check .
uv run python -m pytest -q
uv run python -m compileall -q stocks tests
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
python3 .local/verify_data_sources.py   # 网络诊断,输出留存对照
```

### D0-1 技术指标质量按 data_points 判级(消灭"瞎了报健康")

**文件**:`stocks/engine/context_builder.py`、`tests/engine/test_context_builder.py`
**问题**:`TechnicalIndicators.calculate()` 数据不足时所有指标为 None,但返回字典仍含 `data_points` 故恒为真值;`_collect_technical_indicators`(context_builder.py:614 附近 `if q.indicators:`)只判字典真值即标 `ok`;`_indicator_quality` 按 `status=="ok"` 聚合,把假 ok 放大为整体 `ok/fresh`。已复现:单 bar 输入 → 全 None 指标 + `status:"ok"` + `freshness:"fresh"`。
**证据**:`.local/history/a_*.json` 六文件各仅 1 条 realtime 记录的当日,test_run 快照的指标质量仍报健康。

- [x] 【验证前提】`grep -n "if q.indicators" stocks/engine/context_builder.py` 命中 614 行附近;`grep -n "data_points" stocks/engine/indicators.py` 确认 `calculate()` 已输出该字段(59 行附近)。
- [x] `_collect_technical_indicators` 判级:`ok` = `data_points >= 35` 且 `ma_20`/`rsi_14`/`macd.hist`/`bollinger.upper` 全非 None;`partial` = 不满足 ok 但至少一个核心指标非 None;`missing` = 核心指标全 None 或 `data_points < 15`。输出随附 `data_points` 与 `unavailable`(不可用指标名列表)。
- [x] `_indicator_quality` 聚合规则同步:全 ok → `ok`;混合 → `partial`;全非 ok → `missing`;`missing_symbols` 语义保持(status 非 ok 即列入)。
- [x] `_build_raw_prompt` 市场行情段:status 为 partial/missing 的标的,指标行显式标注 `(历史仅 N bars,指标不可用/部分可用)`,禁止静默省略。
- [x] 新增测试:单 bar → missing;20 bars → partial(MACD 不可用被列入 unavailable);60 bars → ok。

**验收**:单 bar 复现(向 `_collect_technical_indicators` 喂 `data_points:1` 的全 None 指标字典)输出 missing 而非 ok;存量测试(312 项)不回归。

**完成记录(2026-07-02)**:
- 阈值常量落在 `stocks/engine/context_builder.py:614-617`(`_INDICATOR_OK_MIN_BARS=35`、`_INDICATOR_MISSING_MAX_BARS=15`、`_INDICATOR_CORE_KEYS=("ma_20","rsi_14","macd.hist","bollinger.upper")`)。
- 判级函数 `_classify_indicator_item`(context_builder.py:619-644)按核心指标可用性 + `data_points` 三态输出 (status, unavailable);`_collect_technical_indicators`(context_builder.py:646-667)对无 indicators 的 Quote 也显式产出 `status="missing"` + 全量 `unavailable`,不再依赖字典真值。
- `_indicator_quality`(context_builder.py:486-527)聚合改为 ok_count/partial_count 双计:全 ok→ok、全非 ok→missing、否则 partial;`freshness` 仅在存在任一 ok/partial 时置 fresh。
- `_build_raw_prompt`(context_builder.py:833 附近)对 missing/partial 分别追加 `(历史仅 N bars,指标不可用)` / `(历史仅 N bars,指标部分可用)`,不再静默省略。
- 单元测试新增 `TestIndicatorClassification`(tests/engine/test_context_builder.py:287-398):`test_missing_when_single_bar`、`test_partial_when_20_bars`、`test_ok_when_60_bars`、`test_aggregate_partial_when_mixed`、`test_raw_prompt_annotates_partial_and_missing`。
- 联动更新:`tests/engine/test_context_builder.py` 中 data_points=1 的两处旧断言由 ok→missing;`tests/engine/test_end_to_end.py` e2e_engine_with_history fixture 历史行数 30→60(保证 MACD 26+9 满足)、对应下游断言由 `>= 30` 改 `>= 60`;`test_to_dict_serializable` 断言 `technical_indicators["a:000001"]["status"]` 由 `ok` 改 `missing`(e2e_engine 未预热历史,该判级即为真实反映)。
- 四道闸:`uv run ruff check .` clean;`uv run python -m compileall -q stocks tests` clean;`uv run python -m pytest -q` 317 passed(D0-1 前 312,新增 5 项);`uv run python -m stocks.adapters.cli --output json --no-news --no-quotes` 正常返回 data_quality 节点。
- schema 判断:未新增/删除 AnalysisContext 顶层字段与 data_quality 节点,仅扩展 `technical_indicators[symbol]` 内部值域(status 由 {ok,missing} 扩到 {ok,partial,missing},新增 `unavailable` 列表)。DATA_MODEL.md 描述 technical_indicators 时未硬编码枚举,不触发 schema_version 升级红线;若后续 D0-3 引入 `history_backfill` 顶级节点则需重新评估。

### D0-2 三个实时 Provider 补源时间戳,`as_of`/`freshness` 真实化

**文件**:`stocks/providers/tencent_a.py`、`stocks/providers/eastmoney_a.py`、`stocks/providers/finnhub_quote.py`、`stocks/engine/context_builder.py`、`tests/`
**问题**:三个 Quote 构造点(tencent_a.py:74 / eastmoney_a.py:71 / finnhub_quote.py:167)均不写 `source`/`as_of`,而 `Quote` 定义了这两个字段;`history_cache.record()` 的 `if quote.as_of:` 分支(跨天交易日归属保护)因此是死代码;`_quote_quality`(context_builder.py:387 附近)把 `generated_at` 当 `as_of`,只要有行情 freshness 恒为 `fresh`——收盘后深夜抓的收盘价也标 fresh。
**证据**:Finnhub 实测响应含 `t=1782936000`(= 2026-07-01 20:00 UTC,美股收盘时刻),当前被丢弃。

- [x] 【验证前提】grep 三个 `Quote(` 构造点行号;确认 finnhub `_data_to_quote` 未读 `data["t"]`;确认 eastmoney 实时请求 fields 参数(eastmoney_a.py:46)不含 `f86`;确认 tencent 使用 `s_` 简版格式(`_build_symbol` 返回 `s_` 前缀,简版响应**无**时间字段)。
- [x] finnhub:`as_of` = `data["t"]`(unix 秒)→ UTC ISO;`source="finnhub"`。
- [x] eastmoney 实时:fields 追加 `f124`(行情 unix 时间戳),`as_of` = f124 → UTC ISO;`source="eastmoney_a"`。**实测纠偏(2026-07-02)**:原裁决指定的 `f86` 在该端点返回 `16.32`/`8.17`,不是 unix 时间戳;同响应 `f124=1782979895`/`1782979899` 才与腾讯北京时间更新时间一致。为遵守“真实 as_of、禁止伪造”,实现与 fixture 据真实响应改用 f124;f124 缺失/为 0/为 "-" 时 `as_of` 如实 None。
- [x] tencent:**裁决(2026-07-02,用户侧确认)——走完整格式(原选项 a),不拆分任务**。理由:tencent 是 markets.json 里 A 股的 `default_provider`,主源缺 as_of 会让本任务对用户主市场落空;完整格式还顺带补齐简版缺失的 open/high/low/prev_close。实现:`_build_symbol` 去 `s_` 前缀(如 `q=sh000300`);`_parse_line` 按完整格式重写——已知锚点索引:3=price、4=prev_close、5=open、30=行情时间(YYYYMMDDHHMMSS,北京时间 → UTC ISO 写入 `as_of`)、31=change、32=pct_change、33=high、34=low;成交量/成交额索引**以一次真实响应做 fixture 逐字段核对后再写死,禁止凭记忆填未核对的索引**;解析前校验字段数下限,时间字段空/长度异常时 `as_of` 如实 None;`source="tencent_a"`。`tests/providers/test_tencent_a.py` fixture 用真实完整格式响应重建、全量更新。**禁止用抓取时刻伪造 as_of**(不变)。
- [x] `_quote_quality`:**口径裁决(2026-07-02,用户侧确认)——顶层 `as_of` 改为全部 Quote 真实 `as_of` 中的最旧值**(最保守口径)。细则:as_of 为 None 的 Quote 不参与取最旧,但计入新增字段 `missing_as_of`(数量);`by_market` 每市场同法增加市场级 `as_of`;全部 Quote 均无 as_of 时顶层 `as_of=None`、`freshness="unknown"`——**禁止回退 generated_at 冒充**;`data_quality` 顶层 `generated_at` 保留不动,语义仍为"上下文组装时刻"。随附:`data_quality.schema_version` 由 1 升 2;若 `stocks/DATA_MODEL.md` 记载了 data_quality 结构则同步;存量断言 `as_of == generated_at` 的测试按新口径更新,更新的每个断言在完成记录中列名——**预期内行为变化,不是回归**。
- [x] 新增测试:带昨日 `as_of` 的 quote 经 `record()` 归属昨日交易日;finnhub mock 含 `t` → as_of 正确;tencent 完整格式 fixture → as_of 与 OHLC 正确;混合 as_of → 顶层取最旧、by_market 各自正确、missing_as_of 计数正确;全无 as_of → unknown。

**验收**:`data_quality.quotes.as_of` 不再等于 `generated_at`;用收盘后时间戳构造的行情,freshness 不为 fresh;tencent 行情含 OHLC 与真实 as_of。

**完成记录(2026-07-02)**:
- 腾讯真实完整响应共 88 字段;同一时点与东方财富逐字段交叉核对:index 6=`f5` 成交量,index 35 第三段=`f6` 元值,index 37/57=`f6 / 10000` 成交额万元。实现固定 index 6 → `volume_lot`、index 37 → `amount_10k`,字段下限为 38;真实网络冒烟返回沪深300 OHLC 与 `as_of="2026-07-02T08:14:06+00:00"`。
- Finnhub `t`、东方财富 `f124`、腾讯北京时间均转为 UTC ISO 且写入各自 `source`;缺失、0、`"-"` 或异常时间不使用抓取时刻兜底。
- `_quote_quality` 顶层和 `by_market` 均取有效 Quote 时间的最旧值,顶层新增 `missing_as_of`;全无时间时 `as_of=None`/`freshness="unknown"`;`generated_at` 仍只表示组装时刻。`data_quality.schema_version` 与 `stocks/DATA_MODEL.md` 同步升至 v2。
- 全库未发现存量 `as_of == generated_at` 测试断言,故该类断言更新数为 0。schema 断言按预期更新 2 处:`TestBasicBuild.test_build_minimal`、`TestEndToEnd.test_data_quality_serializable`。
- 测试覆盖:`test_record_uses_quote_as_of_for_previous_trading_day`;Finnhub 两组 timestamp/source 测试;东方财富真实 fixture、请求字段与异常 timestamp 测试;腾讯完整 fixture 的 OHLC/量额/as_of、字段下限和异常时间测试;`test_quote_quality_uses_oldest_as_of_per_market_and_counts_missing`、`test_quote_quality_after_close_is_not_fresh`、`test_quote_quality_all_missing_as_of_is_unknown`。
- 四道闸:`uv run ruff check .` clean;`uv run python -m compileall -q stocks tests` clean;`uv run python -m pytest -q` 335 passed(D0-1 后 317,本任务净增 18);CLI `--no-news --no-quotes` 正常返回 `data_quality.schema_version=2`、quotes `as_of=None`/`missing_as_of=0`。

### D0-3 历史回填结果显性上报 + 失败可重试

**文件**:`stocks/engine/__init__.py`(552-564 附近)、`stocks/engine/history_provider.py`、`stocks/engine/context_builder.py`、`stocks/DATA_MODEL.md`
**问题**:`warm_history_cache` 的返回值(每标的回填行数)在调用点被丢弃;**warm 全部失败也执行 `self._history_warmed = True`,进程内永不重试**;`data_quality` 无任何回填状态节点——审计方只能靠翻磁盘缓存文件才发现 A 股 6/6 回填失败,系统自己不上报。
**证据**:`.local/history/` 中 a_* 六文件各 1 条 realtime 记录 vs us/crypto 文件 60-61 条 provider 日 K,同一次运行落差如此之大,当次 test_run 的 data_quality 却无迹可寻。

- [x] 【验证前提】确认 `stocks/engine/__init__.py:555` 的 `await warm_history_cache(...)` 结果未接收;确认 `_history_warmed = True` 在 try 内无条件执行。
- [x] warm 结果(每标的:回填行数/所用源/失败原因)保存在 engine 实例,经 `build_context` 注入 `data_quality` 新节点 `history_backfill`(整体 status:`ok/partial/failed` + per-instrument 明细)。
- [x] 回填全部为 0 行时不置 `_history_warmed=True`,下次 build 重试;为避免每次 build 都打满上游,加最小退避(如距上次失败 < 10 分钟内不重试),策略在完成记录注明。
- [x] `data_quality` 新增节点属于 AnalysisContext 内容变化:核对 `stocks/DATA_MODEL.md` 对 data_quality 的描述粒度,若列节点清单则同步;`schema_version` 是否升级由"DATA_MODEL 是否把节点清单列为 schema 约束"决定,判断依据写入完成记录(红线:变更 schema 必须三处同步)。
- [x] 新增测试:warm 全失败 → `history_backfill.status="failed"` 且技术指标节点非 ok(与 D0-1 联动);warm 部分成功 → partial。

**验收**:模拟 A 股回填失败,`data_quality` 一眼可见 history_backfill 失败明细,prompt 中对应标的带"指标不可用"标注。

**完成记录**(2026-07-02):
- 提交:`005b59f` D0-3: 历史回填结果显性上报 + 失败可重试
- 验证前提证据:
  - `stocks/engine/__init__.py:555` 原代码 `await warm_history_cache(...)` 返回值未接收(改前 grep 已核实,当前已重写为接收结构化 report)。
  - 原 `try` 分支内 `self._history_warmed = True` 无条件执行,重写后仅当 `effective = ok+skipped_cached > 0` 才置真。
- 实现要点:
  - `stocks/engine/history_provider.py:280-352`:`warm_history_cache` 返回类型由 `dict[str,int]` 变为 `list[dict]`,每项 `{symbol, market, source, rows, status, error}`;`status ∈ {ok, skipped_cached, failed}`;`source` 通过 `_MARKET_TO_HISTORY_SOURCE` 映射到 `eastmoney_kline` / `yahoo_kline` / `unknown`,与 `CompositeKLineProvider.fetch` 路由一致。
  - `stocks/engine/__init__.py`:新增实例字段 `_history_backfill_report`、`_history_warm_last_failed_at`、`_history_warm_retry_cooldown = timedelta(minutes=10)`;`build_context` 中先判断冷却是否过期,再决定是否调用 warm;失败(所有标的都不是 ok/skipped_cached)时**不置 `_history_warmed=True`**、**打冷却时间戳**,并在日志中提示。**退避策略**:自上次全失败起 10 分钟内不再重试,10 分钟后同一 engine 实例仍在则允许重试一次。
  - `stocks/engine/context_builder.py`:`build()` 增加可选参数 `history_backfill_report`;新增 `_history_backfill_quality()` 方法,产出 `history_backfill` 节点,聚合规则:`all ok/skipped_cached → ok`,`mixed → partial`,`all failed → failed`,`empty → not_requested`。
- Schema 判断(红线):`stocks/DATA_MODEL.md` §data_quality 显式列举了 6 个节点清单,属于 schema 约束——**新增 `history_backfill` 节点必须升 v2→v3**;同时保留 `AnalysisContext.schema_version=6` 不动(仅 `data_quality.schema_version` 升级)。已同步更新 `stocks/DATA_MODEL.md`(v3 描述 + 节点字段清单)、`context_builder._build_data_quality` 及 2 处 tests 断言(`test_context_builder.py:136`、`test_end_to_end.py:360`)。
- 测试:
  - `tests/engine/test_history_provider.py::TestWarmHistoryCache` 4 用例改造为断言结构化 report(status/source/error),明确验证 `eastmoney_kline` / `yahoo_kline` source 标签、`provider returned empty frame` error 文案。
  - `tests/engine/test_context_builder.py::TestHistoryBackfillQuality` 新增 6 用例,覆盖 empty→not_requested、all ok/skipped→ok、mixed→partial、all failed→failed、build 默认路径 schema=3、build 显式传 report 反映到 data_quality。
  - `tests/engine/test_end_to_end.py::TestHistoryBackfillCooldown` 新增 2 用例,通过 `patch("stocks.engine.warm_history_cache")` 验证冷却期内不再重试与冷却过期后允许重试的完整状态机。
- 与 D0-1 联动:回填失败时 A 股 6 标的历史缓存为空 → `_enrich_with_indicators` 得到单条实时数据 → `data_points=1` → D0-1 判级 `missing` → 顶层 `technical_indicators.status=missing` 且 `raw_prompt` 内每标的写 `[指标不可用]`;`history_backfill.status=failed` 与技术指标 `missing` 双通道显现,验收要求满足。
- 4 gates(2026-07-02):
  - `uv run ruff check stocks tests` → All checks passed
  - `uv run python -m pytest -q` → 343 passed(相较 D0-2 后 335 pass 增加 8 用例)
  - `uv run python -m compileall -q stocks tests` → 0 exit
  - `uv run python -m stocks.adapters.cli --output json --no-news --no-quotes` → `data_quality.history_backfill` 节点存在且 `status="not_requested"`(未 warm 场景符合预期)

### D0-4 fallback 配置语义修正(降级链真实化)

**文件**:`stocks/config/engine.yaml`(25-30 行)、`stocks/engine/context_builder.py`、`tests/engine/test_fetchers.py` 或 `test_context_builder.py`
**问题**:`fallback.us: [finnhub]` 的备用源就是主源自己(markets.json 中 us 主源即 finnhub),`_pick_fallback_provider` 按名排除已失败主源后实际候选为空,`fallback_success` 分支对 us 永远无法触发;`crypto` 在 fallback 映射中连键都没有。配置制造了"有降级链"的错觉。

- [x] 【验证前提】已核对 `engine.yaml` 与 `stocks/config/markets.json`:us/crypto 主源均为 finnhub,旧 us fallback 指向主源自身且 crypto 缺键。
- [x] engine.yaml/代码默认值删除 `us: [finnhub]` 自我回退;显式写 `us: []`、`crypto: []`,注释"当前无独立第二源,接入见 D1-2"。
- [x] `_quote_quality.by_market` 每市场增加 `single_source: true/false`;DataFetcher 按“显式配置候选去除主源后是否为空”计算,既有 `us_quotes: "single_source_failed"` 保持兼容。
- [x] 新增测试:us/crypto 的 by_market 为 single_source true、a 为 false;显式空列表禁用 registry 自动发现且不报错;独立备用源存在时为 false。

> 完成(2026-07-03):`DataFetcher.independent_fallback_names/is_single_source` 统一运行时与质量层口径;显式空 fallback 不再被“自动发现”覆盖。`quotes.by_market` 新字段属于契约变化,data_quality v5→v6,DATA_MODEL/context_builder/tests 三处同步。定向 `101 passed`;全量门禁见本阶段提交记录。

**验收**:`data_quality` 自己承认单源事实;`engine.yaml` 中不存在指向主源自身的假降级配置。

**D0 出口验收**:D0-1~D0-4 全部完成后,真实网络跑一次全流程 `build_context`(含行情+新闻),把 `data_quality` 输出与 `.local/history/` 磁盘实况人工比对一致(回填失败可见、as_of 真实、缺数据指标非 ok),留存输出。**通过后才能开工 D1。**

> D0 出口通过(2026-07-03 09:20 Asia/Shanghai):真实 CLI 全流程返回 AnalysisContext v8/data_quality v6;quotes 11/11、最旧 as_of=`2026-07-02T20:00:00+00:00`、freshness=stale,A 股 single_source=false、us/crypto=true;news 10 条且 freshness=fresh;history_backfill=partial(37 请求,2 ok,28 skipped_cached,7 failed);technical_indicators=partial 且 `a:588000` 明确 missing;rotation=partial(30 可用/7 missing);upcoming_events 过滤 1 个已发生事件。磁盘复核:可用缓存为 59~61 records,7 个 rotation missing 中 6 个无文件、`a_588000.json` 仅 2 records,与 data_quality 完全一致。D1 开工门槛已满足。

### D1-1 A 股历史 K 线接入腾讯第二源 + 回填降级链

**文件**:`stocks/engine/history_provider.py`、`tests/engine/test_history_provider.py`
**证据(2026-07-02 实测)**:eastmoney push2his 从用户网络 6/6 RemoteDisconnected(40-60ms 即断,疑反爬/限流,当日更早时段曾成功——间歇性失败);腾讯 `web.ifzq.gtimg.cn` 日 K 6/6 成功(60-61 bars);新浪接口通但数据窗口异常(每支仅 2 bars),**不选新浪**。

- [x] 【验证前提】重跑 `.local/verify_data_sources.py`:eastmoney 0/6 RemoteDisconnected、腾讯 6/6 各 60 bars、新浪仍仅 2 bars。
- [x] 新增 `TencentKLineProvider`,解析 `qfqday` 并回落 `day`;列对齐 HistoryCache(含 `data_source=provider`),prev_close 按前一根收盘推导;sh/sz 规则抽为 `tencent_market_prefix` 供实时与历史 Provider 共用。
- [x] `CompositeKLineProvider` 的 a 市场路由改为 eastmoney → tencent;DataFrame attrs 与 history_backfill item 同时记录 source/primary_source/fallback_source/degradation_result/逐源 errors。
- [x] 新增测试:qfqday/day fixture、共享前缀、主源成功、备用成功、两源全空、warm report fallback 均覆盖。

> 完成(2026-07-03):真实网络以正确 exchange 元数据复测 6 个 watchlist A 股标的,腾讯均返回 60~61 bars;eastmoney 当时仍全失败,结果均为 `fallback_success`。`history_backfill.items` 新增逐源降级字段属于契约变化,data_quality v6→v7 并同步 DATA_MODEL/context_builder/tests。

**验收**:模拟 eastmoney 失败,A 股回填仍 6/6 成功且 `history_backfill` 记录降级来源;真实网络冒烟一次。

### D1-2 美股/加密第二行情与历史源(暂缓项已解禁,见 PLAN 决策日志)

**文件**:`stocks/engine/history_provider.py`、`stocks/providers/`(新增)、`stocks/config/engine.yaml`、`stocks/config/markets.json`
**证据(2026-07-02 实测)**:Yahoo chart 从用户网络全线 HTTP 429(日 K 0/5、宏观 0/6)——**美股/crypto 历史回填主链路当前已断**,磁盘上 60 根缓存是早前成功的遗产;Binance `/api/v3` 可达(354ms);Finnhub `/quote` 正常。

- [x] 【验证前提·硬门槛】现有 key 实测 Finnhub candle 返回 HTTP 403 `You don't have access to this resource`。随后实测 Stooq 指定 CSV URL 返回 JS proof-of-work 反爬页而非 CSV,不可作为机器源;为保持“真实可用独立源”目标,改选免 key 的 Nasdaq 公开历史端点,实测 AAPL/QQQ 均返回 60 bars。偏离原二选一的原因和证据已写入完成记录。
- [x] crypto 历史:新增 `BinanceKLineProvider` 为主源,Yahoo 降为备源;剔除 closeTime 尚在未来的未收盘日 K,多取一根保证 60 根已完成 bars;Composite crypto 路由走统一降级链。
- [x] 美股历史:新增 `NasdaqKLineProvider` 为主源(自动 stocks→etf assetclass 回落,逐行跳过 N/A),Yahoo 降为备源;统一输出 source/降级记录。
- [x] crypto 实时:新增 `BinanceQuoteProvider`(`/api/v3/ticker/24hr`,closeTime → as_of),注册为 crypto fallback;engine.yaml 与 markets.json 同步。
- [x] 美股实时:维持 finnhub 主源 + 既有 stale 历史兜底;Nasdaq 这里只提供日 K,未把日线伪装实时 fallback。
- [x] 新增测试:Nasdaq/Binance 历史 fixture、stocks→etf、未收盘 K 过滤、Binance 实时 typed error/OHLC/as_of、crypto fallback_success、engine.yaml/markets.json 一致性。

> 完成(2026-07-03):Finnhub candle=403;Stooq CSV=200 text/html 反爬挑战,故按任务目标改用实测可达的 Nasdaq 免 key 日 K。真实冒烟:AAPL/QQQ Nasdaq 各 60 根已完成日 K,BTCUSDT/ETHUSDT Binance 各 60 根已完成日 K,Binance 实时 quote 带源 closeTime。Yahoo 仅备用;全量 `410 passed`。本任务只扩 Provider/配置与既有 source 值域,未增删 AnalysisContext/data_quality 字段,不升 schema。

**验收**:重跑诊断脚本,在 Yahoo 429 前提下 us/crypto 历史仍能回填 ≥ 40 bars;crypto 实时在 finnhub mock 失败时由 binance 兜住且 `fallback_success` 记录正确。

### D1-3 宏观数据换权威源(FRED 主链 + 报价代理降级)

**文件**:`stocks/engine/macro_data.py`、`stocks/engine/context_builder.py`、`stocks/DATA_MODEL.md`、`tests/engine/test_macro_data.py`
**证据(2026-07-02 实测)**:Yahoo 宏观 6 指标 0/6(HTTP 429);FRED `fredgraph.csv` 可达免 key(276ms)。现另有设计缺陷:`CompositeMacroProvider` "第一个提供者有任意字段即整体返回",Yahoo 只回 1 个字段也会短路后续兜底。

- [x] 6 个市场指标改为逐字段降级链:FRED 主源(VIXCLS/DGS10/DTWEXBGS/DEXCHUS/DCOILWTICO),gold 由 Yahoo 提供;Yahoo 备源、static_config 最后兜底。DEXCHUS 口径核对为“人民币/1 美元”;DTWEXBGS 在 prompt 明示为广义美元指数代理。
- [x] `CompositeMacroProvider` 改为按字段合并,上游部分成功不再阻断下游补齐;逐源错误即使被补齐仍保留用于降级审计。
- [x] `MacroSnapshot.field_sources` 为每字段携带 source/as_of;FRED 使用观测日期,Yahoo 使用 regularMarketTime/最后 chart timestamp,static 不伪造时点;`_macro_quality` 取全部有效字段最旧 as_of 并报告 missing_as_of。
- [x] 新增月度 `official_stats`(CPIAUCSL 同比、UNRATE、FEDFUNDS)与 24h 原子磁盘缓存;raw prompt 分组展示“市场定价代理”与“官方统计（滞后月度，不代表实时）”。
- [x] 开工时 AnalysisContext 已因 F0 升至 v8,本任务按单调 +1 升 v9;data_quality 宏观节点扩展由 v7 升 v8;models/context_builder/DATA_MODEL/tests 三处同步,未倒写计划中的旧基线 v7。
- [x] 新增测试:FRED 单/多序列 CSV、CPI 同比、官方缓存命中、字段级合并、Yahoo 全 429 时 FRED 仍提供 5 个市场字段+3 官方统计、最旧 as_of、prompt 分组、schema 三处一致。

> 完成(2026-07-03):全量 `414 passed`。真实出口时 FRED 连接超时被逐字段显式记录,Yahoo 恢复并补齐 6/6 市场字段,宏观 status=partial/freshness=stale(最旧真实时点),官方统计如实 missing；fixture 验证相反故障方向(Yahoo 全 429)仍由 FRED 提供 5+3 字段。真实故障与测试故障两向均不静默。

**验收**:mock Yahoo 全 429 时宏观仍 ≥ 5 字段有值且逐字段来源可溯;LLM 上下文能区分市场代理与官方统计;全局验收通过。

**D1 出口验收**:重跑 `.local/verify_data_sources.py` + 真实全流程 build_context,对照 D0 出口留存输出:A 股/美股/crypto 历史、宏观在任一主源失败下仍有数据且降级全程可见。**通过后才能开工 D2。**

> D1 出口通过(2026-07-03 10:05 Asia/Shanghai):诊断重跑见 D1-1/D1-2 完成记录;真实全流程 history_backfill=ok(37/37 usable,7 新回填中 eastmoney 5 成功、2 失败由 tencent fallback_success),rotation=ok(37 items/0 missing)。美股 Nasdaq 与 crypto Binance 独立日 K 各实测 60 bars;crypto Binance 实时可用。宏观真实运行中 FRED timeout、Yahoo 逐字段补齐 6/6 且 FRED errors 全保留;反向 Yahoo 429/FRED 成功由 fixture 门禁覆盖。相较 D0 的 30/37,历史覆盖已达到 100%,D2 开工门槛满足。

### D2-1 财报日历可靠化(Finnhub 免费 tier,合并现有 F-1,禁止另建平行模块)

**文件**:`stocks/engine/market_events.py` 或新增独立模块、`stocks/engine/context_builder.py`、`tests/`

- [x] 在现有 `stocks/engine/event_calendar.py::FinnhubEarningsCalendarProvider` 上增量实现,不新增第二个财报模块;watchlist 中 us 标的调用 `/calendar/earnings?from=<今-7d>&to=<今+14d>&symbol=`,复用 finnhub provider 的节流/typed errors。
- [x] 结果缓存 12h(磁盘,gitignore 目录),缓存携带 fetched_at/source/window;单标的失败不应中止其余标的,按标的记录 partial errors。
- [x] 注入 `upcoming_events` 并可投影为 `market_events(event_type=earnings)`;raw_prompt 复用【未来催化剂日历】而非再建重复【财报日历】段;失败在 `data_quality` 标注,不阻塞主流程。
- [x] 新增测试:fixture 解析、临近财报进入事件、单标的失败仍保留其他结果、缓存命中/过期、typed error 降级;与 F0 的已过时点过滤联动。

**验收**:真实跑通一次,持仓相关财报日出现在上下文;失败场景 data_quality 可见。

> 完成(2026-07-03):真实 Finnhub 财报接口对 watchlist 3 个美股标的请求成功并写入 12h 缓存；当前 14 日窗口合法返回 0 条，二次完整运行 `cache.hits=3/misses=0`，不得伪造财报事件满足数量条件。fixture 覆盖窗口内财报进入 `upcoming_events` 并投影为 `market_events(event_type=earnings)`；逐标的 typed error/partial、缓存过期及已发生时点过滤均有门禁。

### D2-2 公司公告一手源(仅 watchlist 范围,不做全市场)

**文件**:`stocks/providers/`(新增两个)、`stocks/engine/news_sources.py` 装配、`tests/`

- [x] 美股:SEC EDGAR submissions API(`data.sec.gov/submissions/CIK{10位}.json`;**UA 必须带联系方式标识,遵守 10 req/s 限速**;watchlist 内 us 个股映射 CIK,取最近 30 天 8-K/10-Q/10-K)。
- [x] A 股:巨潮资讯公告查询(POST `www.cninfo.com.cn/new/hisAnnouncement/query`,form 表单,按 watchlist 代码查询;实测域名可达,接口字段以实抓为准先写 fixture)。
- [x] 两者实现为 NewsProvider(`source_type="filing"`),汇入现有 NewsAggregator 去重排序;单源失败不阻塞。
- [x] 新增测试:fixture 解析;聚合后 filing 与 rss 共存去重正常。

**验收**:真实网络冒烟各拉到 ≥1 条持仓相关公告;失败时 data_quality.news 的 sources 计数如实反映。

> 完成(2026-07-03):SEC 真实返回 QCOM 最近 30 天 1 条 8-K，并进入 10 条聚合结果；巨潮公开检索确认 159110 的 `orgId=jjjl0000050` 后按 `代码,orgId` 实抓，当前 30 天合法返回 0 条且无请求错误。这里不把合法空集伪造成 ≥1；真实公告数量是时点依赖的外部事实，fixture 固化解析/代码过滤，逐标的异常继续及 `data_quality.news.errors` 透传均有门禁。`SEC_USER_AGENT` 配置已写入 AGENT_GUIDE/README，缺失时逐标的显式报错。

### D2-3 持仓定向新闻(scope 标注)

**文件**:`stocks/config/news_sources.json`、`stocks/engine/news_sources.py`、`stocks/engine/market_events.py`、`tests/`

- [x] 为 watchlist 每标的生成 Google News RSS 定向 feed(复用 RSSNewsProvider,query=`{名称} OR {代码}`,hl/gl 按市场);源配置支持模板化生成而非手写 N 条。
- [x] `NewsItem` 增加 `scope` 字段(`holding`/`general`);`MarketEventExtractor` 持仓匹配优先消费 holding 源,关键词匹配保留为增强。
- [x] 总量控制:定向源纳入现有 `max_source_items`/`max_items` 机制,防止淹没 general 源。
- [x] `NewsItem` 字段变更同步 `stocks/DATA_MODEL.md` 与测试(若 DATA_MODEL 列出 NewsItem schema)。
- [x] 新增测试:模板生成源正确;scope 标注与优先匹配;去重与总量上限。

**验收**:上下文中持仓相关新闻占比显著提升且带 scope 标注;总条数不超上限。

> 完成(2026-07-03):真实完整运行聚合 81 条原始新闻后严格输出 10 条，其中 holding=6/general=4，且 SEC filing 在 holding 配额内保留；全部带 scope，未超过 `max_items`。AnalysisContext 升 v10、data_quality 升 v9，DATA_MODEL/ARCHITECTURE 同步。

**Phase D 出口标准**:D0/D1/D2 全部带可复现证据完成;重跑 `.local/verify_data_sources.py` 与真实全流程 build_context,`data_quality` 满足:任一数据源失败均显性可见、所有 as_of 为真实源时间或如实 unknown、缺数据指标绝不报 ok、单源风险自我声明;对照 VISION 成功标准第 4、5 条各留一条证据。

> Phase D 出口通过(2026-07-03 10:34 Asia/Shanghai):`ruff`、`compileall`、全量 `431 passed`；真实完整上下文为 AnalysisContext v10/data_quality v9，历史 37/37 可用、rotation 37/0 missing、财报缓存 3 hits、新闻 holding/general=6/4 且含 SEC 8-K。`.local/verify_data_sources.py` 复跑显示东方财富 6/6、腾讯 6/6、Finnhub 行情/财报接口可达、巨潮/SEC/Binance/FRED 可达；同一时刻 Yahoo 被 429，正式链路由 D1 的 Nasdaq/Binance/FRED 分层和显式降级承担。合法空窗口（Finnhub 当前无未来 14 日财报、巨潮当前无 30 日公告）按空集记录，未伪造事件或回退抓取时间。

---

## F — 前瞻决策层组件(2026-07-03 已修复并重新验收)

> 背景:用户对实际分析输出的直接反馈——回顾式总结"有数据只分析"无实质意义,
> 要求系统在当下时间节点给出"提前布置哪些板块/调整哪些仓位"的条件化预案。
> 决策日志见 PLAN.md 2026-07-02 Phase F 条目。
>
> **状态纠偏**:原说明“与 Phase D 并行不冲突”不成立。rotation/action_signals 对历史回填有硬依赖,而 D1 未完成导致当前 26 个扫描池标的全部无本地历史。保留已落盘组件,但 F0 修复与 D1 数据覆盖通过前不得验收 Phase F。

### F-1 事件日历(upcoming_events)

- [x] 新增 `stocks/engine/event_calendar.py`:StaticEventCalendarProvider(读 `stocks/config/event_calendar.json` 官方日程) + FinnhubEarningsCalendarProvider(watchlist 美股财报日) + EventCalendar 组合器(lookahead 窗口过滤、days_until、按类别匹配 watchlist 标的)。
- [x] 新增 `stocks/config/event_calendar.json`:2026 下半年 FOMC/CPI/非农官方日程(来源 federalreserve.gov / bls.gov,2026-07-02 核对)。
- [x] `engine.yaml` 与 `DEFAULT_ENGINE_CONFIG` 增 `calendar` 节(enabled/lookahead_days/earnings.enabled),StocksEngine 装配并注入 ContextBuilder;F0-3 已补配置一致性测试。
- [x] `AnalysisContext` 增 `upcoming_events`(v6→v7);`data_quality` 增 `upcoming_events` 节点(v3→v4);raw_prompt_input 增【未来催化剂日历】小节。
- [x] 失败语义:无 Provider → not_configured;部分 Provider 失败 → partial + errors 明细;全失败 → missing。Finnhub key 缺失显式报错不静默。

> 历史落盘记录:`b54dc3d` 已含事件日历代码与测试;但未覆盖“事件时点已过”与跨时区,且配置项未落盘,本任务保持未验收。

### F-2 板块轮动脚手架(rotation)

- [x] 新增 `stocks/engine/rotation.py`:compute_rotation 纯函数,基于 HistoryCache 日 K 收盘算 5/20 根 K 线累计涨跌幅、MA20 上下方、按 r20 排名、类别动量聚合、leaders/laggards;历史不足显式进 missing。
- [x] 新增 `stocks/config/sector_scan.json`:8 只 A 股行业 ETF + 8 只美股板块 ETF 扫描池;与 watchlist 去重;只参与历史回填与轮动,不请求实时行情、不进 MarketState。
- [x] ContextBuilder 增 `_build_rotation`;`AnalysisContext.rotation` + `data_quality.rotation`;raw_prompt_input 增【板块轮动排名】小节。

> 完成:代码与测试已落盘(commit 待本机提交) | 证据:`tests/engine/test_rotation.py` 5 个用例(收益计算/排名/partial+missing/no_data/可序列化)。

### F-3 建议触发闭环(triggers)

- [x] `AdviceRecord` 增可选 `triggers` 字段:{instrument:"market:code", type∈{price_above,price_below,pct_change_above,pct_change_below}, level:number, action:非空, invalidation?};严格校验,旧记录兼容按 [] 加载。
- [x] `advice_review.py` 增 `_review_trigger`:按建议后收盘序列核对跨越/百分比条件,并为缺数据场景返回结构化 no_data;F0-2 已完成。
- [x] `engine.save_advice` 允许 triggers 透传;MCP advice_save 描述更新;raw_prompt_input【上次建议】增触发器核对行。

> 复核:`AdviceRecord.triggers` schema/保存透传已落盘;trigger_review 的 6 个行为测试全部失败(KeyError),不得记完成。

### F-4 决策导向分析契约(prompt)

- [x] 重写 `stocks/prompts/personal_advice_prompt.txt`:六节强制结构(核心结论/上期预案复盘/催化剂情景预案/调仓触发清单/下一个机会/风险与数据边界);触发条件必须可验证;禁止无条件"观察/等待";"下一个机会"不允许空章节;保留金额脱敏与不编造红线。
- [x] raw_prompt_input 收尾指令同步指向新契约。
- [x] `stocks/DATA_MODEL.md`(当前 v8/data_quality v5/UpcomingEvent 生命周期/rotation/triggers)、`ARCHITECTURE.md`、`AGENT_GUIDE.md`、PLAN 决策日志同步;ARCHITECTURE 头部/图/数据流版本已在 F0 同步。

> 完成:文档已同步(commit 待本机提交) | 证据:DATA_MODEL "AnalysisContext v7"/"data_quality v4"/"UpcomingEvent"/"rotation" 小节;AGENT_GUIDE triggers 示例。

### F-5 候选池扩充与 pool 分层(用户裁决)

- [x] `Instrument` 增可选 `pool` 字段;`sector_scan.json` 扩至 26 标的:A 股 8 行业 ETF + 美股大盘(SPY/IWM)/板块(XLK/SMH/IGV/XLF/XLE/XLV/XBI/XLI)/防守(XLU/XLP)/利率(TLT)/AI 链个股(NVDA/AMD/AVGO/TSM/MU),分层 broad/sector/defensive/rates/ai_chain。
- [x] rotation items 增 `pool` 字段;watchlist 标的按 core 处理。

### F-6 引擎动作信号层(用户裁决修订 §4 红线后启用)

- [x] 新增 `stocks/engine/action_signals.py`:7 类信号(accumulate_candidate/wait_for_pullback/reduce_risk/avoid_catching_falling_knife/rotation_candidate/neutral_hold/no_data) + event_watch 叠加(T+3 内催化剂);规则阈值集中定义;每信号必附 reasons 指标事实;历史 <15 bars 一律 no_data。
- [x] ContextBuilder 复用轮动 frames 统一为 watchlist+扫描池算指标;`AnalysisContext.action_signals`(并入 v7)+`data_quality.action_signals`(并入 v4)+raw_prompt【引擎动作信号】小节。
- [x] prompt 契约更新:动作信号为初始底稿,Agent 必须逐条采纳或给理由推翻。
- [x] PLAN §4 红线修订与决策日志登记;DATA_MODEL/ARCHITECTURE/AGENT_GUIDE 同步。

> 完成:代码与测试已落盘(commit 待本机提交) | 证据:`tests/engine/test_action_signals.py` 用例覆盖 7 类信号规则/事件叠加/缺数据/可序列化。

**F 组历史验收记录(2026-07-02 云端;已被 2026-07-03 本机复核否决)**:

- [x] `ruff check .` 全通过
- [x] `python -m compileall -q stocks tests` 全通过
- [x] CLI 冒烟 `--output json --no-news --no-quotes`:schema_version=7、data_quality.schema_version=4、upcoming_events 2 条(非农 T+0/CPI T+12)、dq.upcoming_events=partial(finnhub key 未配置显式报错)、rotation=no_data(无历史,诚实缺失)
- [ ] `uv run python -m pytest -q` 全量通过(云端无 pytest 网络权限,待本机执行;新增用例已用等价 runner 全部通过)

**Phase F 重新验收条件**:F0 出口通过;D1 出口后真实扫描池至少 90% 标的有 ≥40 根可用日 K,不足者逐项列明;rotation 不混用过期数据且顶层 as_of 采用最保守口径;action signal 不再以近20根仅略大于 0 的噪声直接产出 accumulate_candidate;一份真实上下文必须至少能解释“为什么有/没有下一个机会”,不能靠提示词强制编造。

> Phase F 重新验收通过(2026-07-03):真实上下文 rotation=ok，37/37 标的均 ≥40 bars、missing=0；顶层 `as_of=2026-07-02T00:00:00+00:00` 等于逐标的最旧值而非最新值。`accumulate_candidate` 新增 r20≥2% 门槛，真实计数从 9 降为 7，`+0.27%`/`+1.12%` 不再被包装成布局机会；新增微小正收益和最旧 as_of 回归测试。当前输出可用 `signal/reasons/action_hint` 解释为何有 7 个布局候选、为何其余标的是等待回踩/规避下跌刀/neutral，而非靠 prompt 强制补写。

---

## G — 决策产品化与个人组合行动系统(Phase G,2026-07-03 用户裁决)

**愿景**:工具在对话当下不是只返回数据或市场总结,而是稳定交付“下一个机会 + 提前布局/调仓预案”。`AnalysisContext` 继续作为证据层;新增 `DecisionPlan` 作为决策层唯一权威中间产物;内部 LLM 或用户 Agent 只负责在证据约束内生成/完善 DecisionPlan;用户 Agent最后结合当前对话做完整分析。系统不自动下单。

**硬前置**:F0、D0、D1、D2 全部通过出口验收。原因:持仓动作和机会排序不能建立在缺历史、过期事件或单源静默失败之上。G 内严格按 G0→G7 顺序执行。

### G0 现有交付路径审计 + DecisionEnvelope 契约冻结

**文件**:`stocks/adapters/mcp.py`、`stocks/adapters/cli.py`、`stocks/adapters/http.py`、`stocks/engine/llm_analysis.py`、`stocks/DATA_MODEL.md`、`tests/`

- [x] 【验证前提】证明当前 MCP 只有 `get_analysis_context`,不暴露决策生成工具;证明内部 LLM 默认 `analysis_enabled:false`;证明 raw_prompt 只引用本地 `personal_advice_prompt` 名称,外部 Agent 未必获得完整契约。
- [x] 定义 `DecisionEnvelope` 顶层协议:`status ∈ {ok,degraded,setup_required,validation_failed,failed}`、`mode_requested`、`mode_used ∈ {internal_llm,agent_delegate,deterministic_only}`、`decision_plan`、`agent_task`、`setup_required`、`quality`、`errors`、`final_analysis_instructions`。
- [x] 明确三层职责:deterministic engine 产事实/候选/仓位边界;决策生成器产结构化 DecisionPlan;用户 Agent审查后产最终自然语言分析。最终分析不是系统事实,不得反写市场数据。
- [x] schema 写入 `stocks/DATA_MODEL.md`;使用 JSON Schema 或等价本地校验器作为唯一契约,禁止只靠 prompt 文案约束。

**验收**:同一个 mock 上下文分别走 internal_llm/agent_delegate,二者返回同形 DecisionEnvelope;外部 Agent不读取仓库文件也能从 envelope 理解下一步。

> 完成(2026-07-03):读码确认 MCP 工具首项仍为 `get_analysis_context` 且无 decision 工具，`DEFAULT_ENGINE_CONFIG.llm.analysis_enabled=false`，旧 LLM 只在进程内读取 `personal_advice_prompt.txt` 后生成自由文本。新增冻结 dataclass、JSON Schema 2020-12 与等价本地校验器；`test_internal_and_delegate_envelopes_have_identical_shape` 用同一 mock quality/plan 证明两种模式九个顶层字段同形，缺字段、未知字段和 setup_required 空载荷均被拒绝。完整自包含 agent_task 的生成在 G6 接入同一协议。

### G1 持仓—标的身份映射与可交易事实

**文件**:`stocks/domain/models.py`、资产 CRUD/持久化、`stocks/DATA_MODEL.md`、`tests/`

- [ ] `FinancialAsset` 增可选、向后兼容字段:`instrument_key`(`market:code`,非证券资产为 None)、`quantity`、`cost_basis`、`position_role`(core/satellite/hedge/cash/buffer/locked)、`tradable`、`liquidity_note`;源币种/源金额保持不变。
- [ ] 映射只接受用户确认或明确配置,禁止按资产名称模糊猜证券代码;未映射资产仍参与资产桶,但任何标的级调仓必须标 `position_unknown`/`not_linked`。
- [ ] watchlist、持仓和扫描池统一使用 `instrument_key`;同一标的多平台持仓可聚合但保留来源明细;候选池未持有标的当前权重明确为 0。
- [ ] 资产迁移/round-trip/CRUD/MCP/CLI 测试覆盖旧记录缺字段、外币持仓、非交易资产、同标的多账户;未经确认不得写映射。

**验收**:任一 DecisionPlan 动作都能回答“当前是否持有/持有多少权重/是否可交易”;回答不了时显式阻断仓位幅度,不让 LLM猜。

### G2 PortfolioActionEngine:仓位影响、幅度与资金来源

**文件**:`stocks/engine/portfolio_actions.py`(新增)、`scaffolds.py`、`context_builder.py`、`stocks/DATA_MODEL.md`、`tests/`

- [ ] 实现纯函数 `build_portfolio_actions(holdings,mapping,constraints,candidates,risk_metrics)`:输出动作候选而非下单,含 `action ∈ {add,increase,reduce,exit,replace,hold,no_action}`、`current_weight`、`target_weight_range`、`delta_weight_range`、`funding_source`、`constraint_effects`、`blocked_reasons`。
- [ ] 最小风险集正式解禁并实现:组合年化波动率、最大回撤、HHI、候选与组合/核心持仓相关性;数据不足逐项 no_data,不得用默认 0。
- [ ] 仓位幅度首先服从用户 min/max、现金缓冲、锁定资产、单标的/类别上限和风险偏好;没有足够可用资金时不得输出加仓;减仓释放资金与新增资金来源必须守恒。
- [ ] 输出相对比例给 prompt/Agent;精确金额只留在受控结构化层,沿用金额脱敏规则;不生成订单数量、不连接券商。
- [ ] 增加不变量测试:调整后权重和≈100%、不突破硬约束、资金来源守恒、锁定资产不被出售、缺持仓映射不伪造 delta。

**验收**:固定组合能明确产出“从哪个层/标的减多少比例,转给哪个候选,调整后约束和风险如何”;不是泛泛“增加权益/关注科技”。

### G3 OpportunityEngine:可比较的机会评分与轻量校准

**文件**:`stocks/engine/opportunities.py`(新增)、`rotation.py`、`action_signals.py`、配置、`tests/`

- [ ] 候选先按市场/资产类型分组,禁止直接用跨市场绝对 20 日涨幅混排;为每组配置基准(如 A 股宽基/美股 SPY/crypto BTC),计算 5/20/60 日相对强弱。
- [ ] 最小特征:趋势与相对强弱、成交量确认、年化波动率、近期最大回撤、距均线/过热度、数据 freshness、事件/公告催化剂、与现有组合相关性;每个特征保留 source/as_of/缺失状态。
- [ ] 输出 `OpportunityCandidate`:`score`、`rank_within_market`、`thesis_facts`、`entry_zone_or_trigger`、`invalidation`、`horizon`、`portfolio_fit`、`missing_inputs`;分数是排序工具,不是收益概率。
- [ ] 设最低有效强度与 freshness 门槛:近20根仅略大于 0、过期数据或缺关键特征不得直接变 accumulate_candidate;旧 rotation/action_signals 保留兼容但降为 OpportunityEngine 的输入之一。
- [ ] 实现轻量 walk-forward 校准(不是通用回测平台):统计信号后 5/10/20 根 K 线收益、最大有利/不利波动、样本数;阈值没有最小样本时标 `uncalibrated`,禁止宣称胜率。
- [ ] fixture 测试覆盖跨市场分组、基准相对强弱、过热/下跌刀、陈旧数据排除、缺失特征、校准样本不足。

**验收**:扫描池中每个入选机会都能解释“相对谁更强、承担多少波动/回撤、为何适合当前组合”;不再把微小正收益包装成布局机会。

### G4 事件生命周期、预期差与候选映射

**文件**:`event_calendar.py`、D2 Provider、`market_events.py`、`opportunities.py`、`tests/`

- [ ] 在 F0 的 scheduled_at 基础上统一事件状态:`scheduled/imminent/released/expired`;事件发生后从 upcoming 移出,进入 released 观察窗口,禁止继续写“T+0 未来催化剂”。
- [ ] 对支持的数据源增加 `consensus/actual/previous/unit`;没有一致预期时,情景树只能写定性条件并标 `consensus_missing`,禁止编造“超预期”阈值。
- [ ] 统一 category taxonomy,事件影响范围同时匹配持仓、watchlist 和扫描池;当前 generic `tech/bond/gold` 与 `us_tech/us_rates/...` 必须有显式映射表,禁止字符串碰运气。
- [ ] 财报/公告事件复用 D2 的缓存、限流、来源和质量记录;同一事件跨 RSS/公告/日历去重但保留多来源。
- [ ] 事件落地后重算 OpportunityCandidate 与受影响 PortfolioAction,记录“预案分支 → 事实结果”,不自动执行动作。

**验收**:事件前能生成条件化预案;事件发生后一秒旧事件不再 upcoming;有 actual/consensus 时选择正确分支,无数据时明确无法判定。

### G5 DecisionPlan:结构化决策产物与确定性校验

**文件**:`stocks/domain/models.py`、`stocks/engine/decision_plan.py`(新增)、`stocks/DATA_MODEL.md`、`tests/`

- [ ] 定义 `DecisionPlan`:`schema_version/generated_at/horizon/portfolio_diagnosis/immediate_actions/opportunities/scenario_plans/no_action_conditions/data_boundary/evidence_index`。
- [ ] 每条 action/opportunity 必含:`instrument_key 或 asset_bucket`、`action`、`current_weight`、`target/delta range`、`funding_source`、`entry_trigger`、`invalidation`、`catalyst`、`horizon`、`confidence`、`evidence_refs`、`quality_flags`;不存在的信息显式 None + reason。
- [ ] 先由 deterministic planner 将 OpportunityCandidate + PortfolioAction 拼成可用草案;即使没有 LLM也返回 `deterministic_only` 的结构化 plan,但不得补写推断型 thesis。
- [ ] Validator 强制:标的必须存在于持仓/watchlist/扫描池;证据引用必须能在 context 定位;仓位约束/资金守恒必须通过;事件不得过期;缺关键数据的 action 必须 blocked/no_action;禁止收益承诺与精确金额泄漏。
- [ ] LLM 输出只允许补充 inference/rationale/scenario,不能改写事实字段;事实冲突以 deterministic 层为准并记录 validation error。
- [ ] `AnalysisContext` 不再作为主要用户交付;新增 `StocksEngine.build_decision()` 先构建 context 再生成 DecisionEnvelope。CLI 普通分析默认调用 build_decision;MCP 将 `decision_generate` 列为首要用户决策工具;原 `get_analysis_context` 保留给调试/高级 Agent并明确标注“仅证据,不可直接作为最终建议”。

**验收**:无 LLM也能返回可执行结构和明确缺口;任意伪造标的、无资金来源加仓、过期事件或不存在 evidence_ref 均被拒绝。

### G6 内部 LLM 默认开启 + 用户 Agent delegate 双路径

**文件**:`engine.yaml`、`config_loader.py`、`llm_analysis.py`(可重构为 decision generator)、Adapters、`AGENT_GUIDE.md`、`README*`、`tests/`

- [ ] 新配置 `decision_generation.enabled:true`(默认开启)、`mode:auto`、`model`、`api_key_env`、`base_url_env`;API URL/key 从环境变量或 `.secret/` 安全读取,不得作为 MCP/HTTP 普通参数传递。默认开启作用于 `build_decision/decision_generate`,纯 `build_context` 保持确定性且不产生付费 LLM 调用;默认开启不等于缺 key 时伪装成功。
- [ ] `auto` 模式:URL+key 可用 → `internal_llm`;缺任一项 → DecisionEnvelope.status=`setup_required`,列出缺失字段、环境变量名、CLI/`.secret` 安全配置方式,由用户 Agent向用户说明;错误/日志/响应永不回显 key。
- [ ] 用户 Agent得到 setup_required 后提供两项选择:①用户在安全环境配置 URL/key 后重试;②明确选择 `agent_delegate`,不要求用户把 key 粘贴进普通对话。不得静默改用旧 summary 报告。
- [ ] `internal_llm`:输入完整 DecisionTask(证据摘要+deterministic plan+DecisionPlan schema+决策契约),输出严格 JSON DecisionPlan;本地 validator 失败允许一次带错误列表的修复重试,再失败返回 validation_failed + agent_task。
- [ ] `agent_delegate`:工具返回自包含 `agent_task`,其中嵌入完整决策契约、脱敏上下文、deterministic plan、schema、evidence index 和校验要求,不能只写“请读取本地 prompt”。用户 Agent先扮演内部决策生成器产 DecisionPlan,调用 `decision_validate` 校验后,再切换到最终分析阶段。
- [ ] 无论 internal_llm 或 agent_delegate,DecisionEnvelope 都附 `final_analysis_instructions`:用户 Agent必须审查数据质量、指出采纳/推翻项、结合当前对话和用户临时意图输出最终完整分析;禁止原样复述内部报告。
- [ ] MCP 增 `decision_generate(mode=auto|internal_llm|agent_delegate)`、`decision_validate(plan)`,并在工具排序/描述中把 decision_generate 定义为“面向用户建议的默认入口”;CLI 无特殊 flag 时默认生成决策,另提供显式 `--context-only` 调试开关;HTTP 如暴露则沿用认证/金额脱敏。保留 `get_analysis_context` 但工具描述明确它只是证据接口。
- [ ] mock 测试覆盖:默认凭据齐全、缺 URL、缺 key、401/429/timeout、空 content、非法 JSON、validator 修复成功/失败、agent_delegate 无仓库文件访问、secret 不泄漏。

**验收**:Agent 调工具时,有凭据直接得到内部 DecisionPlan;无凭据必定收到可执行配置提示或 agent_delegate 任务;两条路径均能通过同一 validator,随后由用户 Agent完成最终分析。

### G7 触发监控、建议留痕、效果复盘与端到端出口

**文件**:`advice_review.py`、`persistence.py`、Adapters、`tests/`

- [ ] trigger schema 扩展为可校验类型:价格/涨跌幅、MA cross、event outcome、time window;每类有明确数据源、时点和 fired 语义。无法自动核对的自然语言 invalidation 单独标 manual。
- [ ] 每次 `decision_generate` 先检查上期 plan:输出 pending/approaching/fired/invalidated/no_data;接近触发与已触发不自动下单,只进入 DecisionPlan immediate_actions 并由用户 Agent提示。
- [ ] 用户确认后保存 DecisionPlan 摘要、triggers、关键 evidence refs 与 mode_used;未确认不写。internal_llm 原始思维链/长文不持久化。
- [ ] 提供显式 `decision_review` MCP/CLI 工具用于随时核对触发;后台定时/通知若实现必须另有用户授权,不在本阶段暗中运行。
- [ ] 轻量效果复盘按建议后 5/10/20 bars 记录事实,区分“触发器是否执行条件成立”和“建议后表现”,不把单次结果当模型胜率。
- [ ] 端到端双路径测试A:配置 mock URL/key → internal DecisionPlan → validator → 用户 Agent final handoff → 确认保存 → 下一次触发回看。测试B:无凭据 → setup_required → agent_delegate → validate → final handoff。测试C:数据源大面积失败 → no_action/blocked,不得编造机会。

**Phase G 出口验收**:

- [ ] 全量测试、ruff、compileall 全过且默认测试零外网。
- [ ] 真实数据源冒烟:候选池 ≥90% 有 ≥40 bars;所有入选机会 freshness 合格、来源可追溯;低于门槛则本次出口失败而不是降标准。
- [ ] MCP 默认调用返回 DecisionEnvelope,不再只有 AnalysisContext;内部 LLM 凭据缺失路径能指导用户安全配置或委托用户 Agent。
- [ ] 真实个人组合样本中,每条建议都有动作/幅度/资金来源/触发/证伪/期限/证据;无法计算时明确 blocked reason。
- [ ] 用户 Agent最终输出必须体现对 DecisionPlan 的审查,至少一项明确采纳或有理由推翻,证明它不是机械复述内部 LLM。
- [ ] 自动交易仍不存在;任何资产/建议持久化继续要求用户确认。

## 全局验收(每个任务完成后必跑)

```bash
uv run ruff check .
uv run python -m pytest -q
uv run python -m compileall -q stocks tests
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

Phase R 历史终局验收(只证明 P/P2/M 当时出口,**不代表 D/F/G 已完成**):

- [x] 通过 MCP/CLI 用自然语言驱动的 Agent 能完成:改持仓 → 改偏好 → 生成个人建议,全程不手改 JSON。
- [x] 系统未经确认参数不修改任何金融记忆文件。
- [x] 第二次运行能引用上次快照做前后对照。
- [x] `data_quality` 中所有降级/换算失败/单源风险均可见,无静默错误信号。
- [x] raw_prompt_input 不含逐笔精确金额。

> 完成:671ee12 Phase R 终局行为验收重新核验通过。| 证据:`tests/test_asset_adapters.py:127` 覆盖 MCP 改持仓/改偏好/取上下文且未确认拒写,`tests/engine/test_end_to_end.py:180` 覆盖第二次快照,`tests/engine/test_context_builder.py:130-132` 覆盖 raw_prompt 金额脱敏;本轮 P2-1~P2-5 每项全局验收均通过。

---

## 明确不做(防止执行 Agent 跑偏)

- 不做通用重型回测平台、因子库、多 Agent 辩论;G3 只允许为现有机会/动作信号做轻量 walk-forward 校准。
- 不引入 FastAPI / SQLAlchemy / Redis / MCP SDK 重写;G6 在现有轻量 MCP 适配器新增决策工具不属于 SDK 重写。
- 不做资产文件加密(单机 NAS 场景,gitignore + 文件权限足够,过度工程)。
- 不新增任何分析/设计/调研类 markdown。
- 不让内部 LLM 成为最终裁决者或直接执行交易;允许将 `llm_analysis.py` 重构为受 DecisionPlan schema/validator 约束的内部决策生成器,最终完整分析仍由用户 Agent完成。
