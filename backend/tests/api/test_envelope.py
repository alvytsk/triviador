"""§6.3: every source of failure leaves through one envelope.

The row that matters is the last one. Starlette emits its own shapes for
404, 405 and unhandled 500s, and those would reach the frontend's Zod
boundary as unparseable bodies — at which point the client cannot tell an
application error from a proxy being down, which is the one distinction
`apiFetch`'s transport error exists to preserve.
"""

from collections.abc import AsyncIterator
from typing import Any

import httpx
import pytest
import pytest_asyncio
from fastapi import APIRouter, FastAPI
from pydantic import BaseModel
from sqlalchemy.exc import OperationalError

from triviador.api.errors import ApiError, ApiErrorCode, install_error_handlers
from triviador.domain.game.actions import RejectCode, RejectedCommand
from triviador.runtime.errors import GameRecovering, GameUnrecoverable, ServerBusy
from triviador.services.ports import RuntimeCode


class Body(BaseModel):
    name: str
    password: str


def probe_app() -> FastAPI:
    app = FastAPI()
    router = APIRouter()

    @router.post("/echo")
    async def echo(body: Body) -> dict[str, str]:
        return {"name": body.name}

    @router.get("/boom")
    async def boom() -> None:
        raise RuntimeError("connection to postgres://user:hunter2@db failed")

    @router.get("/rejected")
    async def rejected() -> None:
        raise RejectedCommand(RejectCode.NOT_ADJACENT, "'r7' is not adjacent")

    @router.get("/busy")
    async def busy() -> None:
        raise ServerBusy("queue is full")

    @router.get("/recovering")
    async def recovering() -> None:
        raise GameRecovering("game is recovering")

    @router.get("/unrecoverable")
    async def unrecoverable() -> None:
        raise GameUnrecoverable("stream will never decode")

    @router.get("/db-down")
    async def db_down() -> None:
        raise OperationalError("SELECT 1", {}, Exception("server closed the connection"))

    @router.get("/too-big")
    async def too_big() -> None:
        raise ApiError(ApiErrorCode.PAYLOAD_TOO_LARGE, 413, "body exceeds 1048576 bytes")

    app.include_router(router)
    install_error_handlers(app)
    return app


@pytest_asyncio.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    # `raise_app_exceptions=False`: Starlette's ServerErrorMiddleware calls
    # our 500 handler, sends its response, and then *re-raises* so a real
    # server can log the traceback. Without this the unhandled-exception
    # test would see the RuntimeError instead of the response the handler
    # produced — testing the raise, not the envelope.
    transport = httpx.ASGITransport(app=probe_app(), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as ac:
        yield ac


async def envelope(client: httpx.AsyncClient, method: str, path: str, **kw: Any) -> Any:
    response = await client.request(method, path, **kw)
    body = response.json()
    assert set(body) <= {"code", "message", "details"}, body
    assert isinstance(body["code"], str) and isinstance(body["message"], str)
    return response, body


async def test_the_two_code_enums_are_disjoint() -> None:
    """`code` is one closed union of `ApiErrorCode | RejectCode`. The moment
    a value appears in both, the union stops discriminating and a client's
    `switch` silently takes the wrong branch."""
    assert not ({c.value for c in ApiErrorCode} & {c.value for c in RejectCode})


async def test_every_runtime_code_is_also_an_api_error_code() -> None:
    """§6.3 maps all four to 503, so the envelope must be able to name them
    without inventing a parallel vocabulary."""
    for code in RuntimeCode:
        assert code.value in {c.value for c in ApiErrorCode}


async def test_a_validation_failure_is_422_and_never_echoes_the_input(
    client: httpx.AsyncClient,
) -> None:
    """Pydantic's own error list carries `input`. On a login body that is
    the password, and it would land in a response body and in whatever logs
    it — so the handler keeps `loc` and `type` and drops the rest."""
    response, body = await envelope(client, "POST", "/echo", json={"name": "n", "password": 5})
    assert response.status_code == 422
    assert body["code"] == ApiErrorCode.VALIDATION_FAILED
    assert "hunter" not in response.text
    assert body["details"] == {"fields": [{"loc": "body.password", "type": "string_type"}]}


async def test_a_missing_route_is_the_envelope_not_starlettes_shape(
    client: httpx.AsyncClient,
) -> None:
    response, body = await envelope(client, "GET", "/nope")
    assert response.status_code == 404
    assert body["code"] == ApiErrorCode.NOT_FOUND
    assert "detail" not in body


async def test_a_wrong_method_is_the_envelope(client: httpx.AsyncClient) -> None:
    response, body = await envelope(client, "DELETE", "/echo")
    assert response.status_code == 405
    assert body["code"] == ApiErrorCode.METHOD_NOT_ALLOWED


async def test_a_rejected_command_is_409_carrying_its_reject_code(
    client: httpx.AsyncClient,
) -> None:
    response, body = await envelope(client, "GET", "/rejected")
    assert response.status_code == 409
    assert body["code"] == RejectCode.NOT_ADJACENT


@pytest.mark.parametrize(
    ("path", "code"),
    [
        ("/busy", ApiErrorCode.SERVER_BUSY),
        ("/recovering", ApiErrorCode.GAME_RECOVERING),
        ("/unrecoverable", ApiErrorCode.GAME_UNRECOVERABLE),
        ("/db-down", ApiErrorCode.DATABASE_UNAVAILABLE),
    ],
)
async def test_every_temporary_condition_is_503_with_its_own_code(
    client: httpx.AsyncClient, path: str, code: ApiErrorCode
) -> None:
    response, body = await envelope(client, "GET", path)
    assert response.status_code == 503
    assert body["code"] == code


async def test_a_payload_too_large_is_413(client: httpx.AsyncClient) -> None:
    response, body = await envelope(client, "GET", "/too-big")
    assert response.status_code == 413
    assert body["code"] == ApiErrorCode.PAYLOAD_TOO_LARGE


async def test_an_unhandled_exception_is_500_and_carries_no_exception_text(
    client: httpx.AsyncClient,
) -> None:
    """The route raises a message containing a connection string with a
    password in it — the shape real exceptions actually have."""
    response, body = await envelope(client, "GET", "/boom")
    assert response.status_code == 500
    assert body["code"] == ApiErrorCode.INTERNAL_ERROR
    assert "hunter2" not in response.text
    assert "postgres" not in response.text
    assert "Traceback" not in response.text
    assert body["message"] == "internal error"


async def test_a_500_carries_the_request_id_so_the_log_can_be_found(
    client: httpx.AsyncClient,
) -> None:
    response, body = await envelope(client, "GET", "/boom")
    assert body["details"] is not None
    assert body["details"]["request_id"] == response.headers["x-request-id"]
