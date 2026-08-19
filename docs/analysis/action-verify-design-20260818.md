# 交易动作调研分析管道 — 执行设计文档

> 状态：设计待确认
> 日期：2026-08-18
> 作者：Mita（对齐 Kari 2026-08-18 指令）

## 1. 背景与目标

### 1.1 起因

2026-08-18，系统对 APP（AppLovin）推送"减仓 100%"动作，理由是"趋势走弱（MA20偏离 14.6%）"。Kari 质疑："APP 现在不是很低位置吗？"

手动调查确认：APP 从 60 日高点 613.70 回撤 -49.2%，RSI 25.1 超卖，布林带下半区，4/4 左侧建仓特征命中。系统的"减仓"信号来自右侧趋势逻辑，与 Kari 的左侧分批建仓策略错配。该动作最终判定"不执行，需人工复核"。

### 1.2 本次调查的 7 步流程（已验证可行）

1. 确认质疑对象与动作定义（artifact 的 approved_actions / instruction_card）
2. 核实真实持仓（截图 = 真相源，读图用 NAS GLM-4.6V-Flash）
3. 拉取实时行情 + 技术指标（price / MA5/20/60 / RSI / MACD / Bollinger）
4. 历史走势与回撤（60 日高点回撤、近 20 日 / 近 5 日涨跌）
5. 新闻 / 消息面核查（系统新闻源 + 外部搜索）
6. 追溯系统决策逻辑（decision_reason / evidence_summary）
7. 策略适配判定（左侧/右侧错配）并输出结论

### 1.3 目标

把上述 7 步**通用化**：对系统推送的**每一个动作、每一个持仓标的**都能自动执行，
并固化为可被 cron 定时驱动的方案，分析结果推送到飞书。

**关键约束：不是改核心系统，是旁路增量。**

## 2. 现状与耦合现实（为什么不能直接改）

### 2.1 系统规模

| 模块 | 行数 | 角色 |
|------|------|------|
| scheduled_analysis.py | 3634 | 调度分析主链 |
| context_builder.py | 3068 | 上下文构建（持仓/行情/新闻/宏观聚合） |
| presentation.py | 1620 | 展示渲染 |
| portfolio_adjudicator.py | 1236 | 组合裁决（approved/suppressed） |
| quant_action.py | 1120 | 量化动作信号 |
| 全部 engine 模块 | ~30101 | — |

测试：85 个测试文件，基线 1118 passed / 5 failed。

### 2.2 耦合点（改造核心的高风险区）

1. **context_builder 是中心枢纽**：持仓（financial_assets.json）、行情（finnhub/polygon）、新闻、宏观、历史全部汇入 `AnalysisContext`，下游所有模块消费同一对象。
2. **scheduled_analysis 是编排器**：build_context → build_action_cards → adjudicate → presentation 一条链，动作的"信号 → 裁决 → 展示"强绑定。
3. **presentation 与 user_view 耦合**：LLM 渲染依赖 `portfolio_decision.user_view`，格式由 prompt 契约约束（validator 兜底）。
4. **信号判定散落多处**：quant_action（量化）、intelligence_analyzer（情报）、signal_adjudicator（裁决）、risk_state（风控档位）共同决定最终动作。

**结论：在核心链路上新增"调研分析"环节 = 改动 context_builder / scheduled_analysis / adjudicator 三处 + 全量回归，风险高、周期长。**

### 2.3 可行路径：旁路只读分析层

核心系统的**产物**（artifact JSON）是稳定的契约：`.local/scheduled_runs/latest/<session>.json`。
调研分析只需要**消费产物 + 现有数据文件**，不写回核心链路：

- 输入：`latest/<session>.json`（approved_actions、instruction_card、risk_state、outlook）
- 数据源：`.local/history/us_<SYM>.json`（历史收盘）、`.local/financial_assets.json`（持仓）、`.local/risk_state.json`（风控）
- 输出：独立分析报告 JSON + 飞书推送（仅在有需人工关注项时推送，fail-closed）

这样**零侵入核心**，改造范围 = 新增 1 个分析脚本 + 1 个推送脚本 + 1 个 cron。

## 3. 设计

### 3.1 总体架构（旁路三层）

```
[核心系统]  ──生成──>  artifact JSON  ──┐
                                        │ 只读
[分析层]  verify_trade_action.py  <────┘
   │  读 latest/*.json + history/ + financial_assets.json
   │  对每个 approved action 跑 7 步调研（步骤 2-4 确定性计算，步骤 5 可选，步骤 6-7 规则判定）
   ▼
  分析结果 JSON（verdict: ok / needs_manual_review / below_min_trade_unit / no_quote_data）
                                        │
[推送层]  push_action_verify.py  <──────┘
   │  过滤 needs_manual_review → 格式化 markdown
   │  无关注项 → 静默（fail-closed）
   ▼
  飞书群（Traders）
```

### 3.2 分析层：verify_trade_action.py（已实现 v1）

