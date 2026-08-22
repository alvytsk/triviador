"""The ASGI entrypoint uvicorn imports.

`build_app` is a factory taking `Settings`, which uvicorn cannot call (its
`--factory` mode requires a zero-argument callable) and `fastapi dev` cannot
discover. This module is the one place that binds the two together.

Constructed at import rather than behind a lazy factory on purpose: §10.4
wants an unconfigured deploy to fail loudly, and `build_app` already raises
`RuntimeError` listing every configuration problem. Deferring that to the
first request would turn a startup failure into a 500 on a live request.
"""

from fastapi import FastAPI

from triviador.api.app import build_app
from triviador.config import get_settings

app: FastAPI = build_app(get_settings())
