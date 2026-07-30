# stocks-claw 系统对抗式一致性分析报告

> 日期：2026-07-30
> 方法：通读 VISION/ROADMAP/ARCHITECTURE/STATUS/contracts 后，直接阅读生产链路全部
> 关键代码（adjudicator / presentation / outlook_validation / execution_rules /
> build_push_payload / scheduled_analysis / quant_action / factor_rules /
> risk_state / cron / 配置），并在本机实际运行验证基线。
> 立场：对抗式校验。只报告可定位到文件与行号的证据，不做恭维性总结。

## 0. 验证基线（本机实测，非引用 STATUS.md）

| 项目 | STATUS.md 声称 | 本机实测 | 结论 |
|---|---|---|---|
| HEAD | `96ed43d` | `ae8abc6`（`96ed43d` 在仓库中**不存在**） | **不一致** |
| Tag `v2.8-e1e2-complete` | 指向 E1/E2 完成点 | 指向 `cc1eaa0`，**也不是 HEAD** | **不一致** |
| 全量测试 | 1253 passed / 0 failed | **6 failed**, 1247 passed, 7 skipped | **不一致** |
| ruff | clean | clean | 一致 |
| 工作区 | clean | clean | 一致 |

6 个失败全部在 `tests/engine/test_data_quality_gate.py`，原因是测试引用
`.superpowers/sdd/task-2-a512480-fixture.json`（`test_data_quality_gate.py:19`），
该文件**从未纳入 git**（`git ls-files` 无记录、`.gitignore` 无条目、磁盘上不存在）。
也就是说：当前"全绿基线"只在这台机器的某个历史状态下成立，任何干净 clone 上
测试套件都是红的。历史显然被 rebase/amend 过，而 STATUS.md 和 tag 没有同步更新。

**这是全报告最重要的元发现：一个以"可审计、单一事实源"为核心设计原则的系统，
它自己的状态账本（STATUS.md）、版本锚点（tag）和验证基线（测试）三者已经互相脱节。**
以下所有代码层发现都应在这个前提下阅读——文档声称已验证的东西，未必可复现。

## 1. 系统真实形态（与宣称形态的对照）

宣称的目标责任链（VISION/ROADMAP）：确定性证据 → LLM 分析师综合判断 → 确定性校验
→ 纯投影渲染 → 用户决策。

**实际生产责任链（代码实测）**：

```text
硬编码规则信号（quant_action: -12%止损/10-20-30%止盈/MA20±2%加仓）
  → 因子管道乘法降权（factor_rules: 0.5×0.5 可叠加）
  → finalize_decision 规则裁决
  → portfolio_adjudicator 二次规则裁决（含冲突场景默认 50% 执行）
  → execution_rules.yaml 结算/数量解析（唯一配置化、fail-closed 的干净层）
  → presentation.build_user_view 投影（E1 后质量较高）
  → validate_push_truth + validate_payload_text 双闸门（E2 后其中一道已近乎失效）
  → Feishu
```

LLM 在生产中只出现在两个位置：新闻情报聚类（intelligence_analyzer）和受限 Outlook
（outlook_synthesizer）。Advisory shadow 管线（A0–A4）已存在但与生产零接触。
**系统的决策中枢今天仍是规则引擎，与 VISION 的四方责任模型方向相反**——这一点
ARCHITECTURE.md §3.2 自己承认，本报告确认属实，且下文指出它比文档承认的更深。

## 2. 对抗式发现清单

分级标准：P0 = 会向用户输出错误数字/错误动作，或使验证体系失效；
P1 = 违反系统自己声明的不变式，或制造语义裂缝；P2 = 结构性脆弱，长期腐蚀一致性。

---

### P0-1 替换链买入腿的估算金额基准错误

