from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from stocks.services.provider_service import ProviderService


class FailProvider:
    def __init__(self):
        self.calls = 0

    def get_quote(self, instrument):
        self.calls += 1
        raise RuntimeError('boom')


class OkProvider:
    def __init__(self):
        self.calls = 0

    def get_quote(self, instrument):
        self.calls += 1
        return {'ok': True, 'instrument': instrument}


class FakeRegistry:
    def __init__(self):
        self.bad = FailProvider()
        self.good = OkProvider()

    def get_market_provider_names(self, market_key):
        return ['bad', 'good']

    def get(self, market_key, provider_name):
        if provider_name == 'bad':
            return self.bad
        if provider_name == 'good':
            return self.good
        raise KeyError(provider_name)


if __name__ == '__main__':
    registry = FakeRegistry()
    service = ProviderService(registry=registry, retries=2)
    result = service.first_success('a', lambda provider: provider.get_quote('601899'))
    assert result['ok'] is True
    assert registry.bad.calls == 2
    assert registry.good.calls == 1
    print('provider fallback ok')
