"""§6.4: cookie auth with no CSRF token makes this load-bearing.

`SameSite=Lax` does not cover it on its own — a top-level POST navigation
from another site sends a Lax cookie — so an unsafe method with a foreign
or missing `Origin` is refused before it reaches a route.
"""

import json
from collections.abc import AsyncIterator

import httpx
import pytest
from starlette.types import Message, Receive, Scope, Send

from tests.api.conftest import ORIGIN
from triviador.api.deps import AppDependencies
from triviador.api.errors import ApiErrorCode
from triviador.api.middleware import HostMiddleware, origin_allowed


@pytest.mark.parametrize(
    ("origin", "allowed"),
    [
        ("http://box.lan", True),
        ("http://box.lan:5173", False),
        ("http://evil.lan", False),
        ("http://box.lan.evil.lan", False),
        ("null", False),
        ("", False),
    ],
)
def test_origin_matching_is_exact(origin: str, allowed: bool) -> None:
    """Exact string equality, not a prefix or a suffix. `box.lan.evil.lan`
    is the attack a `endswith` check waves through, and a port is part of
    an origin — `http://box.lan:5173` is a different origin from
    `http://box.lan`, which is why §10.4 requires both to be listed if both
    are used."""
    assert origin_allowed(origin, ("http://box.lan",)) is allowed


async def test_a_safe_method_needs_no_origin(client: httpx.AsyncClient) -> None:
    """A GET cannot be a CSRF write, and requiring an origin on reads would
    break a plain address-bar navigation."""
    response = await client.get("/api/auth/me", headers={"Origin": "http://evil.lan"})
    assert response.status_code == 401  # reached the route, refused on auth


@pytest.mark.parametrize("method", ["POST", "PUT", "PATCH", "DELETE"])
async def test_an_unsafe_method_from_a_foreign_origin_is_403(
    client: httpx.AsyncClient, method: str
) -> None:
    response = await client.request(
        method, "/api/auth/logout", headers={"Origin": "http://evil.lan"}
    )
    assert response.status_code == 403
    assert response.json()["code"] == ApiErrorCode.FORBIDDEN


async def test_an_unsafe_method_with_no_origin_at_all_is_403(client: httpx.AsyncClient) -> None:
    """A missing header is not a pass. Non-browser clients — curl, a script
    — simply send the header; a browser always does for cross-origin
    writes, and the same-origin case is the one the frontend produces."""
    response = await client.post("/api/auth/logout", headers={"Origin": ""})
    assert response.status_code == 403


async def test_an_unsafe_method_from_an_allowed_origin_reaches_the_route(
    client: httpx.AsyncClient,
) -> None:
    response = await client.post("/api/auth/logout", headers={"Origin": ORIGIN})
    assert response.status_code == 401  # reached the route, refused on auth


async def test_a_refusal_from_a_middleware_still_carries_a_request_id(
    client: httpx.AsyncClient,
) -> None:
    """The reason request-id is outermost. A 403 that no route produced is
    still a response an operator has to be able to find in the log."""
    response = await client.post("/api/auth/logout", headers={"Origin": "http://evil.lan"})
    assert response.status_code == 403
    assert response.headers["x-request-id"]


async def test_no_cors_headers_are_ever_emitted(client: httpx.AsyncClient) -> None:
    """§6.4: "CORS disabled". An `Access-Control-Allow-Origin` would invite
    exactly the cross-origin request the origin check exists to refuse."""
    response = await client.get("/api/auth/me")
    assert not [h for h in response.headers if h.lower().startswith("access-control-")]


