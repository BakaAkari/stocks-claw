"""分析脚手架 — 轻量规则计算，供 LLM 参考"""

from typing import Optional

from stocks.domain.models import DriftCheck, FinancialAsset, MarketState, PortfolioMapping, Quote

# 资产类型到 bucket 的映射规则
_ASSET_TYPE_TO_BUCKET = {
    # 权益类
    "equity": "权益",
    "stock": "权益",
    "股票": "权益",
    "fund": "权益",
    "股票ETF": "权益",
    "股票etf": "权益",
    "etf": "权益",
    "指数基金": "权益",
    "指数": "权益",
    # 固收类
    "bond": "固收",
    "fixed_income": "固收",
    "理财": "固收",
    "固收": "固收",
    "债券": "固收",
    # 现金类
    "cash": "现金",
    "现金": "现金",
    "deposit": "现金",
    "money_market": "现金",
    "现金管理": "现金",
    "货币基金": "现金",
    "活期": "现金",
    # 黄金
    "gold": "黄金",
    "黄金ETF": "黄金",
    "黄金etf": "黄金",
    # 其他
    "commodity": "商品",
    "crypto": "加密",
    "reits": "REITs",
    "alternative": "另类",
    "locked": "锁定",
    "unknown": "其他",
}


def _map_asset_type(asset_type: str) -> str:
    """将 asset_type 映射到标准 bucket 名称"""
    return _ASSET_TYPE_TO_BUCKET.get(asset_type.lower(), "其他")


class PortfolioScaffold:
    """组合映射脚手架 — 基于轻量规则计算组合结构，供 LLM 参考"""

    def build(self, assets: list[FinancialAsset], constraints: dict) -> PortfolioMapping:
        """根据资产和约束构建组合映射"""
        if not assets:
            return PortfolioMapping()

        # 按 asset_type 分组到 buckets
        buckets: dict[str, list[FinancialAsset]] = {}
        for asset in assets:
            bucket = _map_asset_type(asset.asset_type)
            buckets.setdefault(bucket, []).append(asset)

        # 计算总资产金额
        total = sum(a.amount for a in assets)
        total = total if total > 0 else 1.0  # 避免除零

        # 计算各 bucket 占比
        ratios: dict[str, float] = {}
        for bucket, bucket_assets in buckets.items():
            ratios[bucket] = round(sum(a.amount for a in bucket_assets) / total, 4)

        # 判断 dominant_layers（占比 > 30% 的层）
        dominant_layers = [b for b, r in ratios.items() if r > 0.30]
        dominant_layers.sort(key=lambda b: ratios[b], reverse=True)

        # 判断 growth_exposure（权益类占比）
        equity_ratio = ratios.get("权益", 0.0)
        if equity_ratio > 0.60:
            growth_exposure = "high"
        elif equity_ratio > 0.35:
            growth_exposure = "moderate"
        elif equity_ratio > 0.10:
            growth_exposure = "light"
        else:
            growth_exposure = "none"

        # 判断 buffer_strength（固收+现金占比）
        buffer_ratio = ratios.get("固收", 0.0) + ratios.get("现金", 0.0)
        if buffer_ratio > 0.50:
            buffer_strength = "strong"
        elif buffer_ratio > 0.25:
            buffer_strength = "moderate"
        elif buffer_ratio > 0.05:
            buffer_strength = "light"
        else:
            buffer_strength = "none"

        # 判断 liquidity_status（现金占比）
        cash_ratio = ratios.get("现金", 0.0)
        if cash_ratio > 0.20:
            liquidity_status = "ample"
        elif cash_ratio > 0.08:
            liquidity_status = "adequate"
        else:
            liquidity_status = "thin"

        # 判断 locked_assets_present（是否有 locked 资产）
        locked_assets_present = "锁定" in buckets and len(buckets["锁定"]) > 0

        return PortfolioMapping(
            buckets=buckets,
            ratios=ratios,
            dominant_layers=dominant_layers,
            growth_exposure=growth_exposure,
            buffer_strength=buffer_strength,
            liquidity_status=liquidity_status,
            locked_assets_present=locked_assets_present,
        )

    def check_drift(
        self,
        mapping: PortfolioMapping,
        constraints: dict
    ) -> list[DriftCheck]:
        """检查约束偏离 — 对每个 bucket 检查是否超出约束范围"""
        drift_checks: list[DriftCheck] = []

        # 约束声明是检查基准：组合里缺失的 bucket 也必须按 0% 检查最小值。
        for bucket, bucket_constraints in constraints.items():
            ratio = mapping.ratios.get(bucket, 0.0)
            target_min = bucket_constraints.get("min")
            target_max = bucket_constraints.get("max")

            if target_min is None and target_max is None:
                continue

            status = "within_range"
            gap = 0.0

            if target_min is not None and ratio < target_min:
                status = "below_min"
                gap = round(target_min - ratio, 4)
            elif target_max is not None and ratio > target_max:
                status = "above_max"
                gap = round(ratio - target_max, 4)

            drift_checks.append(DriftCheck(
                bucket=bucket,
                current_ratio=ratio,
                target_min=target_min,
                target_max=target_max,
                status=status,
                gap=gap,
            ))

        return drift_checks


