# 情报信号裁决器（Intelligence Signal Adjudicator）设计 — v4

> 状态: PLANNED — v4.1 (2026-08-12, 五轮对抗性校验; v4 完整重写 + v4.1 时间语义增补)
> 前置: v2.11-intel-llm-pipeline-fix (信号已能产生, MODE=llm)
> 相关: `intelligence_analyzer.py` / `news_intelligence_store.py` /
> `quant_action.py::_build_drivers` / `risk_warning.py` / `context_builder.py`
> 本版整合四轮校验结论(D1-D6/E1-E3/F1-F3), 重构为单一连贯设计。

## 1. 背景与问题

8-11 修复 LLM 分析路径三处断链后，信号层从 0 信号恢复真实产出。
实测基线(2026-08-12 四次采样): **真实 LLM 方向信号稳定 3-4 条**
(BTCUSDT/GLD/USO ± NVDA), 不是早期看到的 21 条——那 21 条包含
`_pad_category_signals` 生成的 17 条规则 hold 占位。

信号产生后直接进消费端，缺少确定性防线。**真实信号极少 + 无质量门 +
padding 泛滥**三件事叠加，构成当前信号层的核心问题：

| 问题 | 说明 |
|---|---|
| 无溯源校验 | LLM 信号不强制挂 `source_article_ids`, 幻觉无法审计 |
| 无置信度门槛 | 0.65 弱信号与 0.9 强信号同等消费 |
| 无时效衰减 | critical 事件 6h 前与 23h 前权重相同; 信号写入后直到次日覆盖前都有效 |
| 无冲突聚合 | 同 symbol 反向信号取多数不记证据; 美伊矛盾只作为文本 note |
| padding 泛滥 | `_pad_category_signals` 给 9 类持仓全补 hold; 消费端 `match_intelligence` 又补一轮 neutral — **双层冗余** |

## 2. 设计原则

- **LLM 产出候选，规则层输出信号** — 裁决器是"这条候选能不能进数据
  链路"的确定性质量门。
- **纯函数、可单测** — 输入 `list[IntelligenceSignal]` + `now`，输出
  裁决结果；不碰网络/存储。
- **默认静默，动作时暴露** — 弱信号只展示不进 driver；被丢弃信号写
  `data_quality_notes`（诚实报告）。
- **分来源处理** — llm / rule_fallback / category_padding 三类信号语义
  不同（LLM 判断 vs 规则占位），规则不能一刀切。
- **消费时裁决** — 裁决在 digest 构建时（consumption-time）执行，不在
  生成时；TTL 用实时 `now` 判断。

## 2.5 第五轮校验：时间语义前置条件（G1-G3）

第五轮（2026-08-12）发现 **R3 的时间前提不成立**，必须先修时间语义，
裁决器才能上线：

| # | 发现 | 证据 | 影响 |
|---|---|---|---|
| G1 | brief 时区混用 — source_generated_at 带 +08:00 而 brief_generated_at
  是 UTC(无后缀)，同文件两种格式 | source=2026-08-12T07:55:00+08:00
  (真实UTC=08-11T23:55Z), brief=2026-08-11T17:00:46(无后缀) | 解析器
  按 naive datetime 处理时 age 偏差 8h — R3 critical TTL(6h) 被吞没 |
| G2 | risk_eligible(48h) vs R3 TTL(6-72h) 层级未定义 | _compute_brief_health
  max_age_hours=48 | 47h 旧信号: 整批可用但单条过期 — v4 未定义边界行为 |
| G3 | risk_eligible=false 时双路径不一致已存在 | top_signals 清空(2111)
  vs parsed_signals 照常(2062) | 加裁决器不处理此边界则"过期信号仍驱动
  action card"原样保留 |

**v4.1 增补**:
- G1: 信号/brief 时间戳统一 UTC — 比较前 astimezone(utc)。验收解析
  误差 < 1 分钟。
- G2: **两层独立串联** — risk_eligible 批级门(48h) + R3 条级门(6-72h);
  裁决输出标 stale_batch / expired 两种过期原因。
- G3: risk_eligible=false 时确定性面也标 batch_stale, 消费端降级 —
  消除"LLM 空、确定性照常"的不一致; 是否禁用由 quant_action 决策。

## 3. 前置清理：padding 双层冗余（F3）

**裁决器设计必须建立在一个干净的上游上**，否则统计被 padding 污染。

### 现状（两次实测确认）

