from __future__ import annotations

"""
建议脚手架服务 - 部分冻结 (Partially Frozen)

状态说明:
- 核心约束读取和偏离检查功能保持维护
- 硬编码的条件式建议规则不再扩展（保留现有规则，不新增）
- 建议质量提升应通过 LLM 上下文优化实现，而非增加规则复杂度

设计原则:
- 程序层只负责: 约束读取、结构映射、偏离检测
- LLM 负责: 综合判断、建议生成、边界说明
- 避免将本服务扩展为重型规则引擎

修改约束:
- 可修复约束读取/保存的 bug
- 可优化偏离检测算法
- 不可新增条件分支、规则类别、复杂策略逻辑
"""

import json
from datetime import datetime
from pathlib import Path

from stocks.logging_utils import log_event
from stocks.services.financial_memory_service import FinancialMemoryService
from stocks.services.market_state_service import MarketStateService
from stocks.services.portfolio_mapping_service import PortfolioMappingService

ROOT = Path(__file__).resolve().parents[1]
DATA_PATH = ROOT / 'data' / 'advisory_plan.json'


class AdvisoryService:
    def __init__(
        self,
        memory_service: FinancialMemoryService | None = None,
        market_state_service: MarketStateService | None = None,
        portfolio_mapping_service: PortfolioMappingService | None = None,
        data_path: Path | None = None,
    ):
        self.memory_service = memory_service or FinancialMemoryService()
        self.market_state_service = market_state_service or MarketStateService()
        self.portfolio_mapping_service = portfolio_mapping_service or PortfolioMappingService()
        self.data_path = data_path or DATA_PATH

    def refresh(self) -> dict:
        memory = self.memory_service.load()
        market_state = self.market_state_service.load()
        portfolio_mapping = self.portfolio_mapping_service.load()
        if not (portfolio_mapping.get('portfolio_structure') or portfolio_mapping.get('market_impact')):
            portfolio_mapping = self.portfolio_mapping_service.build(memory, market_state)
        payload = self.build(memory, market_state, portfolio_mapping)
        self.save(payload)
        log_event(
            'advisory_plan.built',
            posture=(payload.get('posture') or {}).get('action_bias'),
            suggestions=len(payload.get('allocation_advice') or []),
            conditional=len(payload.get('conditional_recommendations') or []),
        )
        return payload

    def build(self, memory: dict, market_state: dict, portfolio_mapping: dict) -> dict:
        profile = memory.get('portfolio_profile_notes') or {}
        structure = portfolio_mapping.get('portfolio_structure') or {}
        impact = portfolio_mapping.get('market_impact') or {}
        pressure = portfolio_mapping.get('pressure_map') or {}
        ratios = structure.get('bucket_ratios') or {}

        risk_state = (market_state.get('risk_appetite') or {}).get('state') or 'neutral'
        tech_state = (market_state.get('tech_state') or {}).get('state') or 'neutral'
        safe_state = (market_state.get('safe_haven_state') or {}).get('state') or 'neutral'
        china_state = (market_state.get('china_state') or {}).get('state') or 'neutral'

        constraint_policy = self._resolve_constraint_policy(memory, ratios)
        drift_checks = self._build_drift_checks(ratios, constraint_policy)
        conditional_recommendations = self._build_conditional_recommendations(
            ratios=ratios,
            drift_checks=drift_checks,
            constraint_policy=constraint_policy,
            risk_state=risk_state,
            tech_state=tech_state,
            safe_state=safe_state,
            china_state=china_state,
        )

        allocation_advice = []
        risk_flags = []
        monitoring_focus = []

        action_bias = 'hold_and_observe'
        confidence = 'medium'
        summary = '当前更适合做结构复核与观察，而不是输出命令式操作。'

        growth_ratio = float(ratios.get('us_growth', 0.0) or 0.0) + float(ratios.get('us_diversified', 0.0) or 0.0)
        buffer_ratio = float(ratios.get('gold_buffer', 0.0) or 0.0)
        defense_ratio = float(ratios.get('defense', 0.0) or 0.0)
        liquidity_ratio = float(ratios.get('liquidity', 0.0) or 0.0)
        china_ratio = float(ratios.get('china_thematic', 0.0) or 0.0) + float(ratios.get('china_resource', 0.0) or 0.0)

        active_conditionals = [item for item in conditional_recommendations if item.get('status') == 'active']
        if active_conditionals:
            action_bias = 'conditional_rebalance_candidate'
            summary = '当前更像“按条件做偏离修正”的阶段，但仍不该跳过你的目标仓位与边界约束。'
            confidence = 'medium_high' if constraint_policy.get('confidence') in ('confirmed', 'user_defined') else 'medium'

        if risk_state in ('cooling', 'broad_risk_off') or tech_state in ('under_pressure', 'soft'):
            if action_bias == 'hold_and_observe':
                action_bias = 'rebalance_only_if_drifted'
            summary = '风险偏好偏冷，系统更适合提示“是否偏离原结构”，而不是鼓励主动进攻。'
            monitoring_focus.append('先看美股成长层何时止压，再决定是否提高风险暴露')
            if growth_ratio >= 0.28:
                risk_flags.append('成长暴露不低，而当前科技主线承压，组合波动源会更集中')
                allocation_advice.append({
                    'id': 'growth_drift_check',
                    'priority': 'high',
                    'kind': 'drift_check',
                    'title': '检查成长层是否偏离你的原始目标仓位',
                    'summary': '如果最近没有主动提高成长仓位目标，先确认当前暴露是否已经高于你想要的稳健区间。',
                    'rationale': '当前风险偏好偏冷，成长层更像波动来源，不适合默认继续抬高。',
                    'boundary': '这是结构复核建议，不是直接减仓指令。',
                })
            if liquidity_ratio < 0.05 and defense_ratio < 0.20:
                risk_flags.append('防守和机动层偏薄，遇到继续波动时缓冲可能不够')
                allocation_advice.append({
                    'id': 'defense_liquidity_gap',
                    'priority': 'high',
                    'kind': 'allocation_gap',
                    'title': '优先检查防守层和机动资金是否过薄',
                    'summary': '若你准备后续继续调仓，先确认是否需要把一部分空间留给防守层或流动性，而不是继续加重单一进攻暴露。',
                    'rationale': '在 risk-off 环境下，缺少缓冲层会放大被动感。',
                    'boundary': '这里只提示结构缺口，不指定具体资产。',
                })

        if safe_state in ('strengthening', 'supported') and buffer_ratio > 0:
            monitoring_focus.append('黄金缓冲仍在起作用，但要区分方向判断和你手里具体工具的成本体验')
            if buffer_ratio >= 0.30:
                allocation_advice.append({
                    'id': 'gold_buffer_review',
                    'priority': 'medium',
                    'kind': 'concentration_review',
                    'title': '检查黄金缓冲是否已高于你想要的缓冲强度',
                    'summary': '黄金当前有效，但如果它已经明显变成组合主导层，后续更需要看它是缓冲角色，还是已经挤占了其他层的配置空间。',
                    'rationale': '缓冲有效不等于越多越好，过厚会改变组合性格。',
                    'boundary': '这是角色校准建议，不是直接让你卖黄金。',
                })

        if china_ratio > 0:
            monitoring_focus.append('A股部分要区分宽基稳住和你手里主题/资源仓位的真实体验')
            if china_state in ('stable', 'stable_positive'):
                allocation_advice.append({
                    'id': 'china_structure_check',
                    'priority': 'medium',
                    'kind': 'structure_check',
                    'title': '不要把宽基稳定误读成你当前A股持仓就同步轻松',
                    'summary': '若你的A股仓位主要仍是主题/资源暴露，应单独评估它和宽基的偏离，而不是直接套市场平均感受。',
                    'rationale': '你的A股仓位结构与宽基并不等价。',
                    'boundary': '这是解释口径与复核建议，不是切换板块命令。',
                })

        if growth_ratio <= 0.15 and risk_state not in ('cooling', 'broad_risk_off'):
            allocation_advice.append({
                'id': 'upside_capture_review',
                'priority': 'low',
                'kind': 'opportunity_review',
                'title': '若市场重新转强，可复核成长暴露是否过轻',
                'summary': '你的组合更擅长稳住，不一定能吃满风险回升时的弹性；如果你的目标仍是稳健偏成长，这部分可以在风险回暖时再评估。',
                'rationale': '当前结构偏平衡，天然不追求最猛弹性。',
                'boundary': '这是条件式复核，不是现在就加仓。',
            })

        if liquidity_ratio >= 0.10:
            monitoring_focus.append('流动性层够用，后续若要动仓，不必在市场最差的时候被动处理')
        else:
            monitoring_focus.append('流动性层不算厚，后续若连续调整，记得把交易空间单独算出来')

        if '稳健' in str(profile.get('investment_preference') or ''):
            monitoring_focus.append('判断建议是否合理时，优先拿“稳健偏成长”而不是“追最强主线”做尺子')

        drift_flags = [item for item in drift_checks if item.get('status') in ('below_min', 'above_max')]
        for item in drift_flags[:4]:
            if item['status'] == 'above_max':
                risk_flags.append(f"{item['bucket']} 当前高于目标区间上沿，已出现结构漂移")
            if item['status'] == 'below_min':
                risk_flags.append(f"{item['bucket']} 当前低于目标区间下沿，缓冲/暴露可能不足")

        if constraint_policy.get('source') == 'derived_from_preference':
            monitoring_focus.append('当前约束区间仍是按偏好推导的暂定值，最好尽快改成你亲自确认的目标仓位')
            confidence = 'medium' if confidence != 'medium_high' else confidence
        elif constraint_policy.get('source') == 'user_defined':
            monitoring_focus.append('当前条件式建议已开始参考你定义的目标区间，但仍未进入明确买卖指令层')
            user_constraints = memory.get('portfolio_constraints') or {}
            locked_assets = user_constraints.get('locked_assets') or []
            if locked_assets:
                monitoring_focus.append('这些资产被视为长期不动资产：' + '、'.join(locked_assets[:4]))
            if user_constraints.get('allow_stop_loss') is False:
                monitoring_focus.append('你当前关闭了止损建议，系统不会把短期承压直接翻译成止损动作')
            if user_constraints.get('allow_take_profit') is False:
                monitoring_focus.append('你当前关闭了止盈建议，系统会优先提示结构复核而非落袋动作')

        if not allocation_advice:
            allocation_advice.append({
                'id': 'stay_with_structure',
                'priority': 'low',
                'kind': 'hold_review',
                'title': '现阶段先维持结构观察',
                'summary': '目前更像检查组合是否偏离初始设计，而不是急着做方向性动作。',
                'rationale': '现有市场状态与当前组合结构并未形成明显冲突。',
                'boundary': '不提供明确买卖动作。',
            })

        if not risk_flags:
            risk_flags.append('当前未识别出必须立刻处理的结构性失衡，但这不等于未来几天不会变化')
            confidence = 'medium' if confidence != 'medium_high' else confidence

        return {
            'schema_version': 2,
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'posture': {
                'action_bias': action_bias,
                'confidence': confidence,
                'summary': summary,
            },
            'inputs': {
                'risk_state': risk_state,
                'tech_state': tech_state,
                'safe_haven_state': safe_state,
                'china_state': china_state,
                'dominant_layers': structure.get('dominant_layers') or [],
                'growth_exposure': structure.get('growth_exposure'),
                'buffer_strength': structure.get('buffer_strength'),
                'liquidity_status': structure.get('liquidity_status'),
            },
            'constraint_policy': constraint_policy,
            'drift_checks': drift_checks[:12],
            'allocation_advice': allocation_advice[:8],
            'conditional_recommendations': conditional_recommendations[:8],
            'risk_flags': self._dedupe(risk_flags)[:8],
            'monitoring_focus': self._dedupe(monitoring_focus)[:10],
            'source_notes': [
                *((impact.get('notes') or [])[:3]),
                *[f'{bucket}: {label}' for bucket, label in list(pressure.items())[:4]],
            ],
            'boundaries': [
                '本层输出配置与结构建议，不输出明确买卖指令。',
                '条件式建议依赖目标区间与触发条件，若约束未确认，结论只应视为暂定建议。',
                '若要进入调仓建议，需要继续补用户约束、仓位目标、触发阈值与回归测试。',
            ],
        }

    def load(self) -> dict:
        if not self.data_path.exists():
            return {'schema_version': 2, 'updated_at': None}
        with open(self.data_path, 'r', encoding='utf-8') as f:
            return json.load(f)

    def save(self, payload: dict) -> None:
        self.data_path.parent.mkdir(parents=True, exist_ok=True)
        with open(self.data_path, 'w', encoding='utf-8') as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.write('\n')

    def _resolve_constraint_policy(self, memory: dict, ratios: dict) -> dict:
        raw = memory.get('portfolio_constraints') or {}
        ranges = raw.get('target_bucket_ranges') if isinstance(raw, dict) else None
        policy = {
            'source': 'derived_from_preference',
            'confidence': 'provisional',
            'rebalance_trigger': 'only_when_outside_range_and_market_state_confirms',
            'target_bucket_ranges': self._default_bucket_ranges(memory, ratios),
            'notes': ['当前目标区间由投资偏好推导，尚未由用户逐项确认'],
        }
        if isinstance(ranges, dict) and ranges:
            policy['source'] = 'user_defined'
            policy['confidence'] = 'user_defined'
            policy['target_bucket_ranges'] = self._normalize_bucket_ranges(ranges)
            policy['rebalance_trigger'] = raw.get('rebalance_trigger') or 'only_when_outside_range_and_market_state_confirms'
            notes = raw.get('notes')
            if isinstance(notes, list) and notes:
                policy['notes'] = notes[:6]
            elif isinstance(notes, str) and notes.strip():
                policy['notes'] = [notes.strip()]
            else:
                policy['notes'] = ['使用用户定义的目标区间进行偏离检查']
        return policy

    def _default_bucket_ranges(self, memory: dict, ratios: dict) -> dict:
        preference = str((memory.get('portfolio_profile_notes') or {}).get('investment_preference') or '')
        if '稳健' in preference and '成长' in preference:
            return {
                'defense': {'min': 0.20, 'max': 0.45},
                'gold_buffer': {'min': 0.12, 'max': 0.35},
                'growth_total': {'min': 0.10, 'max': 0.30},
                'liquidity': {'min': 0.05, 'max': 0.20},
                'china_total': {'min': 0.05, 'max': 0.25},
            }
        return {
            'defense': {'min': 0.10, 'max': 0.40},
            'gold_buffer': {'min': 0.05, 'max': 0.30},
            'growth_total': {'min': 0.10, 'max': 0.35},
            'liquidity': {'min': 0.03, 'max': 0.20},
        }

    def _normalize_bucket_ranges(self, ranges: dict) -> dict:
        out = {}
        for key, value in ranges.items():
            if not isinstance(value, dict):
                continue
            try:
                min_value = float(value.get('min')) if value.get('min') is not None else None
                max_value = float(value.get('max')) if value.get('max') is not None else None
            except Exception:
                continue
            out[key] = {'min': min_value, 'max': max_value}
        return out

    def _build_drift_checks(self, ratios: dict, constraint_policy: dict) -> list[dict]:
        checks = []
        ranges = constraint_policy.get('target_bucket_ranges') or {}
        resolved_ratios = {
            'defense': float(ratios.get('defense', 0.0) or 0.0),
            'gold_buffer': float(ratios.get('gold_buffer', 0.0) or 0.0),
            'growth_total': float(ratios.get('us_growth', 0.0) or 0.0) + float(ratios.get('us_diversified', 0.0) or 0.0),
            'liquidity': float(ratios.get('liquidity', 0.0) or 0.0),
            'china_total': float(ratios.get('china_thematic', 0.0) or 0.0) + float(ratios.get('china_resource', 0.0) or 0.0),
        }
        for bucket, target in ranges.items():
            current = resolved_ratios.get(bucket, 0.0)
            min_value = target.get('min')
            max_value = target.get('max')
            status = 'within_range'
            gap = 0.0
            if min_value is not None and current < min_value:
                status = 'below_min'
                gap = round(min_value - current, 4)
            elif max_value is not None and current > max_value:
                status = 'above_max'
                gap = round(current - max_value, 4)
            checks.append({
                'bucket': bucket,
                'current_ratio': round(current, 4),
                'target_min': min_value,
                'target_max': max_value,
                'status': status,
                'gap': gap,
            })
        return checks

    def _build_conditional_recommendations(
        self,
        *,
        ratios: dict,
        drift_checks: list[dict],
        constraint_policy: dict,
        risk_state: str,
        tech_state: str,
        safe_state: str,
        china_state: str,
    ) -> list[dict]:
        items = []
        check_map = {item['bucket']: item for item in drift_checks}
        source = constraint_policy.get('source')
        provisional = source != 'user_defined'

        growth = check_map.get('growth_total')
        if growth and growth['status'] == 'above_max':
            active = risk_state in ('cooling', 'broad_risk_off') or tech_state in ('under_pressure', 'soft')
            items.append({
                'id': 'growth_overweight_under_pressure',
                'status': 'active' if active else 'watching',
                'trigger': '成长总暴露高于目标区间，且风险偏好/科技状态没有提供顺风环境',
                'direction': '优先做成长层偏离修正，再考虑是否继续提高进攻暴露',
                'action_class': 'reduce_or_hold_growth',
                'rationale': '这不是否定成长主线，而是避免在承压阶段让超配继续放大波动。',
                'boundary': '仅给方向，不指定卖出哪只标的。',
                'constraint_source': source,
            })
        if growth and growth['status'] == 'below_min':
            active = risk_state in ('risk_on', 'warming', 'mixed_positive') and tech_state not in ('under_pressure', 'soft')
            items.append({
                'id': 'growth_underweight_when_recovery',
                'status': 'active' if active else 'watching',
                'trigger': '成长总暴露低于目标区间，且市场重新回暖',
                'direction': '评估是否恢复成长层到目标区间，而不是长期停在过轻暴露',
                'action_class': 'add_growth_if_recovery_confirms',
                'rationale': '稳健不等于长期放弃弹性，但需要等市场状态支持。',
                'boundary': '只提示回补方向，不指定买入资产。',
                'constraint_source': source,
            })

        gold = check_map.get('gold_buffer')
        if gold and gold['status'] == 'above_max':
            active = safe_state not in ('strengthening', 'supported') or provisional is False
            items.append({
                'id': 'gold_above_target',
                'status': 'active' if active else 'watching',
                'trigger': '黄金缓冲高于目标区间',
                'direction': '检查黄金是否仍是缓冲角色，还是已经挤占其他层配置空间',
                'action_class': 'review_gold_concentration',
                'rationale': '缓冲有效不等于越厚越好。',
                'boundary': '不直接给出卖黄金指令。',
                'constraint_source': source,
            })

        liquidity = check_map.get('liquidity')
        if liquidity and liquidity['status'] == 'below_min':
            active = True
            items.append({
                'id': 'liquidity_below_floor',
                'status': 'active' if active else 'watching',
                'trigger': '流动性低于目标下限',
                'direction': '若后续还要继续调整仓位，优先恢复机动空间，避免在波动里被动处理',
                'action_class': 'rebuild_liquidity_buffer',
                'rationale': '流动性是调仓自由度，不只是闲置资金。',
                'boundary': '只提示先后顺序，不指定资金来源。',
                'constraint_source': source,
            })

        defense = check_map.get('defense')
        if defense and defense['status'] == 'below_min' and risk_state in ('cooling', 'broad_risk_off'):
            items.append({
                'id': 'defense_below_floor_in_risk_off',
                'status': 'active',
                'trigger': '防守层低于目标下限，且市场风险偏好偏冷',
                'direction': '优先复核组合是否缺少稳定底仓，而不是继续抬高进攻暴露',
                'action_class': 'rebuild_defense',
                'rationale': '在 risk-off 环境里，防守层过薄会明显放大体验波动。',
                'boundary': '不指定具体理财/固收工具。',
                'constraint_source': source,
            })

        china = check_map.get('china_total')
        if china and china['status'] == 'above_max' and china_state not in ('stable', 'stable_positive', 'independent'):
            items.append({
                'id': 'china_exposure_needs_context',
                'status': 'watching',
                'trigger': '中国相关暴露高于目标区间，但市场状态未明显提供顺风',
                'direction': '更应区分主题/资源仓位与宽基状态的偏离，而不是看大盘均值下结论',
                'action_class': 'review_china_mix',
                'rationale': '中国暴露内部差异很大，不能当成一个桶粗暴处理。',
                'boundary': '先复核结构，不直接给切换建议。',
                'constraint_source': source,
            })

        return items

    def _dedupe(self, items: list[str]) -> list[str]:
        out = []
        seen = set()
        for item in items:
            if item not in seen:
                out.append(item)
                seen.add(item)
        return out
