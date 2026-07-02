# EXECUTION_PLAN.md — 修复与收口执行清单

> 生成时间:2026-07-02
> 来源:对全部文档(约 9,600 行)与全部核心代码(约 5,900 行)的交叉审计
> 用途:供执行 Agent 按顺序落地。本文档是**唯一的行动清单**,与 `PLAN.md`(阶段规划)互补,不新增其他文档。
> 执行原则:每个任务动手前先用 grep/读码验证前提(标注为【验证前提】的行),防止基于过时快照误改。

---

## 使用说明(执行 Agent 必读)

1. 严格按 P0 → P4 顺序执行,同一优先级内按编号顺序。
2. 每完成一个任务,运行全局验收(见文末),全部通过后才能进入下一任务。
3. 勾选格式:完成后把 `- [ ]` 改为 `- [x]`,并在任务末尾追加一行 `> 完成:<commit hash> <一句话说明>`。
4. 遵守 `PLAN.md` 第 8 节禁止事项:不引入重型依赖、不伪造指标、不提交 `.local/`、`.secret/`、缓存。
5. 任何任务如果发现前提不成立(代码已改过、文件不存在等),不要硬改——在任务下记录 `> 跳过:<原因>` 并继续。

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

> 完成:b6bc27b CLI/MCP 暴露确认式资产 CRUD，并以 adapter 全流程测试验证落盘。

**验收**:外部 Agent 能通过 MCP/CLI 完成资产维护,无需手改 JSON。

### P2-2 落地 `investor_profile.json`(偏好记忆)

**文件**:`stocks/engine/__init__.py`、新增 `stocks/data/investor_profile.example.json`
**问题**:代码读取 `investor_profile.json` 但该文件在仓库中不存在,连示例都没有,用户画像恒为空。

- [x] 定义最小 schema(建议字段:`risk_tolerance`、`investment_horizon`、`preferences`(自由文本数组)、`constraints`(禁投/上限类)、`updated_at`)。
- [x] 提交 example 文件;真实文件路径为 `.local/investor_profile.json`(gitignore),加载优先级与资产文件一致。
- [x] MCP/CLI 暴露 `profile_get` / `profile_update`(同样要求显式确认参数)。
- [x] `ContextBuilder` 确认 profile 注入 `raw_prompt_input` 的【用户画像】段。
- [x] 新增测试:有/无 profile 文件两种情况下 build_context 均正常,有文件时画像段非空。

> 完成:6cc4024 新增本地画像 schema 与 example，CLI/MCP 确认式更新，并验证 prompt 注入。

**验收**:CLI smoke 输出的 raw_prompt 含画像内容。

### P2-3 闭合历史快照回路

**文件**:`stocks/engine/__init__.py`、`stocks/engine/persistence.py`
**问题**:`build_context` 读 `load_recent(5)` 但 `save_context()` 全库零调用,`recent_snapshots` 永远为空,系统不记得上次说过什么。

- [x] 【验证前提】grep 确认 `save_context` 零调用。
- [x] `build_context` 成功后按配置写入快照(`engine.yaml` 的 `save_to_file` / `max_snapshots` 真正生效——同时消灭这两个僵尸配置);快照目录 gitignore。
- [x] 快照内容最小化:时间戳、组合概要、market_state、drift 结果,不必存全量新闻。
- [x] 实现 `max_snapshots` 滚动清理。
- [x] 新增测试:连续两次 build_context,第二次的 `recent_snapshots` 非空且能对照差异。

> 完成:c6e5c87 build_context 保存最小滚动快照，第二次构建注入上次组合状态用于对照。

**验收**:第二次运行的上下文中包含"上次快照"信息,LLM 可做前后对照。

### P2-4 把 `personal_advice_prompt.txt` 接进主链路

**文件**:`stocks/engine/llm_analysis.py`、`stocks/prompts/`、`AGENT_GUIDE.md`
**问题**:全库质量最高的 prompt(反幻觉、金额脱敏、格式规范)没有任何代码加载;`llm_analysis` 用的是内联弱 prompt,且两者对"是否暴露金额"要求直接矛盾。

- [x] `LLMAnalysis.generate_report()` 改为从 `stocks/prompts/personal_advice_prompt.txt` 加载系统 prompt,内联 prompt 删除。
- [x] 在 `AGENT_GUIDE.md` 增加一节:外部 Agent 作为主脑时,应读取该 prompt 文件作为分析指引(明确它是给内部 LLM 和外部 Agent 共用的"分析宪法")。
- [x] 解决金额矛盾:`raw_prompt_input` 中资产金额改为**占比 + 量级区间**表达(遵循 prompt 的脱敏要求);精确金额仅保留在结构化 `to_dict()` 中,由调用方决定是否使用。
- [x] 删除死代码 `extract_constraints()`(其职能由 P2-2 的 profile_update 承接)或接线到 profile 更新流程——二选一,倾向删除。

> 完成:6cf7688 内外部分析统一加载 advice prompt，删除死提取器，并将 raw prompt 金额改为占比与量级。

**验收**:`--llm-analysis` 输出遵循 advice prompt 的格式约束;raw_prompt 不再出现逐笔精确金额。

