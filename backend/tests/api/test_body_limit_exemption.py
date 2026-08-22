"""An exempt path is not "unlimited" — it is "bounded by the route".

The middleware buffers whole bodies (see its docstring); a 32 MiB import
would be held in memory twice and refused at 1 MiB. So the two upload
paths opt out, and the routes that own them cap themselves. This module
tests the middleware half; `test_admin_media.py` and
`test_admin_imports.py` test that the routes really do cap.
"""

import httpx
import pytest
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import JSONResponse
from starlette.routing import Route

from triviador.api.middleware import BodyLimitMiddleware


async def _echo_size(request: Request) -> JSONResponse:
    body = await request.body()
    return JSONResponse({"received": len(body)})


def _app(exempt: tuple[str, ...]) -> Starlette:
    app = Starlette(
        routes=[
            Route("/open", _echo_size, methods=["POST"]),
            Route("/capped", _echo_size, methods=["POST"]),
        ]
    )
    app.add_middleware(BodyLimitMiddleware, max_bytes=16, exempt_paths=exempt)
    return app


async def _post(app: Starlette, path: str, size: int) -> httpx.Response:
    transport = httpx.ASGITransport(app=app, raise_app_exceptions=False)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        return await client.post(path, content=b"x" * size)


@pytest.mark.parametrize("size", [17, 4096])
async def test_a_non_exempt_path_is_still_refused(size: int) -> None:
    response = await _post(_app(("/open",)), "/capped", size)
    assert response.status_code == 413
    assert response.json()["code"] == "payload_too_large"


async def test_an_exempt_path_receives_the_whole_body(size: int = 4096) -> None:
    response = await _post(_app(("/open",)), "/open", size)
    assert response.status_code == 200
    assert response.json() == {"received": size}


async def test_exemption_is_a_prefix_match_on_the_path_only() -> None:
    """`/openish` must not inherit `/open`'s exemption by accident — the
    match is on the full path, not on `startswith`."""
    app = Starlette(routes=[Route("/openish", _echo_size, methods=["POST"])])
    app.add_middleware(BodyLimitMiddleware, max_bytes=16, exempt_paths=("/open",))
    response = await _post(app, "/openish", 4096)
    assert response.status_code == 413
