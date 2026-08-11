# 情报信号裁决器（Intelligence Signal Adjudicator）设计

> 状态: PLANNED (2026-08-12)
> 前置: v2.11-intel-llm-pipeline-fix (信号已能产生, MODE=llm 21 signals)
> 相关: `intelligence_analyzer.py` / `news_intelligence_store.py` /
> `quant_action.py::_build_drivers` / `risk_warning.py` / `context_builder.py`

## 1. 背景与问题

8-11 修复了 LLM 分析路径三处断链后，信号层从 0 信号恢复为真实产出
（8 clusters / 21 signals / GLD、NEM、USO、XLE、ITA buy 信号匹配到持仓）。
但信号**产生后直接进消费端**，缺少一道确定性防线：

1. **无溯源校验** — LLM 信号没有强制挂 `source_article_ids`，无法回链到
   文章。LLM 幻觉（编 symbol/方向）会直接进入 action card。
2. **无置信度门槛** — `confidence: 0.65` 的弱信号与 `0.9` 的强信号同等
   消费。`coerce_intelligence_signals` 不区分强弱。
3. **无时效衰减** — 信号一旦写入 `.local/news_intelligence/signals/`，
   直到次日覆盖前都有效。critical 事件 6 小时前与 23 小时前的信号
   权重相同；`_compute_brief_health` 只检查 snapshot 年龄，不检查
   单条信号年龄。
4. **无冲突聚合** — 同 symbol 多信号方向相反时直接由
   `_intel_consensus_direction_from_matched` 取多数，不记录 dissent
   证据，不交叉验证。8-11 DQ 已出现"美伊和谈方向相反矛盾"——这个
   信息现在只作为文本 note，不进结构化判定。
5. **padding 噪音** — `match_intelligence` 的 category_padding 会给所有
   无匹配 exposure_tag 生成 `neutral` 信号，稀释方向信号的可信度。

## 2. 设计原则

- **LLM 产出候选，规则层输出信号** — LLM 负责理解新闻、提取方向；
  裁决器负责"这条候选能不能进数据链路"（校验/过滤/聚合/给时效）。
- **纯函数、可单测** — 裁决器不碰网络/存储，输入 `list[IntelligenceSignal]`
  + 上下文，输出裁决结果。存储读写仍在现有 store 层。
- **默认静默，动作时暴露** — 弱信号只进 research 展示，不进 action
  driver；被丢弃的信号写 `data_quality_notes`（诚实报告，不静默吞掉）。
- **与现有消费路径兼容** — 不改变 `match_intelligence` 的四层匹配逻辑
  和 `_build_drivers` 的 driver 结构；裁决器插在信号存储与 digest
  构建之间。

## 3. 裁决器位置（数据流）

```
LLM analyze() → AnalysisResult.signals (候选)
                     ↓
        [NEW] SignalAdjudicator.adjudicate(candidates)
                     ↓ 四道确定性规则
   {passed: [信号], weak: [信号], rejected: [信号+原因]}
                     ↓
store.save_signals(adjudicated.passed)   ← 现有调用点改为存裁决后
                     ↓
context_builder._build_intelligence_digest
  读取 signals → match_intelligence → coverage
                     ↓
消费端: quant_action._build_drivers (intelligence driver)
      + risk_warning (cluster urgency 不变, 信号不参与风险)
      + outlook_evidence (信号作为 evidence)
```

关键决策点：**风险触发仍只用 cluster（urgency/负面计数），裁决器不改变
风险路径**。裁决器只管"信号"这条线（action card direction/dissent 的
输入质量）。

## 4. 四道确定性规则

### R1 溯源校验（Provenance Check）

- **规则**: `source_article_ids` 非空且每个 id 能解析为文章索引
  （0 <= id < articles_input）。LLM 返回的 `signals[].source_article_ids`
  必须存在。
- **动作**: 无溯源 → rejected，原因 `missing_provenance`。
- **理由**: 无源信号无法审计，是幻觉第一入口。8-11 验证时 LLM 已能
  正确回链（GLD buy 引用美伊冲突文章），契约可行。
- **兼容**: 当前 `IntelligenceSignal` 无此字段 → 需在
  `LLMIntelligenceAnalyzer._parse_signals` 增加解析 + `IntelligenceSignal`
  加 `source_article_ids: list[int] = []`。

### R2 置信度门槛（Confidence Gate）

- **规则**: 分三层：
  - `confidence >= 0.70` → passed（可进 action driver）
  - `0.55 <= confidence < 0.70` → weak（只进 research 展示，不进 driver）
  - `confidence < 0.55` → rejected（丢弃，记 note）
- **理由**: 8-11 信号置信度 0.65-0.75，若门槛 0.75 会全灭；0.70 保留
  强信号（GLD 0.75/NEM 0.66→weak/USO 0.67→weak），0.55 以下纯噪音。
  阈值可配置（config_loader `llm.intel_signal_confidence`）。
- **注意**: 弱信号不丢弃而是降级展示，符合"默认静默，动作时暴露"。

### R3 时效衰减（Temporal Decay）

- **规则**: 每条信号按其 `urgency` 计算有效期：
  - `critical` → 6 小时
  - `high` → 12 小时
  - `medium` → 24 小时
  - `low` → 72 小时
  `generated_at + valid_ttl < now` → rejected，原因 `expired`。
  衰减曲线：过半衰期后 confidence 乘以 (1 - elapsed/ttl) 打折，
  打到 0.55 以下自动降为 weak。
