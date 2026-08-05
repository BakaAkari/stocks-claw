# Release Process

本文档定义 stocks-claw 的标准化发版流程，确保每次版本发布都有可重复的
检查清单和一致的质量门槛。

## 版本号规则（Semantic Versioning）

版本格式：`MAJOR.MINOR.PATCH`

- **MAJOR** — 架构级迁移或不兼容 API 变更（例如 CLI 参数废弃、
  配置文件格式 breaking change）。
- **MINOR** — 里程碑（Milestone）功能落地（例如 M2 投研主线上线、
  M4 约束模型升级）。
- **PATCH** — 缺陷修复、文档更新、基础设施改进（例如发版流程本身、
  测试加固、代码清理）。

版本号的**唯一权威来源**是 `stocks/__init__.py` 中的 `__version__`。
`pyproject.toml` 中的 `version` 字段必须与其保持同步。

## 发版前检查清单

每次打 tag 之前必须逐项完成并通过：

1. **代码质量** — `ruff check stocks tests` 零报错。
2. **单元测试** — `pytest -q` 全部通过（允许已标记的 skip）。
3. **字节码编译** — `python -m compileall -q stocks tests` 零失败。
4. **版本号一致性** — `stocks/__init__.py` 与 `pyproject.toml` 版本号相同。
5. **Smoke 测试** — 运行以下命令，确认不抛异常：
   ```bash
   python -m stocks.adapters.cli --output json --no-news --no-quotes
   ```
6. **STATUS.md 更新** — 在文件顶部记录本次发版的版本号、日期、
   主要变更摘要。

> 步骤 1–5 可由 `scripts/release_check.py` 自动执行；
> 步骤 6 需人工确认并提交。

## 自动化脚本

```bash
python scripts/release_check.py
```

该脚本会：
- 读取并比对 `stocks/__init__.py` 与 `pyproject.toml` 的版本号；
- 运行 `ruff check stocks tests`；
- 运行 `pytest -q`；
- 运行 `python -m compileall -q stocks tests`；
- 运行 smoke：`python -m stocks.adapters.cli --output json --no-news --no-quotes`；
- 输出每项的通过/失败状态，以非 0 退出码表示存在阻塞问题。

## Git Tag 规则

Tag 命名格式：`v{major}.{minor}.{patch}`

示例：`v2.10.1`

发版步骤：
1. 确保所有检查项通过。
2. 提交 STATUS.md 更新（如有）。
3. 打 tag 并推送：
   ```bash
   git tag -a v$(python -c "import stocks; print(stocks.__version__)") -m "Release v$(python -c "import stocks; print(stocks.__version__)")"
   git push origin v$(python -c "import stocks; print(stocks.__version__)")
   ```

## 变更记录

- **v2.10.1 (2026-08-05)** — 发版流程基础设施落地（RELEASE.md +
  scripts/release_check.py），统一版本号来源，修复 `stocks/__init__.py`
  重复定义与版本号冲突。