`portfolio_adjudicator.py:503-506`：`estimated_amount_cny = market_value × |final_ratio|`
对所有动作统一按**持仓市值**计算。但替换链买入腿的 ratio 是在
`adjudicate_portfolio:724` 用 `sale_proceeds / total_after` 算出的**组合基准**
（`ratio_basis="portfolio"`，`portfolio_adjudicator.py:758`）。
正确金额应为 `组合总值 × final_ratio`，实际算的是 `替代标的市值 × final_ratio`。
除非替代标的市值恰好等于组合总值，否则用户在报告里看到的"约 ¥X"是错的，
且方向不定（替代标的远小于组合时严重低估）。E1 truth gate 只校验百分比文案与
final_ratio 一致，不校验金额的基准。买入腿同时被强制 `review_required`
（execution_rules.py:116-120），所以它会走 deferred 文案路径——但见 P1-4，
那条路径的文案也是错的。**换仓这个系统里最复杂的动作，两条腿在用户侧都是失真呈现的。**

### P0-2 E2 改造后，渲染文本数字授权闸门近乎失效

`build_push_payload.py:801-806`：`validate_payload_text` 在做数字授权扫描前，
将文本在第一个命中的 `**本窗口变化**` 标记处截断。这个截断逻辑是 legacy 版式
（确定性段落在前、`中长期研判` 在后）时代设计的，意图是"只切掉 outlook 段落"。
E2 版式把 `本窗口变化`（内含 outlook 摘要）放到了**第一节**，于是截断点落在
全文开头，"可执行动作 / 禁止与延后 / 组合影响 / 下一检查点"四节——也就是所有
含金额、数量、百分比的确定性内容——**完全不再被数字授权扫描**。
目前文本由 payload 直接渲染，漂移需要渲染器本身出 bug 才会兑现，所以这是
纵深防御的空洞而非正在发生的错误；但 E1/E2 的全部设计意图就是"渲染层必须被
独立复核"，这道闸门现在名存实亡。

### P0-3 超过 3 个可执行动作被静默丢弃，无任何提示

`presentation.py:701`：`approved_cards = all_approved[:3]`。第 4 个及以后的
**可执行**动作不进卡片；它们不是 deferred（可执行门通过），不在
`no_action_reasons`，不在 `do_not_do`，`validate_push_truth` 也不检查
"approved 集合是否被完整呈现"。用户会拿着一份"全部可执行动作"的报告，
却不知道还有动作没显示。E2 的 3 动作上限是设计决策，但"超出即消失且无计数提示"
是真理缺口。当前持仓数量下触发概率低，属于潜伏型 P0。

### P0-4 测试基线不可复现 + 状态账本失真（见 §0）

6 个测试依赖未入库的本地 fixture；STATUS.md 的 HEAD hash 不存在；tag 指向与
HEAD 不同。三者叠加意味着：任何人（包括未来的 agent 会话）按 AGENTS.md 流程
"以 STATUS.md 为动态事实源"开始工作时，拿到的是一个无法复现的绿色幻觉。
**这直接破坏 AGENTS.md 的 Precedence 第 3 条。**

---

### P1-1 冲突场景的"默认执行 50%"违反 VISION §3.3

`portfolio_adjudicator.py:814-829`：权益低配 + 减仓信号冲突时，系统"默认先执行
部分仓位（50%），剩余等待人工确认"。VISION §3.3 第 6 条及末尾明确要求"冲突
无法裁决时必须交给用户，不得用默认规则伪装成唯一正确答案"。50% 是一个无出处
的魔法数字，本质是规则引擎在冲突场景给出默认裁决。缓解因素：整体 decision 会
落入 `review_required`，文案也标注了"等待人工确认"；但动作本身已进入
`approved_actions` 并携带具体比例。**系统在最需要谦逊的场景（方向冲突）给出了
一个武断数字。**

### P1-2 "硬止损不受约束限制"让规则引擎在最关键场景保留最高权威

`portfolio_adjudicator.py:800-808`：`stop_loss` 信号直接绕过约束冲突进入
approved。而止损阈值是 `quant_action.py:123` 的硬编码 `-12%`，无回测、无配置、
无用户画像接线（`_build_persona` 里读取的用户止损偏好只影响文案语气，不影响
引擎阈值）。ROADMAP 的方向是"规则信号降级为候选证据"，现状是**规则引擎持有的
最激进的一张牌（满仓止损）恰好享有最高的免审特权**。