### P2-5 HTTP adapter 安全声明与最小防护

**文件**:`stocks/adapters/http.py`
**问题**:无鉴权接口全量输出资产精确金额;500 响应直接 `str(exc)` 外泄内部错误。

- [x] 默认绑定 `127.0.0.1` 强制校验(非 127.0.0.1 启动时要求显式 `--allow-remote` 并打印告警)。
- [x] 增加最简 Bearer Token 校验(从 `.secret/http-token` 读,文件不存在则拒绝非 localhost 请求)——不引入框架,标准库实现。
- [x] 500 响应改为通用错误消息 + 内部日志记录详情。
- [x] 资产金额输出遵循 P2-4 的脱敏口径,提供 `?include_amounts=true` 显式开关。

> 完成:671ee12 HTTP 默认本机绑定，远程强制 Bearer 鉴权，错误收口且金额默认脱敏。

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
- [ ] `engine.yaml` 僵尸项逐一处理(实现或删除,不留假配置):
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

- [ ] 美股行情失败时,`data_quality` 明确标注 `us_quotes: single_source_failed`,并尝试用 `HistoryCache` 最近收盘价作 stale 兜底(带 `stale: true` 标记)。
- [ ] 第二数据源列为 Phase 3 候选,写入 `PLAN.md` 暂缓区,本清单不展开。

**验收**:Finnhub 挂掉时上下文仍有带 stale 标记的美股参考价。

---

## P5 — 文档收口(一次性做完,之后冻结)

### P5-1 归档过时文档

**问题**:`stocks/` 目录下整套 v1 文档与当前系统矛盾却自称"当前主线";v2 五份文档大量重复且部分结论已被互相推翻。

- [ ] 新建 `docs/archive/`,移入:`stocks/README.md`(v1 版)、`stocks/ARCHITECTURE.md`、`stocks/LLM_DRIVEN_DESIGN.md`、`stocks/ANALYSIS_RULES.md`、`stocks/NEWS_INPUT_RULES.md`、`stocks/DATA_SOURCES.md`(P0-1 清理 key 之后)、`stocks/REFACTOR_PRINCIPLES.md`、`stocks/ROADMAP.md`、`ARCHITECTURE_BOUNDARY_ANALYSIS.md`、`DESIGN_GAP_ANALYSIS.md`、`LLM_ENHANCER_ANALYSIS.md`、三份 `LLM_QUANT_*.md`。每份文件头部加一行:`> ARCHIVED 2026-07:结论已吸收或废弃,勿作为开发依据。`
- [ ] `MEMORY_RULES.md` 不归档——它是 P2 的需求规格,移到根目录或并入 AGENT_GUIDE。
- [ ] `stocks/DATA_MODEL.md`:删除其中 v1 遗留段(AdvisoryPlan/AdvisorContext),保留 AnalysisContext v5 部分,作为现行 schema 文档。
- [ ] `DESIGN.md` 与 `ARCHITECTURE_V2.md` 合并为一份 ≤300 行的 `ARCHITECTURE.md`(根目录),只描述**当前实际实现**,删除所有未实现的设计段(三级粒度、子命令 CLI 等);其余内容进 archive。
- [ ] 修正 README.md / README.zh.md 中与实际不符的描述。

**验收**:根目录活跃文档 ≤ 6 份(README ×2、AGENT_GUIDE、PLAN、ARCHITECTURE、EXECUTION_PLAN);任何一份活跃文档中引用的文件/命令/模块必须真实存在。

### P5-2 文档冻结规则写入 PLAN.md

- [ ] 在 `PLAN.md` 禁止事项中追加:"新增 .md 文件前必须先证明现有文档无法承载;分析/调研类文档一律进 `docs/archive/`,不进根目录。"

---

## 全局验收(每个任务完成后必跑)

```bash
uv run ruff check .
uv run python -m pytest -q
uv run python -m compileall -q stocks tests
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes
```

全部完成后的终局验收(对照 VISION.md 成功标准):

- [ ] 通过 MCP/CLI 用自然语言驱动的 Agent 能完成:改持仓 → 改偏好 → 生成个人建议,全程不手改 JSON。
- [ ] 系统未经确认参数不修改任何金融记忆文件。
- [ ] 第二次运行能引用上次快照做前后对照。
- [ ] `data_quality` 中所有降级/换算失败/单源风险均可见,无静默错误信号。
- [ ] raw_prompt_input 不含逐笔精确金额。

---

## 明确不做(防止执行 Agent 跑偏)

- 不做回测框架、因子库、多 Agent 辩论(已被 LLM_QUANT_RESEARCH_VERIFICATION 否决)。
- 不引入 FastAPI / SQLAlchemy / Redis / MCP SDK 重写(PLAN.md Phase 4 之前不动)。
- 不做资产文件加密(单机 NAS 场景,gitignore + 文件权限足够,过度工程)。
- 不新增任何分析/设计/调研类 markdown。
- 不扩展 `llm_analysis.py` 的决策能力边界(它输出参考报告,最终判断归外部 Agent)。