1. `intelligence_analyzer._pad_category_signals` (979): 给 9 类持仓
   (gold/us_tech/us_energy/us_defense/china_broad/china_sci/qdii/active/
   bonds) 中未被 LLM 信号覆盖的类别生成 `hold` 信号，
   `generation_method="category_padding"`。
2. `intelligence_analyzer.match_intelligence` (1294-1304): 对无匹配
   exposure_tag 再生成 `neutral` category padding。

同一缺口被补两次，语义重叠。**决策：保留消费端 `match_intelligence`
的 padding（它按持仓 exposure_tag 动态补位），去掉 analyzer 层的
`_pad_category_signals`（静态 9 类硬编码补位）。** 理由：
- 消费端 padding 更精确（按实际持仓 tag 补），analyzer 层是静态类别。
- `_compute_coverage` 已正确排除 category_padding 的 directional 统计，
  消费端 padding 不污染方向覆盖。
- 去掉 analyzer 层后，`AnalysisResult.signals` = 纯 LLM 真实信号
  (3-4 条) + rule_fallback，信号量诚实反映分析能力。

**影响**: 8-11 场景信号数 21 → 3-4。`intelligence_coverage.directional`
保持真实；`by_generation_method` 不再出现 padding 假覆盖。
这是行为变化——报告"Candidate identified by LLM analyst"板块会显著
减少，**这是期望行为**（暴露真实覆盖，不是假满）。

### 验收（前置清理）

- 8-11 重放: `AnalysisResult.signals` 不含 `category_padding` 来源信号。
- `match_intelligence` 仍对无匹配 tag 补 neutral（消费端行为不变）。
- 全量测试无回归（现有测试若断言 padding 信号存在需同步更新）。

## 4. 裁决器位置（数据流）

```
LLM analyze() → AnalysisResult.signals (真实候选, 已无 analyzer padding)
                     ↓
store.save_signals(原始信号 + valid_until)   ← 存原始, 不预裁
                     ↓
context_builder._build_intelligence_digest (consumption-time)
   2058 raw_signals → 2059 parsed_signals
   [NEW] SignalAdjudicator.adjudicate(parsed_signals, now)
   → {passed, weak, rejected}
                     ↓
   passed → match_intelligence → coverage      (确定性面, action driver)
   passed+weak → top_signals (标 weak:true)     (LLM 面, agent_task)
   rejected → data_quality_notes                (透明报告)
                     ↓
消费端: quant_action._build_drivers / risk_warning(cluster) / outlook
```

关键决策点：
- **风险触发仍只用 cluster**（urgency/负面计数），裁决器不改变风险路径。
- **裁决器插入点 = digest 构建内(2058-2062 之间)**（E2 修正）——一次
  裁决同时服务确定性面(parsed_signals)和 LLM 面(top_signals)，双路径
  一致无泄漏。
- **生成时存原始信号，不预裁**（D3 修正）——TTL 由消费时 `now` 实时
  判断，存储层不冻结裁决状态。

## 5. 四道确定性规则（v4 定稿）

### R1 溯源校验（Provenance Check）

- **范围**: 仅 `generation_method == "llm"` 的信号（D5 修正）。
  rule_fallback/category_padding 跳过——它们的 provenance 是规则本身。
- **规则**: `source_article_ids` 非空，且每个 id 在
  `0 <= id < articles_input` 范围内。
- **动作**: 不满足 → rejected，原因 `missing_provenance`。
- **可行性**: 已实测（第三轮 E1）——实验 prompt 加溯源要求后 LLM 4/4
  服从，引用正确（BTCUSDT[2,3,4]=比特币跌、GLD[3,4,13]=黄金避险、
  USO[6,12,24]=美伊/油价、NVDA[5]=Cramer）。
- **实现前置**: `_LLM_PROMPT_SYSTEM` 信号 schema 加 `source_article_ids`
  + `_parse_signals` 解析该字段（E1）。若 LLM 不服从（返回空），该条
  信号 rejected 并在 note 记录——不静默。

### R2 置信度门槛（Confidence Gate）

- **规则**: 三档，**不 hard reject**（D2/D6 修正）:
  - `confidence >= 0.70` → passed（进 action driver）
  - `0.55 <= confidence < 0.70` → weak（进 LLM 面展示，不进确定性面）
  - `confidence < 0.55` → weak + note(low_confidence)（降级不丢弃）