```
输入：--session <id> | --all
遍历：portfolio_decision.approved_actions（合并 instruction_card.actions）
对每个 action：
  1. 解析 position_id → symbol（us_xxx → XXX，a_xxx → xxx）
  2. holdings_for()：从 financial_assets.json 读持仓（数量/成本）
  3. compute_indicators()：从 history/us_<SYM>.json 计算
     price / MA5 / MA20 / MA60 / Bollinger(20,2) / RSI(14) Wilder / 60日回撤 / 近20日 / 近5日
  4. left_side_score()：4 项左侧建仓特征计分
     - neg_deviation_gt_10pct（现价 < MA20 超 10%）
     - rsi_below_35（RSI < 35）
     - drawdown_gt_40pct（60 日回撤 > 40%）
     - lower_bollinger_half（布林带下半区）
  5. 判定：
     - reduce/take_profit + LS≥2 → needs_manual_review（错配，需人工）
     - executable_quantity==0 → below_min_trade_unit（不构成动作）
     - 无行情数据 → no_quote_data
     - 否则 ok
```

**注意（v1 局限，v2 消除）**：
- v1 的 left_side_score 是从 APP 单案例提炼的"错配检测器"，**不是完整调研**。它是通用管道的**第一步**，不是终点。
- v1 不含新闻核查（步骤 5）、不含决策逻辑深挖（步骤 6 只读 reason 原文）。

### 3.3 推送层：push_action_verify.py（已实现 v1）

- 复用 resend_report.py 的飞书发送机制（post + md 富文本）
- 只推送 `needs_manual_review` 的动作，格式：
  ```
  ⚠️ 交易动作需人工复核（左侧策略错配检测）
  **APP**：reduce 1.0（趋势走弱（MA20偏离 14.6%），减仓 100%）
  - 现价 311.53 / MA20 363.78 / RSI 25.1 / 60日回撤 -49.2%
  - 左侧建仓特征 4/4 → ...
  _系统技术信号按右侧趋势触发，与左侧分批建仓策略可能冲突。建议人工确认后再执行。_
  ```
- 无关注项 → 静默退出（不打扰）

### 3.4 cron 触发（v1）

```
Name: stocks-claw-action-verify
Schedule: 每个主交易窗口推送后 30 分钟（跟随各 session 推送 cron）
Script: scripts/stocks-claw-action-verify.sh（wrapper → push_action_verify.py --all）
Workdir: /mnt/user/code-project/stocks-claw
Deliver: local（脚本自己推飞书）
```

## 4. 分阶段实施

### Phase 1（已完成 2026-08-18）：案例验证 + v1 脚本
- [x] 手动 7 步调查（APP 案例）
- [x] 调查流程沉淀为 skill reference：`trade-action-adversarial-verification-20260818.md`
- [x] verify_trade_action.py v1（确定性计算，无 LLM）
- [x] push_action_verify.py v1（fail-closed 推送）
- [x] dry-run 验证通过（APP 判定 needs_manual_review，4/4 特征）

### Phase 2（待确认）：通用化分析层
- [ ] 扩展调研维度：加入 news 核查（系统新闻源 + searxng 外部搜索）
- [ ] 决策逻辑深挖：解析 decision_reason → 输出"系统为什么给这个信号"的可读说明
- [ ] 风险上下文：合并 risk_state（hedge 档位）与 outlook（短期/中期方向）到报告
- [ ] 阈值可配置：左侧特征阈值从常量改为 config（如 `config/verify_config.json`）
- [ ] 单元测试：verify_trade_action 的指标计算与判定函数（对标项目 pytest 规范）

### Phase 3（待确认）：完整调研报告 + cron 上线
- [ ] 输出完整调研报告格式（7 步全包含，markdown）
- [ ] 全部主 session 的 cron 触发（us_post_open / us_after_close / cn_post_open / cn_after_close）
- [ ] 一周试运行：观察误报率（needs_manual_review 中实际该执行的占比）
- [ ] 误报调优：根据试运行调整阈值/特征权重
- [ ] 写入 ARCHITECTURE.md / AGENT_GUIDE.md

## 5. 验收标准

1. 系统推送的每个 approved action 都自动跑 7 步调研（Phase 3 后）
2. 调研结果对**所有标的**通用，不绑定单一股票/信号模式
3. 核心系统零改动（git diff 核心模块为空）
4. cron 自动触发，错配动作推送到飞书，无错配时静默
5. 误报率可接受（试运行统计，目标：needs_manual_review 中 ≥70% 人工确认后确实不执行）

## 6. 风险与边界

| 风险 | 影响 | 缓解 |
|------|------|------|
| 指标计算与核心系统不一致 | 误判 | Phase 3 用同一 history 数据源，加对照测试 |
| 阈值过敏感（左侧特征误判） | 推送噪音 | Phase 3 试运行调优，threshold 配置化 |
| 新闻核查不稳定（搜索源抖动） | 报告缺消息面 | 新闻为可选维度，缺失标注"未核查"不阻塞 |
| 飞书推送频率 | 打扰 | fail-closed，仅错配推送；可加静默期 |
| 与核心系统的语义漂移 | artifact 字段变化 | 脚本对缺失字段 fail-safe（no_quote_data 等） |

## 7. 与现有文档的关系

- 本设计的 skill reference：`trade-action-adversarial-verification-20260818.md`（已入 stocks-claw-operations）
- 后续合入：`ARCHITECTURE.md`（旁路分析层）、`AGENT_GUIDE.md`（新增工具说明）
- 数据契约：复用 `.local/scheduled_runs/latest/*.json` 既有 schema，不新增核心数据模型
