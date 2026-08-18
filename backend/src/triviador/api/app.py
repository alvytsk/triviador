"""The app factory. The *composition root* — which builds the real
adapters — is `build_app` in Task 17; this half only assembles routers,
handlers and middleware around a dependency bundle it is handed.

Split that way on purpose: every contract test in `tests/api/` constructs
an app over fakes, and a factory that reached for an engine could not be
called without a database.
"""

from fastapi import FastAPI

from triviador.api.deps import AppDependencies
from triviador.api.errors import install_error_handlers
from triviador.api.http import auth
from triviador.api.logging import RequestContextMiddleware


def create_app(deps: AppDependencies) -> FastAPI:
    app = FastAPI(title="Triviador", version="1", docs_url=None, redoc_url=None)
    app.state.deps = deps
    app.add_middleware(RequestContextMiddleware)
    app.include_router(auth.router)
    install_error_handlers(app)
    return app
