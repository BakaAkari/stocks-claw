import unittest
from datetime import datetime
from pathlib import Path
from unittest.mock import patch, MagicMock

from stocks.services.event_log_service import EventLogService, EventEntry


class EventLogServiceTest(unittest.TestCase):
    def setUp(self):
        self.service = EventLogService()
        self.service.events_dir = Path('/tmp/test_events')
        self.service.events_dir.mkdir(parents=True, exist_ok=True)
    
    def tearDown(self):
        # Clean up test files
        import shutil
        if self.service.events_dir.exists():
            shutil.rmtree(self.service.events_dir)

    def test_extract_key_topics(self):
        service = EventLogService()
        text = '黄金仓位过高，NVDA承压，建议观望'
        topics = service._extract_key_topics(text)
        self.assertIn('黄金仓位', topics)
        self.assertIn('NVDA', topics)
        self.assertIn('观望建议', topics)

    def test_generate_fingerprint(self):
        service = EventLogService()
        text = '测试报告内容'
        fp1 = service._generate_fingerprint(text)
        fp2 = service._generate_fingerprint(text)
        self.assertEqual(fp1, fp2)
        self.assertEqual(len(fp1), 16)

    def test_generate_summary(self):
        service = EventLogService()
        text = '核心结论：你的组合当前处于防守状态。\n\n详细分析...'
        summary = service._generate_summary(text)
        self.assertIn('核心结论', summary)

    def test_log_and_retrieve(self):
        service = EventLogService()
        # Use a temp directory
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            service.events_dir = Path(tmpdir)
            
            report_text = '黄金仓位过高，NVDA承压'
            entry = service.log_report_generated(report_text)
            
            self.assertEqual(entry.event_type, 'report_generated')
            self.assertIn('黄金仓位', entry.key_topics)
            
            # Retrieve
            events = service.get_today_events()
            self.assertEqual(len(events), 1)
            self.assertEqual(events[0].event_type, 'report_generated')

    def test_get_today_topics(self):
        service = EventLogService()
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            service.events_dir = Path(tmpdir)
            
            service.log_report_generated('黄金仓位过高')
            service.log_report_generated('NVDA承压')
            
            topics = service.get_today_topics()
            self.assertIn('黄金仓位', topics)
            self.assertIn('NVDA', topics)

    def test_has_topic_been_mentioned(self):
        service = EventLogService()
        import tempfile
        with tempfile.TemporaryDirectory() as tmpdir:
            service.events_dir = Path(tmpdir)
            
            service.log_report_generated('黄金仓位过高')
            
            self.assertTrue(service.has_topic_been_mentioned('黄金仓位'))
            self.assertFalse(service.has_topic_been_mentioned('AAPL'))


if __name__ == '__main__':
    unittest.main()
