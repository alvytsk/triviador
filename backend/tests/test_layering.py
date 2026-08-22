"""Spec 1 §4: `domain/` imports nothing from `services/`, `api/`, or `db/`.

Enforced by reading the source, not by convention. The domain's value is that
it is plain Python — the whole ruleset runs in milliseconds with no database,
no event loop, and no third-party package. One stray import ends that quietly.

A second, narrower gate below covers `db/codec/`: the event codec's whole
reason for living in its own package — pure translation, no session, no
engine — is what lets `tests/codec/` run with PostgreSQL stopped (see
`db/codec/__init__.py`'s docstring). Nothing else enforced that; importing
`db.models` or `sqlalchemy` into the codec would break no test today, only
quietly turn the "PG-stopped" claim into wishful thinking.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
DOMAIN = SRC / "triviador" / "domain"
CODEC = SRC / "triviador" / "db" / "codec"

FORBIDDEN = (
    "triviador.db",
    "triviador.services",
    "triviador.api",
    "triviador.maps",  # filesystem I/O — `domain/maps/` is the pure half
    "sqlalchemy",
    "asyncpg",
    "alembic",
    "pydantic",
    "pydantic_settings",
    "fastapi",
    "starlette",
)

# `db/codec/` legitimately imports `triviador.db.errors` (its exception
# types) and `pydantic` (its serialization engine) — both fine, neither
# pulls in a database. What must stay out is anything that talks to
# PostgreSQL, or anything a level up the stack that would make the codec's
# purity a one-way trip to import it from `services/` or `api/`.
#
# `triviador.db.*` is handled as an allowlist, not a denylist: a denylist
# enumerating known-bad submodules (`db.models`, `db.repositories`, ...) has
# a gap for every submodule nobody thought to add, which is exactly how
# `db.engine`, `db.base`, and `db.unit_of_work` — all of which import
# SQLAlchemy — slipped past an earlier version of this gate. Only the
# codec's own exception types (`triviador.db.errors`) and its own package
# (`triviador.db.codec`, for its sibling registry/upcasters modules) may be
# imported; every other `triviador.db.*` submodule is forbidden.
CODEC_DB_ALLOWED = (
    "triviador.db.errors",
    "triviador.db.codec",
)

CODEC_FORBIDDEN = (
    "sqlalchemy",
    "asyncpg",
    "alembic",
    "triviador.services",
    "triviador.api",
)


def _is_forbidden(module: str, forbidden: tuple[str, ...] = FORBIDDEN) -> bool:
    # Exact match or a real dotted descendant. A bare `startswith` would also
    # flag a hypothetical `triviador.mapsomething`, and a gate that reports
    # phantom violations is a gate people learn to edit around.
    return any(module == f or module.startswith(f + ".") for f in forbidden)


def _is_forbidden_for_codec(module: str) -> bool:
    if module == "triviador.db" or module.startswith("triviador.db."):
        return not any(
            module == allowed or module.startswith(allowed + ".") for allowed in CODEC_DB_ALLOWED
        )
    return _is_forbidden(module, CODEC_FORBIDDEN)


def _package_of(path: Path) -> list[str]:
    """`.../src/triviador/domain/game/reducer.py` -> ['triviador','domain','game']."""
    return list(path.relative_to(SRC).parts[:-1])


def _imported_modules(path: Path) -> set[str]:
    """Every module this file imports, as an absolute dotted name.

    Relative imports are resolved against the file's own package: `from ...db
    import models` inside `triviador/domain/game/` means `triviador.db`, and a
    gate that skipped it would be trivially bypassable by writing dots.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    package = _package_of(path)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0:
                base = node.module or ""
            else:
                # level 1 is the current package; each extra level strips one.
                prefix = package[: len(package) - (node.level - 1)]
                base = ".".join([*prefix, node.module] if node.module else prefix)
            if not base:
                continue
            names.add(base)
            # `from triviador import db` imports a module, not a name.
            names.update(f"{base}.{alias.name}" for alias in node.names)
    return names


def test_domain_imports_nothing_below_it() -> None:
    violations = [
        f"{path.relative_to(SRC)}: {module}"
        for path in sorted(DOMAIN.rglob("*.py"))
        for module in sorted(_imported_modules(path))
        if _is_forbidden(module)
    ]
    assert violations == [], "domain/ must stay pure:\n" + "\n".join(violations)


def test_codec_stays_free_of_the_database_and_the_layers_above_it() -> None:
    violations = [
        f"{path.relative_to(SRC)}: {module}"
        for path in sorted(CODEC.rglob("*.py"))
        for module in sorted(_imported_modules(path))
        if _is_forbidden_for_codec(module)
    ]
    assert violations == [], "db/codec/ must stay pure:\n" + "\n".join(violations)


@pytest.mark.parametrize(
    "source",
    [
        "import sqlalchemy",
        "import asyncpg",
        "import alembic",
        "from triviador.db.models import games",
        "from triviador.db import models",
        "from triviador.db.engine import create_engine",
        "from triviador.db.base import Base",
        "from ...db.repositories import events",  # relative — same bypass as the domain gate
        "from triviador import services",
        "from triviador import api",
    ],
)
def test_the_codec_gate_sees_each_form_of_violation(source: str) -> None:
    """Mirrors `test_the_gate_sees_each_form_of_violation` for the narrower
    codec gate: a guard nobody has watched fail is a guard nobody can trust.

    A filename unique to this test (not shared with
    `test_a_legitimate_codec_import_is_not_flagged`): both write and delete a
    probe file in `src/`, and sharing one filename would make the two race
    under parallel execution and leave the wrong test's residue behind if
    either is killed mid-run."""
    module = CODEC / "_probe_violation.py"
    module.write_text(source, encoding="utf-8")
    try:
        assert any(_is_forbidden_for_codec(m) for m in _imported_modules(module)), source
    finally:
        module.unlink()


def test_a_legitimate_codec_import_is_not_flagged() -> None:
    """The codec's own real imports — its sibling registry/upcasters modules,
    `db.errors` for its exception types, and `pydantic` — must all pass."""
    module = CODEC / "_probe_legitimate.py"
    module.write_text(
        "from triviador.db.errors import NaiveDatetime\n"
        "from triviador.db.codec.registry import WIRE_NAMES\n"
        "import pydantic\n",
        encoding="utf-8",
    )
    try:
        assert not any(_is_forbidden_for_codec(m) for m in _imported_modules(module))
    finally:
        module.unlink()


@pytest.mark.parametrize(
    "source",
    [
        "import sqlalchemy",
        "import sqlalchemy.orm",
        "from triviador.db.models import games",
        "from triviador import db",
        "from ...db import models",  # relative — the bypass this gate closes
        "from ...db.repositories import events",
    ],
)
def test_the_gate_sees_each_form_of_violation(source: str) -> None:
    """A guard nobody has watched fail is a guard nobody can trust — and each
    import form is a separate code path through `_imported_modules`.

    A filename unique to this test (not shared with
    `test_a_legitimate_domain_import_is_not_flagged`): both write and delete
    a probe file in `src/`, and sharing one filename would make the two race
    under parallel execution and leave the wrong test's residue behind if
    either is killed mid-run."""
    module = DOMAIN / "game" / "_probe_violation.py"
    module.write_text(source, encoding="utf-8")
    try:
        assert any(_is_forbidden(m) for m in _imported_modules(module)), source
    finally:
        module.unlink()


@pytest.mark.parametrize(
    "source",
    [
        "from ..maps.definition import MapDefinition\n",
        # From `triviador/domain/game/`, `..maps.registry` resolves to
        # `triviador.domain.maps.registry` — the pure half of maps, distinct
        # from the filesystem-backed `triviador.maps.registry` that IS
        # forbidden. Getting this resolution backwards would make the gate
        # reject legitimate domain code.
        "from ..maps.registry import MapRegistry\n",
    ],
)
def test_a_legitimate_domain_import_is_not_flagged(source: str) -> None:
    """The gate must also be capable of passing, or it is just an assert False."""
    module = DOMAIN / "game" / "_probe_legitimate.py"
    module.write_text(source, encoding="utf-8")
    try:
        assert not any(_is_forbidden(m) for m in _imported_modules(module))
    finally:
        module.unlink()


SERVICES = SRC / "triviador" / "services"
RUNTIME = SRC / "triviador" / "runtime"


def test_services_does_not_import_adapters() -> None:
    """`services/` is the contract layer. It may name `domain` and
    `triviador.maps` (both pure); naming `db`, `runtime`, or `api` would
    make the contract depend on an implementation of itself."""
    forbidden = (
        "triviador.db",
        "triviador.runtime",
        "triviador.api",
        "sqlalchemy",
        "asyncpg",
        "alembic",
        "fastapi",
        "starlette",
    )
    violations = [
        f"{path.relative_to(SRC)}: {module}"
        for path in sorted(SERVICES.rglob("*.py"))
        for module in sorted(_imported_modules(path))
        if _is_forbidden(module, forbidden)
    ]
    assert violations == [], violations


def test_runtime_does_not_import_persistence_or_api() -> None:
    """Every capability the runtime uses arrives through `services.ports`.
    One `from triviador.db...` here and the port layer is decoration.

    `runtime/` does not exist yet (it lands in Tasks 2-16 of this plan), so
    this passes vacuously today — `rglob` over a missing directory yields no
    files. The gate is written now so the first file added under `runtime/`
    is already covered."""
    forbidden = ("triviador.db", "triviador.api", "sqlalchemy", "asyncpg", "alembic")
    violations = [
        f"{path.relative_to(SRC)}: {module}"
        for path in sorted(RUNTIME.rglob("*.py"))
        for module in sorted(_imported_modules(path))
        if _is_forbidden(module, forbidden)
    ]
    assert violations == [], violations


PROJECTION = SRC / "triviador" / "api" / "projection"


def test_projection_stays_a_pure_function_of_state_and_viewer() -> None:
    """`api/` as a whole is the composition root and may import anything.
    `api/projection/` may not: it is where §8.7's per-viewer withholding
    lives, and it is called from inside the synchronous broadcaster, on the
    consumer task's own stack (§8.6). A projection module that can open a
    session is a projection module that will eventually await one there —
    which is the one thing `Broadcaster` being a `def` exists to prevent.

    Empty today (the package lands in Tasks 8-10); `rglob` over a directory
    with only `__init__.py` yields no violations, and the gate is written
    now so the first projection module is already covered.
    """
    forbidden = (
        "triviador.db",
        "triviador.runtime",
        "triviador.api.http",
        "triviador.api.ws",
        "sqlalchemy",
        "asyncpg",
        "alembic",
        "fastapi",
        "starlette",
    )
    violations = [
        f"{path.relative_to(SRC)}: {module}"
        for path in sorted(PROJECTION.rglob("*.py"))
        for module in sorted(_imported_modules(path))
        if _is_forbidden(module, forbidden)
    ]
    assert violations == [], violations


STORAGE = SRC / "triviador" / "storage"
MEDIA = SRC / "triviador" / "media"
IMPORTS = SRC / "triviador" / "imports"


@pytest.mark.parametrize("package", [STORAGE, MEDIA, IMPORTS])
def test_the_adapter_packages_do_not_import_the_layers_above_them(package: Path) -> None:
    """`storage/`, `media/` and `imports/` sit where `maps/` sits: concrete
    adapters, below `api/` and beside `db/`. Naming `api` would let the
    composition root's shape leak into a pixel encoder; naming `db` would
    put a session inside one, which is how a 200-image import ends up
    holding a transaction open for the length of a CPU-bound encode.

    `media/gc.py` is the one place that legitimately reads the event store,
    and it does so through a repository handed to it — never by importing
    `db` itself. That is why `db` is on this list rather than excused.
    """
    forbidden = ("triviador.api", "triviador.runtime", "triviador.db", "fastapi", "starlette")
    violations = [
        f"{path.relative_to(SRC)}: {module}"
        for path in sorted(package.rglob("*.py"))
        for module in sorted(_imported_modules(path))
        if _is_forbidden(module, forbidden)
    ]
    assert violations == [], violations
