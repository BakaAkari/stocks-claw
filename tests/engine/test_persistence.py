"""最小化上下文快照持久化测试。"""

from types import SimpleNamespace

from stocks.domain.models import DriftCheck, MarketState, PortfolioMapping
from stocks.engine.persistence import DataPersistence


def _context(index: int):
    return SimpleNamespace(
        generated_at=f"2026-07-02T00:00:0{index}+00:00",
        asset_count=index,
        portfolio_mapping=PortfolioMapping(ratios={"现金": 1.0}),
        market_state=MarketState(),
        drift_checks=[
            DriftCheck("现金", 1.0, 0.05, 0.3, "above_max", 0.7),
        ],
    )


def test_snapshot_is_minimal_and_rolls_over(tmp_path):
    persistence = DataPersistence(str(tmp_path), max_snapshots=2)

    persistence.save_context(_context(1))
    persistence.save_context(_context(2))
    persistence.save_context(_context(3))

    assert len(persistence.list_snapshots()) == 2
    recent = persistence.load_recent(2)
    assert len(recent) == 2
    assert "portfolio_summary" in recent[0]
    assert "market_state" in recent[0]
    assert "drift_checks" in recent[0]
    assert "assets" not in recent[0]


def test_disabled_persistence_does_not_write(tmp_path):
    persistence = DataPersistence(str(tmp_path), enabled=False)

    assert persistence.save_context(_context(1)) is None
    assert list(tmp_path.iterdir()) == []