- **理由**: LLM 输出不稳定（D2: 21 vs 3 vs 4 条波动），hard reject 会
  在波动时误杀 NVDA 0.65 这类真实持仓信号。弱信号保留信息，只降级。
- **门槛可配置**: `llm.intel_signal_confidence`（0.70/0.55）。
- **padding 不适用**: 前置清理后 analyzer 层无 padding；消费端 padding
  由 `match_intelligence` 处理，不经过 R2。

### R3 时效衰减（Temporal Decay）

- **规则**: 按 `urgency` 计算有效期（consumption-time 判断）:
  - `critical` → 6 小时
  - `high` → 12 小时
  - `medium` → 24 小时
  - `low` → 72 小时
  `generated_at + ttl < now` → rejected，原因 `expired`。
  衰减曲线: 过半衰期后 confidence × (1 - elapsed/ttl)，低于 0.55 自动
  降 weak。
- **实现**: `IntelligenceSignal.valid_until`（R3 字段）; 裁决器注入
  `now`（可测）。存储写 `valid_until`，消费时比较。
- **与 `_compute_brief_health` 互补**: snapshot 年龄控制"整批情报是否
  可用"（risk_eligible），R3 控制"单条信号是否还有效"。两者独立。

### R4 冲突聚合（Conflict Aggregation）

- **范围**: 仅 passed 信号。
- **规则**: 同 symbol 多信号方向相反（buy vs sell）→ confidence 加权
  取主流方向，记录 `dissent`:
  ```json
  {"symbol": "USO", "direction": "buy", "dissent": {
     "evidence": [{"direction": "sell", "confidence": 0.68, "rationale": "..."}],
     "weighted_margin": 0.07}}
  ```
- **动作**: dissent 进信号对象; `_build_drivers` 遇 dissent 在 reason
  附加"存在反向证据"；**不强消歧**（与 `_detect_dissent` 的 driver 间
  冲突互补，R4 是信号内部冲突）。
- **理由**: 8-11 已验证 LLM 会产出方向矛盾，需要结构化记录。

## 6. 数据模型变更

`IntelligenceSignal`（news_intelligence_store.py）新增字段（默认值
向后兼容，D4 修正——from_dict/to_dict 同步）：

```python
source_article_ids: list[int] = []        # R1 溯源
valid_until: Optional[datetime] = None     # R3 有效期
dissent: Optional[dict] = None             # R4 冲突证据
adjudication: str = "pending"             # pending/passed/weak/rejected
reject_reason: str = ""                    # 丢弃原因(用于 data_quality_notes)
```

`AnalysisResult.metadata` 增加裁决摘要（分来源统计，F1 修正）：

```json
{"adjudication": {
   "input": 4, "passed": 2, "weak": 1, "rejected": 1,
   "by_generation": {"llm": 4, "rule_fallback": 0},
   "by_reason": {"missing_provenance": 1}}}
```

**注意**: input 是真实 LLM 信号数（前置清理后无 padding），不再是
21 条假基线（F2 修正）。

## 7. 新文件与改动点

| 文件 | 动作 | 说明 |
|---|---|---|
| `stocks/engine/signal_adjudicator.py` | **新建** | 纯函数裁决器: R1 分来源 / R2 三档 / R3 TTL / R4 dissent + `AdjudicationResult` |
| `stocks/engine/intelligence_analyzer.py` | 改 | `_LLM_PROMPT_SYSTEM` 加 source_article_ids; `_parse_signals` 解析; **去掉 `_pad_category_signals` 调用**(F3); 存原始信号 |
| `stocks/engine/news_intelligence_store.py` | 改 | `IntelligenceSignal` 5 字段 + to_dict/from_dict 同步(D4) |
| `stocks/engine/context_builder.py` | 改 | digest 构建内 2058-2062 插裁决(E2); top_signals 含 passed+weak(标 weak:true); rejected 进 notes |
| `stocks/engine/quant_action.py` | 改 | `_build_drivers` 遇 dissent 附加反向证据提示 |
| `stocks/engine/config_loader.py` | 改 | `llm.intel_signal_confidence`(0.70/0.55)、`intel_signal_ttl`(critical 6h/high 12h/medium 24h/low 72h) |
| `tests/engine/test_signal_adjudicator.py` | **新建** | 四道规则边界用例 + 集成 |
| `tests/engine/test_intelligence.py` | 改 | 适配新字段 + padding 移除断言 |

## 8. 消费端行为变化（对报告的影响）

