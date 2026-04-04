from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass
from typing import Any

from src.auth import AuthContext


@dataclass
class RequestScopedState:
    auth: AuthContext
    service: Any
    google_calendar: Any


_state_var: ContextVar[RequestScopedState | None] = ContextVar("request_scoped_state", default=None)


def set_request_state(state: RequestScopedState) -> Token:
    return _state_var.set(state)


def reset_request_state(token: Token) -> None:
    _state_var.reset(token)


def current_request_state() -> RequestScopedState:
    state = _state_var.get()
    if state is None:
        raise RuntimeError("Request-scoped service is not available outside a request context.")
    return state


class ServiceProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(current_request_state().service, name)


class GoogleCalendarProxy:
    def __getattr__(self, name: str) -> Any:
        return getattr(current_request_state().google_calendar, name)
