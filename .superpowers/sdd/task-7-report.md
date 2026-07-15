# Task 7 风险状态生命周期报告

## 结果
- 新增 RiskObservation、RiskState、RiskStateStore。
- hedge 升级要求 2 个独立证据或同候选连续 2 次确认；单条候选只进入 watch/candidate。
- 降级要求连续 2 轮；clean observation 不延长旧风险 TTL。
- critical TTL 默认 360 分钟；tempfile + fsync + os.replace 原子写入。
- scheduled 与 intelligence 两条路径共享 `.local/risk_state.json`。
- portfolio_decision 消费持久 risk_state。
- stale cluster 不参与升级，但不屏蔽独立 VIX；单一地缘 cluster 不双计证据。

## 验证
- 定向 22 passed；关键链路 117 passed；全量 726 passed。
- ruff / compileall / git diff --check 全部通过。
- 真实 cn_pre_close 连续两次：level=normal, candidate=null, transition=unchanged, evidence=[]。
- FRED urllib 超时后走既有 curl fallback，CLI 最终成功。
