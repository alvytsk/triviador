"""`_head_revision()` locates `alembic.ini` to compute Alembic's idea of
"head", which the lifespan (`app.py`'s `_lifespan`) compares against the
database's current revision before the server is allowed to serve traffic.

It used to resolve `alembic.ini` via `Path(__file__).resolve().parents[3]`
— correct in the repo, where `__file__` sits under `backend/src/triviador/
api/app.py`, but wrong the moment the package is installed non-editable
(`infra/backend.Dockerfile`'s `uv sync --no-editable`), at which point
`__file__` resolves under `site-packages` and `parents[3]` lands nowhere
near `alembic.ini`. That made the backend unable to start in the built
image at all — `_head_revision()` is called from the lifespan, whose
failure path raises `RuntimeError`.

The fix reuses `triviador.cli`'s `_alembic_ini()`, which resolves against
`Path.cwd()` instead — matching both the container's `WORKDIR /app` and
every documented `cd backend && …` invocation, and the same fix
`migrate_head` needed for the identical bug (see `test_cli_migrate.py`'s
sibling module `cli.py`).

A `Path(__file__)`-based implementation does not care what the process's
working directory is — it would resolve to the real `alembic.ini` (or the
wrong location) regardless. So the test that actually distinguishes the
two strategies is not "does it work from the repo root" — both
implementations pass that — it's "does changing the working directory
change the outcome". Only the `Path.cwd()`-based fix does, which is
exactly what makes it fail loudly instead of silently misresolving inside
a container.
"""

from pathlib import Path

import pytest

from triviador.api.app import _head_revision

BACKEND_ROOT = Path(__file__).resolve().parents[2]


def test_head_revision_resolves_from_the_backend_project_root(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The documented invocation shape (`cd backend && …`, or the
    container's `WORKDIR /app`): cwd is the backend root, where
    `alembic.ini` actually lives."""
    monkeypatch.chdir(BACKEND_ROOT)
    assert _head_revision() is not None


def test_head_revision_raises_when_cwd_is_not_the_backend_root(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The regression this guards against: a `Path(__file__)`-based
    resolution would still find the real `alembic.ini` here, via the
    module's location on disk, regardless of `cwd` — masking exactly the
    bug that made the built image unbootable. Only a `cwd`-based
    resolution is sensitive to this, and it must fail loudly (not silently
    resolve to nothing, and not silently resolve to the wrong file) when
    run from the wrong directory.
    """
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError, match=r"alembic\.ini"):
        _head_revision()