- **实现**: `IntelligenceSignal` 加 `valid_until: Optional[datetime]`；
  裁决器输入 `now`（注入，可测）。
- **兼容**: `_compute_brief_health` 只查 snapshot 年龄，与信号有效期
  互补（snapshot 控制"整批情报是否可用"，R3 控制"单条信号是否还有效"）。

### R4 冲突聚合（Conflict Aggregation）

- **规则**: 同 symbol 的 passed 信号方向相反（buy vs sell）→ 按
  confidence 加权取主流方向，并在结果里记录 `dissent`：
  ```json
  {"symbol": "USO", "direction": "buy", "dissent": {
     "evidence": [{"direction": "sell", "confidence": 0.68, "rationale": "..."}],
     "weighted_margin": 0.07}}
  ```
- **动作**: dissent 记入信号对象（`dissent` 字段），消费端
  `_build_drivers` 遇 dissent 时在 reason 里附加"存在反向证据"；
  **不强消歧** — 与 `_detect_dissent`（driver 间冲突）互补，R4 是
  信号内部冲突。
- **理由**: 8-11 已验证 LLM 会产出方向矛盾（美伊和谈 [4]/[19] vs [9]），
  系统需要结构化记录而非靠文本 note 传递。

## 5. 数据模型变更

`IntelligenceSignal`（news_intelligence_store.py）新增字段（均带默认值，
向后兼容）：

```python
source_article_ids: list[int] = []        # R1 溯源
valid_until: Optional[datetime] = None     # R3 有效期
dissent: Optional[dict] = None             # R4 冲突证据
adjudication: str = "pending"             # pending/passed/weak/rejected
reject_reason: str = ""                    # 丢弃原因(用于 data_quality_notes)
```

`AnalysisResult.metadata` 增加裁决摘要：

```json
{"adjudication": {"input": 21, "passed": 12, "weak": 5, "rejected": 4,
  "by_reason": {"missing_provenance": 2, "low_confidence": 1, "expired": 1}}}
```

## 6. 新文件与改动点

| 文件 | 动作 | 说明 |
|---|---|---|
| `stocks/engine/signal_adjudicator.py` | **新建** | 纯函数裁决器，四道规则 + `AdjudicationResult` |
| `stocks/engine/intelligence_analyzer.py` | 改 | `_parse_signals` 解析 `source_article_ids`；`analyze()` 末尾接裁决器 |
| `stocks/engine/news_intelligence_store.py` | 改 | `IntelligenceSignal` 加 5 字段 + to_dict/from_dict |
| `stocks/engine/context_builder.py` | 改 | `_build_intelligence_digest` 消费 `adjudication` 字段；`top_signals` 只含 passed |
| `stocks/engine/quant_action.py` | 改 | `_build_drivers` 遇 `dissent` 附加反向证据提示 |
| `stocks/engine/config_loader.py` | 改 | `llm.intel_signal_confidence`（门槛）、`intel_signal_ttl`（有效期表） |
| `tests/engine/test_signal_adjudicator.py` | **新建** | 四道规则单测 + 集成 |
| `tests/engine/test_intelligence.py` | 改 | 适配新字段 |

## 7. 消费端行为变化（对报告的影响）

1. **action card intelligence driver**：只消费 passed 信号（confidence
   >= 0.70 + 有溯源 + 未过期）；weak 信号不再影响 direction。
2. **coverage 统计**：`_compute_coverage` 只统计 passed（directional
   count 更真实）；weak/rejected 进 `data_quality_notes`。
3. **"Candidate identified by LLM analyst"**（报告 5 板块标记）：只对
   passed 信号显示；weak 信号对应板块显示"弱情报（待确认）"。
4. **风险状态**：不变（仍走 cluster urgency/负面计数，裁决器不干预）。
5. **报告透明度**：`禁止与延后`/`数据质量` 增加一条
   "N 条情报信号因无溯源/低置信/过期未进入判定"（诚实报告丢弃）。

## 8. 验收标准

1. **单元测试**：四道规则各 ≥3 用例（边界：置信度 0.70/0.55 精确值、
   TTL 过期 1 分钟、同 symbol 3 buy 1 sell 加权、无溯源信号）。
2. **真实数据**：8-11 snapshot 重放 — 21 条候选输入，passed + weak +
   rejected 数量与 reason 分布可审计，GLD（0.75）passed，NEM/USO
   （0.66/0.67）weak。
3. **全量测试**：1395 passed 基线不倒退（新增测试后 ≥1398）。
4. **端到端**：重新生成 us_post_open artifact，`top_signals` 只含
   passed；报告"Candidate identified by LLM analyst"板块数 ≤ 修复前
   （弱信号不再冒充确认）。
5. **ruff/compileall/diff-check** clean。

## 9. 范围外（不做）

- 不改 `match_intelligence` 四层匹配逻辑（exact/proxy/exposure/category）。
- 不改风险状态消费路径（cluster 驱动的风险判定保持现状）。
- 不做信号回测/胜率统计（SignalTracker 已有回测记录，裁决器只做
  实时质量门）。
- 不做多源交叉验证（只做信号内部冲突聚合，跨源验证属后续）。

## 10. 后续（独立任务）

- 裁决器接入后，观察 1-2 周真实报告，校准置信度门槛（0.70 是否过严/
  过松）与 TTL 表（critical 6h 是否符合实际事件衰减）。
- 若 weak 信号长期无价值，降级为纯 research 展示或直接弃用。
