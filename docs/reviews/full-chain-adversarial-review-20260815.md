# stocks-claw 全链路对抗性审查报告

> 日期: 2026-08-15
> 审查起点: 最新真实报告结果(cn_after_close.json 渲染)
> 审查方式: 从报告结果出发 → 回溯全链路代码 → 对抗性核验 → 用户/开发者双视角
> 基线: 当前 HEAD 349e1d0 / 全量 1438 passed

## 〇、审查方法论说明(重要)

**关键事实: 所有 `.local/scheduled_runs/latest/` 的最近 artifact 都是修复前旧代码
(commit f6c082d/e5dd5b5, version 2.10.1) 生成的, 当前 HEAD 已推进多个 commit
(硬编码根治/分批档位/A股财报/渲染纪律)。**

因此本报告**严格区分**两类发现:
- **历史遗留(旧 artifact 暴露, 但当前代码已修复)** — 不作为当前缺陷, 仅作演进记录
- **当前系统(HEAD 349e1d0)真实状态** — 通过当前代码 + 测试锁定的证据链判断

不以旧 artifact 的"问题"误导"当前系统有问题"。

## 一、全链路运作逻辑(已追踪确认)

```
数据源/输入 (financial_assets.json + 各 provider)
   ↓
持仓估值 position_valuations (currency 换算: exchange_rate.py, 含 HKD 联系汇率)
   ↓
QuantActionEngine.review_position (每标的一次, config=quant_config)
   ↓  内部分层: 硬止损 > 组合约束 > 趋势信号 > 指标信号
adjudicate() 裁决 → 最终 ratio (DataFreshnessRule 等 factor ×ratio_modifier)
   ↓
_format_action_text(final ratio) → action 文本与 ratio 同源 (无脱节)
   ↓
action_cards (position_id/label/action/ratio/facts/stop_price/target...)
   ↓
_build_portfolio_risk_summary(action_cards) → 组合风险 (无双引擎调用, 消费 action_cards)
   ↓
build_user_view → 结构化 user_view (instruction_card + assistant_brief)
   ↓
agent_task v5 (指令随数据走: must_answer/must_not_do/data_reference/output_structure)
   ↓
build_push_payload (确定性渲染 + LLM) → push payload → 飞书推送
```

## 二、用户视角(报告可用性)发现

### 历史遗留(旧 artifact, 当前已修)
1. **动作区 vs 待决区信息差**: 旧报告"可执行动作"只列 1 个, 但组合区列 6 个减仓/止盈
   信号。根因: approved_actions 多个 full 可执行, 但展示只取前 3 + 溢出概括 → 用户难以
   判断"到底要我动哪些"。→ 当前 render_discipline 指令卡列全部动作+overflow 一行, 已改善。
2. **"减仓 50%(按 40% 比例)"矛盾**: 旧 artifact action 文本用阶梯 ratio(50%)生成,
   final ratio 被裁决改 0.4 但文本未跟随。→ 当前 `_format_action_text(final_ratio)` 已修复。
3. **NVDA 多处重复列出**噪音。
4. **明日计划 ①②③ 乱序**(非按优先级/执行顺序)。

### 当前系统仍需留意(基于当前代码)
5. **单币种失败整份 block**: HKD 已修(联系汇率 7.8, 有测试), 但其他不支持货币(GBP/EUR)
   仍会触发 asset_completeness blocked → 整份报告降级。建议单币种失败降级为"该资产近似
   估值+标注", 而非整份 block。
6. **分批档位表价格重合**: MA60 与布林下轨同价时(实测 0.66)被当两个档位各接 35%/25%,
   用户可能误以为"两档分散"实际同价。建议合并同价位档位。

## 三、开发者视角(架构与可维护性)

### 做得好
1. **无双引擎调用**: `_build_portfolio_risk_summary` 消费 action_cards("不再独立计算")。
2. **Final action 与 ratio 同源**: `_format_action_text` 用 final ratio, 单 source of truth。
3. **指令随数据走(agent_task v5)**: must_answer/must_not_do/data_reference 内嵌 artifact。
4. **硬编码文案识别已根治**: no_action_reason_types 结构化优先。
5. **优雅降级链**: HKD/数据源失败走 cache→fixed→cross→fail, 有测试锁定。
6. **分层清晰**: engine/presentation/build_push_payload 分离。

### 需关注
7. **schema 兼容双轨**: latest artifact 是 schema v1(user_view 空), 但 agent_task v5 引用
   `portfolio_decision.user_view` → 靠兼容渲染(mandatory_blocks)兜底。指令-数据-消费
   三轨风险: 一旦 user_view 字段缺失, 回退是否完整? 建议强制 v2+ schema 或回归兼容渲染。
8. **展示上限信息隐藏**: approved 6 个 full 只显示前 3 + overflow。
9. **magic number**: 大量阈值(0.995/0.85/40%/7.8)散落, 部分依赖 convention。
10. **prompt/validator/consumer schema drift** 风险: 渲染严格依赖 agent_task 的
    must_not_do, 需持续对齐。

## 四、对抗性核验结论(关键: 旧 vs 新)

| 疑点(旧 artifact) | 当前代码状态 | 证据 |
|---|---|---|
| us_aapl ratio 0.4 vs action "减仓50%" | ✅ 已修复 | _format_action_text(final_ratio) |
| HKD 整份报告降级 | ✅ 已修复 | exchange_rate 联系汇率 + test_exchange_rate |
| user_view 空(schema v1) | ⚠️ 兼容渲染兜底 | mandatory_blocks |
| 动作区信息不完整 | ⚠️ 已改善(render_discipline) | 指令卡列全部动作 |

## 五、五门验收(当前系统)

| Gate | 判定 | 依据 |
|---|---|---|
| 1 一致性 | ✅ Pass | 六层一致(单引擎, action/ratio 同源) |
| 2 执行 | ⚠️ Partial | 动作有分类, 但展示上限可能隐藏动作 |
| 3 归因 | ✅ Pass | artifact 带 code_version/run_id/schema |
| 4 打扰 | ✅ Pass | notification digest + quiet_hours |
| 5 用户价值 | ⚠️ 待真实验证 | 需 Kari 确认 render_discipline 后报告实际可用 |

## 六、Top 建议(按优先级)
1. **P0**: schema v1 artifact 与 agent_task v5 user_view 的兼容渲染做完整性回归(防空白 section)。
2. **P1**: 单币种换算失败不应整份 block(建议"该资产近似估值+标注")。
3. **P2**: 分批档位表合并同价位档位(MA60 与布林下轨重合时只列一次)。
4. **P2**: 用当前 HEAD 跑一次真实 scheduled run, 验证 render_discipline 后报告实际输出。

---
(本报告基于静态代码 + 旧 artifact 交叉 + 测试锁定证据链; 未做实时市场数据跑批验证——周六休市。)