### P1-3 数字授权原则在两处被自己违反

VISION §3.2："数字授权必须基于 metric + value + unit + source/fact_ref，不能用
'其他字段碰巧出现相同数字'授权。"实际实现：

- `outlook_validation.py:492-538`：`_collect_evidence_numbers` 递归收集证据包里
  **所有**数字（含计数、序号、四舍五入变体），narrative 中任何数字只要命中即授权；
  更有一条 `1 <= num <= 100` 无条件放行（line 533-535）。这恰恰是 VISION 明文
  禁止的授权方式，外加一个覆盖绝大多数百分比/比率的万能后门。
- `build_push_payload.py:50-70`：`_number_values` 为每个数字同时授权其 ×100
  变体（0.05 → 5），授权范围再次放大。

Outlook 校验器整体（来源授权、置信度上限、三情景完备性、禁止概率字段、注入检测）
是系统里设计最认真的组件，但数字授权这一环的实现强度远低于其宣称的语义。

### P1-4 替换链买入腿文案系统性失真

买入腿因 `ratio_basis="portfolio"` 永远 `review_required`（设计如此），落入
`presentation._deferred_action_text` → `_safe_reason_text`（presentation.py:166-175）。
该函数用子串匹配分类 reason 文本；"卖出资金到账后转买替代标的，维持权益敞口；
quantity basis portfolio is not modeled" 匹配不到任何已知类别，最终渲染为
**"组合约束或风险条件尚未满足，等待下一检查点"**——一个设计内的换仓买入动作，
被描述成约束不满足的待定项。用户无法从报告中区分"换仓的买入腿"和"被约束挡住
的动作"。

### P1-5 两套互相矛盾的结算权威并存

- 权威 1：`stocks/config/engine.yaml` execution_rules（配置化、fail-closed、
  有序匹配）——fund_platform + t2_plus → T+2。
- 权威 2：`scheduled_analysis.py:107-120` `_settlement_timing_for_institution`
  ——fund_platform → **"T+1"**，未知机构默认 **"T+1"**。

两者对同一平台给出不同结算周期。当前 approved 动作走权威 1（正确），但权威 2
的字符串仍写入每张 action card 的 `settlement_timing` 字段并流入
`format_run_markdown` 等审计面。VISION §3.2"同一种约束只能有一个权威验证位置"
被违反。未知数：是否有消费者仍读 card 级字段做展示。

### P1-6 情报推送通道整体在 truth gate 之外，且渲染买入/卖出标签

`build_push_payload.py:153-154`：`validate_push_truth` 对 `session_type !=
"trading"` 直接返回空；`validate_payload_text` 的 E2 结构校验同样只对 trading
生效（line 826）。而 `_render_intelligence_payload`（line 247-252）把
`action_signal_reviews` 渲染为"**操作信号** → **买入/卖出** 标的: hint"。
这些 signal 来自 LLM 情报分析器（带关键词回退），未经裁决器、未经可执行门、
未经来源授权校验。**系统存在第二条直达用户的建议通道，且它恰好绕过了为第一条
通道修建的全部闸门。** 即便其内容定位是"情报"，`买入/卖出` 的标签措辞与
"不产出交易指令"的架构声明（ARCHITECTURE.md §2 表）直接冲突。

### P1-7 桶暴露多重计数，约束判定与组合投影可被高估

`portfolio_adjudicator.py:443-454`：一个持仓若映射到多个 exposure bucket，
其**完整市值**计入每个桶；`bucket_ratios` 各桶之和可超过 100%。
`gold_pct`/`equity_pct` 的约束判定（line 668/686）和
`post_trade_projection` 的 before/after 对比都建立在这个可能膨胀的分母分子上。
多标签持仓越多，超限误报/漏报越偏离真实敞口。

### P1-8 现金五桶分解被隐性打破

