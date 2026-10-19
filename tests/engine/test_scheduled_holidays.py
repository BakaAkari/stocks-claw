import json
from datetime import date

from stocks.engine.scheduled_analysis import MarketSessionCalendar, load_scheduled_config


def _write_config(tmp_path, cn_holidays):
    config = {
        "schema_version": 1,
        "user_timezone": "Asia/Shanghai",
        "markets": {
            "cn": {"exchange_timezone": "Asia/Shanghai", "holidays": cn_holidays,
                   "sessions": [{"id": "cn_after_close", "time": "15:00"}]},
            "us": {"exchange_timezone": "America/New_York", "holidays": [],
                   "sessions": [{"id": "us_after_close", "time": "16:00"}]},
        },
    }
    (tmp_path / "scheduled_sessions.json").write_text(json.dumps(config), encoding="utf-8")


def test_exchange_calendar_holidays_are_merged_with_configured(tmp_path):
    _write_config(tmp_path, ["2099-01-05"])
    config = load_scheduled_config(tmp_path)
    cn = config["markets"]["cn"]
    us = config["markets"]["us"]
    assert "2099-01-05" in cn["holidays"]          # 手填保留
    assert "2026-01-02" in cn["holidays"]          # 库中有、旧配置漏填
    assert "2026-11-26" in us["holidays"]          # 感恩节
    assert cn["holidays_covered_until"] >= "2026-12-31"

    calendar = MarketSessionCalendar(config)
    session = calendar.find_session("us_after_close")
    assert calendar._is_market_date(session, date(2026, 11, 26)) is False
    assert calendar._is_market_date(session, date(2026, 11, 27)) is True


def test_dates_beyond_library_coverage_warn(tmp_path, caplog):
    _write_config(tmp_path, [])
    calendar = MarketSessionCalendar(load_scheduled_config(tmp_path))
    session = calendar.find_session("cn_after_close")
    with caplog.at_level("WARNING"):
        assert calendar._is_market_date(session, date(2099, 1, 5)) is True
    assert "only covers through" in caplog.text
