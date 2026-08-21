# syntax=docker/dockerfile:1

# uv's own image supplies the pinned binary; copying it into a plain python
# base keeps the runtime image free of build tooling.
FROM ghcr.io/astral-sh/uv:0.5.11 AS uv

FROM python:3.13-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app

# Dependencies resolve from the lockfile alone, in their own layer, so a
# source edit does not re-resolve the whole dependency tree.
FROM base AS deps
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

FROM base AS runtime
COPY --from=deps /opt/venv /opt/venv
COPY backend/ ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable --no-dev
ENV PATH="/opt/venv/bin:$PATH"

# Non-root. The container writes nothing outside /tmp: media goes to Garage,
# logs go to stdout, and the maps volume is mounted read-only.
RUN useradd --create-home --uid 10001 triviador
USER triviador

EXPOSE 8000
CMD ["uvicorn", "triviador.asgi:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
