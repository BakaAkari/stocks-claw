"""Tests for human_readable_scan.scan_run security scanning."""

from __future__ import annotations

from scripts.human_readable_scan import scan_run


def test_scan_catches_position_id_and_anomaly_code_in_user_view():
    run = {
        "portfolio_decision": {
            "user_view": {
                "instruction_card": {
                    "actions": [{"display_label": "\u5316\u5de5ETF\uff08516020\uff09"}],
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
                    "status_label": "\u9700\u8981\u64cd\u4f5c",
                    "actions": [{
                        "display_label": "\u5316\u5de5ETF\uff08516020\uff09", "action_label": "\u51cf\u4ed3",
                        "ratio": 0.25, "estimated_amount_cny": 4200.0,
                        "amount_is_estimate": True,
                    }],
                    "no_action_reasons": [],
                },
                "assistant_brief": {
                    "why": ["\u8d8b\u52bf\u8d70\u5f31\uff0c\u51cf\u4ed3\u9632\u5fa1"],
                    "do_not_do": ["\u52a0\u4ed3\uff1a\u5f53\u524d\u98ce\u9669\u6682\u505c"],
                    "cash": {"immediate": {"label": "\u73b0\u5728\u80fd\u7528", "amount_cny": 1000}},
                    "risk": {"label": "\u9632\u5fa1\u72b6\u6001", "transition": "\u98ce\u9669\u5347\u7ea7"},
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
                "instruction_card": {"status_label": "\u4eca\u65e5\u65e0\u9700\u64cd\u4f5c", "actions": [], "no_action_reasons": []},
                "assistant_brief": {"why": [], "do_not_do": [], "cash": {}, "risk": {}, "research": []},
            }
        },
    }
    errors = scan_run(run, "test.json", rendered_markdown="**cn_pre_open \u00b7 2026-07-17**")
    assert any("cn_pre_open" in error for error in errors)



def test_scan_catches_internal_ids_in_outlook_fields():
    """scan_run traverses outlook and outlook_delta fields."""
    run = {
        "portfolio_decision": {
            "user_view": {
                "instruction_card": {"actions": [], "no_action_reasons": []},
                "assistant_brief": {
                    "why": [],
                    "do_not_do": [],
                    "cash": {},
                    "risk": {},
                    "research": [],
                    "outlook": {
                        "summary": "a_516020 internal-id-leak",
                        "near_term": {"summary": "test"},
                        "medium_term": {"summary": "test"},
                    },
                },
            }
        }
    }
    errors = scan_run(run, "test.json")
    assert any("a_516020" in e for e in errors)


def test_scan_allows_valid_outlook_text():
    """Valid outlook narrative passes scan."""
    run = {
        "portfolio_decision": {
            "user_view": {
                "instruction_card": {"actions": [], "no_action_reasons": []},
                "assistant_brief": {
                    "why": [],
                    "do_not_do": [],
                    "cash": {},
                    "risk": {},
                    "research": [],
                    "outlook": {
                        "summary": "\u673a\u4f1a\u4e0e\u98ce\u9669\u5e76\u5b58",
                        "near_term": {"summary": "\u77ed\u671f\u9707\u8361"},
                        "medium_term": {"summary": "\u4e2d\u671f\u770b\u597d"},
                    },
                },
            }
        }
    }
    errors = scan_run(run, "test.json")
    assert errors == []


def test_scan_traverses_outlook_delta():
    """scan_run traverses outlook_delta fields."""
    run = {
        "portfolio_decision": {
            "user_view": {
                "instruction_card": {"actions": [], "no_action_reasons": []},
                "assistant_brief": {
                    "why": [],
                    "do_not_do": [],
                    "cash": {},
                    "risk": {},
                    "research": [],
                    "outlook_delta": {
                        "summary": "\u65b9\u5411\u53d8\u5316",
                        "changed_sectors": ["a_516020 internal code"],
                    },
                },
            }
        }
    }
    errors = scan_run(run, "test.json")
    assert any("a_516020" in e for e in errors)


def test_scan_catches_position_id_in_outlook_keys():
    """scan_run detects position_id/decision_id as dict keys in outlook."""
    run = {
        "portfolio_decision": {
            "user_view": {
                "instruction_card": {"actions": [], "no_action_reasons": []},
                "assistant_brief": {
                    "why": [], "do_not_do": [], "cash": {}, "risk": {}, "research": [],
                    "outlook": {
                        "position_id": "cn_588000",
                        "summary": "\u6d4b\u8bd5",
                    },
                },
            }
        }
    }
    errors = scan_run(run, "test.json")
    assert any("position_id" in e for e in errors)


def test_scan_catches_decision_id_in_keys():
    """scan_run detects decision_id as a dict key."""
    run = {
        "portfolio_decision": {
            "user_view": {
                "instruction_card": {"actions": [], "no_action_reasons": []},
                "assistant_brief": {
                    "why": [], "do_not_do": [], "cash": {}, "risk": {}, "research": [],
                    "outlook": {
                        "decision_id": "decision-abc123",
                        "summary": "\u6d4b\u8bd5",
                    },
                },
            }
        }
    }
    errors = scan_run(run, "test.json")
    assert any("decision_id" in e for e in errors)


def test_scan_catches_horizon_internal_token():
    """scan_run detects internal tokens in horizon field values."""
    run = {
        "portfolio_decision": {
            "user_view": {
                "instruction_card": {"actions": [], "no_action_reasons": []},
                "assistant_brief": {
                    "why": [], "do_not_do": [], "cash": {}, "risk": {}, "research": [],
                    "outlook": {
                        "summary": "\u6d4b\u8bd5",
                        "near_term": {"horizon": "a_secret"},
                    },
                },
            }
        }
    }
    errors = scan_run(run, "test.json")
    assert any("a_secret" in e for e in errors)


def test_scan_allows_legitimate_horizon_values():
    """scan_run allows normal horizon values."""
    run = {
        "portfolio_decision": {
            "user_view": {
                "instruction_card": {"actions": [], "no_action_reasons": []},
                "assistant_brief": {
                    "why": [], "do_not_do": [], "cash": {}, "risk": {}, "research": [],
                    "outlook": {
                        "summary": "\u6d4b\u8bd5",
                        "near_term": {"horizon": "1-2w", "direction": "supportive"},
                    },
                },
            }
        }
    }
    errors = scan_run(run, "test.json")
    assert errors == []


def test_scan_chinese_regex_matches_multiple_digits():
    """Chinese trade regex matches \u6b62\u635f/\u6b62\u76c8 with multiple digits."""
    run = {
        "portfolio_decision": {
            "user_view": {
                "instruction_card": {"actions": [], "no_action_reasons": []},
                "assistant_brief": {
                    "why": [], "do_not_do": [], "cash": {}, "risk": {}, "research": [],
                    "outlook": {
                        "summary": "\u5efa\u8bae\u6b62\u635f15%",
                    },
                },
            }
        }
    }
    errors = scan_run(run, "test.json")
    assert any("\u6b62\u635f15" in e for e in errors)
