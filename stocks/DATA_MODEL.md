# DATA_MODEL.md - Advisor Data Model

## 1. FinancialAssetRecord

单条用户金融资产记录。

字段：

- `asset_name`: 资产名称
- `platform`: 平台 / 券商 / 银行 / 账户
- `amount`: 当前金额（保留用户原始口径）
- `asset_type`: 资产类型
- `notes`: 备注
- `confirmed_by_user`: 是否由用户确认

说明：

- 当前允许不同币种并存
- 若用户明确要求外币不折算，则保持原币种描述
- 这是 advisor 最重要的基础事实之一

---

## 2. FinancialMemoryPayload

长期金融记忆载荷。

字段：

- `schema_version`
- `updated_at`
- `assets`: `FinancialAssetRecord[]`
- `portfolio_profile_notes`: 用户画像与分析口径
- `portfolio_constraints`: 用户约束（可逐步补齐）
- `notes`: 用户级补充备注（可选）

`portfolio_profile_notes` 常见内容：

- `investment_preference`
- `portfolio_focus`
- `analysis_expectation`
- `base_currency_policy`

`portfolio_constraints` 结构（v1）：

```json
{
  "schema_version": 1,
  "updated_at": "2026-03-29 01:00:00",
  "target_bucket_ranges": {
    "growth_total": {"min": 0.10, "max": 0.30, "rationale": "稳健偏成长，成长暴露控制在30%以内"},
    "gold_buffer": {"min": 0.10, "max": 0.25},
    "defense": {"min": 0.20, "max": 0.40},
    "liquidity": {"min": 0.05, "max": 0.20}
  },
  "locked_assets": ["香港中国银行5年期寿险"],
  "tactical_budget_ratio": 0.10,
  "max_drawdown_tolerance": 0.20,
  "allow_stop_loss": false,
  "allow_take_profit": false,
  "rebalance_trigger": "only_when_drifted"
}
```

字段说明：

- `target_bucket_ranges`: 各资产桶目标区间
  - `min`: 最低占比（如低于此值视为不足）
  - `max`: 最高占比（如高于此值视为超配）
  - `rationale`: 设置理由（可选，帮助理解用户意图）
- `locked_assets`: 长期锁定资产列表（不考虑调仓）
- `tactical_budget_ratio`: 机动资金比例（可用于战术调整）
- `max_drawdown_tolerance`: 最大回撤容忍度（如0.20表示20%）
- `allow_stop_loss`: 是否允许止损建议
- `allow_take_profit`: 是否允许止盈建议
- `rebalance_trigger`: 再平衡触发条件

---

## 3. NewsItem

标准新闻输入对象。

字段：

- `source`
- `title`
- `summary`
- `url`
- `published_at`
- `tags`
- `quality_flag`

说明：

- 新闻属于临时输入
- 不属于用户长期金融记忆

---

## 4. MarketState

轻量市场状态对象。

主要维度：

- `risk_appetite`
- `tech_state`
- `safe_haven_state`
- `china_state`
- `cross_asset_summary`

说明：

- 这是给 advisor 用的轻量状态脚手架
- 不是最终建议本身

---

## 5. PortfolioMapping

程序对用户组合做的轻量映射对象。

主要内容：

- 资产桶归类
- 桶占比
- 市场影响映射
- 压力映射
- 解释性提示

说明：

- 这只是帮助 LLM 理解组合结构
- 不直接替代最终建议

---

## 6. AdvisoryPlan

程序输出的轻量建议脚手架。

主要内容：

- `posture`
- `constraint_policy`
- `drift_checks`
- `allocation_advice`
- `conditional_recommendations`
- `risk_flags`
- `monitoring_focus`
- `boundaries`

说明：

- 这是 advisor 的辅助建议层
- 不是自动交易逻辑
- 不应继续无限膨胀成重型规则引擎

---

## 7. SnapshotHistoryEntry

历史快照摘要对象。

字段示例：

- `generated_at`
- `conclusion`
- `hot_state`
- `portfolio_health_label`

说明：

- 用于给 LLM 提供近期变化的短时记忆
- 不是完整历史仓库

---

## 8. AdvisorContext

送给 LLM 的核心上下文对象。

应至少包含：

- 完整金融记忆
- 用户偏好与约束
- 新闻输入
- 市场状态
- 组合映射
- 建议脚手架
- 近期快照变化

说明：

- LLM 应基于这个上下文生成个人投资建议
- 不是为了生成“更整齐的报告”，而是为了做更有用的判断
