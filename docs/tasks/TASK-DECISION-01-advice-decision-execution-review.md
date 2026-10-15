# TASK-DECISION-01：真实建议、用户决定、实际执行与复盘

用户于 2026-10-15 指定本任务，替代继续扩展 E4 指标的顺序。先修复产品事实链，
动态状态在实现稳定后统一更新至 STATUS.md。本文件先确定本轮方案和验收。

## 用户价值与范围

用户能够回答：系统当时具体建议什么，我如何决定，我实际做了什么，随后发生什么，
哪些信息仍不足以判断建议是否有帮助。系统批准建议不代表用户接受，用户接受不代表
成交，成交后的标的涨跌不代表组合收益。股票/加密复盘都必须遵守这几个区别。

直接修改当前项目，保留上一轮冻结观测基础。不恢复服务，不修改真实金融记忆，
不提交推送。不扩展 E4 胜率/净收益/回撤，不顺便迁移 LLM 动作权威或重建基本面研究。

## 最小责任链

```text
生产 PortfolioDecision + 实际报告结构 + 当时标的/持仓证据
→ 不可变 RecommendationSnapshot（系统生成事实，未声称用户执行）
→ 用户确认保存 AdviceRecord（稳定 ID，保留来源快照）
→ 用户确认的 UserDecisionRecord（追加，接受/部分采纳/拒绝/延期）
→ 用户确认的 ExecutionRecord（逐笔成交，价格/数量/币种/时间/费用）
→ 只读 DecisionReview（完整关联、现金流、冻结观测、缺口）
```

1. 新建议快照逐动作保存一次，不为 1/5/20 日复制三条“已执行”记录。保留原始
   action、run/decision ID、标的、持仓证据、生成时刻及来源；缺失标的或价格不猜。
   source snapshot 与用户看到的 report 结构一起保留，明确系统建议/用户保存的边界。
2. Advice 保存经确认发生，保留稳定 ID、原文/来源、action 的 decision_id。
   每次用户决定独立追加，不再用一个可覆盖 feedback 字段代表完整决定历史。
3. 成交保持独立：支持同一建议多笔成交、部分采纳后拒绝剩余部分、成交后补记录。
   没有用户决定记录也允许确认实际成交，但明确关联缺口，不补造历史接受事件。
4. 新写入严格验证引用关系、标的与时间、有限正数和费用币种；旧记录只读兼容。
   相同事件 ID/内容重复为 no-op，内容冲突拒绝，保留全部历史，不自动裁剪金融记忆。
5. 复盘按精确 ID 关联，不按标的模糊匹配或把哈希 decision_id 当成 run 前缀。
   未录入成交显示 unknown/unrecorded，不能声称用户没交易；最新决定不抹掉早期成交。
6. 现金流仅在真实 quantity/price/currency 与费用明确时计算；卖出流入不是利润。
   缺数量、费用、成本批次、资金基线时明确 unavailable。关联冻结观测仅显示后续
   价格事实，不重读当前价格、不恢复旧反向卖出收益、默认佣金或旧胜率。
7. 保留并修复原 decision_attribution 读入口，让它使用同一复盘函数及当前项目，
   消除签名错误/二次构造引擎/错误根目录。旧归因快照标记 legacy_unverified。

## 验收标准

- 通过真实生产建议生成函数和真实 confirmed API 走完整链，测试不得手工补齐
  实际保存模型不接受的字段。默认 smoke 数据在临时目录，明确为模拟用户动作。
- 系统批准但未记录用户决定、接受但无成交、拒绝、延期、两笔部分成交、成交后
  拒绝剩余部分都得到不同且准确的复盘状态。
- 两次决定都保留；两笔成交都回读；现金流逐笔与独立手算一致；未知手续费不当零。
- 重复提交不增加记录，同 ID 修改被拒绝；跨 advice/decision/target 错关联拒绝。
- 未确认写入零落盘；未知执行不升级为已执行；历史数量超过旧上限仍保留。
- 从明确任务键读取上一轮冻结价格结果，重复复盘不改原建议、金融记忆或冻结结果。
- 标注本轮只能证明事实链和计算正确，不能证明策略增益。比较原计划、简单规则与
  建议方案的价值检验留在产品验收中，不用采纳率替代投资效果。
- 聚焦 tests、变更文件 Ruff、最终全量 pytest、compileall、diff-check 和真实行情
  smoke；现存无关 lint 问题单独如实报告。

## 真实烟测

使用正式函数生成一条建议，保存其不可变来源；通过 CLI/MCP confirmed 入口在全新
数据目录保存建议、用户决定与成交示例，读取正式 review。行情使用真实历史 provider
和已冻结 observation 管线；来源标注 historical_replay / simulated_user_actions。
烟测不表示 Kari 做过这些决定或成交。具体可重跑命令和证据在最终验收记录中保存。

## 验收记录（2026-10-15）

实施后的范围及运行状态见 [STATUS](../../STATUS.md)，字段与实际使用见
[事实复盘契约](../contracts/decision-review-v2.md)。以下是本次验收证据快照。

- 标准全量 `.venv/bin/pytest -q -o 'addopts='`：1646 passed、7 skipped。
- 全部变更 Python 文件 Ruff、compileall、diff-check 通过。全仓 Ruff 仍有 48 条
  与上一轮完全相同的既有诊断，没有宣称全仓 lint 通过。
- `test_decision_lifecycle.py` 覆盖真实 CLI/MCP confirmed 门、引用与时间/币种校验、
  多次决定/成交、拒绝余量、未知费用、源目录一致性、任务幂等及旧入口复盘。
  `test_decision_records.py` / `test_outcome_attribution.py` 覆盖完整持久化与复盘边界。
- 真实 smoke：

```bash
.venv/bin/python scripts/smoke_decision_review.py \
  --output-dir /private/tmp/stocks-decision-review-acceptance-20261015
```

默认 518880 真实历史行情在 10 月 9 日产生正式规则 reduce / 30% 建议，模拟持仓
1000 份；模拟 deferred → accepted，10 月 12/13 日分别按真实收盘价
8.585 / 8.599 卖出 150 份，模拟费用每笔 1 CNY。独立算术净现金流 2575.6 CNY，
不是利润。10 月 9 日 entry 8.617 → 10 月 14 日 exit 8.501，原始价格变化
-1.346176%。选择建议时只使用当时历史，不按未来盈利挑样例。

验收产物位于上述目录：`smoke-result.json`、`review.json`、
`review-before-fills.json`、`evaluation-report.json`、SQLite 及原始行情证据。
冻结 result ID：`c69fb87fbf25d383a11d4f11301aaba0d04b7a5a7ac3002a35a495519fa0b0b7`。
1 条建议、2 次决定、2 笔成交、1 个冻结结果；未确认零写入、重复无新增、行情证据和
输入哈希通过校验。只读重建与 review 完全一致，目录 22 个文件哈希不变。

本轮同时修复了建议写读目录不一致、逆建议成交被误算完整执行、旧手工建议的时间戳
引用兼容问题。缺成本批次/完整持仓路径时 realized_pnl 继续 unavailable；缺可比资金
基线时 portfolio_gain 继续 unavailable。最终推送原文/送达回执、完整 LLM/config
lineage、资金与持仓自动核对不是这次完成的能力，不能从本次工程验收推导投资效果。