`build_cash_schedule`（line 368-371）：硬编码 5% 安全垫从 `immediate_cash_cny`
中扣除后，`to_dict()` 的 `available_now` 已缩水，但 `safety_buffer_cny` 不属于
五桶中的任何一个。契约注释声称"每分钱落在五桶之一"（line 66-67），实际
`available_now + confirmed_settling + planned_release + strategic_exit + locked
+ safety_buffer = 总额`。用户看到"现在能用"的金额被静默打了 95 折，且五桶
加总对不上组合总值，无任何说明。

### P1-9 freshness 语义跨层双重标准

同一份陈旧行情：`factor_rules.DataFreshnessRule`（line 143-159）对 stale 信号
"降权 50% 放行"；`presentation._market_quote_stale`（line 322-330）对同一市场
"完全阻断"。一个说减半执行，一个说禁止执行。当前 presentation 层更严所以实际
效果是阻断——但这意味着因子管道的降权逻辑在这些场景是死代码，且两层对
"数据多旧算不可用"没有共享定义。

### P1-10 会话词汇三层漂移 + 6 个 cron 入口指向不存在的 session

- 调度配置（scheduled_sessions.json）只有 5 个 session；
- `build_push_payload._SESSION_LABELS` 支持同样 5 个；
- `presentation._session_checkpoint`（line 866-877）却为 `cn_pre_open /
  cn_open_watch / cn_pre_close ...` 8 个 id 准备文案——对真实 session 全部落到
  默认值，checkpoint 文案从不生效；
- `scripts/cron/` 下 6 个脚本（cn_pre_open / cn_open_watch / cn_pre_close /
  us_pre_open / us_open_watch / us_pre_close）调 `run_push_report.py --session
  <不存在的session>`，每次运行必然 INVALID exit 2。
  （git 历史显示 `6299da6` 曾"collapse 12+ push windows to 5 tactical nodes"，
  这些脚本是 collapsing 的遗留。）

### P1-11 节假日表为空，会话日历把假日当交易日

`scheduled_sessions.json` 中美 `holidays: []`。`_is_market_date` 只能按星期判断。
法定假日会产生基于前一交易日陈旧行情的"正常"分析运行；虽然
`_market_quote_stale` 会兜住可执行性，但报告仍以正常窗口口径推送，且 outlook、
研究候选、风险状态都会照常更新。**数据新鲜度闸存在，"今天是不是交易日"的闸不存在。**

---

### P2-1 路由回退依赖命名启发式

`scheduled_analysis.py:2362-2386`：product_type 缺失时，用 `account_id` 是否含
"alipay"/"ccb"/"boc"、`position_id` 是否含 "gold"/"wmp"/"mm"/"cash" 子串决定
产品路由（fund/precious/info_only/skip）。用户重命名一个持仓即可改变其交易路由
与可执行性分类。金融事实应从确认过的结构化字段读取，不应从命名约定猜测。

### P2-2 魔法数字无出处地散布在代码而非配置

-12% 硬止损、10/20/30% 止盈阶梯（0.25/0.25/0.50）、MA20 ±2% 加仓、¥800 最小
金额（`build_capital_allocation_with_suppression`）、5% 安全垫、情景冲击系数表
（crypto -30% / energy -12% / gold +8%...，`quant_action.py:949-972`）、
risk_state 的 10%/15% 现金目标。execution_rules 已经证明了"规则进配置、代码
fail-closed"是可行的更好的模式，其余阈值没有跟随这个模式。`rule_backtest.py`
（178 行）无任何生产消费者——回测能力存在但没接进任何决策依据。

### P2-3 上帝文件与循环依赖

`scheduled_analysis.py` 3056 行、`context_builder.py` 2585 行、
`engine/__init__.py` 1541 行。`portfolio_adjudicator` 在函数体内反向 import
`scheduled_analysis._build_capital_allocation`（line 386）。ARCHITECTURE §3.2
承认职责过宽；实测比承认的更严重：调度、裁决、渲染辅助、文案映射、平台知识
全部搅在一个文件里，是 A5 迁移时最大的单点改造风险。

### P2-4 失败语义 = 静默

