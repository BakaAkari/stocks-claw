"""结构化建议 actions 校验测试。"""

from __future__ import annotations

import pytest

from stocks.domain.models import AdviceRecord


def _payload(**overrides) -> dict:
    payload = {
        "instruments": [{"market": "a", "code": "588000", "name": "科创50ETF"}],
        "direction": {"a:588000": "watch"},
        "rationale_summary": "等待回踩确认后再分批处理。",
        "based_on": ["quotes", "portfolio"],
        "boundary": [{"type": "fact", "text": "现金层偏高"}],
        "actions": [
            {
                "target": "a:588000",
                "action": "increase",
                "size_hint": "5%~8%",
                "trigger": "回踩20日线后重新转强",
                "invalidation": "跌破前低",
                "horizon": "short",
            }
        ],
    }
    payload.update(overrides)
    return payload


def test_legacy_record_without_actions_loads_as_empty_list():
    record = AdviceRecord.from_dict({
        "created_at": "2026-07-03T00:00:00+00:00",
        "instruments": [{"market": "a", "code": "588000", "name": "科创50ETF"}],
        "direction": {"a:588000": "watch"},
        "rationale_summary": "旧记录",
        "based_on": ["quotes"],
        "boundary": [{"type": "fact", "text": "旧事实"}],
    })

    assert record.actions == []
    assert record.to_dict()["actions"] == []


def test_valid_actions_round_trip():
    record = AdviceRecord.create(**_payload())
    restored = AdviceRecord.from_dict(record.to_dict())

    assert restored.actions == record.actions
    assert restored.actions[0]["size_hint"] == "5%~8%"


@pytest.mark.parametrize("size_hint", ["¥12,000", "$3,400", "12000元"])
def test_actions_reject_exact_currency_amounts(size_hint):
    action = _payload()["actions"][0] | {"size_hint": size_hint}

    with pytest.raises(ValueError, match="exact currency amounts"):
        AdviceRecord.create(**_payload(actions=[action]))
