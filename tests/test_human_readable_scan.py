

from scripts.human_readable_scan import scan_run


def test_scan_catches_position_id_and_anomaly_code_in_user_view():
    run = {
        "portfolio_decision": {
            "user_view": {
                "instruction_card": {
                    "actions": [{"display_label": "化工ETF（516020）"}],
                    "no_action_reasons": [],
                },
                "assistant_brief": {
                    "do_not_do": ["a_516020 prev_close_mismatch"],
                },
            }
        }
    }
    errors = scan_run(run, "test.json")
    assert any("a_516020" in e for e in errors)
    assert any("prev_close_mismatch" in e for e in errors)


def test_scan_allows_real_chinese_reports():
    run = {
        "portfolio_decision": {
            "user_view": {
                "instruction_card": {
                    "status_label": "需要操作",
                    "actions": [{
                        "display_label": "化工ETF（516020）", "action_label": "减仓",
                        "ratio": 0.25, "estimated_amount_cny": 4200.0,
                        "amount_is_estimate": True,
                    }],
                    "no_action_reasons": [],
                },
                "assistant_brief": {
                    "why": ["趋势走弱，减仓防御"],
                    "do_not_do": ["加仓：当前风险暂停"],
                    "cash": {"immediate": {"label": "现在能用", "amount_cny": 1000}},
                    "risk": {"label": "防御状态", "transition": "风险升级"},
                    "research": [],
                },
            }
        }
    }
    errors = scan_run(run, "test.json")
    assert errors == []



def test_scan_catches_internal_session_id_in_rendered_markdown():
    run = {
        "session": "cn_pre_open",
        "market_date": "2026-07-17",
        "portfolio_decision": {
            "user_view": {
                "instruction_card": {"status_label": "今日无需操作", "actions": [], "no_action_reasons": []},
                "assistant_brief": {"why": [], "do_not_do": [], "cash": {}, "risk": {}, "research": []},
            }
        },
    }
    errors = scan_run(run, "test.json", rendered_markdown="**cn_pre_open · 2026-07-17**")
    assert any("cn_pre_open" in error for error in errors)