1. **action card intelligence driver**: 只消费 passed（confidence ≥0.70
   + llm 有溯源 + 未过期）；weak 不驱动 direction。
2. **coverage**: `_compute_coverage` 统计 passed（directional 真实）；
   weak/rejected 进 notes。
3. **"Candidate identified by LLM analyst"**: 只对 passed 显示；weak
   板块显示"弱情报（待确认）"。**板块数会大幅减少**（前置清理去 padding
   + 门槛过滤）——这是暴露真实，不是退化。
4. **风险状态**: 不变（cluster 驱动，裁决器不干预）。
5. **报告透明度**: "N 条情报信号因无溯源/低置信/过期未进入判定"
   （诚实报告丢弃，不静默）。

## 9. 验收标准（v4）

1. **单元测试**: 四道规则各 ≥3 边界用例（0.70/0.55 精确值、TTL 过期
   1 分钟、同 symbol 3 buy 1 sell 加权、无溯源 llm 信号 rejected、
   rule_fallback 跳过溯源）。
2. **真实数据**: 8-11 重放 — 真实 LLM 信号 3-4 条, 无 padding 混入;
   passed/weak/rejected 与 reason 分布可审计; GLD(0.75) passed,
   NVDA(0.65) weak。
3. **全量测试**: 1395 passed 基线不倒退（新增测试后 ≥1398; 若现有
   测试断言 padding 信号存在需同步更新）。
4. **端到端**: 重新生成 us_post_open artifact — `top_signals` 不含
   padding, 只含 passed+weak; report "Candidate identified by LLM
   analyst" 板块数显著减少但方向信号真实。
5. **ruff/compileall/diff-check** clean。

## 10. 范围外（不做）

- 不改 `match_intelligence` 四层匹配逻辑（exact/proxy/exposure/category）。
- 不改消费端 `match_intelligence` 的 category padding（保留——它按实际
  持仓 tag 动态补位，且 `_compute_coverage` 已排除其 directional）。
- 不改风险状态消费路径（cluster 驱动保持现状）。
- 不做信号回测/胜率统计（SignalTracker 已有回测记录）。
- 不做多源交叉验证（跨源验证属后续独立任务）。

## 11. 后续（独立任务）

- 裁决器接入后观察 1-2 周，校准 R2 门槛（0.70/0.55 是否匹配 LLM 实际
  输出分布）与 R3 TTL 表（critical 6h 是否符合事件衰减）。
- 若 weak 信号长期无价值，降级纯 research 或弃用。
- 信号层覆盖真实化后，评估是否需要"LLM 多轮采样 + 合并去重"提升
  方向信号稳定性（D2 的根本缓解——当前裁决器接受单次输出波动）。

## 12. 五轮对抗性校验完整记录（整合）

### 第一轮（v1 → v2）: D1-D6

| # | 缺陷 | 证据 | v2 修正 |
|---|---|---|---|
| D1 | R1 前提不成立 — LLM 不返回 source_article_ids | 信号 keys 无 article_ids; prompt schema 未要求 | R1 分来源(仅 llm 强制) + 改 prompt |
| D2 | LLM 输出不稳定 — 裁决不可复现 | 同批数据 21 vs 3 vs 4 条 | R2 不 hard reject, 三档降级 |
| D3 | R3 时间逻辑错 — 生成时裁决无法处理过期 | save_signals 按天写, digest 不重裁 | 裁决移到消费时(digest 构建) |
| D4 | from_dict/to_dict 不同步 — 新字段存了读不回 | from_dict 只取固定 key | 同步新增字段 |
| D5 | R1 误杀规则信号 | padding 天生无 article_ids | R1 仅 llm 强制溯源 |
| D6 | R2 hard reject 误杀持仓信号 | NVDA 0.65 < 0.70 | 弱信号降级不丢 |

### 第二轮（v2 → v3）: E1-E3

| # | 问题 | 证据 | v3 修正 |
|---|---|---|---|
| E1 | R1 可行性已验证, 但需 _parse_signals 接住字段 | 实验 prompt 4/4 服从且引用正确 | 实施清单加 _parse_signals |
| E2 | 消费端双路径泄漏风险 | top_signals 喂 LLM(1805), parsed_signals 喂匹配(2062) | 裁决插入点=digest 构建内, 双路径共用 |
| E3 | weak 消费语义未定义 | v2 只说"只展示不进 driver" | weak 保留进 LLM 面, 排除出确定性面, 标 weak:true |