推送管线任何一环失败都是 `INVALID` + exit 2：用户**收不到任何东西，也收不到
"今天失败了"的通知**。artifact 45 分钟有效期（`build_push_payload.py:88`）+
cron 抖动 + LLM 超时都会直接转化为静默漏报。对一个"主动节奏"是核心能力域
（VISION §4.7）的系统，没有 dead-man 报警是能力缺口。另：us_after_close
16:00 ET = 北京时间凌晨 4–5 点，`quiet_hours.enabled = false`。

### P2-5 risk_state 单机文件锁；现金目标只展示不闭环

`RiskStateStore` 用 fcntl 文件锁保证单机原子性，部署到 NAS/多机即失效。
`cash_target_pct`（10%/15%）会进入 mandatory blocks 文案，但没有任何逻辑检查
实际现金与目标的偏离，更没有把它接入 adjudicator 的约束判定——风险状态的
"现金目标"是纯展示品。

### P2-6 投影假设即时成交

`post_trade_projection` 在卖出资金 T+1/T+2 未到账时就把买入腿计入 after_ratios，
替换链的"权益敞口不变"结论只在结算完成后成立。结算窗口内的真实敞口变化
（先降后回）没有建模。

### P2-7 prompt-injection 与文本分类防线的对抗强度有限

`_PROMPT_INJECTION_RE` 只覆盖 5 个固定模式；`_safe_reason_text` /
`_section_blocked_and_deferred` 的优先级分类靠中英文子串匹配，构造特定措辞
即可改变分类落点。真正的防线是 presentation 的白名单投影
（`project_outlook_for_display`），正则只是装饰。这个判断不影响当前安全
（白名单确实兜底），但文档和注释不应把正则描述成"检测"。

## 3. 分角色分析

### 3.1 投资交易分析师视角

做得对的：五段报告结构（变化/可执行/禁止/组合影响/检查点）符合交易纪律；
取消条件与下一检查点是职业级设计；研究候选与已裁决动作的双向去重（含显示帽
之外的集合）是 E1 的真实进步；execution_rules 的配置化 + fail-closed 是整条
链路里工程质量最高的部分。

不可接受的：换仓是系统能做的最复杂操作，而它的买入腿**金额算错（P0-1）、
文案说错（P1-4）**——用户最该被精确告知的场景恰恰双重失真。节假日空表
（P1-11）意味着假日清晨会收到一份"正常"报告。冲突默认 50%（P1-1）违背了
交易系统第一原则：不确定时不动作。情报通道的"买入/卖出"标签（P1-6）如果
被用户按字面执行，责任边界完全模糊——系统的永久边界"不自动下单"在工程上
成立，在语义上被自己的情报报告稀释。

### 3.2 量化交易分析师视角

信号层没有量化研究基础：所有阈值是拍脑袋常量，无回测接线（rule_backtest 是
孤儿模块）、无样本外验证、无参数稳定性分析。因子管道的乘法降权（0.5×0.5=0.25）
在多个条件同时触发时会非直觉缩量。freshness 双重语义（P1-9）说明"数据状态"
没有统一的领域模型。decision_id 用 float 的 str() 参与哈希，跨 Python 版本有
理论上的不稳定风险。好消息是：系统定位本就不是策略执行器，止损/止盈阶梯作为
"纪律提醒"是合理的——但代码里它们的名字是"引擎"和"裁决"，名实不符会诱导
未来的维护者按策略系统的标准去信任它。

### 3.3 中长期经济资产分析师视角

Outlook 子系统是全系统最成熟的部分：三情景 + 验证/证伪条件 + 置信度上限 +
来源四元组授权 + 概率字段禁用，这是接近卖方研究合规要求的设计。三处硬伤：
（1）数字授权过宽（P1-3）让"每个数字都有证据出处"的承诺名不副实；
（2）forecast_candidates 的 deadline 必须命中 evidence 里已有的事件日历日期
（outlook_validation.py:742-748），意味着**系统只能对已知日历事件做可问责预测，
无法对自发情景设立验证时限**——可问责预测能力被自己阉割了一半；
（3）宏观数据陈旧只产生一条 data_note 而不影响 Outlook 的置信度上限判定之外的
任何结论，宏观证据的时效性与研判结论之间没有传导机制。

