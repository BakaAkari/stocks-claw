from __future__ import annotations

from dataclasses import dataclass

from stocks.logging_utils import log_event
from stocks.services.command_service import CommandService, CommandResult


@dataclass(frozen=True)
class ChatRouteResult:
    handled: bool
    response: str | None = None
    reason: str | None = None
    command: CommandResult | None = None


class ChatRouterService:
    def __init__(self, command_service: CommandService | None = None):
        self.command_service = command_service or CommandService()

    def route(self, text: str) -> ChatRouteResult:
        raw = (text or '').strip()
        if not raw:
            log_event('chat_router.ignored', reason='empty')
            return ChatRouteResult(handled=False, reason='empty')

        command_result = self.command_service.handle(raw)
        if command_result is None:
            log_event('chat_router.ignored', reason='not_stock_command', text=raw)
            return ChatRouteResult(handled=False, reason='not_stock_command')

        log_event(
            'chat_router.handled',
            kind=command_result.kind,
            market=command_result.market_key,
        )
        return ChatRouteResult(
            handled=True,
            response=command_result.content,
            reason='handled',
            command=command_result,
        )