### 第三轮（v3 → v4）: F1-F3

| # | 发现 | 证据 | v4 处理 |
|---|---|---|---|
| F1 | padding 占 80% 信号, 裁决统计被污染 | 21 条 = 3-4 真实 + 17 padding | 前置清理去 analyzer padding; 分来源统计 |
| F2 | 真实 LLM 信号基线 3-4 条, 不是 21 | 四次采样 3/3/3/3 条稳定 | 验收基线改为真实信号数 |
| F3 | 双层 padding 冗余(analyzer + match_intelligence) | 代码 979 + 1294 | 去 analyzer 层, 保留消费端动态 padding |

### 第四轮（v3 → v4）: F1-F3

| # | 发现 | 证据 | v4 处理 |
|---|---|---|---|
| F1 | padding 占 80% 信号, 裁决统计被污染 | 21 条 = 3-4 真实 + 17 padding | 前置清理去 analyzer padding; 分来源统计 |
| F2 | 真实 LLM 信号基线 3-4 条, 不是 21 | 四次采样 3/3/3/3 条稳定 | 验收基线改为真实信号数 |
| F3 | 双层 padding 冗余(analyzer + match_intelligence) | 代码 979 + 1294 | 去 analyzer 层, 保留消费端动态 padding |

### 第五轮（v4 → v4.1）: G1-G3

| # | 发现 | 证据 | v4.1 处理 |
|---|---|---|---|
| G1 | brief 时区混用 — R3 TTL 计算可能偏差 8 小时 | source=+08:00 vs brief=UTC 同文件 | 统一 UTC 时间语义, 比较前 astimezone(utc) |
| G2 | risk_eligible(48h) vs R3 TTL(6-72h) 层级未定义 | _compute_brief_health max_age_hours=48 | 两层独立串联: 批级 stale_batch + 条级 expired |
| G3 | risk_eligible=false 时双路径不一致已存在 | top_signals 清空(2111) vs parsed 照常(2062) | 裁决器输出 batch_stale, 消费端按批级降级 |

### 实施前检查清单（五轮整合）
- [ ] **时间语义修复(G1)**: 信号/brief 时间戳统一 UTC, 比较前
      astimezone(utc); 验收解析误差 < 1 分钟
- [ ] **批级/条级两层(TTL 语义)**(G2): risk_eligible 批级(48h) 与 R3
      条级(6-72h) 串联; 裁决输出标 stale_batch / expired
- [ ] **risk_eligible=false 时双路径一致**(G3): 确定性面也标
      batch_stale, 消费端降级; 消除"LLM 空、确定性照常"
- [ ] 前置清理: 去 `_pad_category_signals` 调用; 8-11 重放 signals 无
      padding 混入; 消费端 padding 行为不变; 全量测试同步
- [ ] `_LLM_PROMPT_SYSTEM` 信号 schema 加 `source_article_ids`（已实验
      验证服从）
- [ ] `_parse_signals` 解析 `source_article_ids`
- [ ] `IntelligenceSignal` 5 新字段 + from_dict/to_dict 同步
- [ ] `signal_adjudicator.py`: R1 分来源 / R2 三档不 hard reject /
      R3 TTL consumption-time / R4 dissent
- [ ] digest 构建内(2058-2062 之间)插裁决, raw+parsed 双路径一致
- [ ] weak 标 `"weak": true` 进 top_signals, 不进 parsed_signals
- [ ] 分来源裁决统计(by_generation), 无 padding 假基线

- [ ] 前置清理: 去 `_pad_category_signals` 调用; 8-11 重放 signals 无
      padding 混入; 消费端 padding 行为不变; 全量测试同步
- [ ] `_LLM_PROMPT_SYSTEM` 信号 schema 加 `source_article_ids`（已实验
      验证服从）
- [ ] `_parse_signals` 解析 `source_article_ids`
- [ ] `IntelligenceSignal` 5 新字段 + from_dict/to_dict 同步
- [ ] `signal_adjudicator.py`: R1 分来源 / R2 三档不 hard reject /
      R3 TTL consumption-time / R4 dissent
- [ ] digest 构建内(2058-2062 之间)插裁决, raw+parsed 双路径一致
- [ ] weak 标 `"weak": true` 进 top_signals, 不进 parsed_signals
- [ ] 分来源裁决统计(by_generation), 无 padding 假基线
- [ ] 验收: 8-11 重放(真实信号 3-4 条) + 全量测试基线 + ruff/compileall