class MarketScaffold:
    """市场状态脚手架 — 基于行情数据判断市场状态，供 LLM 参考"""

    def build(self, quotes: dict[str, list[Quote]]) -> MarketState:
        """根据行情数据判断市场状态"""
        if not quotes:
            return MarketState()

        # 收集各市场/类别的涨跌幅和代表性标的
        all_quotes = []
        for market_quotes in quotes.values():
            all_quotes.extend(market_quotes)

        # 分类统计。资产类别只由 watchlist 的 category 决定，名称不参与推断。
        equity_changes = []      # 权益类
        tech_changes = []        # 科技相关
        safe_haven_changes = []  # 黄金/债券等避险
        china_changes = []       # A股
        rates_changes = []       # 债券/利率相关
        crypto_changes = []      # 加密资产

        for q in all_quotes:
            if q.pct_change is None:
                continue
            pc = q.pct_change

            category = (q.instrument.category or "").strip().lower()
            if category in ("equity_cn", "equity_us", "equity", "tech"):
                equity_changes.append(pc)
            if category == "equity_cn":
                china_changes.append(pc)
            if category == "tech":
                tech_changes.append(pc)
            if category == "gold":
                safe_haven_changes.append(pc)
            if category == "bond":
                safe_haven_changes.append(pc)
                rates_changes.append(pc)
            if category == "crypto":
                crypto_changes.append(pc)

        # 判断 risk_appetite（风险情绪）
        risk_appetite = self._judge_risk_appetite(equity_changes)

        # 判断 tech_state（科技股状态）
        tech_state = self._judge_tech_state(tech_changes)

        # 判断 safe_haven_state（避险资产状态）
        safe_haven_state = self._judge_safe_haven(safe_haven_changes)

        # 判断 china_state（中国市场状态）
        china_state = self._judge_china_state(china_changes)

        # 判断 rates_state（利率/债券状态）
        rates_state = self._judge_rates_state(rates_changes)

        # 判断 crypto_state（加密资产状态）
        crypto_state = self._judge_crypto_state(crypto_changes)

        # 生成 cross_asset_summary（跨资产摘要）
        cross_asset_summary = self._build_summary(
            risk_appetite, tech_state, safe_haven_state, china_state, rates_state,
            equity_changes, china_changes, safe_haven_changes, crypto_changes
        )

        return MarketState(
            risk_appetite=risk_appetite,
            tech_state=tech_state,
            safe_haven_state=safe_haven_state,
            china_state=china_state,
            rates_state=rates_state,
            crypto_state=crypto_state,
            cross_asset_summary=cross_asset_summary,
        )

    # ---------- 判断规则 ----------

    def _avg(self, values: list[float]) -> Optional[float]:
        """计算平均值"""
        return sum(values) / len(values) if values else None

    def _judge_risk_appetite(self, equity_changes: list[float]) -> str:
        avg = self._avg(equity_changes)
        if avg is None:
            return "no_data"
        if avg > 1.5:
            return "risk_on"
        elif avg > 0.3:
            return "cooling"
        elif avg < -1.0:
            return "broad_risk_off"
        return "mixed"

    def _judge_tech_state(self, tech_changes: list[float]) -> str:
        if tech_changes:
            avg = self._avg(tech_changes)
            if avg is not None:
                if avg > 1.0:
                    return "expanding"
                elif avg < -1.5:
                    return "under_pressure"
                elif avg < -0.5:
                    return "soft"
                return "mixed"
        return "no_data"

    def _judge_safe_haven(self, safe_haven_changes: list[float]) -> str:
        avg = self._avg(safe_haven_changes)
        if avg is None:
            return "no_data"
        if avg > 0.5:
            return "strengthening"
        elif avg < -0.5:
            return "weakening"
        return "supported"

    def _judge_china_state(self, china_changes: list[float]) -> str:
        avg = self._avg(china_changes)
        if avg is None:
            return "no_data"
        if avg > 1.0:
            return "stable_positive"
        elif avg > 0.0:
            return "stable"
        elif avg > -1.0:
            return "mixed_pressure"
        return "under_pressure"

    def _judge_rates_state(self, rates_changes: list[float]) -> str:
        avg = self._avg(rates_changes)
        if avg is None:
            return "no_data"
        # 债券价格上涨 = 收益率下降 = 利率压力缓解，但这里简单处理
        if avg > 0.3:
            return "bonds_bid"
        elif avg < -0.3:
            return "rates_pressure"
        return "neutral"

    def _judge_crypto_state(self, crypto_changes: list[float]) -> str:
        avg = self._avg(crypto_changes)
        if avg is None:
            return "no_data"
        if avg > 1.5:
            return "strong"
        if avg > 0.3:
            return "positive"
        if avg < -1.5:
            return "under_pressure"
        if avg < -0.3:
            return "soft"
        return "mixed"

    def _build_summary(
        self,
        risk_appetite: str,
        tech_state: str,
        safe_haven_state: str,
        china_state: str,
        rates_state: str,
        equity_changes: list[float],
        china_changes: list[float],
        safe_haven_changes: list[float],
        crypto_changes: list[float],
    ) -> list[str]:
        """生成跨资产摘要文本"""
        summary = []

        eq_avg = self._avg(equity_changes)
        if eq_avg is not None:
            summary.append(f"权益市场整体涨跌: {eq_avg:+.2f}%")

        cn_avg = self._avg(china_changes)
        if cn_avg is not None:
            summary.append(f"A股市场涨跌: {cn_avg:+.2f}%")

        sh_avg = self._avg(safe_haven_changes)
        if sh_avg is not None:
            summary.append(f"避险资产涨跌: {sh_avg:+.2f}%")

        crypto_avg = self._avg(crypto_changes)
        if crypto_avg is not None:
            summary.append(f"加密资产涨跌: {crypto_avg:+.2f}%")

        # 状态描述
        if risk_appetite == "risk_on":
            summary.append("风险偏好较高，权益资产表现强势")
        elif risk_appetite == "broad_risk_off":
            summary.append("避险情绪升温，权益资产承压")

        if safe_haven_state == "strengthening" and risk_appetite in ("cooling", "broad_risk_off"):
            summary.append("避险资产与风险资产呈现跷跷板效应")

        if china_state == "under_pressure":
            summary.append("中国市场面临较大调整压力")
        elif china_state == "stable_positive":
            summary.append("中国市场情绪积极")

        return summary
