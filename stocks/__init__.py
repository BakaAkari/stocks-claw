"""stocks-claw v2 — Agent 能力扩展包

将 stocks-claw 作为 Agent 工具包使用：
    from stocks import StocksEngine
    engine = StocksEngine()
    context = await engine.build_context()

或作为 CLI 工具：
    python -m stocks.adapters.cli --assets assets.json --output json
"""

from stocks.engine import StocksEngine

__all__ = ["StocksEngine"]
__version__ = "2.0.0"