async def test_a_body_over_the_limit_is_413(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    oversized = "x" * (deps.settings.max_body_bytes + 1)
    response = await client.post("/api/auth/login", json={"username": "a", "password": oversized})
    assert response.status_code == 413
    assert response.json()["code"] == ApiErrorCode.PAYLOAD_TOO_LARGE
    # Request-id outermost applies to BodyLimit's refusal too, not only
    # Origin's — see test_a_refusal_from_a_middleware_still_carries_a_request_id.
    assert response.headers["x-request-id"]


async def test_a_chunked_body_over_the_limit_is_also_413(
    client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """The one that matters. A chunked request declares no
    `Content-Length`, so the header check cannot see it — and a middleware
    that merely *counted* the bytes on their way to the route would have
    bounded nothing, because the route already has them. This is an
    unauthenticated path, so "the client is well behaved" is not an
    assumption available here.
    """

    async def oversized_chunks() -> AsyncIterator[bytes]:
        for _ in range((deps.settings.max_body_bytes // 1024) + 2):
            yield b"x" * 1024

    response = await client.post("/api/auth/login", content=oversized_chunks())
    assert response.status_code == 413
    assert response.json()["code"] == ApiErrorCode.PAYLOAD_TOO_LARGE


async def test_a_body_under_the_limit_still_reaches_the_route_intact(
    client: httpx.AsyncClient,
) -> None:
    """The replay half: the middleware reads the body, so it must hand the
    route the same bytes. A silent truncation here would surface as a 422
    on a request that was perfectly valid."""
    response = await client.post(
        "/api/auth/login", json={"username": "alice", "password": "correct horse"}
    )
    assert response.status_code == 401  # reached the route, refused on credentials
    assert response.json()["code"] == ApiErrorCode.CREDENTIALS_INVALID


async def test_a_foreign_host_header_is_refused(deps: AppDependencies) -> None:
    """`ALLOWED_HOSTS` (§10.4, §10.11). A DNS-rebinding page in a player's
    browser reaches a LAN service by name; checking `Host` is what stops
    it, and §10.11 asks for it at the edge *and* here."""
    from triviador.api.app import create_app

    transport = httpx.ASGITransport(app=create_app(deps), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://evil.lan") as client:
        assert (await client.get("/api/auth/me")).status_code == 400


async def test_a_foreign_host_header_is_refused_with_an_envelope(deps: AppDependencies) -> None:
    from triviador.api.app import create_app

    transport = httpx.ASGITransport(app=create_app(deps), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://evil.lan") as client:
        response = await client.get("/api/auth/me")
    assert response.status_code == 400
    assert response.json()["code"] == ApiErrorCode.FORBIDDEN
    # Request-id outermost applies to Host's refusal too, not only Origin's
    # — see test_a_refusal_from_a_middleware_still_carries_a_request_id.
    assert response.headers["x-request-id"]


async def test_a_host_with_a_port_matches_the_bare_entry(deps: AppDependencies) -> None:
    """`Host: testserver:8000` is the same host as `testserver`, and a
    development deploy reached directly rather than through Caddy sends
    exactly that."""
    from triviador.api.app import create_app

    transport = httpx.ASGITransport(app=create_app(deps), raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver:8000") as c:
        assert (await c.get("/api/auth/me")).status_code == 401


async def test_a_host_refused_websocket_uses_the_denial_extension_when_available() -> None:
    """A pre-accept refusal *can* carry a real response: when the server
    advertises `websocket.http.response`, `envelope()`'s `JSONResponse`
    called against a websocket scope translates `http.response.*` into
    `websocket.http.response.*` (Starlette's `_wrap_websocket_denial_send`)
    and a real client sees the JSON envelope with a real status — not a
    dropped close code and not a bare `text/plain` 403.

    No test client here drives a real websocket handshake, so this drives
    the ASGI callable directly, which is all `HostMiddleware` is.
    """
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async def receive() -> Message:
        raise AssertionError("the middleware must not read from the socket")

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        raise AssertionError("a refused host must never reach the app")

    scope: Scope = {
        "type": "websocket",
        "headers": [(b"host", b"evil.lan")],
        "extensions": {"websocket.http.response": {}},
    }
    middleware = HostMiddleware(app, allowed_hosts=("box.lan",))
    await middleware(scope, receive, send)

    assert [m["type"] for m in sent] == [
        "websocket.http.response.start",
        "websocket.http.response.body",
    ]
    assert sent[0]["status"] == 400
    body = json.loads(sent[1]["body"])
    assert body["code"] == ApiErrorCode.FORBIDDEN


async def test_a_host_refused_websocket_falls_back_to_close_without_the_extension() -> None:
    """Where the server does not advertise the denial extension,
    `websocket.close` is the only thing left — and the code is the
    generic 1008 ("policy violation"), not the application-level 4403,
    because uvicorn renders a pre-accept close as a bare `text/plain` 403
    and drops the code before any client observes it."""
    sent: list[Message] = []

    async def send(message: Message) -> None:
        sent.append(message)

    async def receive() -> Message:
        raise AssertionError("the middleware must not read from the socket")

    async def app(scope: Scope, receive: Receive, send: Send) -> None:
        raise AssertionError("a refused host must never reach the app")

    scope: Scope = {
        "type": "websocket",
        "headers": [(b"host", b"evil.lan")],
        "extensions": {},
    }
    middleware = HostMiddleware(app, allowed_hosts=("box.lan",))
    await middleware(scope, receive, send)

    assert sent == [{"type": "websocket.close", "code": 1008}]
