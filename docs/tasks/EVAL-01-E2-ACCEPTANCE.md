# EVAL-01 E2 验收

范围：唯一评价任务与幂等结果落盘；不改结算入口，不恢复生产，不导入旧账本。

现状：evaluation_ledger.py 以 (signal_id, policy_id, target_at) 为主键的 SQLite 表实现任务唯一性、租约领取、结果单次提交与释放重试；evaluation_runner.py 规范化目标时间到 UTC、到期判断、领取、求值、成功落结果、不可用释放、异常释放并上抛。

验收证据：tests/engine 三个测试文件共8项通过，覆盖重复schedule只留一条、8线程20次领取仅1次成功、旧租约不可提交、实际子进程崩溃(exit 23/24)后重启可接管、被杀事务回滚为scheduled、等价时区偏移共享同一任务、naive目标时间拒绝、调用方异常不落结果。生产仓库全量 1486 passed / 7 skipped / 0 failed。

未做：未接入 scripts/signal_settlement.py（其仍以 cron 当前价结算，需 E3 目标时点观测才有意义）；未迁移或回填历史账本；未启用旧胜率反馈。隔离副本全量另有1项环境性失败：测试依赖真实仓库 .secret 符号链接，隔离副本未复制 .secret，生产仓库该测试4项通过。

系统保持停机；9个cron仍暂停；E0封存文件与用户原有改动哈希未变。
