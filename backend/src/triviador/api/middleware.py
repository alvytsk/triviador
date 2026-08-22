"""Origin checking, and a body limit Starlette does not provide.

All three are pure ASGI rather than `BaseHTTPMiddleware`, which is also
what `RequestContextMiddleware` (Task 4) became. Two reasons, and both
have already produced a bug in this plan: a `BaseHTTPMiddleware` cannot
refuse a request before its body is read, and it runs the downstream app
in a *separate task*, which is what made the first version of the request
id vanish from exactly the 500 responses that document it.
"""

from collections.abc import Sequence

from starlette.types import ASGIApp, Message, Receive, Scope, Send

from triviador.api.errors import ApiErrorCode, envelope

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def origin_allowed(origin: str, allowed: Sequence[str]) -> bool:
    """Exact match. Not a prefix (`http://box.lan` would pass
    `http://box.lan.evil.lan` under `startswith`), not a suffix, and not a
    parsed-host comparison that would discard the port."""
    return origin in allowed


class HostMiddleware:
    """`ALLOWED_HOSTS` (§10.4, §10.11), answering with the envelope.

    Starlette ships `TrustedHostMiddleware`, and it emits `text/plain`.
    That would be the single hole in "every response body is an envelope",
    and the hole matters more than the convenience: `apiFetch` parses every
    body and reports an unparseable one as a *transport* error — "the
    backend was never reached" — which is exactly the wrong diagnosis for a
    host the backend deliberately refused. Fifteen lines is cheaper than an
    exception to the contract.

    `"*"` disables the check, matching Starlette's behaviour so a
    development configuration does not have to enumerate every interface.

    A websocket refusal is not exempt from the envelope either. Starlette's
    denial-response extension lets a *pre-accept* refusal carry a real
    response: when the server advertises `websocket.http.response` in
    `scope["extensions"]`, calling the same `envelope()` response against
    the websocket scope translates `http.response.start`/`.body` into
    `websocket.http.response.start`/`.body` and a real client sees the
    JSON envelope with a real status. Only where that extension is absent
    does this fall back to `websocket.close` — and even then a status-4xx
    application code would be meaningless, because uvicorn turns a
    pre-accept close sent without the extension into a bare HTTP 403 with
    a `text/plain` body and drops the close code on the floor; nothing
    ever observes it.
    """

    def __init__(self, app: ASGIApp, *, allowed_hosts: Sequence[str]) -> None:
        self.app = app
        self.allowed = tuple(allowed_hosts)
        self.any_host = "*" in self.allowed

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if self.any_host or scope["type"] not in ("http", "websocket"):
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        # The port is not part of the comparison: a LAN deployment is
        # reached on `:80` through Caddy and on `:8000` directly in
        # development, and both are the same host.
        host = headers.get("host", "").split(":")[0]
        if host in self.allowed:
            await self.app(scope, receive, send)
            return
        if scope["type"] == "websocket":
            # Starlette's denial extension lets a *pre-accept* refusal
            # carry a real response. Accepting first and closing with a
            # code would be worse: it completes a handshake with a host
            # we do not trust just to hang up on it. Where the server
            # does not advertise the extension, `websocket.close` is the
            # only thing left — uvicorn renders it as a bare 403 and
            # discards the code, which is why this is the fallback and
            # not the primary path, and why the code is the generic 1008
            # ("policy violation") rather than an application-level one
            # that nothing can ever observe.
            if "websocket.http.response" in scope.get("extensions", {}):
                response = envelope(400, ApiErrorCode.FORBIDDEN, "host not allowed")
                await response(scope, receive, send)
            else:
                await send({"type": "websocket.close", "code": 1008})
            return
        response = envelope(400, ApiErrorCode.FORBIDDEN, "host not allowed")
        await response(scope, receive, send)


class OriginMiddleware:
    """§6.4, for REST. The `/ws` half lives in the endpoint (Task 14),
    because a handshake is refused with a close code, not a status."""

    def __init__(self, app: ASGIApp, *, allowed_origins: Sequence[str]) -> None:
        self.app = app
        self.allowed = tuple(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return
        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        if not origin_allowed(headers.get("origin", ""), self.allowed):
            response = envelope(403, ApiErrorCode.FORBIDDEN, "origin not allowed")
            await response(scope, receive, send)
            return
        await self.app(scope, receive, send)


class BodyLimitMiddleware:
    """413 above `max_bytes`, for a declared body *and* an undeclared one.

    The body is read to completion here, bounded, **before** the app is
    invoked, and replayed to it from memory. That is the part that has to
    be right: a chunked request carries no `Content-Length`, so counting
    bytes while streaming them onward bounds nothing — the app has already
    received them. Reading first means an oversized body is refused with
    at most `max_bytes + 1` held, and the route never starts.

    The cost is that every request is buffered. At 1 MiB and §1.1's two to
    four players that is not a tradeoff worth agonising over; Plan 7's
    media upload, which is the one genuinely large body in the system,
    needs a streaming route of its own and must exclude itself from this
    middleware rather than raise the cap for everybody.

    An exempt path is not unbounded — it is bounded by its own route,
    which reads the stream itself and stops at its own cap
    (`media_max_bytes`, `import_max_bytes`). The exemption exists because
    buffering a 32 MiB import here would hold it twice and refuse it at
    1 MiB; it is a list of exact paths (`http/admin.UPLOAD_PATHS`), never
    a prefix, so widening it is a deliberate edit.
    """

    def __init__(self, app: ASGIApp, *, max_bytes: int, exempt_paths: tuple[str, ...] = ()) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.exempt_paths = exempt_paths

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        # Equality, not `startswith`: a prefix match would extend the
        # exemption to every path that merely begins with an exempt one,
        # and the exemption is the security-relevant half of this class.
        if scope["path"] in self.exempt_paths:
            await self.app(scope, receive, send)
            return

        headers = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in scope["headers"]}
        declared = headers.get("content-length")
        if declared is not None and declared.isdigit() and int(declared) > self.max_bytes:
            # The cheap path: refuse without reading a byte.
            await self._refuse(scope, receive, send)
            return

        chunks: list[bytes] = []
        total = 0
        more = True
        while more:
            message = await receive()
            if message["type"] == "http.disconnect":
                # The client went away mid-body. There is nobody to answer.
                return
            chunk: bytes = message.get("body", b"")
            total += len(chunk)
            if total > self.max_bytes:
                # Stop reading here: the remaining bytes are the client's
                # problem, and continuing to drain them is the DoS.
                await self._refuse(scope, receive, send)
                return
            chunks.append(chunk)
            more = bool(message.get("more_body", False))

        body = b"".join(chunks)
        delivered = False

        async def replay() -> Message:
            nonlocal delivered
            if delivered:
                # Anything after the single body message is the client
                # disconnecting; a route that reads twice must not hang.
                return {"type": "http.disconnect"}
            delivered = True
            return {"type": "http.request", "body": body, "more_body": False}

        await self.app(scope, replay, send)

    async def _refuse(self, scope: Scope, receive: Receive, send: Send) -> None:
        response = envelope(
            413, ApiErrorCode.PAYLOAD_TOO_LARGE, f"body exceeds {self.max_bytes} bytes"
        )
        await response(scope, receive, send)
