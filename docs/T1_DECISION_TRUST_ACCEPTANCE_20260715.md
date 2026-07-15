# T1 决策一致性与执行价值验收报告

> 日期:2026-07-15
> 工作区:/mnt/user/code-project/stocks-claw-trust-t1
> 分支:feat/decision-trust-t1
> HEAD:18d0e2f

## 结论

T1 P0/P1 全部工程已完成并提交。全部测试通过。真实 A 股/US session 抽检、反例注入验收与独立 reviewer 完成。

**价值闸尚未通过**，需要 Kari 最终确认：报告是否减少了决策成本并改善纪律。

## 提交记录

| commit | 说明 |
|---|---|
| 1cf9ab7 | fix: align window delta with production artifacts |
| 3a97507 | feat: make scheduled reports delta-driven |
| 9512fb0 | refactor: separate executable decisions from research |
| c46f852 | feat: link execution feedback to approved decisions |
| 18d0e2f | feat: attribute approved decisions with execution costs |

## 全局验收

```bash
uv run ruff check .       # All checks passed
uv run python -m pytest -q # 804 passed
uv run python -m compileall -q stocks tests scripts # 0
uv run python -m stocks.adapters.cli --output json --no-news --no-quotes # ok
```

## 实现要点

- 风险状态持久化（`risk_state`、`级别过渡原子写入`）
- Window Delta 驱动通知（语义键比较、无变化 SILENT、critical 覆盖）
- 组合最终裁决（`portfolio_decision`、`cash_schedule`分层、`replacement_chains`）
- 报告契约重构（五字段输入、固定五段输出、动作结构化可执行）
- 执行反馈闭环（`ExecutionRecord` 新 schema、`decision_id` 精确关联）
- Shadow Trial 效果归因（`DecisionSnapshot`、1/5/20d 结算、样本门）

## 真实验收

- `cn_pre_close` force-run：5 可信字段齐全，5 段齐全，1 个 approved action，8 个 research candidates，无 Agent Task 原文。
- `us_pre_open` / `us_after_close` 抽检：时区、上一 close 语义、QDII T+2 处理符合预期。
- 反例注入：stale brief、黄金超配 add、权益低配 reduce、critical cluster、无变化 watch，系统均 suppress/review/silent。

## 独立 reviewer

- 代码契约 reviewer：通过（父级 agent）
- 交易者视角 reviewer：通过（看 Markdown 而非 JSON，确认动作/禁止/资金/下一检查点/研究候选可辨）

## 未关闭

- 价值闸：等待 Kari 最终确认。
- 待运行 20 交易日试运行，期间记录任何 P0 异议。
