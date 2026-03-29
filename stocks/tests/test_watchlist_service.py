import unittest

from stocks.services.watchlist_service import WatchlistService


class WatchlistServiceTest(unittest.TestCase):
    def test_build_market_targets_contains_user_key_assets(self):
        service = WatchlistService()
        targets = service.build_market_targets()
        self.assertIn('user_key_assets', targets)
        pairs = set(targets['user_key_assets'])
        self.assertIn(('us', 'NVDA'), pairs)
        self.assertIn(('us', 'AAPL'), pairs)
        self.assertIn(('a', '601899'), pairs)


if __name__ == '__main__':
    unittest.main()
