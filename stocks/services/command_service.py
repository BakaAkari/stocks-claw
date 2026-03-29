from __future__ import annotations

from dataclasses import dataclass

from stocks.config_loader import normalize_market
from stocks.logging_utils import log_event
from stocks.services.personal_llm_report_service import PersonalLLMReportService
from stocks.services.query_service import QueryService
from stocks.services.resolver_service import InstrumentResolver


def render_quote_text(quote) -> str:
    sign = '+' if (quote.pct_change or 0) > 0 else ''
    return (
        f"{quote.instrument.name} ({quote.instrument.code})\n"
        f"最新价：{quote.price}\n"
        f"涨跌额：{quote.change}\n"
        f"涨跌幅：{sign}{quote.pct_change}%"
    )


@dataclass(frozen=True)
class CommandResult:
    kind: str
    market_key: str
    content: str


class CommandService:
    def __init__(
        self,
        query_service: QueryService | None = None,
        personal_report_service: PersonalLLMReportService | None = None,
    ):
        self.query_service = query_service or QueryService()
        self.personal_report_service = personal_report_service or PersonalLLMReportService()

    def handle(self, text: str) -> CommandResult | None:
        raw = (text or '').strip()
        if not raw:
            return None

        log_event('command.received', text=raw)

        personal_report_result = self._handle_personal_report(raw)
        if personal_report_result is not None:
            log_event('command.matched', kind='personal_report', market=personal_report_result.market_key)
            return personal_report_result

        query_result = self._handle_query(raw)
        if query_result is not None:
            log_event('command.matched', kind='query', market=query_result.market_key)
            return query_result

        log_event('command.ignored', text=raw)
        return None

    def _handle_personal_report(self, text: str) -> CommandResult | None:
        normalized = text.replace(' ', '')
        if normalized not in ('个人简报', '我的简报', '个人研报', '我的研报'):
            return None
        content = self.personal_report_service.generate()
        return CommandResult(kind='personal_report', market_key='personal', content=content)

    def _handle_query(self, text: str) -> CommandResult | None:
        if not text.startswith('查'):
            return None

        body = text[1:].strip()
        if not body:
            return None

        for market_text in ('A股', 'a股', '美股'):
            if body.startswith(market_text):
                keyword = body[len(market_text):].strip()
                if not keyword:
                    return None
                market_key = normalize_market(market_text)
                quote = self.query_service.query(market_key, keyword)
                return CommandResult(kind='query', market_key=market_key, content=render_quote_text(quote))
        return None

        for market_text in ('A股', 'a股', '美股'):
            if body.startswith(market_text):
                keyword = body[len(market_text):].strip()
                if not keyword:
                    return None
                market_key = normalize_market(market_text)
                quote = self.query_service.query(market_key, keyword)
                return CommandResult(kind='query', market_key=market_key, content=render_quote_text(quote))
        return None
