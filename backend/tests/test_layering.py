"""Spec 1 §4: `domain/` imports nothing from `services/`, `api/`, or `db/`.

Enforced by reading the source, not by convention. The domain's value is that
it is plain Python — the whole ruleset runs in milliseconds with no database,
no event loop, and no third-party package. One stray import ends that quietly.
"""

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).parent.parent / "src"
DOMAIN = SRC / "triviador" / "domain"

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
)


def _is_forbidden(module: str) -> bool:
    # Exact match or a real dotted descendant. A bare `startswith` would also
    # flag a hypothetical `triviador.mapsomething`, and a gate that reports
    # phantom violations is a gate people learn to edit around.
    return any(module == f or module.startswith(f + ".") for f in FORBIDDEN)


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
    import form is a separate code path through `_imported_modules`."""
    module = DOMAIN / "game" / "_probe.py"
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
    module = DOMAIN / "game" / "_probe.py"
    module.write_text(source, encoding="utf-8")
    try:
        assert not any(_is_forbidden(m) for m in _imported_modules(module))
    finally:
        module.unlink()
