> ARCHIVED 2026-07:结论已吸收或废弃,勿作为开发依据。

# ROADMAP.md - Legacy historical roadmap

> 状态：Legacy / 历史参考
> 当前主线文档：`../PLAN.md`

本文件保留旧阶段的收口原则，但不再作为当前开发计划执行依据。

当前真实状态已经在根目录 `PLAN.md` 中维护，包括：

- 当前项目定位
- Phase 1 已完成事项
- 验证结果
- 下一步 Phase 2 最小闭环
- 禁止事项与提交验收清单

请以后优先读取：

```text
PLAN.md
README.md
README.zh.md
AGENT_GUIDE.md
```

不要再依据本文件中的旧 OpenClaw/Feishu/旧 CLI/旧 service 任务推进开发。

---

## 仍然保留的原则

以下原则仍然有效：

- 不为未来假设提前交房租。
- 不继续把中间层扩成半个投顾平台。
- 不新增与主线无关的独立项目壳子。
- 不把“体系感”当成产品进展。
- 优先巩固可用主链，再扩展分析深度。

---

## 历史背景摘要

旧路线的目标是将项目从一个较膨胀的个人投资顾问系统收敛为更明确的 Agent 能力扩展。

该方向已被吸收到当前主线：

```text
stocks-claw = Agent-first personal finance context toolkit
```

当前不再追求：

- 独立投顾机器人
- 复杂 OpenClaw cron 投递链
- 更多中间状态层
- 旧 `stocks.cli.stocks` 子命令入口
- 旧 `stocks/tests/test_v2.py` 测试入口

当前开发从 `PLAN.md` 的 Phase 2 最小闭环继续。
> ARCHIVED 2026-07:结论已吸收或废弃,勿作为开发依据。
