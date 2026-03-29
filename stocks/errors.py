from __future__ import annotations


class StocksError(RuntimeError):
    """Base error for the stocks module."""


class ConfigError(StocksError):
    """Configuration is invalid or incomplete."""


class ResolverError(StocksError):
    """Instrument resolution failed."""


class ProviderError(StocksError):
    """Provider returned an error or unusable payload."""


class ProviderExhaustedError(ProviderError):
    """All configured providers failed."""


class ReportBuildError(StocksError):
    """Report building failed after retries/fallbacks."""


class FinancialMemoryError(StocksError):
    """Financial memory read/write failed."""


class AssetUpdateError(StocksError):
    """Asset update input is invalid or incomplete."""