### 3.4 系统架构设计师视角

架构的真正强项：fail-closed 哲学在 execution_rules、`_market_quote_stale`、
`_is_executable`、`_check_source_refs_presence` 等处一以贯之；E1 确立的
"单一生产者"原则（`_finalize_approved_action`）和"投影不重算"原则
（build_user_view verbatim projection）落实得很干净；shadow 管线与生产的
物理隔离（grep 验证无交叉调用）是教科书式的迁移纪律。

架构的真正弱点不在代码内，而在**元层**：状态账本失真、tag 漂移、测试基线
依赖未入库文件、6 个 cron 脚本指向被删除的 session、checkpoint 文案与真实
session 词汇不匹配。这些全是"文档/配置/脚本/测试"四个外围面与代码不同步的
症状——系统在代码评审层面的严谨度（E1/E2 的 defect 追踪质量很高）没有延伸
到仓库治理层面。其次是 A5 迁移缺乏工程抓手：没有 `report_mode` 开关、A3/A5
门禁客观上需要数十个真实交易日，意味着"规则引擎拥有最终权威"这一与愿景相反
的状态将长期固化，双轨并存期越长，两套语义（如 freshness、结算）相互渗漏的
概率越高。

### 3.5 对抗/红队视角

若我是恶意输入：（1）我可以在 outlook narrative 里放任何 1-100 的数字，合法；
（2）我可以让情报聚类产出带"买入"字样的 signal，它会绕过全部闸门直达推送；
（3）我可以通过让某持仓 evidence 携带多 exposure_tags 来膨胀某个桶的占比，
影响约束判定；（4）我可以让一份 outlook 引用证据包里出现过的任何大数字
（比如某个持仓市值），换一个语义完全不同的上下文使用，依然通过校验。
白名单投影兜住了注入的下限，但"授权"逻辑的上限比文档宣称的低得多。

## 4. 结论

**这套系统现在是什么**：一个工程质量不均的混合体——渲染与投影层（E1/E2 之后）
达到了它宣称的标准；结算与可执行性解析层超过了宣称的标准（配置化 + fail-closed）；
决策层仍停留在 ROADMAP 想要取代的形态；数字授权与情报通道两处，实际强度低于
自己声明的不变式；仓库治理层（状态账本、tag、测试基线、cron 配置）是整个系统
一致性最薄弱的一环，且它恰好是系统赖以自我审计的一层。

**最大的三个结构性风险**：

1. **自我审计能力失真**（§0 + P0-4）：一个把"可验证"作为立身之本的系统，其
   验证基线不可复现、状态账本与 git 事实脱节。这不是代码 bug，是信任根基 bug。
2. **换仓动作的双重失真**（P0-1 + P1-4）：最复杂动作的金额与文案同时错。
3. **第二建议通道在闸门之外**（P1-6）：情报推送的"买入/卖出"标签绕过了为
   交易通道修建的全部真理防线。

**建议修复顺序**（不改变既有任务排序的前提下，建议插入或并入）：

- 立即：修复测试 fixture 入库（或把 6 个测试改为自带合成数据）；修正 STATUS.md
  的 HEAD/tag 记录并补一条"STATUS 失真过"的事实记录；重跑 117 P0 审计确认收敛。
- TASK-002 之前：修 P0-1（买入腿金额基准）、P0-2（数字扫描边界适配 E2 版式）、
  P0-3（超帽动作计数提示）、P1-4（换仓买入腿专用文案类别）。
- TASK-002 期间：统一结算权威（删 `_settlement_timing_for_institution` 或改为
  从 execution_rules 派生）；情报通道的 signal 标签改为中性词汇或纳入 truth gate。
- 后续任务面：节假日表、冲突默认 50% 改为纯 review、桶暴露去重、
  freshness 单层语义、cron 死脚本清理。

**不应做的事**：不要在修复 P0-1/P0-2/P0-3 之前开始 TASK-002——AdviceRecord
会把错误的金额和不完整的动作集合固化进用户确认记录，错误将从"展示层"升级为
"金融记忆层"，那才是不可逆的。
