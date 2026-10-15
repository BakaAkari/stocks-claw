# 信号评价与策略反馈架构复核

> 日期：2026-10-15
> 范围：当前代码、运行账本、现有文档与公开成熟系统设计。
> 性质：只读架构审查；不修改生产逻辑，不把当前胜率当作策略真值。

## 1. 结论

生产分析链已经具备较完整的数据采集、规则分析、LLM 情报/研判、组合裁决和推送能力；但信号评价链仍是实验性附属脚本，不能作为策略调参或信任判断的可靠依据。

> 当前评价基础设施不能稳定、唯一、可复现地回答一条生产建议在目标时间窗内的结果，因此当前胜率只能作为诊断数据，不能作为策略真值。

问题集中在评价边界，不要求立即重写整个投资分析系统。

## 2. 代码事实

### 2.1 信号生成与评价不是同一个契约

生产信号至少来自 `llm` / `fallback_rules` 情报候选和 `engine_action` 组合动作/研究候选路径。`TrackedSignal` 同时承担信号事实、窗口状态、价格、正确性和统计输入。`left_bottom_candidate`、`wait_for_pullback`、`reduce_risk` 等行动语义被压缩成 `buy` / `sell`，不能天然等价于未来 24 小时价格预测。

### 2.2 结算写入不具备真正幂等性

`SignalTracker.settle()` 直接追加 `settlements.jsonl`，随后才重写 `signals.jsonl` 的 settled 标记；两个文件之间没有事务、唯一键或并发锁。

真实账本验证：19,849 条原始记录、2,860 个唯一 `signal_id + window`、16,989 条重复额外记录；1,006 个组合有重复，988 个组合存在结果冲突，最高重复 140 次。历史账本不是一条信号窗口对应一条结果，统计分母已污染。

### 2.3 目标时间与观测时间没有分离

`scripts/signal_settlement.py` 用 `generated_at + 24h/7d` 判断到期，再以 cron 执行时拿到的 `current_price` 结算。没有 `target_at`、`observed_at`、价格来源、数据 as_of 或交易日历会话。`stale_window` 把任务未运行、provider 失败、休市/节假日、过期补跑等不同原因压成一个状态。

代码使用自然时间 `24h` / `7d`，而质量报告称其为“1 个交易日”/“5 个交易日”，存在代码—文档漂移。

### 2.4 entry price 事实链曾经断裂，且仍需运行级验证

生成侧曾只按带前缀 symbol 查行情字典，行情键是裸代码，造成大量 `generation_price=None`。当前 cron 输出仍出现 `a:518880 entry=None`、`a:512480 entry=None`。标的规范化、报价快照和信号记录之间仍有多套解析路径。缺 entry 的信号仍先进入 tracker，结算时才被标为 `missing_entry_price`。

### 2.5 invalid 语义混合

`stale_window`、`missing_entry_price`、`missing_exit_price` 和 `below_min_change` 都进入 `invalid`。其中 `below_min_change` 是可观测但中性，不是数据无效。当前同时保存 `correct` 与 `invalid`，语义容易被误读。

### 2.6 统计脚本没有消除账本污染

`scripts/signal_quality_report.py` 只过滤 `invalid`，没有按 `signal_id + window` 去重，还用 `signal_id.split("_")` 猜 symbol。它能生成报告，但不能证明统计的是独立样本。

### 2.7 生产、回测和评价没有同一版本锚点

仓库同时存在 `rule_backtest.py`、`DecisionSnapshot`/decision attribution、`SignalTracker`/`settlements.jsonl` 和 shadow advisory 路径；对象、目标、时间窗和结果语义不统一。信号没有强制绑定策略版本、配置 hash、输入 snapshot hash、prompt/model 版本和价格数据版本。

### 2.8 测试护栏不足

未发现专门覆盖 `SignalTracker` 生命周期的测试文件。重复 settle、并发/中断、交易日历、目标时点价格、provider 缺价、entry coverage、不同信号语义、迁移和去重统计都缺少独立回归证据。

## 3. 文档对照

`VISION.md`、`ROADMAP.md`、`ARCHITECTURE.md` 已正确提出“确定性证据 → LLM 综合判断 → 可执行性校验 → 用户决策”，也承认规则层过重、统一采集尚未完成、shadow 与 production 并存。

但信号评价层没有同等严格的 contract lifecycle。`SignalTracker` 的实验性设计仍被报告和状态文档视为可用反馈闭环；`DecisionSnapshot`/Forecast 与 `SignalTracker` 的评价边界也没有清晰统一。

处理原则：历史审查保留在 archive；当前权威文档只描述代码已证实状态；新建单一重构方案，明确它是计划而非完成事实；评价结果在验收前不得反馈策略自动调权。

## 4. 成熟系统对照

QuantConnect/LEAN 的公开设计强调 live 与 out-of-sample 对账、目标时间边界、交易日历、费用/滑点/现实成交模型和 walk-forward 的样本外隔离。MLflow 的数据追踪强调输入 lineage、dataset/schema/digest、预测—标签关联和生产质量监控。

这不意味着引入这些平台，而是说明本系统至少需要：唯一标识、目标时间、实际观测、版本 lineage、幂等状态、可复现评价和数据质量指标。

## 5. 对原分析的对抗复核

代码和账本充分支持：重复结算污染、目标时间与 cron 当前时间混淆、entry price 断裂、不同信号语义统一评价、统计脚本未去重、研究/生产/评价版本未统一。

需要收窄的表述：不能仅凭当前账本证明策略本身亏损；不能把所有 `stale_window` 都归咎于 cron；不需要立即重写整个生产分析链；JSONL 可以保留为原始事件日志，但不能继续充当唯一查询账本和统计真相源。

## 6. 最终判断

重构可行，但必须优先重构信号评价与反馈闭环，而不是无边界重写交易助手：先冻结当前胜率的策略反馈用途，建立评价契约和账本不变式，再迁移/重建评价数据，最后才做策略效果分析。只有经过独立信号、跨市场状态、零重复、稳定覆盖和版本 lineage 验收，结果才可用于策略反馈。
