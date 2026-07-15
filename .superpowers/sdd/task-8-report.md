# Task 8 Priority、通知与 Window Delta 报告

## 实现
- 新增 `stocks/engine/window_delta.py` 纯函数模块。
- 比较组合裁决动作语义、风险状态、异常码、fired triggers；忽略 run_id/generated_at 和仅 decision_id 变化。
- 同 session 强制重跑优先比较上一版；首次 session 才回退同市场最近窗口。
- open_watch/pre_close 配置为无实质变化 `archive_only`。
- Priority：仅 hedge escalation、获批硬止损/大比例紧急减仓、明确 critical trigger 为 critical；持久 hedge/review_required 为 high；普通 fired trigger 不升级。
- 风险状态默认路径随 artifact_dir 隔离，生产仍落 `.local/risk_state.json`。

## 验证
- Window Delta 定向 37 tests（含 trigger 恢复 push、decision_id 噪声、同 session 重跑）。
- 相关链路 131 passed。
- 全量 767 passed。
- ruff / compileall / diffcheck 通过。
- 真实 cn_pre_close 连续强制重跑：material=false, changes=[], notification=archive_only。
- FRED urllib 超时后走既有 curl fallback，CLI 最终成功。
