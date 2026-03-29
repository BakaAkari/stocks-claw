from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from stocks.services.advisory_service import AdvisoryService
from stocks.services.market_state_service import MarketStateService
from stocks.services.personal_insight_service import PersonalInsightService
from stocks.services.portfolio_mapping_service import PortfolioMappingService
from stocks.services.theme_analysis_service import ThemeAnalysisService


class ReportAssemblyService:
    REPORTS_DIR = Path(__file__).resolve().parents[1] / 'reports'
    SNAPSHOT_PATH = REPORTS_DIR / 'personal-latest.json'
    SNAPSHOT_HISTORY_DIR = REPORTS_DIR / 'snapshots'

    def __init__(
        self,
        insight_service: PersonalInsightService | None = None,
        theme_service: ThemeAnalysisService | None = None,
        market_state_service: MarketStateService | None = None,
        portfolio_mapping_service: PortfolioMappingService | None = None,
        advisory_service: AdvisoryService | None = None,
    ):
        self.insight_service = insight_service or PersonalInsightService()
        self.theme_service = theme_service or ThemeAnalysisService()
        self.market_state_service = market_state_service or MarketStateService()
        self.portfolio_mapping_service = portfolio_mapping_service or PortfolioMappingService()
        self.advisory_service = advisory_service or AdvisoryService()

    def build(self) -> dict:
        insight = self.insight_service.build_context()
        theme = self.theme_service.analyze()
        market_state = self.market_state_service.refresh()
        portfolio_mapping = self.portfolio_mapping_service.refresh()
        advisory = self.advisory_service.refresh()
        memory = insight['financial_memory']
        assets = memory['assets']
        profile = memory.get('portfolio_profile_notes') or {}
        hot_sector = theme['hot_sector']
        cold_sector = theme['cold_sector']
        observations = theme.get('market_observations', {})

        mapping_structure = portfolio_mapping.get('portfolio_structure') or {}
        mapping_impact = portfolio_mapping.get('market_impact') or {}
        mapping_interpretation = portfolio_mapping.get('interpretation') or []
        dominant_layers = mapping_structure.get('dominant_layers') or []

        portfolio_roles = {
            'dominant_role': dominant_layers[0] if dominant_layers else None,
            'role_names': {},
            'role_amounts': mapping_structure.get('bucket_amounts') or {},
            'role_ratios': mapping_structure.get('bucket_ratios') or {},
            'total_classified_amount': sum((mapping_structure.get('bucket_amounts') or {}).values()),
        }
        portfolio_health = {
            'health_label': 'balanced',
            'strengths': mapping_interpretation[:3],
            'issues': (mapping_impact.get('likely_to_miss_upside') or [])[:2],
            'suggestions': (mapping_impact.get('notes') or [])[:3],
            'interpreted_total_amount': sum((mapping_structure.get('bucket_amounts') or {}).values()),
            'profile_reference': profile,
            'asset_names_by_role': portfolio_mapping.get('asset_names_by_bucket') or {},
        }
        hot_state = self._hot_state(hot_sector, observations, theme.get('cluster_scores', []))
        yesterday_change = self._build_yesterday_change(theme, observations, portfolio_roles, hot_state)
        related_lines = []
        if mapping_impact.get('beneficiaries'):
            related_lines.append('当前相对更舒服的：' + '、'.join(mapping_impact.get('beneficiaries')[:5]))
        if mapping_impact.get('under_pressure'):
            related_lines.append('当前更直接承压的：' + '、'.join(mapping_impact.get('under_pressure')[:5]))
        if mapping_impact.get('buffers'):
            related_lines.append('正在发挥缓冲作用的：' + '、'.join(mapping_impact.get('buffers')[:4]))
        if mapping_impact.get('likely_to_miss_upside'):
            related_lines.extend(mapping_impact.get('likely_to_miss_upside')[:2])
        structural_hints = []
        structural_hints.extend(mapping_interpretation[:6])
        structural_hints.extend((mapping_impact.get('notes') or [])[:4])

        representative_assets = {
            'hot': (hot_sector.get('market_validation') or {}).get('signals', []),
            'cold': (cold_sector.get('market_validation') or {}).get('signals', []),
        }

        watch_themes = theme.get('watch_themes', [])
        hot_name = hot_sector.get('name')
        hot_cluster = hot_sector.get('cluster')
        watch_theme_labels = []
        for item in watch_themes:
            name = item.get('name')
            cluster = item.get('cluster')
            if name:
                watch_theme_labels.append(f"{name}（{cluster}）" if cluster else name)
        watch_names = '、'.join(watch_theme_labels[:2]) or '若干观察主题'
        news_count = insight['news_input']['count']
        asset_count = memory['asset_count']

        if hot_name and hot_state != 'unconfirmed':
            hot_label = f"{hot_name}（{hot_cluster}）" if hot_cluster else hot_name
            if hot_state == 'confirmed_expanding':
                overview = f"主线仍落在{hot_label}，而且扩散还在继续，盘面暂时没有明显掉队"
                conclusion = f"{hot_name}还是主线，而且扩散还在继续，市场暂时没从这条线退潮"
            elif hot_state == 'confirmed_diverging':
                overview = f"主线仍落在{hot_label}，但盘面更像分化，不像共振上攻"
                conclusion = f"{hot_name}还是主线，但强势不整齐，市场更像分化拉扯，不像单边扩散"
            elif hot_state == 'confirmed_under_pressure':
                overview = f"主线仍落在{hot_label}，但价格已经承压，强势开始转弱"
                conclusion = f"{hot_name}还是主线，但已进入承压阶段，弹性和回撤都会放大"
            else:
                overview = f"主线仍落在{hot_label}，但确认度不算扎实，盘面更像边走边试"
                conclusion = f"{hot_name}还占着主位，但确认度一般，盘面更像试探而不是扩散"
        elif watch_themes:
            overview = f"主线还没坐实，资金更像在{watch_names}之间试探轮动"
            conclusion = f"盘面还在试主线，先看{watch_names}里谁能先走出确认"
        else:
            if news_count == 0 and asset_count == 0:
                overview = '今天几乎没有可用输入，暂时看不出新主线'
            else:
                overview = '没有新主线站出来，市场更像存量方向之间的分化拉扯'
            conclusion = '今天更像旧线博弈，不像新一轮共振启动'

        report = {
            'generated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S'),
            'market_state': market_state,
            'portfolio_mapping': portfolio_mapping,
            'advisory_plan': advisory,
            'overview': overview,
            'hot_sector': hot_sector,
            'cold_sector': cold_sector,
            'watch_themes': watch_themes,
            'market_observations': observations,
            'hot_state': hot_state,
            'portfolio_roles': portfolio_roles,
            'portfolio_health': portfolio_health,
            'portfolio_profile_notes': profile,
            'representative_assets': representative_assets,
            'user_relevance': related_lines,
            'structural_hints': structural_hints,
            'analysis_signals': {
                'yesterday_change': yesterday_change,
                'watch_names': watch_names,
                'hot_state_label': self._hot_state_label(hot_state),
                'dominant_role_label': self._role_label(portfolio_roles.get('dominant_role')),
                'portfolio_health_label': portfolio_health.get('health_label'),
            },
            'risk_notes': [
                yesterday_change or (f"额外可看：{watch_names}" if watch_themes else '今天没有挤进正文的额外主题'),
                *((portfolio_mapping.get('market_impact') or {}).get('notes') or [])[:2],
                '代表资产只是轻量映射，不是完整市场扫描',
            ],
            'conclusion': conclusion,
        }
        self._write_snapshot(report)
        report['recent_snapshots'] = self._read_recent_snapshots(limit=3)
        return report

    def _safe_amount(self, value) -> float:
        try:
            if value in (None, ''):
                return 0.0
            return float(value)
        except Exception:
            return 0.0

    def _build_yesterday_change(self, theme: dict, observations: dict, portfolio_roles: dict, hot_state: str) -> str | None:
        previous = self._read_snapshot()
        if not previous:
            return None

        current_hot = theme.get('hot_sector', {}).get('name')
        prev_hot_sector = previous.get('hot_sector') or {}
        prev_hot = prev_hot_sector.get('name')
        current_cluster = (theme.get('hot_sector') or {}).get('cluster')
        prev_cluster = prev_hot_sector.get('cluster')
        current_risk_state = self._risk_state(' '.join(observations.get('risk_appetite', [])))
        prev_risk_state = self._risk_state(' '.join((previous.get('market_observations') or {}).get('risk_appetite', [])))
        current_china_state = self._china_state(' '.join(observations.get('china_market', [])))
        prev_china_state = self._china_state(' '.join((previous.get('market_observations') or {}).get('china_market', [])))
        current_role = portfolio_roles.get('dominant_role')
        prev_role = (previous.get('portfolio_roles') or {}).get('dominant_role')
        prev_hot_state = previous.get('hot_state') or 'unknown'

        if current_hot and prev_hot and current_hot != prev_hot:
            return f"和上一版相比，主线已从{prev_hot}切到{current_hot}"
        if current_cluster and prev_cluster and current_cluster != prev_cluster:
            return f"和上一版相比，主线链路已从{prev_cluster}切到{current_cluster}"
        if hot_state != prev_hot_state and prev_hot_state != 'unknown':
            return f"和上一版相比，主线状态已从{self._hot_state_label(prev_hot_state)}切到{self._hot_state_label(hot_state)}"
        if current_role and prev_role and current_role != prev_role:
            return f"和上一版相比，你组合的主导角色已从{self._role_label(prev_role)}切到{self._role_label(current_role)}"
        if current_risk_state != prev_risk_state:
            return f"和上一版相比，风险偏好已从{self._risk_label(prev_risk_state)}切到{self._risk_label(current_risk_state)}"
        if current_china_state != prev_china_state:
            return f"和上一版相比，中国市场已从{self._china_label(prev_china_state)}切到{self._china_label(current_china_state)}"
        if current_hot and prev_hot and current_hot == prev_hot:
            return f"和上一版相比，主线没换，但{current_hot}仍在主位，只是内部强弱更分化"
        return None

    def _hot_state(self, hot_sector: dict, observations: dict, cluster_scores: list[dict]) -> str:
        if not hot_sector.get('name'):
            return 'unconfirmed'

        risk_text = ' '.join(observations.get('risk_appetite', []))
        us_text = ' '.join(observations.get('us_market', []))
        lead_cluster = cluster_scores[0].get('cluster') if cluster_scores else None
        status = hot_sector.get('status')

        if status == 'confirmed':
            if '承压' in risk_text or ('转弱' in us_text and '分化' in us_text):
                return 'confirmed_under_pressure'
            if '分化' in risk_text or '分化' in us_text:
                return 'confirmed_diverging'
            if hot_sector.get('cluster') and lead_cluster == hot_sector.get('cluster'):
                return 'confirmed_expanding'
            return 'confirmed_diverging'

        if status == 'watch':
            if hot_sector.get('cluster') and lead_cluster == hot_sector.get('cluster'):
                return 'confirmed_under_pressure'
            if '承压' in risk_text or '分化' in risk_text:
                return 'confirmed_under_pressure'
            return 'watching'

        return 'unconfirmed'

    def _hot_state_label(self, state: str) -> str:
        return {
            'confirmed_expanding': '主线扩散',
            'confirmed_diverging': '主线分化',
            'confirmed_under_pressure': '主线承压',
            'watching': '主线试探',
            'unconfirmed': '主线未坐实',
            'unknown': '未知',
            None: '未知',
        }.get(state, '未知')

    def _risk_state(self, text: str) -> str:
        if '降温' in text or '承压' in text:
            return 'cooling'
        if '偏向成长' in text or '维持正向' in text:
            return 'risk_on'
        return 'neutral'

    def _risk_label(self, state: str) -> str:
        return {
            'cooling': '降温',
            'risk_on': 'risk-on',
            'neutral': '中性',
            None: '未知',
        }.get(state, '中性')

    def _china_state(self, text: str) -> str:
        if '没跟跌' in text or '稳住' in text:
            return 'stable'
        if '补跌' in text:
            return 'follow_down'
        if '独立主线' in text or '主动进攻' in text:
            return 'independent'
        return 'neutral'

    def _china_label(self, state: str) -> str:
        return {
            'stable': '稳住承接',
            'follow_down': '跟随补跌',
            'independent': '相对独立',
            'neutral': '中性',
            None: '未知',
        }.get(state, '中性')

    def _role_label(self, role: str) -> str:
        return {
            'attack': '进攻弹性',
            'buffer': '防守缓冲',
            'base': '稳定底仓',
            'defense': '防守现金流',
            'income': '分红防守',
            'healthcare': '防御医药',
            'locked': '长期锁定层',
            'liquidity': '流动性层',
            None: '未知角色',
        }.get(role, '未知角色')

    def _snapshot_payload(self, report: dict) -> dict:
        return {
            'generated_at': report.get('generated_at'),
            'hot_sector': report.get('hot_sector'),
            'cold_sector': report.get('cold_sector'),
            'watch_themes': report.get('watch_themes'),
            'market_observations': report.get('market_observations'),
            'hot_state': report.get('hot_state'),
            'portfolio_roles': report.get('portfolio_roles'),
            'portfolio_health': report.get('portfolio_health'),
            'portfolio_profile_notes': report.get('portfolio_profile_notes'),
            'user_relevance': report.get('user_relevance'),
            'structural_hints': report.get('structural_hints'),
            'analysis_signals': report.get('analysis_signals'),
            'conclusion': report.get('conclusion'),
        }

    def _read_snapshot(self) -> dict | None:
        if not self.SNAPSHOT_PATH.exists():
            return None
        try:
            return json.loads(self.SNAPSHOT_PATH.read_text(encoding='utf-8'))
        except Exception:
            return None

    def _read_recent_snapshots(self, limit: int = 3) -> list[dict]:
        if not self.SNAPSHOT_HISTORY_DIR.exists():
            return []
        snapshots = []
        files = sorted(self.SNAPSHOT_HISTORY_DIR.glob('personal-snapshot-*.json'), reverse=True)
        for path in files[:limit]:
            try:
                payload = json.loads(path.read_text(encoding='utf-8'))
            except Exception:
                continue
            snapshots.append({
                'generated_at': payload.get('generated_at'),
                'conclusion': payload.get('conclusion'),
                'hot_sector_name': (payload.get('hot_sector') or {}).get('name'),
                'hot_state': payload.get('hot_state'),
                'portfolio_health_label': ((payload.get('portfolio_health') or {}).get('health_label')),
            })
        return snapshots

    def _write_snapshot(self, report: dict) -> None:
        payload = self._snapshot_payload(report)
        self.REPORTS_DIR.mkdir(parents=True, exist_ok=True)
        self.SNAPSHOT_HISTORY_DIR.mkdir(parents=True, exist_ok=True)
        self.SNAPSHOT_PATH.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
        timestamp = datetime.now().strftime('%Y%m%d-%H%M%S')
        history_path = self.SNAPSHOT_HISTORY_DIR / f'personal-snapshot-{timestamp}.json'
        history_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')

    def render_text(self) -> str:
        report = self.build()
        obs = report['market_observations']
        lines = [
            '今日概览',
            f"- {report['overview']}",
            '',
            '风险偏好',
        ]
        for item in obs.get('risk_appetite', ['风险偏好暂无明确单边倾向']):
            lines.append(f'- {item}')

        lines.extend(['', '美股观察'])
        for item in obs.get('us_market', ['当前未识别到明确的美股主线']):
            lines.append(f'- {item}')

        lines.extend(['', '黄金观察'])
        for item in obs.get('gold', ['黄金方向暂未成为今日核心主题']):
            lines.append(f'- {item}')

        lines.extend(['', '中国市场观察'])
        for item in obs.get('china_market', ['当前中国市场未识别到足够清晰的核心主题']):
            lines.append(f'- {item}')

        lines.extend(['', '与你资产相关'])
        for item in report['user_relevance'][:6]:
            lines.append(f'- {item}')

        lines.extend(['', '组合结构提示'])
        for item in report.get('structural_hints', [])[:6]:
            lines.append(f'- {item}')

        if report.get('recent_snapshots'):
            lines.extend(['', '最近几版快照'])
            for item in report['recent_snapshots'][:3]:
                lines.append(
                    f"- {item.get('generated_at')} | 主线={item.get('hot_sector_name') or '未知'} | 状态={self._hot_state_label(item.get('hot_state'))} | 组合={item.get('portfolio_health_label') or '未知'}"
                )

        lines.extend(['', '风险提示'])
        for item in report['risk_notes']:
            lines.append(f'- {item}')

        lines.extend(['', '一句话结论', f"- {report['conclusion']}"])
        return '\n'.join(lines)
