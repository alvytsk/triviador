"""§10.10: which fields are emitted, not which bytes appear.

Byte-scanning is the wrong test and §12.3 says so: an MC question's correct
answer is legitimate choice text, and a numeric answer can coincide with a
number in the prompt. So the guarantee is that the *keys* never leave.
"""

import json
import logging
from typing import Any

import httpx
import pytest
import structlog
from fastapi import APIRouter, FastAPI

from triviador.api.errors import install_error_handlers, request_id_var
from triviador.api.logging import REDACTED_KEYS, RequestContextMiddleware, configure_logging


@pytest.fixture(autouse=True)
def json_logging(caplog: pytest.LogCaptureFixture) -> None:
    configure_logging(log_level="INFO", log_format="json")
    # httpx's own client logs "HTTP Request: ..." at INFO and would
    # otherwise propagate to root like everything else once the level
    # below opens the gate; it is not one of our JSON events and would
    # break `emitted()`'s blanket `json.loads` over every captured record.
    # `caplog.set_level` also resets the shared handler's own level on
    # every call it makes, regardless of which logger it targets — so the
    # call that must win (root, at INFO) has to run last.
    caplog.set_level(logging.WARNING, logger="httpx")
    caplog.set_level(logging.INFO)


def emitted(caplog: pytest.LogCaptureFixture) -> list[dict[str, Any]]:
    # `record.message` is only populated once a `Formatter` has run over the
    # record; on the raw `LogRecord` objects `caplog` collects it may not be
    # set at all. `record.getMessage()` is always available.
    #
    # Typed `Any` rather than `object`: this is arbitrarily nested decoded
    # JSON, and the nested-redaction test needs to subscript into it.
    return [json.loads(record.getMessage()) for record in caplog.records]


@pytest.mark.parametrize("key", sorted(REDACTED_KEYS))
def test_every_forbidden_key_is_replaced_rather_than_emitted(
    caplog: pytest.LogCaptureFixture, key: str
) -> None:
    structlog.get_logger().info("probe", **{key: "hunter2"})
    (event,) = emitted(caplog)
    assert event[key] == "[redacted]"
    assert "hunter2" not in json.dumps(event)


def test_redaction_reaches_into_nested_structures(caplog: pytest.LogCaptureFixture) -> None:
    """A command frame arrives as one nested object; logging it whole is
    exactly the mistake §10.10 forbids, and a top-level-only redactor would
    not notice."""
    structlog.get_logger().info("probe", frame={"type": "submit_answer", "payload": {"value": 42}})
    (event,) = emitted(caplog)
    assert event["frame"]["payload"] == "[redacted]"


def test_the_forbidden_keys_cover_every_category_10_10_names() -> None:
    for key in (
        "password",
        "password_hash",
        "token",
        "token_hash",
        "cookie",
        "authorization",
        "code",
        "invite_code",
        "answer",
        "value",
        "payload",
        "s3_secret_access_key",
    ):
        assert key in REDACTED_KEYS


async def test_every_request_gets_an_id_that_reaches_both_the_log_and_the_header(
    caplog: pytest.LogCaptureFixture,
) -> None:
    app = FastAPI()
    router = APIRouter()

    @router.get("/ok")
    async def ok() -> dict[str, str]:
        structlog.get_logger().info("in-handler")
        return {"ok": "yes"}

    app.include_router(router)
    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.get("/ok")

    header = response.headers["x-request-id"]
    assert header
    in_handler = next(e for e in emitted(caplog) if e["event"] == "in-handler")
    assert in_handler["request_id"] == header


async def test_the_id_survives_an_unhandled_exception(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """§6.3 puts the request id in the 500 body so an operator can find the
    traceback. `ServerErrorMiddleware` builds that body *outside* every
    user middleware, so a ContextVar reset on the way out would leave the
    body carrying `"-"` — a value that matches nothing in any log."""
    app = FastAPI()

    @app.get("/boom")
    async def boom() -> None:
        raise RuntimeError("kaboom")

    app.add_middleware(RequestContextMiddleware)
    install_error_handlers(app)

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.get("/boom")

    assert response.status_code == 500
    request_id = response.json()["details"]["request_id"]
    assert request_id != "-"
    assert request_id == response.headers["x-request-id"]


async def test_a_client_supplied_request_id_is_not_trusted(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """An id echoed back from the request would let a client collide two
    unrelated requests in the log, or inject newlines into it."""
    app = FastAPI()
    app.add_middleware(RequestContextMiddleware)

    @app.get("/ok")
    async def ok() -> dict[str, str]:
        return {"id": request_id_var.get()}

    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://t") as client:
        response = await client.get("/ok", headers={"X-Request-Id": "spoofed\nINJECTED"})
    assert response.json()["id"] != "spoofed\nINJECTED"
