import json
from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from stocks.engine.macro_data import ChinaMacroAdapter
from stocks.providers.cn_macro import CnMacroProvider, CnMacroSnapshot, _parse_date


def test_real_eastmoney_field_schema_and_query():
    provider = CnMacroProvider()
    with patch.object(provider, "_request_json", return_value={"result": {"data": [{"MAKE_INDEX": 50.1, "REPORT_DATE": "2026-09-01 00:00:00"}]}}) as request:
        assert provider._fetch_pmi() == (50.1, "2026-09-01")
    assert "RPT_ECONOMY_PMI" in request.call_args.args[0]
    assert "columns=ALL" in request.call_args.args[0]
    with patch.object(provider, "_request_json", return_value={"result": {"data": [{"LPR1Y": 3, "LPR5Y": 3.5, "TRADE_DATE": "2026-09-20"}]}}) as request:
        assert provider._fetch_lpr() == ((3, 3.5), "2026-09-20")
    assert "sortColumns=TRADE_DATE" in request.call_args.args[0]


def test_eastmoney_api_error_not_a_valid_empty_result():
    with patch.object(CnMacroProvider, "_request_json", return_value={"success": False, "result": None}):
        with pytest.raises(ValueError, match="missing_result"):
            CnMacroProvider()._fetch_cpi()


def test_social_financing_source_period_and_sort():
    response = MagicMock()
    response.__enter__.return_value.read.return_value = json.dumps([
        {"date": "202601", "tiosfs": 100}, {"date": "202609", "tiosfs": 200}]).encode()
    with patch("urllib.request.urlopen", return_value=response):
        assert CnMacroProvider()._fetch_social_financing() == (200, "2026-09-01")


def test_safe_parity_uses_source_date_and_per_hundred_unit():
    response = MagicMock()
    response.__enter__.return_value.read.return_value = '<table><tr><th>日期</th><th>美元</th></tr><tr><td>2026-10-19</td><td>672.08</td></tr></table>'.encode()
    with patch("urllib.request.urlopen", return_value=response) as request:
        assert CnMacroProvider()._fetch_usd_cny_mid() == (6.7208, "2026-10-19")
    assert "safe.gov.cn" in request.call_args.args[0].full_url
    assert _parse_date("2026-09-01 00:00:00") == "2026-09-01"


async def test_cn_adapter_never_sends_old_social_financing_as_current():
    today = datetime.now(timezone.utc).date().isoformat()
    snapshot = CnMacroSnapshot(pmi=50, social_financing=100,
        field_sources={"pmi": {"source": "eastmoney", "as_of": today},
                       "social_financing": {"source": "mofcom", "as_of": "2020-01-01"}})
    with patch.object(CnMacroProvider, "fetch", return_value=snapshot):
        result = await ChinaMacroAdapter().fetch()
    assert result.official_stats == {"cn_pmi": 50}
    assert "social_financing" in result.errors
