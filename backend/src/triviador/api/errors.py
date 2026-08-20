"""`ApiErrorCode`, `ApiError`, and the handlers that leave no other exit.

Registering a handler for bare `Exception` is what makes this total.
Without it Starlette's `ServerErrorMiddleware` emits `Internal Server
Error` as `text/plain`, and the frontend's `apiFetch` — which parses every
body — would classify a real application failure as a transport error
(§6.3), losing the only fact that distinguishes "the backend answered"
from "the backend was never reached".
"""

import logging
from contextvars import ContextVar
from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from starlette.exceptions import HTTPException as StarletteHTTPException

from triviador.domain.game.actions import RejectCode, RejectedCommand
from triviador.runtime.errors import (
    GameRecovering,
    GameUnrecoverable,
    RuntimeClosed,
    ServerBusy,
    ServerRestarting,
)

logger = logging.getLogger(__name__)

# Set by the request-id middleware (Task 4). Declared here because the 500
# handler is the one place that *must* be able to read it even when every
# other part of the request failed, and a handler reaching into middleware
# state would invert the dependency.
request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class ApiErrorCode(StrEnum):
    """Every value here and every value in `RejectCode` share one namespace.
    The four `RuntimeCode` values are repeated verbatim rather than
    imported, so this enum is the single closed list codegen exports; a
    test asserts the two sets agree."""

    VALIDATION_FAILED = "validation_failed"
    UNAUTHENTICATED = "unauthenticated"
    FORBIDDEN = "forbidden"
    NOT_FOUND = "not_found"
    METHOD_NOT_ALLOWED = "method_not_allowed"
    PAYLOAD_TOO_LARGE = "payload_too_large"
    CREDENTIALS_INVALID = "credentials_invalid"
    INVITE_INVALID = "invite_invalid"
    USERNAME_TAKEN = "username_taken"
    MAP_UNKNOWN = "map_unknown"
    PRESET_UNKNOWN = "preset_unknown"
    MEDIA_REJECTED = "media_rejected"
    NO_DEFAULT_PRESET = "no_default_preset"
    SERVER_BUSY = "server_busy"
    SERVER_RESTARTING = "server_restarting"
    GAME_RECOVERING = "game_recovering"
    GAME_UNRECOVERABLE = "game_unrecoverable"
    DATABASE_UNAVAILABLE = "database_unavailable"
    INTERNAL_ERROR = "internal_error"


class ApiError(Exception):
    """A failure a route raises deliberately, already carrying its status."""

    def __init__(
        self,
        code: ApiErrorCode,
        status: int,
        message: str,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.status = status
        self.message = message
        self.details = details


_STATUS_CODES: dict[int, ApiErrorCode] = {
    401: ApiErrorCode.UNAUTHENTICATED,
    403: ApiErrorCode.FORBIDDEN,
    404: ApiErrorCode.NOT_FOUND,
    405: ApiErrorCode.METHOD_NOT_ALLOWED,
    413: ApiErrorCode.PAYLOAD_TOO_LARGE,
    415: ApiErrorCode.MEDIA_REJECTED,
}

_TEMPORARY: dict[type[Exception], ApiErrorCode] = {
    ServerBusy: ApiErrorCode.SERVER_BUSY,
    ServerRestarting: ApiErrorCode.SERVER_RESTARTING,
    GameRecovering: ApiErrorCode.GAME_RECOVERING,
    GameUnrecoverable: ApiErrorCode.GAME_UNRECOVERABLE,
    # A runtime that closed under a caller is a retry, not a failure: the
    # caller re-`get()`s the game (§5.6). REST has no way to express "try
    # again immediately" other than 503, and the client's own backoff is
    # already correct for it.
    RuntimeClosed: ApiErrorCode.SERVER_BUSY,
}


def envelope(
    status: int,
    code: ApiErrorCode | RejectCode,
    message: str,
    details: dict[str, Any] | None = None,
) -> JSONResponse:
    body: dict[str, Any] = {"code": str(code), "message": message}
    if details is not None:
        body["details"] = details
    response = JSONResponse(status_code=status, content=body)
    # Set here, not only in the middleware: the 500 body carries the id in
    # `details`, and the two must agree even for a response the middleware
    # never gets to touch. Reads `"-"` until Task 4 installs the middleware
    # that sets the ContextVar — at which point both sides become a real
    # id together.
    response.headers["X-Request-Id"] = request_id_var.get()
    return response


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return envelope(exc.status, exc.code, exc.message, exc.details)

    @app.exception_handler(RejectedCommand)
    async def _rejected(_: Request, exc: RejectedCommand) -> JSONResponse:
        # §6.3: "one case among these, not the privileged one."
        return envelope(409, exc.code, exc.message)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        # `loc` and `type` only. Pydantic's own entries carry `input`, which
        # on a login body is the password and on a command frame is the
        # answer — both of which §10.10 forbids emitting.
        fields = [
            {"loc": ".".join(str(p) for p in error["loc"]), "type": error["type"]}
            for error in exc.errors()
        ]
        return envelope(
            422, ApiErrorCode.VALIDATION_FAILED, "request failed validation", {"fields": fields}
        )

    @app.exception_handler(SQLAlchemyError)
    async def _database(_: Request, exc: SQLAlchemyError) -> JSONResponse:
        logger.error("database unavailable: %s", type(exc).__name__)
        return envelope(503, ApiErrorCode.DATABASE_UNAVAILABLE, "database unavailable")

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _STATUS_CODES.get(exc.status_code, ApiErrorCode.INTERNAL_ERROR)
        # `exc.detail` is ours or Starlette's ("Not Found"), never an
        # exception message, so it is safe to pass through.
        return envelope(exc.status_code, code, str(exc.detail))

    for exc_type, api_code in _TEMPORARY.items():
        _install_temporary(app, exc_type, api_code)

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        # The full exception goes to the log; the body gets a stable code, a
        # generic message, and the id that ties the two together (§6.3).
        request_id = request_id_var.get()
        logger.exception("unhandled exception (request_id=%s)", request_id)
        return envelope(
            500, ApiErrorCode.INTERNAL_ERROR, "internal error", {"request_id": request_id}
        )


def _install_temporary(app: FastAPI, exc_type: type[Exception], code: ApiErrorCode) -> None:
    """A closure per type, so `code` is bound at registration rather than
    read from a loop variable when the handler eventually runs."""

    @app.exception_handler(exc_type)
    async def _handler(_: Request, exc: Exception) -> JSONResponse:
        return envelope(503, code, str(exc))
