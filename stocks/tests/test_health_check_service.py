import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

from stocks.services.health_check_service import HealthCheckService, HealthCheckResult


class HealthCheckServiceTest(unittest.TestCase):
    def test_check_freshness_file_not_exists(self):
        service = HealthCheckService()
        with patch.object(service, '_get_file_mtime', return_value=None):
            result = service._check_freshness('test', 'test.json', timedelta(minutes=10))
        self.assertEqual(result.status, 'error')
        self.assertIn('不存在', result.message)

    def test_check_freshness_stale(self):
        service = HealthCheckService()
        old_time = datetime.now() - timedelta(minutes=60)
        with patch.object(service, '_get_file_mtime', return_value=old_time):
            result = service._check_freshness('test', 'test.json', timedelta(minutes=30))
        self.assertEqual(result.status, 'warning')
        self.assertIn('已', result.message)
        self.assertIn('分钟未更新', result.message)

    def test_check_freshness_fresh(self):
        service = HealthCheckService()
        recent_time = datetime.now() - timedelta(minutes=10)
        with patch.object(service, '_get_file_mtime', return_value=recent_time):
            result = service._check_freshness('test', 'test.json', timedelta(minutes=30))
        self.assertEqual(result.status, 'ok')

    def test_has_issues_with_warnings(self):
        service = HealthCheckService()
        with patch.object(service, 'check_all', return_value=[
            HealthCheckResult('a', 'ok', ''),
            HealthCheckResult('b', 'warning', ''),
        ]):
            self.assertTrue(service.has_issues())

    def test_has_issues_all_ok(self):
        service = HealthCheckService()
        with patch.object(service, 'check_all', return_value=[
            HealthCheckResult('a', 'ok', ''),
            HealthCheckResult('b', 'ok', ''),
        ]):
            self.assertFalse(service.has_issues())


if __name__ == '__main__':
    unittest.main()
