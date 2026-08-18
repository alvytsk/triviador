"""structlog to stdout, one request id per request, and a redactor that
works on keys.

The redactor is a structlog *processor*, not a call-site discipline. A
discipline is a thing every future caller has to remember, and the one who
forgets is logging an exception payload at three in the morning.
"""

import logging
import sys
import uuid
from collections.abc import MutableMapping
from typing import Any, Literal

import structlog
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from triviador.api.errors import request_id_var

REDACTED = "[redacted]"

# Spec 1B §10.10's list, as keys. Deliberately broad — `value` and `answer`
# catch a submitted answer wherever it is nested, `code` catches an invite
# code, and the cost of over-redacting a field that happened to be named
# `value` is a log line that says `[redacted]`.
REDACTED_KEYS = frozenset(
    {
        "password",
        "password_hash",
        "new_password",
        "token",
        "token_hash",
        "session_token",
        "access_token",
        "cookie",
        "set-cookie",
        "authorization",
        "code",
        "invite_code",
        "code_hash",
        "answer",
        "answers",
        "value",
        "correct_value",
        "correct_choice_index",
        "payload",
        "frames",
        "body",
        "s3_access_key_id",
        "s3_secret_access_key",
        "garage_rpc_secret",
        "postgres_password",
        "database_url",
    }
)


def _redact(value: Any, depth: int = 0) -> Any:
    # Bounded: a log event is a dict, not a graph, and an unbounded walk
    # over a value someone accidentally logged is a way to hang the logger.
    if depth > 6:
        return REDACTED
    if isinstance(value, dict):
        return {
            k: REDACTED if k.lower() in REDACTED_KEYS else _redact(v, depth + 1)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        return [_redact(v, depth + 1) for v in value]
    return value


def redact_processor(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> dict[str, Any]:
    return _redact(event_dict)  # type: ignore[no-any-return]


def _add_request_id(
    _logger: Any, _name: str, event_dict: MutableMapping[str, Any]
) -> MutableMapping[str, Any]:
    event_dict["request_id"] = request_id_var.get()
    return event_dict


def configure_logging(*, log_level: str, log_format: Literal["json", "console"]) -> None:
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if log_format == "json"
        else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            _add_request_id,
            # Last before rendering: everything above may add fields, and a
            # redactor that runs early cannot see them.
            redact_processor,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping()[log_level.upper()]
        ),
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=False,
    )
    # The stdlib loggers `runtime/` and `db/` already use are routed to the
    # same stream, so a quarantine logged by `GameManager` and a request
    # logged here end up in one stdout stream (§10.10).
    logging.basicConfig(format="%(message)s", stream=sys.stdout, level=log_level.upper())


class RequestContextMiddleware:
    """One id per request, generated here and never read from the request.

    Pure ASGI, outermost, and it **does not reset the ContextVar**. Both
    are deliberate, and each fixes a way the id would otherwise be missing
    from exactly the responses that need it most:

    1. **No reset.** Starlette's `ServerErrorMiddleware` — the thing that
       runs the 500 handler — sits *outside* every user middleware. A
       `finally: request_id_var.reset(token)` runs while the exception is
       still unwinding, so by the time the 500 body is built the id is
       gone and §6.3's "a 500 body carries the request id" is a comment.
       Setting without resetting is safe because the var is overwritten at
       the top of every request; a connection serving keep-alive requests
       on one task sees the new id, never a stale one.
    2. **Outermost.** The origin, body-limit and host checks all answer
       without reaching a route. Registered inside them, this would hand
       out ids for successful requests and none for refused ones — the
       opposite of useful.

    A client-supplied `X-Request-Id` is ignored: echoing one back would let
    a caller collide two unrelated requests in the log, or inject a newline
    into a line-oriented stream.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return

        request_id = uuid.uuid4().hex
        request_id_var.set(request_id)
        # Also on the scope, so a handler can read it without depending on
        # context propagation across a task boundary.
        scope.setdefault("state", {})["request_id"] = request_id
        if scope["type"] == "websocket":
            await self.app(scope, receive, send)
            return

        status = 500

        async def send_with_id(message: Message) -> None:
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                headers = list(message.get("headers", []))
                if not any(k.lower() == b"x-request-id" for k, _ in headers):
                    headers.append((b"x-request-id", request_id.encode("latin-1")))
                message = {**message, "headers": headers}
            await send(message)

        try:
            await self.app(scope, receive, send_with_id)
        finally:
            # In the `finally` so a request that raised past every handler
            # is still logged — with its id, which is the only thread back
            # to the traceback the 500 handler wrote.
            structlog.get_logger().info(
                "request",
                method=scope.get("method"),
                path=scope.get("path"),
                status=status,
            )
