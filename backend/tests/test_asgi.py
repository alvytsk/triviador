"""The server entrypoint. Until this module existed the application had no
module-level app at all — `build_app` takes a `Settings` argument, so neither
uvicorn nor `fastapi dev` could find anything to serve."""


def test_asgi_exposes_a_module_level_app(monkeypatch, tmp_path):
    # Import-time construction means an unconfigured deploy fails here, at
    # startup, which is §10.4's stated intent. Give it a valid environment.
    monkeypatch.setenv("TRIVIADOR_DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/t")
    monkeypatch.setenv("TRIVIADOR_ALLOWED_ORIGINS", "http://localhost:5173")
    # startup_problems() also rejects empty S3 credentials (config.py:148) —
    # without these two, build_app() raises RuntimeError before the app is
    # ever constructed, regardless of the origin/database settings above.
    monkeypatch.setenv("TRIVIADOR_S3_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("TRIVIADOR_S3_SECRET_ACCESS_KEY", "test-secret")

    from fastapi import FastAPI

    import triviador.asgi as asgi

    assert isinstance(asgi.app, FastAPI)


def test_asgi_app_carries_the_real_routes(monkeypatch):
    """A FastAPI instance with no routes would satisfy the test above and
    serve 404s in production."""
    monkeypatch.setenv("TRIVIADOR_DATABASE_URL", "postgresql+asyncpg://u:p@db:5432/t")
    monkeypatch.setenv("TRIVIADOR_ALLOWED_ORIGINS", "http://localhost:5173")
    monkeypatch.setenv("TRIVIADOR_S3_ACCESS_KEY_ID", "test-key")
    monkeypatch.setenv("TRIVIADOR_S3_SECRET_ACCESS_KEY", "test-secret")

    import triviador.asgi as asgi

    # `asgi.app.routes` does not work here: this project's locked FastAPI
    # (0.141.1) wraps each `include_router()` call as an opaque
    # `_IncludedRouter` with no `.path`, flattened only lazily inside
    # `openapi()`. The OpenAPI path map is the stable, public way to assert
    # a route exists regardless of that internal representation.
    paths = asgi.app.openapi()["paths"]
    assert "/api/health/live" in paths
    assert "/api/health/ready" in paths
