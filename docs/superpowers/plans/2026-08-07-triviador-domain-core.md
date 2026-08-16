# Triviador Plan 1 — Domain Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build the complete Triviador ruleset as a pure Python library — game state, commands, events, and the `decide`/`evolve` reducer — with no I/O of any kind.

**Architecture:** Event-sourced domain. `decide(state, command, ctx) -> events` answers *what happened*; `evolve(state, event) -> state` answers *what the state becomes*. All non-determinism (base draw, question pool, shuffles) is materialised as values in an immutable `DecisionContext` by the caller, never invoked inside the domain. Replay is `fold(evolve, events)`.

**Tech Stack:** Python 3.13 · `uv` · frozen dataclasses · `ruff` · `mypy --strict` · `pytest` · `Hypothesis`

**Source spec:** `docs/superpowers/specs/2026-08-07-triviador-spec1-design.md`

## Global Constraints

Every task's requirements implicitly include this section.

- **ADR-001/3:** the domain layer performs **no I/O**. `domain/` must not import `services/`, `api/`, `db/`, or any library that touches the network, filesystem, clock, or RNG.
- **No hidden non-determinism.** `decide` must never call `random`, `datetime.now()`, `uuid.uuid4()`, or read a repository. Everything comes from `state`, `command`, or `ctx`. `evolve` takes events only.
- **Everything is frozen.** All domain types are `@dataclass(frozen=True)`; collections in state are `Mapping`/`tuple`/`frozenset`, never `dict`/`list`/`set`.
- **`ScoreChanged` is a first-class event.** Never embed `score_delta` or `new_score` into gameplay events.
- **One interaction opportunity = one `DeadlineId`.** Every windowed command carries the `DeadlineId` it was issued against.
- **Guard order is fixed** (§6.2 of the spec): terminal phase → stale window → actor validity → early expire → turn legality → domain constraint.
- **`ignore` vs `reject`:** benign races return `()`; client bugs raise `RejectedCommand`. Never confuse the two.
- Python `>=3.13`. Line length 100. `ruff` and `mypy --strict` must pass on every commit.

---

## File Structure

```
backend/
├── pyproject.toml                       uv project, ruff/mypy/pytest config
└── src/triviador/
    ├── __init__.py
    ├── domain/
    │   ├── __init__.py
    │   ├── ids.py                       NewType id aliases — no logic
    │   ├── maps/
    │   │   ├── definition.py            MapDefinition + adjacency queries
    │   │   └── validation.py            pure validate_map()
    │   ├── questions/
    │   │   └── types.py                 QuestionSnapshot, QuestionPool, QuestionBudget
    │   └── game/
    │       ├── rules.py                 GameRules, validate_rules, required_question_budget
    │       ├── state.py                 Phase, Territory, PlayerState, Deadline, Turn, GameState
    │       ├── actions.py               Command union, DecisionContext, RejectedCommand
    │       ├── events.py                GameEvent union
    │       ├── scoring.py               holding_value, holdings_value
    │       └── reducer.py               decide() / evolve()
    └── maps/
        └── registry.py                  filesystem loader (the only I/O in this plan)

data/maps/czechia/{map.json, map.svg, LICENSE}

backend/tests/
├── conftest.py                          builders: a_rules(), a_question(), a_state()
├── domain/maps/test_validation.py
├── domain/questions/test_pool.py
├── domain/game/test_rules.py
├── domain/game/test_scoring.py
├── domain/game/test_start.py
├── domain/game/test_expansion.py
├── domain/game/test_battle.py
├── domain/game/test_capture.py
├── domain/game/test_endgame.py
├── domain/game/test_matrix.py           all 80 cells
├── domain/game/test_properties.py       Hypothesis
└── maps/test_registry.py
```

Split is by responsibility, not layer: `state.py` holds every type that appears *in* a `GameState`, `events.py` everything the reducer emits. `reducer.py` is the only file with branching logic, which is why it is the only file with a 100 % branch-coverage gate.

---

### Task 1: Backend scaffolding and id types

**Files:**
- Create: `backend/pyproject.toml`
- Create: `backend/src/triviador/__init__.py`
- Create: `backend/src/triviador/domain/__init__.py`
- Create: `backend/src/triviador/domain/ids.py`
- Create: `backend/tests/__init__.py`
- Test: `backend/tests/domain/test_ids.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `GameId`, `PlayerId`, `RegionId`, `MapId`, `QuestionId`, `CategoryId`, `MediaAssetId` (all `NewType` over `str`), `DeadlineId` (`NewType` over `int`).

`DeadlineId` is an `int` on purpose: it is allocated from a counter inside `GameState`, so window identity needs no UUID and therefore no `DecisionContext` entry.

- [ ] **Step 1: Create the uv project**

Run from the repo root:

```bash
mkdir -p backend/src/triviador/domain backend/tests/domain
cd backend && uv init --lib --name triviador --python 3.13 --no-workspace . 2>/dev/null || true
```

Then overwrite `backend/pyproject.toml` with exactly:

```toml
[project]
name = "triviador"
version = "0.1.0"
requires-python = ">=3.13"
dependencies = []

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-cov>=6.0",
    "hypothesis>=6.115",
    "mypy>=1.13",
    "ruff>=0.8",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/triviador"]

[tool.ruff]
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM", "RUF"]

[tool.mypy]
python_version = "3.13"
strict = true
files = ["src/triviador", "tests"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = "-q"

[tool.coverage.report]
show_missing = true
```

- [ ] **Step 2: Write the failing test**

Create `backend/tests/__init__.py` and `backend/tests/domain/__init__.py` as empty files, then `backend/tests/domain/test_ids.py`:

```python
from triviador.domain.ids import DeadlineId, GameId, PlayerId, RegionId


def test_ids_are_distinct_newtypes_over_their_base() -> None:
    assert GameId("g1") == "g1"
    assert PlayerId("p1") == "p1"
    assert RegionId("R1") == "R1"
    assert DeadlineId(17) == 17
```

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/test_ids.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'triviador.domain.ids'`

- [ ] **Step 4: Write minimal implementation**

Create empty `backend/src/triviador/__init__.py` and `backend/src/triviador/domain/__init__.py`, then `backend/src/triviador/domain/ids.py`:

```python
"""Identifier aliases. No logic lives here."""

from typing import NewType

GameId = NewType("GameId", str)
PlayerId = NewType("PlayerId", str)
RegionId = NewType("RegionId", str)
MapId = NewType("MapId", str)
QuestionId = NewType("QuestionId", str)
CategoryId = NewType("CategoryId", str)
MediaAssetId = NewType("MediaAssetId", str)

# Monotonic per game, allocated from GameState.next_deadline_id.
DeadlineId = NewType("DeadlineId", int)
```

- [ ] **Step 5: Run test and the linters**

Run: `cd backend && uv run pytest tests/domain/test_ids.py -v && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: PASS, no lint errors, `Success: no issues found`

- [ ] **Step 6: Commit**

```bash
git add backend/pyproject.toml backend/uv.lock backend/src backend/tests
git commit -m "chore: scaffold backend package with ruff, mypy and pytest"
```

---

### Task 2: Map definition and pure validation

**Files:**
- Create: `backend/src/triviador/domain/maps/__init__.py`
- Create: `backend/src/triviador/domain/maps/definition.py`
- Create: `backend/src/triviador/domain/maps/validation.py`
- Test: `backend/tests/domain/maps/test_validation.py`

**Interfaces:**
- Consumes: `RegionId`, `MapId` from Task 1.
- Produces: `MapDefinition(map_id, regions: tuple[Region, ...], adjacency: Mapping[RegionId, frozenset[RegionId]])` with methods `region_ids() -> tuple[RegionId, ...]`, `neighbours(region_id) -> frozenset[RegionId]`; and `validate_map(defn: MapDefinition, min_independent_set: int = 4) -> tuple[str, ...]` returning human-readable problems (empty tuple = valid).

The independent-set check is what guarantees §3.4.1: four mutually non-adjacent regions must exist, or bases cannot be placed for a 4-player game.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/maps/__init__.py` (empty) and `backend/tests/domain/maps/test_validation.py`:

```python
from triviador.domain.ids import MapId, RegionId
from triviador.domain.maps.definition import MapDefinition, Region
from triviador.domain.maps.validation import validate_map


def a_map(adjacency: dict[str, list[str]]) -> MapDefinition:
    return MapDefinition(
        map_id=MapId("test"),
        regions=tuple(Region(RegionId(r), r.title()) for r in sorted(adjacency)),
        adjacency={
            RegionId(r): frozenset(RegionId(n) for n in ns) for r, ns in adjacency.items()
        },
    )


def test_a_well_formed_map_has_no_problems() -> None:
    # A path a-b-c-d-e-f-g-h: alternating nodes give an independent set of 4.
    chain = {"a": ["b"], "b": ["a", "c"], "c": ["b", "d"], "d": ["c", "e"],
             "e": ["d", "f"], "f": ["e", "g"], "g": ["f", "h"], "h": ["g"]}
    assert validate_map(a_map(chain)) == ()


def test_asymmetric_adjacency_is_reported() -> None:
    problems = validate_map(a_map({"a": ["b"], "b": []}))
    assert any("asymmetric" in p for p in problems)


def test_disconnected_graph_is_reported() -> None:
    problems = validate_map(a_map({"a": ["b"], "b": ["a"], "c": ["d"], "d": ["c"]}))
    assert any("connected" in p for p in problems)


def test_unknown_neighbour_is_reported() -> None:
    # "ghost" is named as a neighbour but never declared as a region.
    defn = MapDefinition(
        map_id=MapId("test"),
        regions=(Region(RegionId("a"), "A"),),
        adjacency={RegionId("a"): frozenset({RegionId("ghost")})},
    )
    assert any("unknown region" in p for p in validate_map(defn))


def test_too_small_independent_set_is_reported() -> None:
    # A complete graph on 4 nodes has a maximum independent set of 1.
    clique = {r: [o for o in "abcd" if o != r] for r in "abcd"}
    problems = validate_map(a_map(clique))
    assert any("independent set" in p for p in problems)


def test_neighbours_returns_the_declared_set() -> None:
    defn = a_map({"a": ["b"], "b": ["a"]})
    assert defn.neighbours(RegionId("a")) == frozenset({RegionId("b")})
    assert defn.region_ids() == (RegionId("a"), RegionId("b"))
```

Note the `unknown region` case: `a_map` builds `regions` from the adjacency keys, so make `ghost` a key with a neighbour but assert it is caught because it is not in `regions`. Adjust the helper for that test by constructing `MapDefinition` directly:

```python
def test_unknown_neighbour_is_reported() -> None:
    defn = MapDefinition(
        map_id=MapId("test"),
        regions=(Region(RegionId("a"), "A"),),
        adjacency={RegionId("a"): frozenset({RegionId("ghost")})},
    )
    problems = validate_map(defn)
    assert any("unknown region" in p for p in problems)
```

Use this version and delete the helper-based one.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/maps -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'triviador.domain.maps'`

- [ ] **Step 3: Write the definition**

Create empty `backend/src/triviador/domain/maps/__init__.py`, then `backend/src/triviador/domain/maps/definition.py`:

```python
from collections.abc import Mapping
from dataclasses import dataclass

from triviador.domain.ids import MapId, RegionId


@dataclass(frozen=True)
class Region:
    region_id: RegionId
    display_name: str


@dataclass(frozen=True)
class MapDefinition:
    """Immutable board topology. Loaded from data/maps/<id>/map.json."""

    map_id: MapId
    regions: tuple[Region, ...]
    adjacency: Mapping[RegionId, frozenset[RegionId]]

    def region_ids(self) -> tuple[RegionId, ...]:
        return tuple(r.region_id for r in self.regions)

    def neighbours(self, region_id: RegionId) -> frozenset[RegionId]:
        return self.adjacency.get(region_id, frozenset())
```

- [ ] **Step 4: Write the validator**

Create `backend/src/triviador/domain/maps/validation.py`:

```python
from collections.abc import Sequence

from triviador.domain.ids import RegionId
from triviador.domain.maps.definition import MapDefinition


def validate_map(defn: MapDefinition, min_independent_set: int = 4) -> tuple[str, ...]:
    """Return every structural problem with a map. Empty tuple means valid."""
    problems: list[str] = []
    known = set(defn.region_ids())

    if len(known) != len(defn.regions):
        problems.append("duplicate region ids declared")

    for region_id, neighbours in defn.adjacency.items():
        if region_id not in known:
            problems.append(f"adjacency declared for unknown region {region_id!r}")
        for neighbour in neighbours:
            if neighbour not in known:
                problems.append(f"{region_id!r} borders unknown region {neighbour!r}")
            elif region_id not in defn.adjacency.get(neighbour, frozenset()):
                problems.append(f"asymmetric adjacency between {region_id!r} and {neighbour!r}")
        if region_id in neighbours:
            problems.append(f"{region_id!r} borders itself")

    if known and not _is_connected(defn):
        problems.append("adjacency graph is not connected")

    if _max_independent_set_at_least(defn, min_independent_set) is False:
        problems.append(
            f"no independent set of size {min_independent_set} — bases cannot be placed"
        )

    return tuple(problems)


def _is_connected(defn: MapDefinition) -> bool:
    ids = defn.region_ids()
    seen: set[RegionId] = {ids[0]}
    frontier = [ids[0]]
    while frontier:
        current = frontier.pop()
        for neighbour in defn.neighbours(current):
            if neighbour not in seen:
                seen.add(neighbour)
                frontier.append(neighbour)
    return len(seen) == len(ids)


def _max_independent_set_at_least(defn: MapDefinition, size: int) -> bool:
    """Greedy-with-backtracking search. Maps have ~15 regions, so this is cheap."""
    if size <= 0:
        return True
    return _search(defn, list(defn.region_ids()), [], size)


def _search(
    defn: MapDefinition,
    candidates: Sequence[RegionId],
    chosen: list[RegionId],
    size: int,
) -> bool:
    if len(chosen) >= size:
        return True
    for index, candidate in enumerate(candidates):
        if any(candidate in defn.neighbours(c) for c in chosen):
            continue
        if _search(defn, candidates[index + 1 :], [*chosen, candidate], size):
            return True
    return False
```

- [ ] **Step 5: Run tests and linters**

Run: `cd backend && uv run pytest tests/domain/maps -v && uv run ruff check . && uv run mypy`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/domain/maps backend/tests/domain/maps
git commit -m "feat(domain): add MapDefinition and structural map validation"
```

---

### Task 3: Map registry and the first map

**Files:**
- Create: `backend/src/triviador/maps/__init__.py`
- Create: `backend/src/triviador/maps/registry.py`
- Create: `data/maps/czechia/map.json`
- Create: `data/maps/czechia/LICENSE`
- Test: `backend/tests/maps/test_registry.py`

**Interfaces:**
- Consumes: `MapDefinition`, `validate_map` from Task 2.
- Produces: `MapRegistry(root: Path)` with `load(map_id: MapId) -> MapDefinition` (raises `InvalidMapError` listing problems) and `available() -> tuple[MapId, ...]`.

This is the only file in Plan 1 that touches the filesystem, and it lives outside `domain/` for that reason.

`map.svg` is not created here — it is an asset sourced during Plan 4. The registry does not read it; only the frontend does. `map.json` is the authoritative topology.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/maps/__init__.py` (empty) and `backend/tests/maps/test_registry.py`:

```python
import json
from pathlib import Path

import pytest

from triviador.domain.ids import MapId, RegionId
from triviador.maps.registry import InvalidMapError, MapRegistry

REPO_MAPS = Path(__file__).resolve().parents[3] / "data" / "maps"


def test_loads_the_shipped_map() -> None:
    defn = MapRegistry(REPO_MAPS).load(MapId("czechia"))
    assert len(defn.regions) >= 12
    assert defn.neighbours(defn.region_ids()[0])


def test_available_lists_shipped_maps() -> None:
    assert MapId("czechia") in MapRegistry(REPO_MAPS).available()


def test_invalid_map_raises_with_problems(tmp_path: Path) -> None:
    broken = tmp_path / "broken"
    broken.mkdir()
    (broken / "map.json").write_text(
        json.dumps(
            {
                "map_id": "broken",
                "regions": [{"id": "a", "name": "A"}, {"id": "b", "name": "B"}],
                "adjacency": {"a": ["b"], "b": []},
            }
        )
    )
    with pytest.raises(InvalidMapError) as excinfo:
        MapRegistry(tmp_path).load(MapId("broken"))
    assert "asymmetric" in str(excinfo.value)


def test_unknown_map_raises(tmp_path: Path) -> None:
    with pytest.raises(InvalidMapError):
        MapRegistry(tmp_path).load(MapId("nope"))


def test_shipped_map_supports_four_bases() -> None:
    defn = MapRegistry(REPO_MAPS).load(MapId("czechia"))
    # load() already validates, so reaching here proves an independent set of 4 exists.
    assert RegionId("praha") in set(defn.region_ids())
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/maps -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'triviador.maps'`

- [ ] **Step 3: Create the map data**

Create `data/maps/czechia/map.json`. This is the Czech Republic's 14 administrative regions with real borders:

```json
{
  "map_id": "czechia",
  "regions": [
    {"id": "praha", "name": "Praha"},
    {"id": "stredocesky", "name": "Středočeský"},
    {"id": "jihocesky", "name": "Jihočeský"},
    {"id": "plzensky", "name": "Plzeňský"},
    {"id": "karlovarsky", "name": "Karlovarský"},
    {"id": "ustecky", "name": "Ústecký"},
    {"id": "liberecky", "name": "Liberecký"},
    {"id": "kralovehradecky", "name": "Královéhradecký"},
    {"id": "pardubicky", "name": "Pardubický"},
    {"id": "vysocina", "name": "Vysočina"},
    {"id": "jihomoravsky", "name": "Jihomoravský"},
    {"id": "olomoucky", "name": "Olomoucký"},
    {"id": "zlinsky", "name": "Zlínský"},
    {"id": "moravskoslezsky", "name": "Moravskoslezský"}
  ],
  "adjacency": {
    "praha": ["stredocesky"],
    "stredocesky": ["praha", "jihocesky", "plzensky", "ustecky", "liberecky", "kralovehradecky", "pardubicky", "vysocina"],
    "jihocesky": ["stredocesky", "plzensky", "vysocina", "jihomoravsky"],
    "plzensky": ["stredocesky", "jihocesky", "karlovarsky", "ustecky"],
    "karlovarsky": ["plzensky", "ustecky"],
    "ustecky": ["stredocesky", "plzensky", "karlovarsky", "liberecky"],
    "liberecky": ["stredocesky", "ustecky", "kralovehradecky"],
    "kralovehradecky": ["stredocesky", "liberecky", "pardubicky"],
    "pardubicky": ["stredocesky", "kralovehradecky", "vysocina", "olomoucky", "jihomoravsky"],
    "vysocina": ["stredocesky", "jihocesky", "pardubicky", "jihomoravsky"],
    "jihomoravsky": ["jihocesky", "pardubicky", "vysocina", "olomoucky", "zlinsky"],
    "olomoucky": ["pardubicky", "jihomoravsky", "zlinsky", "moravskoslezsky"],
    "zlinsky": ["jihomoravsky", "olomoucky", "moravskoslezsky"],
    "moravskoslezsky": ["olomoucky", "zlinsky"]
  }
}
```

Create `data/maps/czechia/LICENSE`:

```
Topology (map.json) is hand-authored from public administrative boundaries and
is released under CC0.

map.svg is NOT yet present. Before Plan 4, source an SVG of the Czech regions
whose path ids match the region ids above, record its licence here, and verify
the terms permit redistribution.
```

- [ ] **Step 4: Write the registry**

Create empty `backend/src/triviador/maps/__init__.py`, then `backend/src/triviador/maps/registry.py`:

```python
import json
from dataclasses import dataclass
from pathlib import Path

from triviador.domain.ids import MapId, RegionId
from triviador.domain.maps.definition import MapDefinition, Region
from triviador.domain.maps.validation import validate_map


class InvalidMapError(Exception):
    """A map directory is missing, malformed, or structurally invalid."""


@dataclass(frozen=True)
class MapRegistry:
    root: Path

    def available(self) -> tuple[MapId, ...]:
        if not self.root.is_dir():
            return ()
        return tuple(
            MapId(child.name)
            for child in sorted(self.root.iterdir())
            if (child / "map.json").is_file()
        )

    def load(self, map_id: MapId) -> MapDefinition:
        path = self.root / map_id / "map.json"
        if not path.is_file():
            raise InvalidMapError(f"map {map_id!r}: no map.json at {path}")

        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            raise InvalidMapError(f"map {map_id!r}: malformed JSON — {exc}") from exc

        defn = MapDefinition(
            map_id=MapId(raw["map_id"]),
            regions=tuple(Region(RegionId(r["id"]), r["name"]) for r in raw["regions"]),
            adjacency={
                RegionId(k): frozenset(RegionId(n) for n in v)
                for k, v in raw["adjacency"].items()
            },
        )

        problems = validate_map(defn)
        if problems:
            raise InvalidMapError(f"map {map_id!r} is invalid: " + "; ".join(problems))
        return defn
```

- [ ] **Step 5: Run tests and linters**

Run: `cd backend && uv run pytest tests/maps -v && uv run ruff check . && uv run mypy`
Expected: PASS — in particular `test_loads_the_shipped_map` proves the hand-authored adjacency is symmetric, connected, and admits four non-adjacent regions.

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/maps backend/tests/maps data/maps
git commit -m "feat(maps): add filesystem map registry and the Czechia map"
```

---

### Task 4: Question snapshots, pool and budget types

**Files:**
- Create: `backend/src/triviador/domain/questions/__init__.py`
- Create: `backend/src/triviador/domain/questions/types.py`
- Test: `backend/tests/domain/questions/test_pool.py`

**Interfaces:**
- Consumes: `QuestionId`, `CategoryId`, `MediaAssetId` from Task 1.
- Produces: `QuestionKind`, `Difficulty`, `CategorySnapshot`, `ChoiceSnapshot`, `QuestionSnapshot`, `QuestionBudget(numeric, multiple_choice)`, and `QuestionPool` with `next_numeric() -> tuple[QuestionSnapshot, QuestionPool]`, `next_multiple_choice() -> tuple[QuestionSnapshot, QuestionPool]`, `covers(budget) -> bool`.

Drawing advances a counter and returns a **new** pool — the pool is frozen like everything else, and drawing is deterministic, so replay picks the same questions.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/questions/__init__.py` (empty) and `backend/tests/domain/questions/test_pool.py`:

```python
from decimal import Decimal

import pytest

from triviador.domain.ids import CategoryId, QuestionId
from triviador.domain.questions.types import (
    CategorySnapshot,
    ChoiceSnapshot,
    Difficulty,
    QuestionBudget,
    QuestionKind,
    QuestionPool,
    QuestionSnapshot,
)

CATEGORY = CategorySnapshot(CategoryId("c1"), "history", "History")


def a_numeric(n: int) -> QuestionSnapshot:
    return QuestionSnapshot(
        question_id=QuestionId(f"n{n}"),
        version=1,
        kind=QuestionKind.NUMERIC,
        prompt=f"numeric {n}?",
        category=CATEGORY,
        difficulty=Difficulty.MEDIUM,
        choices=None,
        numeric_answer=Decimal(n),
        unit="year",
        media_asset_id=None,
    )


def a_mc(n: int) -> QuestionSnapshot:
    return QuestionSnapshot(
        question_id=QuestionId(f"m{n}"),
        version=1,
        kind=QuestionKind.MULTIPLE_CHOICE,
        prompt=f"mc {n}?",
        category=CATEGORY,
        difficulty=Difficulty.EASY,
        choices=(
            ChoiceSnapshot(0, "a", is_correct=True, media_asset_id=None),
            ChoiceSnapshot(1, "b", is_correct=False, media_asset_id=None),
            ChoiceSnapshot(2, "c", is_correct=False, media_asset_id=None),
            ChoiceSnapshot(3, "d", is_correct=False, media_asset_id=None),
        ),
        numeric_answer=None,
        unit=None,
        media_asset_id=None,
    )


def test_drawing_is_sequential_and_returns_a_new_pool() -> None:
    pool = QuestionPool(numeric=(a_numeric(1), a_numeric(2)), multiple_choice=(a_mc(1),))

    first, pool2 = pool.next_numeric()
    second, pool3 = pool2.next_numeric()

    assert first.question_id == QuestionId("n1")
    assert second.question_id == QuestionId("n2")
    assert pool.numeric_used == 0, "drawing must not mutate the original pool"
    assert pool3.numeric_used == 2


def test_drawing_past_the_end_raises() -> None:
    pool = QuestionPool(numeric=(a_numeric(1),), multiple_choice=())
    _, exhausted = pool.next_numeric()
    with pytest.raises(IndexError):
        exhausted.next_numeric()


def test_covers_compares_against_a_budget() -> None:
    pool = QuestionPool(numeric=(a_numeric(1), a_numeric(2)), multiple_choice=(a_mc(1),))
    assert pool.covers(QuestionBudget(numeric=2, multiple_choice=1)) is True
    assert pool.covers(QuestionBudget(numeric=3, multiple_choice=1)) is False


def test_correct_choice_index_is_derived() -> None:
    assert a_mc(1).correct_choice_index() == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/questions -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'triviador.domain.questions'`

- [ ] **Step 3: Write the implementation**

Create empty `backend/src/triviador/domain/questions/__init__.py`, then `backend/src/triviador/domain/questions/types.py`:

```python
from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum

from triviador.domain.ids import CategoryId, MediaAssetId, QuestionId


class QuestionKind(StrEnum):
    MULTIPLE_CHOICE = "multiple_choice"
    NUMERIC = "numeric"


class Difficulty(StrEnum):
    EASY = "easy"
    MEDIUM = "medium"
    HARD = "hard"


@dataclass(frozen=True)
class CategorySnapshot:
    category_id: CategoryId
    slug: str
    name: str


@dataclass(frozen=True)
class ChoiceSnapshot:
    idx: int
    text: str
    is_correct: bool
    media_asset_id: MediaAssetId | None


@dataclass(frozen=True)
class QuestionSnapshot:
    """A question frozen at pool-draw time.

    Once this exists inside the event log, the game never reads the question
    bank again — an admin editing or deactivating the source row cannot change
    a game in flight or corrupt replay.
    """

    question_id: QuestionId
    version: int
    kind: QuestionKind
    prompt: str
    category: CategorySnapshot
    difficulty: Difficulty
    choices: tuple[ChoiceSnapshot, ...] | None
    numeric_answer: Decimal | None
    unit: str | None
    media_asset_id: MediaAssetId | None

    def correct_choice_index(self) -> int:
        if self.choices is None:
            raise ValueError(f"question {self.question_id!r} has no choices")
        return next(c.idx for c in self.choices if c.is_correct)


@dataclass(frozen=True)
class QuestionBudget:
    numeric: int
    multiple_choice: int


@dataclass(frozen=True)
class QuestionPool:
    numeric: tuple[QuestionSnapshot, ...]
    multiple_choice: tuple[QuestionSnapshot, ...]
    numeric_used: int = 0
    mc_used: int = 0

    def covers(self, budget: QuestionBudget) -> bool:
        return (
            len(self.numeric) >= budget.numeric
            and len(self.multiple_choice) >= budget.multiple_choice
        )

    def next_numeric(self) -> tuple[QuestionSnapshot, "QuestionPool"]:
        if self.numeric_used >= len(self.numeric):
            raise IndexError("numeric question pool exhausted")
        question = self.numeric[self.numeric_used]
        return question, replace(self, numeric_used=self.numeric_used + 1)

    def next_multiple_choice(self) -> tuple[QuestionSnapshot, "QuestionPool"]:
        if self.mc_used >= len(self.multiple_choice):
            raise IndexError("multiple-choice question pool exhausted")
        question = self.multiple_choice[self.mc_used]
        return question, replace(self, mc_used=self.mc_used + 1)
```

- [ ] **Step 4: Run tests and linters**

Run: `cd backend && uv run pytest tests/domain/questions -v && uv run ruff check . && uv run mypy`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/domain/questions backend/tests/domain/questions
git commit -m "feat(domain): add question snapshots, immutable pool and budget"
```

---

### Task 5: GameRules, validation and the question budget formula

**Files:**
- Create: `backend/src/triviador/domain/game/__init__.py`
- Create: `backend/src/triviador/domain/game/rules.py`
- Test: `backend/tests/domain/game/test_rules.py`

**Interfaces:**
- Consumes: `QuestionBudget` from Task 4.
- Produces: `GameRules` (all fields per spec §3.2), `DEFAULT_RULES`, `validate_rules(rules) -> tuple[str, ...]`, `required_question_budget(rules) -> QuestionBudget`.

`required_question_budget` is the **single** implementation used by the preset UI, game creation, `StartGame`, pool drawing, and the property tests. Four independent copies of this formula would diverge the first time the rules change.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/game/__init__.py` (empty) and `backend/tests/domain/game/test_rules.py`:

```python
from dataclasses import replace

from triviador.domain.game.rules import (
    DEFAULT_RULES,
    GameRules,
    required_question_budget,
    validate_rules,
)


def test_default_rules_are_valid() -> None:
    assert validate_rules(DEFAULT_RULES) == ()


def test_default_budget_matches_the_spec() -> None:
    # 3 players, 4 expansion rounds, 4 battle rounds:
    #   duels   = 4 * 3 = 12
    #   numeric = 4 expansion + 12 possible tiebreaks + 1 final = 17
    budget = required_question_budget(DEFAULT_RULES)
    assert budget.numeric == 17
    assert budget.multiple_choice == 12


def test_budget_scales_with_players_and_rounds() -> None:
    rules = replace(DEFAULT_RULES, player_count=2, claims_by_rank=(2, 1),
                    expansion_rounds=2, battle_rounds=3)
    budget = required_question_budget(rules)
    assert budget.multiple_choice == 6
    assert budget.numeric == 2 + 6 + 1


def test_claims_must_match_player_count() -> None:
    problems = validate_rules(replace(DEFAULT_RULES, claims_by_rank=(2, 1)))
    assert any("claims_by_rank" in p for p in problems)


def test_player_count_bounds_are_enforced() -> None:
    assert any("player_count" in p for p in validate_rules(
        replace(DEFAULT_RULES, player_count=5, claims_by_rank=(2, 1, 1, 0, 0))))
    assert any("player_count" in p for p in validate_rules(
        replace(DEFAULT_RULES, player_count=1, claims_by_rank=(2,))))


def test_non_positive_counts_are_rejected() -> None:
    assert validate_rules(replace(DEFAULT_RULES, battle_rounds=0)) != ()
    assert validate_rules(replace(DEFAULT_RULES, base_hp=0)) != ()
    assert validate_rules(replace(DEFAULT_RULES, answer_timeout_ms=500)) != ()


def test_rules_are_frozen() -> None:
    rules: GameRules = DEFAULT_RULES
    try:
        rules.battle_rounds = 9  # type: ignore[misc]
    except Exception:
        return
    raise AssertionError("GameRules must be frozen")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_rules.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'triviador.domain.game'`

- [ ] **Step 3: Write the implementation**

Create empty `backend/src/triviador/domain/game/__init__.py`, then `backend/src/triviador/domain/game/rules.py`:

```python
from dataclasses import dataclass

from triviador.domain.questions.types import QuestionBudget

MIN_PLAYERS = 2
MAX_PLAYERS = 4
MIN_TIMEOUT_MS = 3_000
MAX_TIMEOUT_MS = 120_000


@dataclass(frozen=True)
class GameRules:
    player_count: int
    expansion_rounds: int
    battle_rounds: int
    base_hp: int
    answer_timeout_ms: int
    pick_timeout_ms: int
    claims_by_rank: tuple[int, ...]
    pts_base: int
    pts_territory: int
    pts_conquered: int
    pts_defense: int


DEFAULT_RULES = GameRules(
    player_count=3,
    expansion_rounds=4,
    battle_rounds=4,
    base_hp=3,
    answer_timeout_ms=20_000,
    pick_timeout_ms=15_000,
    claims_by_rank=(2, 1, 0),
    pts_base=1000,
    pts_territory=200,
    pts_conquered=400,
    pts_defense=100,
)


def validate_rules(rules: GameRules) -> tuple[str, ...]:
    problems: list[str] = []

    if not MIN_PLAYERS <= rules.player_count <= MAX_PLAYERS:
        problems.append(f"player_count must be {MIN_PLAYERS}..{MAX_PLAYERS}")
    if len(rules.claims_by_rank) != rules.player_count:
        problems.append("claims_by_rank must have exactly player_count entries")
    if any(c < 0 for c in rules.claims_by_rank):
        problems.append("claims_by_rank entries must be non-negative")
    if sum(rules.claims_by_rank) == 0:
        problems.append("claims_by_rank must grant at least one region per round")

    for name, value in (
        ("expansion_rounds", rules.expansion_rounds),
        ("battle_rounds", rules.battle_rounds),
        ("base_hp", rules.base_hp),
    ):
        if value < 1:
            problems.append(f"{name} must be at least 1")

    for name, value in (
        ("answer_timeout_ms", rules.answer_timeout_ms),
        ("pick_timeout_ms", rules.pick_timeout_ms),
    ):
        if not MIN_TIMEOUT_MS <= value <= MAX_TIMEOUT_MS:
            problems.append(f"{name} must be {MIN_TIMEOUT_MS}..{MAX_TIMEOUT_MS}")

    for name, value in (
        ("pts_base", rules.pts_base),
        ("pts_territory", rules.pts_territory),
        ("pts_conquered", rules.pts_conquered),
        ("pts_defense", rules.pts_defense),
    ):
        if value < 0:
            problems.append(f"{name} must be non-negative")

    return tuple(problems)


def required_question_budget(rules: GameRules) -> QuestionBudget:
    """Upper bound on question consumption over every possible trajectory.

    One attack per player per battle round; each may go to a numeric tiebreak.
    Plus one numeric per expansion round and one for the final score tiebreak.
    """
    duels = rules.battle_rounds * rules.player_count
    return QuestionBudget(
        numeric=rules.expansion_rounds + duels + 1,
        multiple_choice=duels,
    )
```

- [ ] **Step 4: Run tests and linters**

Run: `cd backend && uv run pytest tests/domain/game/test_rules.py -v && uv run ruff check . && uv run mypy`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/domain/game backend/tests/domain/game
git commit -m "feat(domain): add GameRules, validation and required_question_budget"
```

---

### Task 6: Game state types

**Files:**
- Create: `backend/src/triviador/domain/game/state.py`
- Test: `backend/tests/domain/game/test_state.py`

**Interfaces:**
- Consumes: ids (Task 1), `MapDefinition` (Task 2), `QuestionSnapshot`/`QuestionPool` (Task 4), `GameRules` (Task 5).
- Produces: `Phase`, `TerritoryKind`, `AcquisitionKind`, `DeadlineKind`, `Deadline`, `Territory`, `PlayerState`, `ChoiceAnswer`, `NumericAnswer`, `AnswerValue`, `SubmittedAnswer`, the seven `Turn` variants, `Turn`, and `GameState` with helpers `active_players()`, `current_deadline()`, `free_regions()`, `owned_by(player_id)`, `allocate_deadline(kind, deadline_at)`.

`GameState` carries the `MapDefinition` itself so that adjacency queries stay pure — maps are immutable repo data keyed by `map_id`, unlike the mutable question bank.

Connection status is deliberately **not** a field: presence is a runtime concern and no rule depends on it (ADR-003).

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/game/test_state.py`:

```python
from datetime import UTC, datetime

from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.game.state import (
    AcquisitionKind,
    DeadlineKind,
    GameState,
    Phase,
    PlayerState,
    Territory,
    TerritoryKind,
)
from triviador.domain.ids import DeadlineId, GameId, MapId, PlayerId, RegionId
from triviador.domain.maps.definition import MapDefinition, Region
from triviador.domain.questions.types import QuestionPool

AT = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)


def a_map() -> MapDefinition:
    ids = ["a", "b", "c"]
    return MapDefinition(
        map_id=MapId("t"),
        regions=tuple(Region(RegionId(i), i.upper()) for i in ids),
        adjacency={
            RegionId("a"): frozenset({RegionId("b")}),
            RegionId("b"): frozenset({RegionId("a"), RegionId("c")}),
            RegionId("c"): frozenset({RegionId("b")}),
        },
    )


def a_state() -> GameState:
    defn = a_map()
    return GameState(
        game_id=GameId("g"),
        seq=0,
        next_deadline_id=1,
        map=defn,
        rules=DEFAULT_RULES,
        phase=Phase.BATTLE,
        round_no=1,
        turn_order=(PlayerId("p1"), PlayerId("p2")),
        players={
            PlayerId("p1"): PlayerState(PlayerId("p1"), "One", seat=0, score=0, bonus_score=0,
                                        base_region=RegionId("a"), is_eliminated=False),
            PlayerId("p2"): PlayerState(PlayerId("p2"), "Two", seat=1, score=0, bonus_score=0,
                                        base_region=RegionId("c"), is_eliminated=True),
        },
        territories={
            RegionId("a"): Territory(RegionId("a"), PlayerId("p1"), TerritoryKind.BASE,
                                     PlayerId("p1"), 3, AcquisitionKind.BASE),
            RegionId("b"): Territory(RegionId("b"), None, TerritoryKind.NORMAL, None, None, None),
            RegionId("c"): Territory(RegionId("c"), PlayerId("p2"), TerritoryKind.BASE,
                                     PlayerId("p2"), 3, AcquisitionKind.BASE),
        },
        turn=None,
        pool=QuestionPool(numeric=(), multiple_choice=()),
        winner_id=None,
    )


def test_active_players_excludes_eliminated_and_keeps_turn_order() -> None:
    assert a_state().active_players() == (PlayerId("p1"),)


def test_free_regions_are_the_unowned_ones() -> None:
    assert a_state().free_regions() == (RegionId("b"),)


def test_owned_by_returns_that_players_regions() -> None:
    assert a_state().owned_by(PlayerId("p1")) == (RegionId("a"),)


def test_current_deadline_is_none_without_a_turn() -> None:
    assert a_state().current_deadline() is None


def test_allocate_deadline_increments_the_counter() -> None:
    state = a_state()
    deadline, next_state = state.allocate_deadline(DeadlineKind.ANSWER, AT)
    assert deadline.id == DeadlineId(1)
    assert deadline.deadline_at == AT
    assert next_state.next_deadline_id == 2
    assert state.next_deadline_id == 1, "allocation must not mutate the input"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_state.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'triviador.domain.game.state'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/triviador/domain/game/state.py`:

```python
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from triviador.domain.game.rules import GameRules
from triviador.domain.ids import DeadlineId, GameId, PlayerId, RegionId
from triviador.domain.maps.definition import MapDefinition
from triviador.domain.questions.types import QuestionPool, QuestionSnapshot


class Phase(StrEnum):
    LOBBY = "lobby"
    EXPANSION = "expansion"
    BATTLE = "battle"
    FINISHED = "finished"
    ABORTED = "aborted"


TERMINAL_PHASES = frozenset({Phase.FINISHED, Phase.ABORTED})


class TerritoryKind(StrEnum):
    NORMAL = "normal"
    BASE = "base"


class AcquisitionKind(StrEnum):
    CLAIMED = "claimed"      # taken while unowned: expansion pick or neutral challenge
    CONQUEST = "conquest"    # taken from another player
    BASE = "base"


class DeadlineKind(StrEnum):
    ANSWER = "answer"
    PICK = "pick"
    TARGET_SELECT = "target_select"


@dataclass(frozen=True)
class Deadline:
    id: DeadlineId
    kind: DeadlineKind
    deadline_at: datetime


@dataclass(frozen=True)
class Territory:
    region_id: RegionId
    owner_id: PlayerId | None
    kind: TerritoryKind
    base_owner_id: PlayerId | None
    base_hp: int | None
    acquisition: AcquisitionKind | None


@dataclass(frozen=True)
class PlayerState:
    player_id: PlayerId
    display_name: str
    seat: int
    score: int
    bonus_score: int
    base_region: RegionId | None
    is_eliminated: bool


@dataclass(frozen=True)
class ChoiceAnswer:
    idx: int


@dataclass(frozen=True)
class NumericAnswer:
    value: Decimal


AnswerValue = ChoiceAnswer | NumericAnswer


@dataclass(frozen=True)
class SubmittedAnswer:
    value: AnswerValue
    elapsed_ms: int


@dataclass(frozen=True)
class ExpansionQuestion:
    deadline: Deadline
    question: QuestionSnapshot
    answers: Mapping[PlayerId, SubmittedAnswer]


@dataclass(frozen=True)
class ExpansionPicking:
    deadline: Deadline
    pick_order: tuple[PlayerId, ...]
    grants_remaining: Mapping[PlayerId, int]
    current_picker: PlayerId


@dataclass(frozen=True)
class BattleTargetSelect:
    deadline: Deadline
    attacker_id: PlayerId


@dataclass(frozen=True)
class BattleDuel:
    deadline: Deadline
    attacker_id: PlayerId
    defender_id: PlayerId
    region_id: RegionId
    question: QuestionSnapshot
    answers: Mapping[PlayerId, SubmittedAnswer]


@dataclass(frozen=True)
class BattleTiebreak:
    deadline: Deadline
    attacker_id: PlayerId
    defender_id: PlayerId
    region_id: RegionId
    question: QuestionSnapshot
    answers: Mapping[PlayerId, SubmittedAnswer]


@dataclass(frozen=True)
class NeutralChallenge:
    deadline: Deadline
    attacker_id: PlayerId
    region_id: RegionId
    question: QuestionSnapshot
    answers: Mapping[PlayerId, SubmittedAnswer]


@dataclass(frozen=True)
class FinalTiebreak:
    deadline: Deadline
    contenders: tuple[PlayerId, ...]
    question: QuestionSnapshot
    answers: Mapping[PlayerId, SubmittedAnswer]


Turn = (
    ExpansionQuestion
    | ExpansionPicking
    | BattleTargetSelect
    | BattleDuel
    | BattleTiebreak
    | NeutralChallenge
    | FinalTiebreak
)


@dataclass(frozen=True)
class GameState:
    game_id: GameId
    seq: int
    next_deadline_id: int
    map: MapDefinition
    rules: GameRules
    phase: Phase
    round_no: int
    turn_order: tuple[PlayerId, ...]
    players: Mapping[PlayerId, PlayerState]
    territories: Mapping[RegionId, Territory]
    turn: Turn | None
    pool: QuestionPool
    winner_id: PlayerId | None

    def active_players(self) -> tuple[PlayerId, ...]:
        return tuple(p for p in self.turn_order if not self.players[p].is_eliminated)

    def current_deadline(self) -> Deadline | None:
        return None if self.turn is None else self.turn.deadline

    def free_regions(self) -> tuple[RegionId, ...]:
        return tuple(
            r for r in self.map.region_ids() if self.territories[r].owner_id is None
        )

    def owned_by(self, player_id: PlayerId) -> tuple[RegionId, ...]:
        return tuple(
            r for r in self.map.region_ids() if self.territories[r].owner_id == player_id
        )

    def allocate_deadline(
        self, kind: DeadlineKind, deadline_at: datetime
    ) -> tuple[Deadline, "GameState"]:
        deadline = Deadline(DeadlineId(self.next_deadline_id), kind, deadline_at)
        return deadline, replace(self, next_deadline_id=self.next_deadline_id + 1)
```

- [ ] **Step 4: Run tests and linters**

Run: `cd backend && uv run pytest tests/domain/game/test_state.py -v && uv run ruff check . && uv run mypy`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/domain/game/state.py backend/tests/domain/game/test_state.py
git commit -m "feat(domain): add game state, turn variants and deadline allocation"
```

---

### Task 7: Commands, decision context and rejection codes

**Files:**
- Create: `backend/src/triviador/domain/game/actions.py`
- Test: `backend/tests/domain/game/test_actions.py`

**Interfaces:**
- Consumes: ids (Task 1), `AnswerValue` (Task 6), `QuestionPool` (Task 4).
- Produces: `JoinGame`, `StartGame`, `SubmitAnswer`, `PickRegion`, `SelectAttackTarget`, `ExpireDeadline`, `Surrender`, `AbortGame`, the `Command` union, `WINDOWED_COMMANDS`, `RejectCode`, `RejectedCommand`, `DecisionContext`.

Every windowed command carries `deadline_id`. `actor_id` is present on every player-issued command so the guard pipeline can validate it uniformly.

`DecisionContext` holds **values**, never capabilities: no `Random`, no repository, no clock object — just the results the runtime has already materialised.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/game/test_actions.py`:

```python
import pytest

from triviador.domain.game.actions import (
    WINDOWED_COMMANDS,
    AbortGame,
    ExpireDeadline,
    JoinGame,
    PickRegion,
    RejectCode,
    RejectedCommand,
    SelectAttackTarget,
    SubmitAnswer,
    Surrender,
)
from triviador.domain.game.state import ChoiceAnswer
from triviador.domain.ids import DeadlineId, PlayerId, RegionId


def test_windowed_commands_all_carry_a_deadline_id() -> None:
    assert WINDOWED_COMMANDS == (SubmitAnswer, PickRegion, SelectAttackTarget, ExpireDeadline)
    for cls in WINDOWED_COMMANDS:
        assert "deadline_id" in cls.__dataclass_fields__


def test_non_windowed_commands_do_not_carry_one() -> None:
    for cls in (JoinGame, Surrender, AbortGame):
        assert "deadline_id" not in cls.__dataclass_fields__


def test_rejected_command_exposes_its_code() -> None:
    error = RejectedCommand(RejectCode.NOT_ADJACENT, "region R9 is not adjacent")
    assert error.code is RejectCode.NOT_ADJACENT
    assert "R9" in str(error)


def test_commands_compare_by_value_for_idempotency_checks() -> None:
    a = SubmitAnswer(PlayerId("p1"), DeadlineId(4), ChoiceAnswer(2), elapsed_ms=900)
    b = SubmitAnswer(PlayerId("p1"), DeadlineId(4), ChoiceAnswer(2), elapsed_ms=900)
    assert a == b


def test_rejected_command_is_an_exception() -> None:
    with pytest.raises(RejectedCommand):
        raise RejectedCommand(RejectCode.WRONG_TURN_STATE, "nope")


def test_pick_and_target_carry_a_region() -> None:
    assert PickRegion(PlayerId("p1"), DeadlineId(1), RegionId("a")).region_id == RegionId("a")
    assert SelectAttackTarget(
        PlayerId("p1"), DeadlineId(1), RegionId("b")
    ).region_id == RegionId("b")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_actions.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'triviador.domain.game.actions'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/triviador/domain/game/actions.py`:

```python
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from triviador.domain.game.state import AnswerValue
from triviador.domain.ids import DeadlineId, PlayerId, RegionId
from triviador.domain.questions.types import QuestionPool


@dataclass(frozen=True)
class JoinGame:
    actor_id: PlayerId
    display_name: str


@dataclass(frozen=True)
class StartGame:
    actor_id: PlayerId


@dataclass(frozen=True)
class SubmitAnswer:
    actor_id: PlayerId
    deadline_id: DeadlineId
    value: AnswerValue
    elapsed_ms: int


@dataclass(frozen=True)
class PickRegion:
    actor_id: PlayerId
    deadline_id: DeadlineId
    region_id: RegionId


@dataclass(frozen=True)
class SelectAttackTarget:
    actor_id: PlayerId
    deadline_id: DeadlineId
    region_id: RegionId


@dataclass(frozen=True)
class ExpireDeadline:
    deadline_id: DeadlineId


@dataclass(frozen=True)
class Surrender:
    actor_id: PlayerId


@dataclass(frozen=True)
class AbortGame:
    actor_id: PlayerId


Command = (
    JoinGame
    | StartGame
    | SubmitAnswer
    | PickRegion
    | SelectAttackTarget
    | ExpireDeadline
    | Surrender
    | AbortGame
)

WINDOWED_COMMANDS = (SubmitAnswer, PickRegion, SelectAttackTarget, ExpireDeadline)


class RejectCode(StrEnum):
    NOT_A_PARTICIPANT = "not_a_participant"
    WRONG_TURN_STATE = "wrong_turn_state"
    NOT_YOUR_TURN = "not_your_turn"
    ALREADY_ANSWERED = "already_answered"
    ALREADY_JOINED = "already_joined"
    GAME_FULL = "game_full"
    NOT_ENOUGH_PLAYERS = "not_enough_players"
    QUESTION_POOL_INSUFFICIENT = "question_pool_insufficient"
    UNKNOWN_REGION = "unknown_region"
    REGION_NOT_FREE = "region_not_free"
    OWN_TERRITORY = "own_territory"
    NOT_ADJACENT = "not_adjacent"
    ANSWER_KIND_MISMATCH = "answer_kind_mismatch"


class RejectedCommand(Exception):
    """A command the client should not have sent. Nothing is persisted or broadcast."""

    def __init__(self, code: RejectCode, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DecisionContext:
    """Materialised values, never capabilities.

    The runtime resolves every non-deterministic input before enqueueing, so
    `decide` stays a mathematical function and replay never diverges.
    """

    now: datetime
    shuffled_player_ids: tuple[PlayerId, ...] | None = None
    base_regions: tuple[RegionId, ...] | None = None
    shuffled_region_ids: tuple[RegionId, ...] | None = None
    drawn_pool: QuestionPool | None = None
```

- [ ] **Step 4: Run tests and linters**

Run: `cd backend && uv run pytest tests/domain/game/test_actions.py -v && uv run ruff check . && uv run mypy`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/domain/game/actions.py backend/tests/domain/game/test_actions.py
git commit -m "feat(domain): add commands, reject codes and DecisionContext"
```

---

### Task 8: Domain events

**Files:**
- Create: `backend/src/triviador/domain/game/events.py`
- Test: `backend/tests/domain/game/test_events.py`

**Interfaces:**
- Consumes: ids, `Deadline`, `SubmittedAnswer`, `AnswerValue`, `AcquisitionKind` (Task 6), `QuestionSnapshot`/`QuestionPool` (Task 4), `GameRules` (Task 5), `MapId`.
- Produces: every event class listed in spec §5.5, the `GameEvent` union, and `ScoreReason`.

Two rules this file encodes:
1. **`ScoreChanged` is standalone.** No gameplay event carries `score_delta` or `new_score`. One gameplay event can cause several score effects, or none under a different preset, and analytics must read scoring history without knowing which rules version produced it.
2. **`QuestionPoolDrawn` carries full snapshots, not ids.** After it, the game never reads the question bank again — including on crash recovery.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/game/test_events.py`:

```python
import dataclasses

from triviador.domain.game import events as ev


def test_no_gameplay_event_embeds_score() -> None:
    banned = {"score_delta", "new_score", "new_total_score", "points"}
    for name in dir(ev):
        cls = getattr(ev, name)
        if not dataclasses.is_dataclass(cls) or cls is ev.ScoreChanged:
            continue
        fields = set(getattr(cls, "__dataclass_fields__", {}))
        assert not (fields & banned), f"{name} embeds scoring: {fields & banned}"


def test_score_changed_carries_reason_and_new_total() -> None:
    fields = set(ev.ScoreChanged.__dataclass_fields__)
    assert {"player_id", "delta", "reason", "new_total"} <= fields


def test_question_pool_drawn_carries_snapshots_not_ids() -> None:
    fields = ev.QuestionPoolDrawn.__dataclass_fields__
    assert "pool" in fields
    assert not any("id" in f for f in fields), "the pool must be snapshots, never ids"


def test_question_presented_carries_a_full_snapshot_and_window() -> None:
    fields = set(ev.QuestionPresented.__dataclass_fields__)
    assert {"question", "deadline"} <= fields


def test_every_event_is_frozen() -> None:
    for name in dir(ev):
        cls = getattr(ev, name)
        if dataclasses.is_dataclass(cls):
            assert cls.__dataclass_params__.frozen, f"{name} must be frozen"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_events.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'triviador.domain.game.events'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/triviador/domain/game/events.py`:

```python
from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from triviador.domain.game.rules import GameRules
from triviador.domain.game.state import AcquisitionKind, Deadline, SubmittedAnswer
from triviador.domain.ids import MapId, PlayerId, RegionId
from triviador.domain.questions.types import QuestionPool, QuestionSnapshot


class ScoreReason(StrEnum):
    BASE = "base"
    TERRITORY = "territory"
    CONQUEST = "conquest"
    DEFENSE = "defense"
    TERRITORY_LOST = "territory_lost"
    BASE_LOST = "base_lost"
    BONUS = "bonus"


# --- lifecycle -------------------------------------------------------------

@dataclass(frozen=True)
class GameCreated:
    map_id: MapId
    rules: GameRules
    host_id: PlayerId


@dataclass(frozen=True)
class PlayerJoined:
    player_id: PlayerId
    display_name: str
    seat: int


@dataclass(frozen=True)
class PlayerLeft:
    player_id: PlayerId


@dataclass(frozen=True)
class GameStarted:
    turn_order: tuple[PlayerId, ...]


@dataclass(frozen=True)
class BasesAssigned:
    assignments: Mapping[PlayerId, RegionId]


@dataclass(frozen=True)
class QuestionPoolDrawn:
    pool: QuestionPool


@dataclass(frozen=True)
class GameFinished:
    winner_id: PlayerId | None
    final_scores: Mapping[PlayerId, int]


@dataclass(frozen=True)
class GameAborted:
    reason: str


# --- questions -------------------------------------------------------------

@dataclass(frozen=True)
class QuestionPresented:
    question: QuestionSnapshot
    deadline: Deadline


@dataclass(frozen=True)
class AnswerSubmitted:
    player_id: PlayerId
    answer: SubmittedAnswer


@dataclass(frozen=True)
class AnswerWindowClosed:
    deadline: Deadline


@dataclass(frozen=True)
class QuestionResolved:
    correct_choice_index: int | None
    correct_value: Decimal | None
    ranking: tuple[PlayerId, ...]
    correct_players: tuple[PlayerId, ...]


# --- expansion -------------------------------------------------------------

@dataclass(frozen=True)
class ExpansionRoundStarted:
    round_no: int


@dataclass(frozen=True)
class PicksGranted:
    pick_order: tuple[PlayerId, ...]
    grants: Mapping[PlayerId, int]
    deadline: Deadline


@dataclass(frozen=True)
class TerritoryClaimed:
    player_id: PlayerId
    region_id: RegionId
    acquisition: AcquisitionKind
    automatic: bool


@dataclass(frozen=True)
class ExpansionRoundCompleted:
    round_no: int


# --- battle ----------------------------------------------------------------

@dataclass(frozen=True)
class BattleRoundStarted:
    round_no: int


@dataclass(frozen=True)
class TurnStarted:
    attacker_id: PlayerId
    deadline: Deadline


@dataclass(frozen=True)
class TurnSkipped:
    attacker_id: PlayerId
    reason: str


@dataclass(frozen=True)
class TurnAborted:
    reason: str


@dataclass(frozen=True)
class AttackDeclared:
    attacker_id: PlayerId
    defender_id: PlayerId | None
    region_id: RegionId


@dataclass(frozen=True)
class DuelResolved:
    winner_id: PlayerId | None


@dataclass(frozen=True)
class TiebreakStarted:
    region_id: RegionId


@dataclass(frozen=True)
class TerritoryCaptured:
    region_id: RegionId
    from_player_id: PlayerId | None
    to_player_id: PlayerId
    acquisition: AcquisitionKind


@dataclass(frozen=True)
class NeutralTerritoryCaptured:
    region_id: RegionId
    player_id: PlayerId


@dataclass(frozen=True)
class NeutralAttackFailed:
    region_id: RegionId
    attacker_id: PlayerId


@dataclass(frozen=True)
class DefenseHeld:
    region_id: RegionId
    defender_id: PlayerId


@dataclass(frozen=True)
class BaseDamaged:
    region_id: RegionId
    hp_remaining: int


@dataclass(frozen=True)
class BaseDestroyed:
    region_id: RegionId
    owner_id: PlayerId


@dataclass(frozen=True)
class BattleRoundCompleted:
    round_no: int


# --- scoring and terminal --------------------------------------------------

@dataclass(frozen=True)
class ScoreChanged:
    player_id: PlayerId
    delta: int
    reason: ScoreReason
    new_total: int


@dataclass(frozen=True)
class PlayerEliminated:
    player_id: PlayerId


@dataclass(frozen=True)
class PlayerSurrendered:
    player_id: PlayerId


@dataclass(frozen=True)
class TerritoryNeutralized:
    region_id: RegionId
    former_owner_id: PlayerId


@dataclass(frozen=True)
class FinalTiebreakStarted:
    contenders: tuple[PlayerId, ...]


GameEvent = (
    GameCreated
    | PlayerJoined
    | PlayerLeft
    | GameStarted
    | BasesAssigned
    | QuestionPoolDrawn
    | GameFinished
    | GameAborted
    | QuestionPresented
    | AnswerSubmitted
    | AnswerWindowClosed
    | QuestionResolved
    | ExpansionRoundStarted
    | PicksGranted
    | TerritoryClaimed
    | ExpansionRoundCompleted
    | BattleRoundStarted
    | TurnStarted
    | TurnSkipped
    | TurnAborted
    | AttackDeclared
    | DuelResolved
    | TiebreakStarted
    | TerritoryCaptured
    | NeutralTerritoryCaptured
    | NeutralAttackFailed
    | DefenseHeld
    | BaseDamaged
    | BaseDestroyed
    | BattleRoundCompleted
    | ScoreChanged
    | PlayerEliminated
    | PlayerSurrendered
    | TerritoryNeutralized
    | FinalTiebreakStarted
)
```

- [ ] **Step 4: Run tests and linters**

Run: `cd backend && uv run pytest tests/domain/game/test_events.py -v && uv run ruff check . && uv run mypy`
Expected: PASS. The first test is the guard that keeps scoring out of gameplay events forever.

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/domain/game/events.py backend/tests/domain/game/test_events.py
git commit -m "feat(domain): add the full game event taxonomy"
```

---

### Task 9: Scoring

**Files:**
- Create: `backend/src/triviador/domain/game/scoring.py`
- Test: `backend/tests/domain/game/test_scoring.py`

**Interfaces:**
- Consumes: `Territory`, `GameState`, `AcquisitionKind` (Task 6), `GameRules` (Task 5).
- Produces: `holding_value(territory, rules) -> int`, `holdings_value(state, player_id) -> int`, `expected_score(state, player_id) -> int`.

`expected_score` is `holdings_value + bonus_score`. It exists so the property test in Task 21 can assert the invariant against every state the reducer produces.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/game/test_scoring.py`:

```python
from dataclasses import replace

from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.game.scoring import expected_score, holding_value, holdings_value
from triviador.domain.game.state import AcquisitionKind, Territory, TerritoryKind
from triviador.domain.ids import PlayerId, RegionId
from tests.domain.game.test_state import a_state


def a_territory(acquisition: AcquisitionKind | None) -> Territory:
    return Territory(RegionId("x"), PlayerId("p1"), TerritoryKind.NORMAL, None, None, acquisition)


def test_holding_value_is_derived_from_acquisition_not_region_type() -> None:
    assert holding_value(a_territory(AcquisitionKind.CLAIMED), DEFAULT_RULES) == 200
    assert holding_value(a_territory(AcquisitionKind.CONQUEST), DEFAULT_RULES) == 400
    assert holding_value(a_territory(AcquisitionKind.BASE), DEFAULT_RULES) == 1000
    assert holding_value(a_territory(None), DEFAULT_RULES) == 0


def test_the_same_region_is_worth_more_to_its_conqueror() -> None:
    claimed = holding_value(a_territory(AcquisitionKind.CLAIMED), DEFAULT_RULES)
    conquered = holding_value(a_territory(AcquisitionKind.CONQUEST), DEFAULT_RULES)
    assert conquered > claimed


def test_holdings_value_sums_only_that_players_regions() -> None:
    state = a_state()
    assert holdings_value(state, PlayerId("p1")) == 1000
    assert holdings_value(state, PlayerId("p2")) == 1000


def test_expected_score_adds_bonuses_to_holdings() -> None:
    state = a_state()
    p1 = state.players[PlayerId("p1")]
    state = replace(state, players={**state.players, PlayerId("p1"): replace(p1, bonus_score=300)})
    assert expected_score(state, PlayerId("p1")) == 1300


def test_bonuses_survive_losing_every_holding() -> None:
    state = a_state()
    p1 = state.players[PlayerId("p1")]
    stripped = {
        r: replace(t, owner_id=None, acquisition=None) for r, t in state.territories.items()
    }
    state = replace(
        state,
        territories=stripped,
        players={**state.players, PlayerId("p1"): replace(p1, bonus_score=300)},
    )
    assert expected_score(state, PlayerId("p1")) == 300
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_scoring.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'triviador.domain.game.scoring'`

- [ ] **Step 3: Write the implementation**

Create `backend/src/triviador/domain/game/scoring.py`:

```python
from triviador.domain.game.rules import GameRules
from triviador.domain.game.state import AcquisitionKind, GameState, Territory
from triviador.domain.ids import PlayerId


def holding_value(territory: Territory, rules: GameRules) -> int:
    """Worth of a territory to its current owner.

    Derived from how it was acquired, not from the region type: the same region
    is worth pts_territory to whoever claimed it and pts_conquered to whoever
    later takes it by force.
    """
    match territory.acquisition:
        case AcquisitionKind.CLAIMED:
            return rules.pts_territory
        case AcquisitionKind.CONQUEST:
            return rules.pts_conquered
        case AcquisitionKind.BASE:
            return rules.pts_base
        case None:
            return 0


def holdings_value(state: GameState, player_id: PlayerId) -> int:
    return sum(
        holding_value(state.territories[region_id], state.rules)
        for region_id in state.owned_by(player_id)
    )


def expected_score(state: GameState, player_id: PlayerId) -> int:
    """score = current holdings + accumulated non-territory bonuses."""
    return holdings_value(state, player_id) + state.players[player_id].bonus_score
```

- [ ] **Step 4: Run tests and linters**

Run: `cd backend && uv run pytest tests/domain/game/test_scoring.py -v && uv run ruff check . && uv run mypy`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/domain/game/scoring.py backend/tests/domain/game/test_scoring.py
git commit -m "feat(domain): add acquisition-derived scoring"
```

---

### Task 10: Reducer skeleton — guard pipeline, legality table and evolve dispatch

**Files:**
- Create: `backend/src/triviador/domain/game/reducer.py`
- Create: `backend/tests/conftest.py`
- Test: `backend/tests/domain/game/test_guards.py`

**Interfaces:**
- Consumes: everything from Tasks 4–9.
- Produces: `decide(state, command, ctx) -> tuple[GameEvent, ...]`, `evolve(state, event) -> GameState`, `fold(state, events) -> GameState`, and `LEGAL_COMMANDS: Mapping[type[Turn] | None, frozenset[type[Command]]]` keyed by turn variant (with `None` for the no-turn case, disambiguated by phase).

The guard order is fixed and must not be reordered: stale-window checking precedes actor validation so that a stale packet from a since-eliminated player is silently dropped rather than answered with an error.

Later tasks fill in the per-turn resolution functions; this task establishes the frame and proves the guards.

- [ ] **Step 1: Write shared builders**

Create `backend/tests/conftest.py`:

```python
"""Shared builders. Every test constructs states through these."""

from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from triviador.domain.game.rules import DEFAULT_RULES, GameRules
from triviador.domain.game.state import (
    AcquisitionKind,
    GameState,
    Phase,
    PlayerState,
    Territory,
    TerritoryKind,
)
from triviador.domain.ids import (
    CategoryId,
    GameId,
    MapId,
    PlayerId,
    QuestionId,
    RegionId,
)
from triviador.domain.maps.definition import MapDefinition, Region
from triviador.domain.questions.types import (
    CategorySnapshot,
    ChoiceSnapshot,
    Difficulty,
    QuestionKind,
    QuestionPool,
    QuestionSnapshot,
)

NOW = datetime(2026, 8, 7, 12, 0, tzinfo=UTC)
CATEGORY = CategorySnapshot(CategoryId("c"), "general", "General")

# A 3x3 grid: nine regions, four-corner independent set, easy to reason about.
GRID_IDS = [f"r{i}" for i in range(9)]


def grid_map() -> MapDefinition:
    def neighbours(i: int) -> set[str]:
        row, col = divmod(i, 3)
        out: set[str] = set()
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            r, c = row + dr, col + dc
            if 0 <= r < 3 and 0 <= c < 3:
                out.add(f"r{r * 3 + c}")
        return out

    return MapDefinition(
        map_id=MapId("grid"),
        regions=tuple(Region(RegionId(rid), rid.upper()) for rid in GRID_IDS),
        adjacency={
            RegionId(f"r{i}"): frozenset(RegionId(n) for n in neighbours(i)) for i in range(9)
        },
    )


def numeric_question(n: int, answer: int) -> QuestionSnapshot:
    return QuestionSnapshot(
        question_id=QuestionId(f"n{n}"), version=1, kind=QuestionKind.NUMERIC,
        prompt=f"numeric {n}?", category=CATEGORY, difficulty=Difficulty.MEDIUM,
        choices=None, numeric_answer=Decimal(answer), unit=None, media_asset_id=None,
    )


def mc_question(n: int, correct: int = 0) -> QuestionSnapshot:
    return QuestionSnapshot(
        question_id=QuestionId(f"m{n}"), version=1, kind=QuestionKind.MULTIPLE_CHOICE,
        prompt=f"mc {n}?", category=CATEGORY, difficulty=Difficulty.EASY,
        choices=tuple(
            ChoiceSnapshot(i, chr(ord("a") + i), is_correct=(i == correct), media_asset_id=None)
            for i in range(4)
        ),
        numeric_answer=None, unit=None, media_asset_id=None,
    )


def full_pool(numeric: int = 40, mc: int = 40) -> QuestionPool:
    return QuestionPool(
        numeric=tuple(numeric_question(i, 100 + i) for i in range(numeric)),
        multiple_choice=tuple(mc_question(i) for i in range(mc)),
    )


def a_player(pid: str, seat: int, **overrides: object) -> PlayerState:
    base = PlayerState(
        player_id=PlayerId(pid), display_name=pid.upper(), seat=seat,
        score=0, bonus_score=0, base_region=None, is_eliminated=False,
    )
    return replace(base, **overrides)  # type: ignore[arg-type]


def empty_territories() -> dict[RegionId, Territory]:
    return {
        RegionId(rid): Territory(RegionId(rid), None, TerritoryKind.NORMAL, None, None, None)
        for rid in GRID_IDS
    }


def lobby_state(
    players: Mapping[str, int] | None = None,
    rules: GameRules = DEFAULT_RULES,
) -> GameState:
    seats = players if players is not None else {"p1": 0, "p2": 1, "p3": 2}
    return GameState(
        game_id=GameId("g1"), seq=0, next_deadline_id=1, map=grid_map(), rules=rules,
        phase=Phase.LOBBY, round_no=0, turn_order=tuple(PlayerId(p) for p in seats),
        players={PlayerId(p): a_player(p, s) for p, s in seats.items()},
        territories=empty_territories(), turn=None,
        pool=QuestionPool(numeric=(), multiple_choice=()), winner_id=None,
    )


def own(state: GameState, region: str, player: str,
        acquisition: AcquisitionKind = AcquisitionKind.CLAIMED) -> GameState:
    rid = RegionId(region)
    territory = replace(state.territories[rid], owner_id=PlayerId(player), acquisition=acquisition)
    new_territories = {**state.territories, rid: territory}
    updated = replace(state, territories=new_territories)
    player_state = updated.players[PlayerId(player)]
    from triviador.domain.game.scoring import expected_score

    return replace(
        updated,
        players={
            **updated.players,
            PlayerId(player): replace(
                player_state, score=expected_score(updated, PlayerId(player))
            ),
        },
    )


@pytest.fixture
def now() -> datetime:
    return NOW
```

- [ ] **Step 2: Write the failing guard test**

Create `backend/tests/domain/game/test_guards.py`:

```python
from dataclasses import replace

import pytest

from tests.conftest import NOW, lobby_state
from triviador.domain.game.actions import (
    AbortGame,
    DecisionContext,
    ExpireDeadline,
    PickRegion,
    RejectCode,
    RejectedCommand,
    SubmitAnswer,
)
from triviador.domain.game.reducer import decide
from triviador.domain.game.state import ChoiceAnswer, Phase
from triviador.domain.ids import DeadlineId, PlayerId, RegionId

CTX = DecisionContext(now=NOW)


def test_terminal_phase_ignores_everything() -> None:
    for phase in (Phase.FINISHED, Phase.ABORTED):
        state = replace(lobby_state(), phase=phase)
        assert decide(state, SubmitAnswer(PlayerId("p1"), DeadlineId(1),
                                          ChoiceAnswer(0), 100), CTX) == ()
        assert decide(state, ExpireDeadline(DeadlineId(1)), CTX) == ()


def test_terminal_phase_rejects_abort() -> None:
    state = replace(lobby_state(), phase=Phase.FINISHED)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, AbortGame(PlayerId("p1")), CTX)
    assert exc.value.code is RejectCode.WRONG_TURN_STATE


def test_stale_window_is_ignored_not_rejected() -> None:
    state = lobby_state()  # turn is None, so no window matches
    assert decide(state, PickRegion(PlayerId("p1"), DeadlineId(99), RegionId("r0")), CTX) == ()


def test_stale_window_is_checked_before_actor_validity() -> None:
    """A stale packet from a non-participant must be silent, not an error."""
    state = lobby_state()
    ghost = PlayerId("nobody")
    assert decide(state, PickRegion(ghost, DeadlineId(99), RegionId("r0")), CTX) == ()


def test_non_participant_in_the_current_window_is_rejected() -> None:
    state = lobby_state()
    with pytest.raises(RejectedCommand) as exc:
        decide(state, AbortGame(PlayerId("nobody")), CTX)
    assert exc.value.code is RejectCode.NOT_A_PARTICIPANT


def test_windowed_command_with_no_open_window_is_silent_not_rejected() -> None:
    """Guard 2 fires before guard 5: with turn=None there is no window to match."""
    state = lobby_state()
    command = SubmitAnswer(PlayerId("p1"), DeadlineId(1), ChoiceAnswer(0), 10)
    assert decide(state, command, CTX) == ()
```

Turn-legality rejection (guard 5) cannot be exercised until a window exists, so
it is covered by Task 20's matrix rather than here.

- [ ] **Step 3: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_guards.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'triviador.domain.game.reducer'`

- [ ] **Step 4: Write the reducer frame**

Create `backend/src/triviador/domain/game/reducer.py`:

```python
"""The pure game reducer.

    events    = decide(state, command, ctx)
    new_state = evolve(state, event)

`decide` answers *what happened*; `evolve` answers *what the state becomes*.
Replay is therefore fold(evolve, events) and needs no context at all.
"""

from collections.abc import Iterable, Mapping
from dataclasses import replace

from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    WINDOWED_COMMANDS,
    AbortGame,
    Command,
    DecisionContext,
    ExpireDeadline,
    JoinGame,
    PickRegion,
    RejectCode,
    RejectedCommand,
    SelectAttackTarget,
    StartGame,
    SubmitAnswer,
    Surrender,
)
from triviador.domain.game.state import (
    TERMINAL_PHASES,
    BattleDuel,
    BattleTargetSelect,
    BattleTiebreak,
    ExpansionPicking,
    ExpansionQuestion,
    FinalTiebreak,
    GameState,
    NeutralChallenge,
    Phase,
    Turn,
)

# Which commands are legal for which turn variant. `None` means "no open turn",
# which in a non-terminal phase can only be LOBBY.
LEGAL_COMMANDS: Mapping[type[Turn] | None, frozenset[type[Command]]] = {
    None: frozenset({JoinGame, StartGame, Surrender, AbortGame}),
    ExpansionQuestion: frozenset({SubmitAnswer, ExpireDeadline, Surrender, AbortGame}),
    ExpansionPicking: frozenset({PickRegion, ExpireDeadline, Surrender, AbortGame}),
    BattleTargetSelect: frozenset({SelectAttackTarget, ExpireDeadline, Surrender, AbortGame}),
    BattleDuel: frozenset({SubmitAnswer, ExpireDeadline, Surrender, AbortGame}),
    BattleTiebreak: frozenset({SubmitAnswer, ExpireDeadline, Surrender, AbortGame}),
    NeutralChallenge: frozenset({SubmitAnswer, ExpireDeadline, Surrender, AbortGame}),
    FinalTiebreak: frozenset({SubmitAnswer, ExpireDeadline, AbortGame}),
}


def decide(
    state: GameState, command: Command, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    # Guard 1 — terminal phases accept nothing.
    if state.phase in TERMINAL_PHASES:
        if isinstance(command, (JoinGame, StartGame, AbortGame)):
            raise RejectedCommand(
                RejectCode.WRONG_TURN_STATE, f"game is {state.phase}"
            )
        return ()

    # Guard 2 — stale window. Deliberately before actor validation: a packet
    # from a window that has already closed is a benign race, never an error.
    if isinstance(command, WINDOWED_COMMANDS):
        current = state.current_deadline()
        if current is None or current.id != command.deadline_id:
            return ()

    # Guard 3 — actor validity.
    actor_id = getattr(command, "actor_id", None)
    if actor_id is not None and not isinstance(command, JoinGame):
        player = state.players.get(actor_id)
        if player is None or player.is_eliminated:
            raise RejectedCommand(
                RejectCode.NOT_A_PARTICIPANT, f"{actor_id!r} is not an active player"
            )

    # Guard 4 — a timer that fired early.
    if isinstance(command, ExpireDeadline):
        current = state.current_deadline()
        assert current is not None  # guaranteed by guard 2
        if ctx.now < current.deadline_at:
            return ()

    # Guard 5 — command legality for this turn.
    turn_key = type(state.turn) if state.turn is not None else None
    if type(command) not in LEGAL_COMMANDS[turn_key]:
        raise RejectedCommand(
            RejectCode.WRONG_TURN_STATE,
            f"{type(command).__name__} is not legal in {turn_key and turn_key.__name__}",
        )

    # Guards 6-7 — domain constraints and event production, per turn.
    return _dispatch(state, command, ctx)


def _dispatch(
    state: GameState, command: Command, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    raise NotImplementedError("filled in by later tasks")


def evolve(state: GameState, event: ev.GameEvent) -> GameState:
    """Apply one event. Always advances seq; never consults anything but the event."""
    return replace(_apply(state, event), seq=state.seq + 1)


def _apply(state: GameState, event: ev.GameEvent) -> GameState:
    raise NotImplementedError("filled in by later tasks")


def fold(state: GameState, events: Iterable[ev.GameEvent]) -> GameState:
    for event in events:
        state = evolve(state, event)
    return state
```

Note `Phase` is imported for later tasks; if `ruff` flags it as unused at this
stage, remove it and re-add it in Task 11.

- [ ] **Step 5: Run tests and linters**

Run: `cd backend && uv run pytest tests/domain/game/test_guards.py -v && uv run ruff check . && uv run mypy`
Expected: PASS — the five guard tests never reach `_dispatch`.

- [ ] **Step 6: Commit**

```bash
git add backend/tests/conftest.py backend/src/triviador/domain/game/reducer.py backend/tests/domain/game/test_guards.py
git commit -m "feat(domain): add reducer frame, guard pipeline and legality table"
```

---

### Task 11: Starting a game — join, bases, pool, first question

**Files:**
- Modify: `backend/src/triviador/domain/game/reducer.py`
- Test: `backend/tests/domain/game/test_start.py`

**Interfaces:**
- Consumes: `LEGAL_COMMANDS`, `decide`, `evolve`, `fold` (Task 10).
- Produces: handling for `JoinGame` and `StartGame`; `_open_expansion_question(state, ctx) -> tuple[events, state]` used again in Task 13.

`StartGame` is where all remaining non-determinism lives: base regions, turn order, and the question pool all arrive through `ctx`, are recorded in events, and are never re-derived.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/game/test_start.py`:

```python
from dataclasses import replace

import pytest

from tests.conftest import NOW, full_pool, lobby_state
from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    DecisionContext,
    JoinGame,
    RejectCode,
    RejectedCommand,
    StartGame,
)
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.state import ExpansionQuestion, Phase, TerritoryKind
from triviador.domain.ids import PlayerId, RegionId
from triviador.domain.questions.types import QuestionPool

P1, P2, P3 = PlayerId("p1"), PlayerId("p2"), PlayerId("p3")
BASES = (RegionId("r0"), RegionId("r2"), RegionId("r6"))


def start_ctx() -> DecisionContext:
    return DecisionContext(
        now=NOW, shuffled_player_ids=(P1, P2, P3), base_regions=BASES, drawn_pool=full_pool()
    )


def test_joining_an_empty_lobby_emits_player_joined() -> None:
    state = lobby_state(players={})
    events = decide(state, JoinGame(P1, "One"), DecisionContext(now=NOW))
    assert events == (ev.PlayerJoined(P1, "One", seat=0),)


def test_joining_twice_is_rejected() -> None:
    state = lobby_state(players={"p1": 0})
    with pytest.raises(RejectedCommand) as exc:
        decide(state, JoinGame(P1, "One"), DecisionContext(now=NOW))
    assert exc.value.code is RejectCode.ALREADY_JOINED


def test_joining_a_full_lobby_is_rejected() -> None:
    state = lobby_state()  # 3 players, player_count is 3
    with pytest.raises(RejectedCommand) as exc:
        decide(state, JoinGame(PlayerId("p4"), "Four"), DecisionContext(now=NOW))
    assert exc.value.code is RejectCode.GAME_FULL


def test_starting_short_handed_is_rejected() -> None:
    state = lobby_state(players={"p1": 0, "p2": 1})
    with pytest.raises(RejectedCommand) as exc:
        decide(state, StartGame(P1), start_ctx())
    assert exc.value.code is RejectCode.NOT_ENOUGH_PLAYERS


def test_starting_without_enough_questions_is_rejected() -> None:
    ctx = replace(start_ctx(), drawn_pool=QuestionPool(numeric=(), multiple_choice=()))
    with pytest.raises(RejectedCommand) as exc:
        decide(lobby_state(), StartGame(P1), ctx)
    assert exc.value.code is RejectCode.QUESTION_POOL_INSUFFICIENT


def test_start_emits_the_full_opening_sequence() -> None:
    events = decide(lobby_state(), StartGame(P1), start_ctx())
    kinds = [type(e) for e in events]
    assert kinds == [
        ev.GameStarted,
        ev.BasesAssigned,
        ev.ScoreChanged, ev.ScoreChanged, ev.ScoreChanged,
        ev.QuestionPoolDrawn,
        ev.ExpansionRoundStarted,
        ev.QuestionPresented,
    ]


def test_start_records_the_pool_as_snapshots_not_ids() -> None:
    events = decide(lobby_state(), StartGame(P1), start_ctx())
    drawn = next(e for e in events if isinstance(e, ev.QuestionPoolDrawn))
    assert drawn.pool.numeric[0].prompt == "numeric 0?"


def test_after_start_bases_are_owned_and_scored() -> None:
    state = fold(lobby_state(), decide(lobby_state(), StartGame(P1), start_ctx()))
    assert state.phase is Phase.EXPANSION
    assert state.round_no == 1
    for player, region in zip((P1, P2, P3), BASES, strict=True):
        territory = state.territories[region]
        assert territory.owner_id == player
        assert territory.kind is TerritoryKind.BASE
        assert territory.base_hp == state.rules.base_hp
        assert state.players[player].score == state.rules.pts_base
        assert state.players[player].base_region == region


def test_after_start_an_expansion_question_window_is_open() -> None:
    state = fold(lobby_state(), decide(lobby_state(), StartGame(P1), start_ctx()))
    assert isinstance(state.turn, ExpansionQuestion)
    assert state.turn.question.prompt == "numeric 0?"
    assert state.turn.deadline.deadline_at > NOW
    assert state.pool.numeric_used == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_start.py -v`
Expected: FAIL with `NotImplementedError: filled in by later tasks`

- [ ] **Step 3: Implement decide branches for join and start**

In `reducer.py`, replace `_dispatch` with a real dispatcher and add the handlers. Add these imports at the top: `from datetime import timedelta`, `from triviador.domain.game.rules import required_question_budget`, `from triviador.domain.game.scoring import expected_score, holding_value`, `from triviador.domain.game.state import AcquisitionKind, Deadline, DeadlineKind, PlayerState, Territory, TerritoryKind`, `from triviador.domain.ids import PlayerId, RegionId`.

```python
def _dispatch(
    state: GameState, command: Command, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    match command:
        case JoinGame():
            return _decide_join(state, command)
        case StartGame():
            return _decide_start(state, ctx)
    raise NotImplementedError(f"no handler for {type(command).__name__}")


def _decide_join(state: GameState, command: JoinGame) -> tuple[ev.GameEvent, ...]:
    if command.actor_id in state.players:
        raise RejectedCommand(RejectCode.ALREADY_JOINED, f"{command.actor_id!r} already joined")
    if len(state.players) >= state.rules.player_count:
        raise RejectedCommand(RejectCode.GAME_FULL, "lobby is full")
    return (ev.PlayerJoined(command.actor_id, command.display_name, seat=len(state.players)),)


def _decide_start(state: GameState, ctx: DecisionContext) -> tuple[ev.GameEvent, ...]:
    if len(state.players) != state.rules.player_count:
        raise RejectedCommand(
            RejectCode.NOT_ENOUGH_PLAYERS,
            f"need {state.rules.player_count} players, have {len(state.players)}",
        )

    pool = ctx.drawn_pool
    if pool is None or not pool.covers(required_question_budget(state.rules)):
        raise RejectedCommand(
            RejectCode.QUESTION_POOL_INSUFFICIENT, "question bank cannot cover this preset"
        )

    order = ctx.shuffled_player_ids
    bases = ctx.base_regions
    if order is None or bases is None or len(bases) != len(order):
        raise RejectedCommand(RejectCode.WRONG_TURN_STATE, "start context is incomplete")

    assignments = dict(zip(order, bases, strict=True))
    events: list[ev.GameEvent] = [ev.GameStarted(order), ev.BasesAssigned(assignments)]
    for player_id in order:
        events.append(
            ev.ScoreChanged(player_id, state.rules.pts_base, ev.ScoreReason.BASE,
                            new_total=state.rules.pts_base)
        )
    events.append(ev.QuestionPoolDrawn(pool))

    # Fold what we have so the question window is opened against real state.
    seeded = fold(state, events)
    events.append(ev.ExpansionRoundStarted(1))
    seeded = evolve(seeded, events[-1])
    question_events, _ = _open_expansion_question(seeded, ctx)
    events.extend(question_events)
    return tuple(events)


def _open_expansion_question(
    state: GameState, ctx: DecisionContext
) -> tuple[tuple[ev.GameEvent, ...], GameState]:
    question, _ = state.pool.next_numeric()
    deadline, _ = state.allocate_deadline(
        DeadlineKind.ANSWER, ctx.now + timedelta(milliseconds=state.rules.answer_timeout_ms)
    )
    event = ev.QuestionPresented(question, deadline)
    return (event,), evolve(state, event)
```

- [ ] **Step 4: Implement the matching evolve branches**

Replace `_apply` in `reducer.py`:

```python
def _apply(state: GameState, event: ev.GameEvent) -> GameState:
    match event:
        case ev.PlayerJoined(player_id=pid, display_name=name, seat=seat):
            player = PlayerState(pid, name, seat, score=0, bonus_score=0,
                                 base_region=None, is_eliminated=False)
            return replace(
                state,
                players={**state.players, pid: player},
                turn_order=(*state.turn_order, pid),
            )

        case ev.GameStarted(turn_order=order):
            return replace(state, turn_order=order, phase=Phase.EXPANSION)

        case ev.BasesAssigned(assignments=assignments):
            territories = dict(state.territories)
            players = dict(state.players)
            for player_id, region_id in assignments.items():
                territories[region_id] = Territory(
                    region_id=region_id, owner_id=player_id, kind=TerritoryKind.BASE,
                    base_owner_id=player_id, base_hp=state.rules.base_hp,
                    acquisition=AcquisitionKind.BASE,
                )
                players[player_id] = replace(players[player_id], base_region=region_id)
            return replace(state, territories=territories, players=players)

        case ev.ScoreChanged(player_id=pid, reason=reason, delta=delta, new_total=total):
            player = state.players[pid]
            bonus = player.bonus_score
            if reason in (ev.ScoreReason.DEFENSE, ev.ScoreReason.BONUS):
                bonus += delta
            return replace(
                state,
                players={**state.players, pid: replace(player, score=total, bonus_score=bonus)},
            )

        case ev.QuestionPoolDrawn(pool=pool):
            return replace(state, pool=pool)

        case ev.ExpansionRoundStarted(round_no=round_no):
            return replace(state, phase=Phase.EXPANSION, round_no=round_no, turn=None)

        case ev.QuestionPresented(question=question, deadline=deadline):
            return _present_question(state, question, deadline)

    raise NotImplementedError(f"no evolve branch for {type(event).__name__}")


def _present_question(
    state: GameState, question: QuestionSnapshot, deadline: Deadline
) -> GameState:
    """Open a question window on whatever turn shape the phase calls for."""
    from triviador.domain.questions.types import QuestionKind

    if question.kind is QuestionKind.NUMERIC:
        _, pool = state.pool.next_numeric()
    else:
        _, pool = state.pool.next_multiple_choice()
    base = replace(state, pool=pool, next_deadline_id=max(state.next_deadline_id, deadline.id + 1))

    if state.phase is Phase.EXPANSION:
        return replace(base, turn=ExpansionQuestion(deadline, question, answers={}))
    raise NotImplementedError("battle question windows arrive in Task 15")
```

Add `from triviador.domain.questions.types import QuestionSnapshot` to the imports.

- [ ] **Step 5: Run tests and linters**

Run: `cd backend && uv run pytest tests/domain/game/test_start.py -v && uv run ruff check . && uv run mypy`
Expected: PASS (9 tests)

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/domain/game/reducer.py backend/tests/domain/game/test_start.py
git commit -m "feat(domain): implement join, start, base assignment and pool draw"
```

---

### Task 12: Expansion question resolution

**Files:**
- Modify: `backend/src/triviador/domain/game/reducer.py`
- Test: `backend/tests/domain/game/test_expansion_question.py`

**Interfaces:**
- Consumes: `_open_expansion_question` (Task 11).
- Produces: `SubmitAnswer` and `ExpireDeadline` handling for `ExpansionQuestion`; `_rank_numeric(question, answers, seats) -> tuple[PlayerId, ...]` reused by `FinalTiebreak` in Task 18.

Ranking: `|guess − correct|` ascending, ties by `elapsed_ms` ascending, non-answerers last ordered by seat. Seat ordering is what makes the tail deterministic, which the replay property test depends on.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/game/test_expansion_question.py`:

```python
from decimal import Decimal

import pytest

from tests.conftest import NOW, full_pool, lobby_state
from tests.domain.game.test_start import BASES, P1, P2, P3, start_ctx
from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    DecisionContext,
    ExpireDeadline,
    RejectCode,
    RejectedCommand,
    StartGame,
    SubmitAnswer,
)
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.state import (
    ChoiceAnswer,
    ExpansionPicking,
    ExpansionQuestion,
    GameState,
    NumericAnswer,
)
from datetime import timedelta


def started() -> GameState:
    base = lobby_state()
    return fold(base, decide(base, StartGame(P1), start_ctx()))


def answer(state: GameState, player, value: int, elapsed: int) -> SubmitAnswer:
    assert isinstance(state.turn, ExpansionQuestion)
    return SubmitAnswer(player, state.turn.deadline.id, NumericAnswer(Decimal(value)), elapsed)


def test_first_answer_only_records_it() -> None:
    state = started()
    events = decide(state, answer(state, P1, 100, 500), DecisionContext(now=NOW))
    assert [type(e) for e in events] == [ev.AnswerSubmitted]


def test_repeating_the_same_answer_is_ignored() -> None:
    state = started()
    state = fold(state, decide(state, answer(state, P1, 100, 500), DecisionContext(now=NOW)))
    assert decide(state, answer(state, P1, 100, 500), DecisionContext(now=NOW)) == ()


def test_changing_the_answer_is_rejected() -> None:
    state = started()
    state = fold(state, decide(state, answer(state, P1, 100, 500), DecisionContext(now=NOW)))
    with pytest.raises(RejectedCommand) as exc:
        decide(state, answer(state, P1, 999, 600), DecisionContext(now=NOW))
    assert exc.value.code is RejectCode.ALREADY_ANSWERED


def test_wrong_answer_kind_is_rejected() -> None:
    state = started()
    assert isinstance(state.turn, ExpansionQuestion)
    command = SubmitAnswer(P1, state.turn.deadline.id, ChoiceAnswer(0), 100)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, command, DecisionContext(now=NOW))
    assert exc.value.code is RejectCode.ANSWER_KIND_MISMATCH


def test_last_answer_closes_and_resolves_the_window() -> None:
    state = started()
    for player, guess, elapsed in ((P1, 100, 500), (P2, 90, 400)):
        state = fold(state, decide(state, answer(state, player, guess, elapsed),
                                   DecisionContext(now=NOW)))
    events = decide(state, answer(state, P3, 105, 300), DecisionContext(now=NOW))
    assert [type(e) for e in events] == [
        ev.AnswerSubmitted, ev.AnswerWindowClosed, ev.QuestionResolved, ev.PicksGranted
    ]


def test_ranking_is_by_distance_then_speed() -> None:
    state = started()  # correct answer for "numeric 0?" is 100
    for player, guess, elapsed in ((P1, 105, 900), (P2, 95, 200), (P3, 95, 100)):
        events = decide(state, answer(state, player, guess, elapsed), DecisionContext(now=NOW))
        state = fold(state, events)
    resolved = next(e for e in events if isinstance(e, ev.QuestionResolved))
    # p3 and p2 are both 5 away; p3 was faster. p1 is 5 away too but slowest.
    assert resolved.ranking == (P3, P2, P1)


def test_non_answerers_rank_last_by_seat() -> None:
    state = started()
    state = fold(state, decide(state, answer(state, P3, 100, 100), DecisionContext(now=NOW)))
    expired = ExpireDeadline(state.turn.deadline.id)  # type: ignore[union-attr]
    late = DecisionContext(now=NOW + timedelta(seconds=30))
    events = decide(state, expired, late)
    resolved = next(e for e in events if isinstance(e, ev.QuestionResolved))
    assert resolved.ranking == (P3, P1, P2)


def test_grants_follow_claims_by_rank_and_open_picking() -> None:
    state = started()
    for player, guess, elapsed in ((P1, 100, 100), (P2, 110, 100), (P3, 120, 100)):
        events = decide(state, answer(state, player, guess, elapsed), DecisionContext(now=NOW))
        state = fold(state, events)
    granted = next(e for e in events if isinstance(e, ev.PicksGranted))
    assert granted.grants == {P1: 2, P2: 1, P3: 0}
    assert granted.pick_order == (P1, P2)
    assert isinstance(state.turn, ExpansionPicking)
    assert state.turn.current_picker == P1


def test_grants_are_truncated_to_free_regions() -> None:
    state = started()
    # 9 regions, 3 taken by bases, leave only 1 free by handing 5 to p1.
    from tests.conftest import own
    for region in ("r1", "r3", "r4", "r5", "r7"):
        state = own(state, region, "p1")
    for player, guess, elapsed in ((P1, 100, 100), (P2, 110, 100), (P3, 120, 100)):
        events = decide(state, answer(state, player, guess, elapsed), DecisionContext(now=NOW))
        state = fold(state, events)
    granted = next(e for e in events if isinstance(e, ev.PicksGranted))
    assert sum(granted.grants.values()) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_expansion_question.py -v`
Expected: FAIL with `NotImplementedError: no handler for SubmitAnswer`

- [ ] **Step 3: Implement the decide branches**

Add to `reducer.py`'s `_dispatch` match:

```python
        case SubmitAnswer() if isinstance(state.turn, ExpansionQuestion):
            return _decide_expansion_answer(state, command, ctx)
        case ExpireDeadline() if isinstance(state.turn, ExpansionQuestion):
            return _close_expansion_question(state, state.turn, ctx)
```

And the handlers:

```python
def _record_answer(
    turn: ExpansionQuestion | BattleDuel | BattleTiebreak | NeutralChallenge | FinalTiebreak,
    command: SubmitAnswer,
) -> ev.AnswerSubmitted | None:
    """None means 'ignore' — an identical resubmission."""
    existing = turn.answers.get(command.actor_id)
    submitted = SubmittedAnswer(command.value, command.elapsed_ms)
    if existing is not None:
        if existing.value == submitted.value:
            return None
        raise RejectedCommand(
            RejectCode.ALREADY_ANSWERED, f"{command.actor_id!r} already answered this window"
        )
    expected_numeric = turn.question.kind is QuestionKind.NUMERIC
    if expected_numeric != isinstance(command.value, NumericAnswer):
        raise RejectedCommand(
            RejectCode.ANSWER_KIND_MISMATCH,
            f"question is {turn.question.kind}, answer was {type(command.value).__name__}",
        )
    return ev.AnswerSubmitted(command.actor_id, submitted)


def _decide_expansion_answer(
    state: GameState, command: SubmitAnswer, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    turn = state.turn
    assert isinstance(turn, ExpansionQuestion)
    recorded = _record_answer(turn, command)
    if recorded is None:
        return ()
    after = evolve(state, recorded)
    assert isinstance(after.turn, ExpansionQuestion)
    if len(after.turn.answers) < len(after.active_players()):
        return (recorded,)
    return (recorded, *_close_expansion_question(after, after.turn, ctx))


def _close_expansion_question(
    state: GameState, turn: ExpansionQuestion, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    ranking = _rank_numeric(turn, state)
    resolved = ev.QuestionResolved(
        correct_choice_index=None,
        correct_value=turn.question.numeric_answer,
        ranking=ranking,
        correct_players=(),
    )
    free = len(state.free_regions())
    grants: dict[PlayerId, int] = {}
    for rank, player_id in enumerate(ranking):
        want = state.rules.claims_by_rank[rank] if rank < len(state.rules.claims_by_rank) else 0
        take = min(want, free)
        grants[player_id] = take
        free -= take
    order = tuple(p for p in ranking if grants[p] > 0)

    if not order:
        return (ev.AnswerWindowClosed(turn.deadline), resolved,
                *_advance_expansion(state, ctx))

    # decide() owns the clock, so the pick deadline is allocated here and
    # carried on the event — evolve() never needs a timestamp of its own.
    deadline, _ = state.allocate_deadline(
        DeadlineKind.PICK, ctx.now + timedelta(milliseconds=state.rules.pick_timeout_ms)
    )
    return (
        ev.AnswerWindowClosed(turn.deadline),
        resolved,
        ev.PicksGranted(order, grants, deadline),
    )


def _rank_numeric(
    turn: ExpansionQuestion | BattleTiebreak | FinalTiebreak, state: GameState
) -> tuple[PlayerId, ...]:
    correct = turn.question.numeric_answer
    assert correct is not None
    contenders = (
        turn.contenders if isinstance(turn, FinalTiebreak) else state.active_players()
    )

    def key(player_id: PlayerId) -> tuple[int, Decimal, int, int]:
        submitted = turn.answers.get(player_id)
        seat = state.players[player_id].seat
        if submitted is None or not isinstance(submitted.value, NumericAnswer):
            return (1, Decimal(0), 0, seat)
        return (0, abs(submitted.value.value - correct), submitted.elapsed_ms, seat)

    return tuple(sorted(contenders, key=key))
```

Add imports: `from decimal import Decimal`, and extend the `state` import with `NumericAnswer, SubmittedAnswer`; add `from triviador.domain.questions.types import QuestionKind`.

- [ ] **Step 4: Implement the evolve branches**

Add to `_apply`:

```python
        case ev.AnswerSubmitted(player_id=pid, answer=submitted):
            turn = state.turn
            assert turn is not None and hasattr(turn, "answers")
            return replace(
                state, turn=replace(turn, answers={**turn.answers, pid: submitted})
            )

        case ev.AnswerWindowClosed():
            return state

        case ev.QuestionResolved():
            return state

        case ev.PicksGranted(pick_order=order, grants=grants, deadline=deadline):
            return replace(
                state,
                next_deadline_id=max(state.next_deadline_id, deadline.id + 1),
                turn=ExpansionPicking(deadline, order, dict(grants), order[0]),
            )
```

`PicksGranted` carries its own `Deadline` because `evolve` has no clock — the
timestamp is decided once, by `decide`, and recorded in the event. This is the
same discipline as `QuestionPresented`.

- [ ] **Step 5: Run tests and linters**

Run: `cd backend && uv run pytest tests/domain/game -v && uv run ruff check . && uv run mypy`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/domain/game backend/tests/domain/game
git commit -m "feat(domain): resolve expansion questions and grant picks"
```

---

### Task 13: Expansion picking, auto-pick and the stage transition

**Files:**
- Modify: `backend/src/triviador/domain/game/reducer.py`
- Test: `backend/tests/domain/game/test_expansion_picking.py`

**Interfaces:**
- Consumes: `ExpansionPicking` turn produced by Task 12.
- Produces: `PickRegion`/`ExpireDeadline` handling; `_advance_expansion(state, ctx)` which either opens the next expansion round or enters the battle stage; `_open_battle_turn(state, attacker_id, ctx)` used by Task 14.

Each individual pick gets its own `DeadlineId` — a player granted two picks must not be able to burn the whole window on the first.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/game/test_expansion_picking.py`:

```python
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

import pytest

from tests.conftest import NOW, lobby_state
from tests.domain.game.test_start import P1, P2, P3, start_ctx
from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    DecisionContext,
    ExpireDeadline,
    PickRegion,
    RejectCode,
    RejectedCommand,
    StartGame,
    SubmitAnswer,
)
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.state import (
    AcquisitionKind,
    BattleTargetSelect,
    ExpansionPicking,
    ExpansionQuestion,
    GameState,
    NumericAnswer,
    Phase,
)
from triviador.domain.ids import RegionId

CTX = DecisionContext(now=NOW)


def picking_state(rules_override: dict[str, object] | None = None) -> GameState:
    base = lobby_state()
    if rules_override:
        base = replace(base, rules=replace(base.rules, **rules_override))  # type: ignore[arg-type]
    state = fold(base, decide(base, StartGame(P1), start_ctx()))
    for player, guess in ((P1, 100), (P2, 110), (P3, 120)):
        assert isinstance(state.turn, ExpansionQuestion)
        cmd = SubmitAnswer(player, state.turn.deadline.id, NumericAnswer(Decimal(guess)), 100)
        state = fold(state, decide(state, cmd, CTX))
    return state


def test_picking_a_free_region_claims_and_scores_it() -> None:
    state = picking_state()
    assert isinstance(state.turn, ExpansionPicking)
    events = decide(state, PickRegion(P1, state.turn.deadline.id, RegionId("r1")), CTX)
    assert [type(e) for e in events] == [ev.TerritoryClaimed, ev.ScoreChanged]
    claimed = events[0]
    assert isinstance(claimed, ev.TerritoryClaimed)
    assert claimed.acquisition is AcquisitionKind.CLAIMED
    assert claimed.automatic is False


def test_picking_an_owned_region_is_rejected() -> None:
    state = picking_state()
    assert isinstance(state.turn, ExpansionPicking)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, PickRegion(P1, state.turn.deadline.id, RegionId("r0")), CTX)
    assert exc.value.code is RejectCode.REGION_NOT_FREE


def test_picking_an_unknown_region_is_rejected() -> None:
    state = picking_state()
    assert isinstance(state.turn, ExpansionPicking)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, PickRegion(P1, state.turn.deadline.id, RegionId("nope")), CTX)
    assert exc.value.code is RejectCode.UNKNOWN_REGION


def test_picking_out_of_turn_is_rejected() -> None:
    state = picking_state()
    assert isinstance(state.turn, ExpansionPicking)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, PickRegion(P2, state.turn.deadline.id, RegionId("r1")), CTX)
    assert exc.value.code is RejectCode.NOT_YOUR_TURN


def test_each_pick_opens_a_fresh_window() -> None:
    state = picking_state()
    assert isinstance(state.turn, ExpansionPicking)
    first_window = state.turn.deadline.id
    state = fold(state, decide(state, PickRegion(P1, first_window, RegionId("r1")), CTX))
    assert isinstance(state.turn, ExpansionPicking)
    assert state.turn.current_picker == P1, "p1 was granted two picks"
    assert state.turn.deadline.id != first_window


def test_timeout_auto_picks_from_the_shuffled_order() -> None:
    state = picking_state()
    assert isinstance(state.turn, ExpansionPicking)
    ctx = DecisionContext(
        now=NOW + timedelta(seconds=60),
        shuffled_region_ids=(RegionId("r8"), RegionId("r7")),
    )
    events = decide(state, ExpireDeadline(state.turn.deadline.id), ctx)
    claimed = next(e for e in events if isinstance(e, ev.TerritoryClaimed))
    assert claimed.region_id == RegionId("r8")
    assert claimed.automatic is True


def test_finishing_all_picks_starts_the_next_expansion_round() -> None:
    state = picking_state()
    for region in ("r1", "r3", "r4"):  # p1 x2 then p2 x1
        assert isinstance(state.turn, ExpansionPicking)
        state = fold(state, decide(
            state, PickRegion(state.turn.current_picker, state.turn.deadline.id,
                              RegionId(region)), CTX))
    assert state.round_no == 2
    assert isinstance(state.turn, ExpansionQuestion)


def test_running_out_of_free_regions_enters_the_battle_stage() -> None:
    # 9 regions: 3 bases + 3 picks per round. Round 2 fills the map.
    state = picking_state()
    for _ in range(2):
        for _ in range(3):
            assert isinstance(state.turn, ExpansionPicking)
            free = state.free_regions()[0]
            state = fold(state, decide(
                state, PickRegion(state.turn.current_picker, state.turn.deadline.id, free), CTX))
        if state.phase is Phase.BATTLE:
            break
        assert isinstance(state.turn, ExpansionQuestion)
        for player, guess in ((P1, 100), (P2, 110), (P3, 120)):
            cmd = SubmitAnswer(player, state.turn.deadline.id,  # type: ignore[union-attr]
                               NumericAnswer(Decimal(guess)), 100)
            state = fold(state, decide(state, cmd, CTX))
    assert state.phase is Phase.BATTLE
    assert state.free_regions() == ()
    assert isinstance(state.turn, BattleTargetSelect)
    assert state.turn.attacker_id == state.active_players()[0]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_expansion_picking.py -v`
Expected: FAIL with `NotImplementedError: no handler for PickRegion`

- [ ] **Step 3: Implement the decide branches**

Add to `_dispatch`:

```python
        case PickRegion() if isinstance(state.turn, ExpansionPicking):
            return _decide_pick(state, state.turn, command, ctx)
        case ExpireDeadline() if isinstance(state.turn, ExpansionPicking):
            return _decide_auto_pick(state, state.turn, ctx)
```

```python
def _decide_pick(
    state: GameState, turn: ExpansionPicking, command: PickRegion, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    if command.actor_id != turn.current_picker:
        raise RejectedCommand(RejectCode.NOT_YOUR_TURN, f"{turn.current_picker!r} is picking")
    if command.region_id not in state.territories:
        raise RejectedCommand(RejectCode.UNKNOWN_REGION, f"{command.region_id!r} is not on the map")
    if state.territories[command.region_id].owner_id is not None:
        raise RejectedCommand(RejectCode.REGION_NOT_FREE, f"{command.region_id!r} is taken")
    return _claim(state, turn, command.region_id, automatic=False, ctx=ctx)


def _decide_auto_pick(
    state: GameState, turn: ExpansionPicking, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    free = set(state.free_regions())
    order = ctx.shuffled_region_ids or state.free_regions()
    region_id = next((r for r in order if r in free), None)
    if region_id is None:
        return _advance_expansion(state, ctx)
    return _claim(state, turn, region_id, automatic=True, ctx=ctx)


def _claim(
    state: GameState,
    turn: ExpansionPicking,
    region_id: RegionId,
    *,
    automatic: bool,
    ctx: DecisionContext,
) -> tuple[ev.GameEvent, ...]:
    picker = turn.current_picker
    claimed = ev.TerritoryClaimed(picker, region_id, AcquisitionKind.CLAIMED, automatic)
    after = evolve(state, claimed)
    score = ev.ScoreChanged(
        picker, state.rules.pts_territory, ev.ScoreReason.TERRITORY,
        new_total=expected_score(after, picker),
    )
    after = evolve(after, score)

    remaining = {**turn.grants_remaining, picker: turn.grants_remaining[picker] - 1}
    next_picker = _next_picker(turn.pick_order, remaining, after)
    if next_picker is None:
        return (claimed, score, *_advance_expansion(after, ctx))

    deadline, _ = after.allocate_deadline(
        DeadlineKind.PICK, ctx.now + timedelta(milliseconds=after.rules.pick_timeout_ms)
    )
    return (claimed, score, ev.PicksGranted(turn.pick_order, remaining, deadline))


def _next_picker(
    order: tuple[PlayerId, ...], remaining: Mapping[PlayerId, int], state: GameState
) -> PlayerId | None:
    if not state.free_regions():
        return None
    return next((p for p in order if remaining.get(p, 0) > 0), None)


def _advance_expansion(state: GameState, ctx: DecisionContext) -> tuple[ev.GameEvent, ...]:
    done = ev.ExpansionRoundCompleted(state.round_no)
    after = evolve(state, done)
    rounds_left = after.round_no < after.rules.expansion_rounds
    if rounds_left and after.free_regions():
        started = ev.ExpansionRoundStarted(after.round_no + 1)
        after = evolve(after, started)
        question, _ = _open_expansion_question(after, ctx)
        return (done, started, *question)
    battle = ev.BattleRoundStarted(1)
    after = evolve(after, battle)
    return (done, battle, *_open_battle_turn(after, after.active_players()[0], ctx))


def _open_battle_turn(
    state: GameState, attacker_id: PlayerId, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    deadline, _ = state.allocate_deadline(
        DeadlineKind.TARGET_SELECT,
        ctx.now + timedelta(milliseconds=state.rules.answer_timeout_ms),
    )
    return (ev.TurnStarted(attacker_id, deadline),)
```

- [ ] **Step 4: Implement the evolve branches**

Add to `_apply`:

```python
        case ev.TerritoryClaimed(player_id=pid, region_id=rid, acquisition=acq):
            territory = replace(state.territories[rid], owner_id=pid, acquisition=acq)
            return replace(state, territories={**state.territories, rid: territory})

        case ev.ExpansionRoundCompleted():
            return replace(state, turn=None)

        case ev.BattleRoundStarted(round_no=round_no):
            return replace(state, phase=Phase.BATTLE, round_no=round_no, turn=None)

        case ev.TurnStarted(attacker_id=attacker, deadline=deadline):
            return replace(
                state,
                next_deadline_id=max(state.next_deadline_id, deadline.id + 1),
                turn=BattleTargetSelect(deadline, attacker),
            )
```

Update the existing `ev.PicksGranted` branch to use `event.deadline` and
`event.grants` rather than allocating one itself.

- [ ] **Step 5: Run tests and linters**

Run: `cd backend && uv run pytest tests/domain/game -v && uv run ruff check . && uv run mypy`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador/domain/game backend/tests/domain/game
git commit -m "feat(domain): implement expansion picking, auto-pick and stage transition"
```

---

### Task 14: Battle target selection and skipping

**Files:**
- Modify: `backend/src/triviador/domain/game/reducer.py`
- Modify: `backend/src/triviador/domain/game/state.py` — adds `GameState.pending_attack`
- Test: `backend/tests/domain/game/test_target_select.py`

**Interfaces:**
- Consumes: `_open_battle_turn` (Task 13) — this task **replaces** that stub with
  the real version that checks `legal_targets` first.
- Produces: `SelectAttackTarget`/`ExpireDeadline` handling for `BattleTargetSelect`; `legal_targets(state, attacker_id) -> tuple[RegionId, ...]` — exported because Plan 3's projection puts it in `turn.your_options`; `_next_battle_turn(state, ctx)` used by Tasks 15–18.

`legal_targets` is the single source of the adjacency rule. The client never derives it.

**No window is ever opened with no legal action:** if `legal_targets` is empty, `TurnSkipped` is emitted instead of `TurnStarted`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/game/test_target_select.py`. The `battle_state`
helper builds a filled 3x3 grid directly rather than playing through expansion,
so the ownership layout is obvious:

```
r0 p1  r1 p1  r2 p2          p1 can reach r2? no (not adjacent)
r3 p1  r4 --  r5 p2          p1 can reach r4 (neutral) and r5? r5 is not
r6 p3  r7 p3  r8 p3          adjacent to any p1 region, so no.
```

```python
from dataclasses import replace
from datetime import timedelta

import pytest

from tests.conftest import NOW, full_pool, lobby_state, own
from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    DecisionContext,
    ExpireDeadline,
    RejectCode,
    RejectedCommand,
    SelectAttackTarget,
)
from triviador.domain.game.reducer import decide, fold, legal_targets
from triviador.domain.game.state import (
    BattleDuel,
    BattleTargetSelect,
    GameState,
    NeutralChallenge,
    Phase,
)
from triviador.domain.ids import PlayerId, RegionId

P1, P2, P3 = PlayerId("p1"), PlayerId("p2"), PlayerId("p3")
CTX = DecisionContext(now=NOW)
LAYOUT = {"r0": "p1", "r1": "p1", "r3": "p1", "r2": "p2", "r5": "p2",
          "r6": "p3", "r7": "p3", "r8": "p3"}


def battle_state(layout: dict[str, str] | None = None) -> GameState:
    state = replace(lobby_state(), phase=Phase.BATTLE, round_no=1, pool=full_pool())
    for region, player in (layout if layout is not None else LAYOUT).items():
        state = own(state, region, player)
    return state


def open_turn(state: GameState, attacker: PlayerId = P1) -> GameState:
    from triviador.domain.game.reducer import _open_battle_turn

    return fold(state, _open_battle_turn(state, attacker, CTX))


def test_legal_targets_are_adjacent_and_not_mine() -> None:
    # p1 owns r0, r1, r3. Their neighbours are r2, r4, r6 (minus p1's own).
    assert set(legal_targets(battle_state(), P1)) == {RegionId("r2"), RegionId("r4"),
                                                      RegionId("r6")}


def test_legal_targets_is_empty_when_everything_adjacent_is_mine() -> None:
    solo = {f"r{i}": "p1" for i in range(9)}
    assert legal_targets(battle_state(solo), P1) == ()


def test_no_legal_target_skips_the_turn_without_opening_a_window() -> None:
    from triviador.domain.game.reducer import _open_battle_turn

    solo = {f"r{i}": "p1" for i in range(9)}
    events = _open_battle_turn(battle_state(solo), P1, CTX)
    assert isinstance(events[0], ev.TurnSkipped)
    assert not any(isinstance(e, ev.TurnStarted) for e in events)


def test_selecting_an_owned_enemy_region_opens_a_duel() -> None:
    state = open_turn(battle_state())
    assert isinstance(state.turn, BattleTargetSelect)
    events = decide(state, SelectAttackTarget(P1, state.turn.deadline.id, RegionId("r2")), CTX)
    assert [type(e) for e in events] == [ev.AttackDeclared, ev.QuestionPresented]
    after = fold(state, events)
    assert isinstance(after.turn, BattleDuel)
    assert after.turn.defender_id == P2
    assert after.turn.question.prompt.startswith("mc")


def test_selecting_a_neutral_region_opens_a_challenge_not_a_duel() -> None:
    state = open_turn(battle_state())
    assert isinstance(state.turn, BattleTargetSelect)
    events = decide(state, SelectAttackTarget(P1, state.turn.deadline.id, RegionId("r4")), CTX)
    declared = events[0]
    assert isinstance(declared, ev.AttackDeclared)
    assert declared.defender_id is None
    after = fold(state, events)
    assert isinstance(after.turn, NeutralChallenge)


def test_selecting_a_non_adjacent_region_is_rejected() -> None:
    state = open_turn(battle_state())
    assert isinstance(state.turn, BattleTargetSelect)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, SelectAttackTarget(P1, state.turn.deadline.id, RegionId("r8")), CTX)
    assert exc.value.code is RejectCode.NOT_ADJACENT


def test_selecting_my_own_region_is_rejected() -> None:
    state = open_turn(battle_state())
    assert isinstance(state.turn, BattleTargetSelect)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, SelectAttackTarget(P1, state.turn.deadline.id, RegionId("r1")), CTX)
    assert exc.value.code is RejectCode.OWN_TERRITORY


def test_selecting_an_unknown_region_is_rejected() -> None:
    state = open_turn(battle_state())
    assert isinstance(state.turn, BattleTargetSelect)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, SelectAttackTarget(P1, state.turn.deadline.id, RegionId("nope")), CTX)
    assert exc.value.code is RejectCode.UNKNOWN_REGION


def test_selecting_out_of_turn_is_rejected() -> None:
    state = open_turn(battle_state())
    assert isinstance(state.turn, BattleTargetSelect)
    with pytest.raises(RejectedCommand) as exc:
        decide(state, SelectAttackTarget(P2, state.turn.deadline.id, RegionId("r4")), CTX)
    assert exc.value.code is RejectCode.NOT_YOUR_TURN


def test_target_timeout_skips_the_turn_and_advances() -> None:
    state = open_turn(battle_state())
    assert isinstance(state.turn, BattleTargetSelect)
    late = DecisionContext(now=NOW + timedelta(seconds=60))
    events = decide(state, ExpireDeadline(state.turn.deadline.id), late)
    assert isinstance(events[0], ev.TurnSkipped)
    after = fold(state, events)
    assert isinstance(after.turn, BattleTargetSelect)
    assert after.turn.attacker_id == P2
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_target_select.py -v`
Expected: FAIL with `NotImplementedError: no handler for SelectAttackTarget`

- [ ] **Step 3: Implement**

```python
def legal_targets(state: GameState, attacker_id: PlayerId) -> tuple[RegionId, ...]:
    mine = set(state.owned_by(attacker_id))
    reachable: set[RegionId] = set()
    for region_id in mine:
        reachable |= state.map.neighbours(region_id)
    return tuple(r for r in state.map.region_ids() if r in reachable and r not in mine)


def _open_battle_turn(
    state: GameState, attacker_id: PlayerId, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    if not legal_targets(state, attacker_id):
        skipped = ev.TurnSkipped(attacker_id, "no adjacent target")
        return (skipped, *_next_battle_turn(evolve(state, skipped), ctx))
    deadline, _ = state.allocate_deadline(
        DeadlineKind.TARGET_SELECT,
        ctx.now + timedelta(milliseconds=state.rules.answer_timeout_ms),
    )
    return (ev.TurnStarted(attacker_id, deadline),)


def _decide_target(
    state: GameState, turn: BattleTargetSelect, command: SelectAttackTarget, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    if command.actor_id != turn.attacker_id:
        raise RejectedCommand(RejectCode.NOT_YOUR_TURN, f"{turn.attacker_id!r} is attacking")
    if command.region_id not in state.territories:
        raise RejectedCommand(RejectCode.UNKNOWN_REGION, f"{command.region_id!r} is not on the map")
    target = state.territories[command.region_id]
    if target.owner_id == command.actor_id:
        raise RejectedCommand(RejectCode.OWN_TERRITORY, "cannot attack your own region")
    if command.region_id not in legal_targets(state, command.actor_id):
        raise RejectedCommand(RejectCode.NOT_ADJACENT, f"{command.region_id!r} is not adjacent")

    declared = ev.AttackDeclared(command.actor_id, target.owner_id, command.region_id)
    after = evolve(state, declared)
    question, _ = after.pool.next_multiple_choice()
    deadline, _ = after.allocate_deadline(
        DeadlineKind.ANSWER, ctx.now + timedelta(milliseconds=after.rules.answer_timeout_ms)
    )
    return (declared, ev.QuestionPresented(question, deadline))


def _next_battle_turn(state: GameState, ctx: DecisionContext) -> tuple[ev.GameEvent, ...]:
    """Advance to the next attacker, the next round, or the end of the game."""
    raise NotImplementedError("completed in Task 18")
```

Extend `_apply` with `ev.AttackDeclared` (store the pending attack on the state
by leaving `turn` as `BattleTargetSelect` and letting `QuestionPresented` build
the duel or challenge) and extend `_present_question` so that in `Phase.BATTLE`
it constructs `BattleDuel` when the declared target has an owner and
`NeutralChallenge` when it does not. Carry the declared attack through a new
`GameState.pending_attack: AttackDeclared | None` field added in this task.

- [ ] **Step 4: Run tests and linters**

Run: `cd backend && uv run pytest tests/domain/game -v && uv run ruff check . && uv run mypy`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/domain/game backend/tests/domain/game
git commit -m "feat(domain): implement battle target selection and turn skipping"
```

---

### Task 15: Neutral challenge

**Files:**
- Modify: `backend/src/triviador/domain/game/reducer.py`
- Test: `backend/tests/domain/game/test_neutral.py`

**Interfaces:**
- Consumes: `NeutralChallenge` turn (Task 14).
- Produces: `SubmitAnswer`/`ExpireDeadline` handling for `NeutralChallenge`.

A neutral capture is **not** a duel and must not emit `DuelResolved` — Spec 2
analytics and the UI both distinguish them.

```
correct         → NeutralTerritoryCaptured + ScoreChanged(+pts_territory, TERRITORY)
wrong / timeout → NeutralAttackFailed
```

No defender, no base damage, no defense points. The captured region's
`acquisition` is `CLAIMED`, not `CONQUEST`: it was taken while unowned.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/game/test_neutral.py`:

```python
from datetime import timedelta

from tests.conftest import NOW
from tests.domain.game.test_target_select import CTX, P1, battle_state, open_turn
from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    DecisionContext,
    ExpireDeadline,
    SelectAttackTarget,
    SubmitAnswer,
)
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.scoring import holding_value
from triviador.domain.game.state import AcquisitionKind, ChoiceAnswer, GameState, NeutralChallenge
from triviador.domain.ids import RegionId

NEUTRAL = RegionId("r4")
DUEL_FREE_EVENTS = (ev.DuelResolved, ev.DefenseHeld, ev.BaseDamaged, ev.TiebreakStarted)


def challenging() -> GameState:
    state = open_turn(battle_state())
    assert state.turn is not None
    return fold(state, decide(
        state, SelectAttackTarget(P1, state.turn.deadline.id, NEUTRAL), CTX))


def answer(state: GameState, idx: int) -> SubmitAnswer:
    assert isinstance(state.turn, NeutralChallenge)
    return SubmitAnswer(P1, state.turn.deadline.id, ChoiceAnswer(idx), 400)


def test_correct_answer_captures_the_region() -> None:
    state = challenging()
    events = decide(state, answer(state, 0), CTX)  # mc questions are correct at index 0
    assert [type(e) for e in events][:5] == [
        ev.AnswerSubmitted, ev.AnswerWindowClosed, ev.QuestionResolved,
        ev.NeutralTerritoryCaptured, ev.ScoreChanged,
    ]


def test_capture_is_claimed_not_conquest() -> None:
    state = challenging()
    after = fold(state, decide(state, answer(state, 0), CTX))
    territory = after.territories[NEUTRAL]
    assert territory.owner_id == P1
    assert territory.acquisition is AcquisitionKind.CLAIMED
    assert holding_value(territory, after.rules) == after.rules.pts_territory


def test_wrong_answer_fails_and_leaves_the_region_neutral() -> None:
    state = challenging()
    events = decide(state, answer(state, 1), CTX)
    assert any(isinstance(e, ev.NeutralAttackFailed) for e in events)
    after = fold(state, events)
    assert after.territories[NEUTRAL].owner_id is None


def test_timeout_behaves_like_a_wrong_answer() -> None:
    state = challenging()
    assert isinstance(state.turn, NeutralChallenge)
    late = DecisionContext(now=NOW + timedelta(seconds=60))
    events = decide(state, ExpireDeadline(state.turn.deadline.id), late)
    assert any(isinstance(e, ev.NeutralAttackFailed) for e in events)
    assert fold(state, events).territories[NEUTRAL].owner_id is None


def test_a_neutral_challenge_is_never_reported_as_a_duel() -> None:
    for idx in (0, 1):
        state = challenging()
        events = decide(state, answer(state, idx), CTX)
        assert not any(isinstance(e, DUEL_FREE_EVENTS) for e in events)


def test_only_the_attacker_may_answer_a_neutral_challenge() -> None:
    from triviador.domain.game.reducer import LEGAL_COMMANDS

    state = challenging()
    assert isinstance(state.turn, NeutralChallenge)
    assert state.turn.attacker_id == P1
    assert SubmitAnswer in LEGAL_COMMANDS[NeutralChallenge]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_neutral.py -v`
Expected: FAIL with `NotImplementedError`

- [ ] **Step 3: Implement**

```python
def _close_neutral_challenge(
    state: GameState, turn: NeutralChallenge, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    submitted = turn.answers.get(turn.attacker_id)
    correct_idx = turn.question.correct_choice_index()
    won = (
        submitted is not None
        and isinstance(submitted.value, ChoiceAnswer)
        and submitted.value.idx == correct_idx
    )
    resolved = ev.QuestionResolved(
        correct_choice_index=correct_idx,
        correct_value=None,
        ranking=(turn.attacker_id,),
        correct_players=(turn.attacker_id,) if won else (),
    )
    head: tuple[ev.GameEvent, ...] = (ev.AnswerWindowClosed(turn.deadline), resolved)

    if not won:
        failed = ev.NeutralAttackFailed(turn.region_id, turn.attacker_id)
        return (*head, failed, *_next_battle_turn(evolve(state, failed), ctx))

    captured = ev.NeutralTerritoryCaptured(turn.region_id, turn.attacker_id)
    after = evolve(state, captured)
    score = ev.ScoreChanged(
        turn.attacker_id, state.rules.pts_territory, ev.ScoreReason.TERRITORY,
        new_total=expected_score(after, turn.attacker_id),
    )
    after = evolve(after, score)
    return (*head, captured, score, *_next_battle_turn(after, ctx))
```

Add the `_apply` branches: `NeutralTerritoryCaptured` sets `owner_id` and
`acquisition=CLAIMED` and clears `turn`; `NeutralAttackFailed` clears `turn`.
Wire `SubmitAnswer`/`ExpireDeadline` for `NeutralChallenge` into `_dispatch`,
reusing `_record_answer` and closing as soon as the attacker has answered.

- [ ] **Step 4: Run tests and linters**

Run: `cd backend && uv run pytest tests/domain/game -v && uv run ruff check . && uv run mypy`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/domain/game backend/tests/domain/game
git commit -m "feat(domain): implement the neutral challenge resolution path"
```

---

### Task 16: Duel and tiebreak

**Files:**
- Modify: `backend/src/triviador/domain/game/reducer.py`
- Test: `backend/tests/domain/game/test_duel.py`

**Interfaces:**
- Consumes: `BattleDuel`/`BattleTiebreak` turns, `_rank_numeric` (Task 12).
- Produces: `SubmitAnswer`/`ExpireDeadline` handling for both; `_resolve_capture(state, attacker, defender, region, ctx)` — implemented in Task 17 and called from here.

```
attacker ✓, defender ✗  → DuelResolved(attacker) → capture branch
attacker ✗, defender ✓  → DuelResolved(defender) → DefenseHeld + ScoreChanged(DEFENSE)
attacker ✗, defender ✗  → DuelResolved(None), turn over, nothing changes
attacker ✓, defender ✓  → TiebreakStarted → BattleTiebreak (numeric)
```

Tiebreak: closer wins, then faster; **both silent → the defender holds.**

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/game/test_duel.py` — one test per row of that table,
plus the tiebreak cases:

```python
from datetime import timedelta
from decimal import Decimal

from tests.conftest import NOW
from tests.domain.game.test_target_select import CTX, P1, P2, battle_state, open_turn
from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    DecisionContext,
    ExpireDeadline,
    SelectAttackTarget,
    SubmitAnswer,
)
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.state import (
    BattleDuel,
    BattleTiebreak,
    ChoiceAnswer,
    GameState,
    NumericAnswer,
)
from triviador.domain.ids import RegionId

TARGET = RegionId("r2")   # owned by p2, adjacent to p1's r1
CORRECT, WRONG = 0, 1


def dueling() -> GameState:
    state = open_turn(battle_state())
    assert state.turn is not None
    return fold(state, decide(
        state, SelectAttackTarget(P1, state.turn.deadline.id, TARGET), CTX))


def mc(state: GameState, player, idx: int, elapsed: int = 300) -> SubmitAnswer:
    assert isinstance(state.turn, BattleDuel | BattleTiebreak)
    return SubmitAnswer(player, state.turn.deadline.id, ChoiceAnswer(idx), elapsed)


def both(state: GameState, attacker_idx: int, defender_idx: int) -> tuple[GameState, tuple]:
    state = fold(state, decide(state, mc(state, P1, attacker_idx), CTX))
    events = decide(state, mc(state, P2, defender_idx), CTX)
    return fold(state, events), events


def test_attacker_right_defender_wrong_captures() -> None:
    _, events = both(dueling(), CORRECT, WRONG)
    resolved = next(e for e in events if isinstance(e, ev.DuelResolved))
    assert resolved.winner_id == P1
    assert any(isinstance(e, ev.TerritoryCaptured) for e in events)


def test_attacker_wrong_defender_right_holds_and_scores_defense() -> None:
    after, events = both(dueling(), WRONG, CORRECT)
    resolved = next(e for e in events if isinstance(e, ev.DuelResolved))
    assert resolved.winner_id == P2
    assert any(isinstance(e, ev.DefenseHeld) for e in events)
    bonus = next(e for e in events
                 if isinstance(e, ev.ScoreChanged) and e.reason is ev.ScoreReason.DEFENSE)
    assert bonus.delta == after.rules.pts_defense
    assert after.players[P2].bonus_score == after.rules.pts_defense
    assert after.territories[TARGET].owner_id == P2


def test_both_wrong_changes_nothing() -> None:
    after, events = both(dueling(), WRONG, WRONG)
    resolved = next(e for e in events if isinstance(e, ev.DuelResolved))
    assert resolved.winner_id is None
    assert not any(isinstance(e, ev.TerritoryCaptured | ev.DefenseHeld) for e in events)
    assert after.territories[TARGET].owner_id == P2


def test_both_right_opens_a_numeric_tiebreak() -> None:
    after, events = both(dueling(), CORRECT, CORRECT)
    assert any(isinstance(e, ev.TiebreakStarted) for e in events)
    assert isinstance(after.turn, BattleTiebreak)
    assert after.turn.question.prompt.startswith("numeric")
    assert not any(isinstance(e, ev.DuelResolved) for e in events)


def numeric(state: GameState, player, value: int, elapsed: int) -> SubmitAnswer:
    assert isinstance(state.turn, BattleTiebreak)
    return SubmitAnswer(player, state.turn.deadline.id, NumericAnswer(Decimal(value)), elapsed)


def test_closer_tiebreak_guess_wins_the_region() -> None:
    state, _ = both(dueling(), CORRECT, CORRECT)
    correct = state.turn.question.numeric_answer  # type: ignore[union-attr]
    state = fold(state, decide(state, numeric(state, P1, int(correct), 300), CTX))
    events = decide(state, numeric(state, P2, int(correct) + 50, 200), CTX)
    assert any(isinstance(e, ev.TerritoryCaptured) for e in events)
    assert fold(state, events).territories[TARGET].owner_id == P1


def test_equal_distance_is_broken_by_speed() -> None:
    state, _ = both(dueling(), CORRECT, CORRECT)
    correct = int(state.turn.question.numeric_answer)  # type: ignore[arg-type]
    state = fold(state, decide(state, numeric(state, P1, correct + 10, 900), CTX))
    events = decide(state, numeric(state, P2, correct - 10, 100), CTX)
    assert any(isinstance(e, ev.DefenseHeld) for e in events), "faster defender holds"


def test_mutual_silence_in_a_tiebreak_favours_the_defender() -> None:
    state, _ = both(dueling(), CORRECT, CORRECT)
    assert isinstance(state.turn, BattleTiebreak)
    late = DecisionContext(now=NOW + timedelta(seconds=60))
    events = decide(state, ExpireDeadline(state.turn.deadline.id), late)
    assert any(isinstance(e, ev.DefenseHeld) for e in events)
    assert fold(state, events).territories[TARGET].owner_id == P2


def test_defense_bonus_survives_losing_every_territory() -> None:
    from dataclasses import replace

    after, _ = both(dueling(), WRONG, CORRECT)
    stripped = {r: replace(t, owner_id=None, acquisition=None)
                for r, t in after.territories.items()}
    after = replace(after, territories=stripped)
    from triviador.domain.game.scoring import expected_score

    assert expected_score(after, P2) == after.rules.pts_defense
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_duel.py -v`
Expected: FAIL with `NotImplementedError`

- [ ] **Step 3: Implement**

```python
def _close_duel(
    state: GameState, turn: BattleDuel, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    correct_idx = turn.question.correct_choice_index()

    def is_right(player_id: PlayerId) -> bool:
        submitted = turn.answers.get(player_id)
        return (
            submitted is not None
            and isinstance(submitted.value, ChoiceAnswer)
            and submitted.value.idx == correct_idx
        )

    attacker_right, defender_right = is_right(turn.attacker_id), is_right(turn.defender_id)
    correct = tuple(p for p in (turn.attacker_id, turn.defender_id) if is_right(p))
    resolved = ev.QuestionResolved(correct_idx, None, (turn.attacker_id, turn.defender_id), correct)
    head: tuple[ev.GameEvent, ...] = (ev.AnswerWindowClosed(turn.deadline), resolved)

    if attacker_right and defender_right:
        started = ev.TiebreakStarted(turn.region_id)
        after = evolve(state, started)
        question, _ = after.pool.next_numeric()
        deadline, _ = after.allocate_deadline(
            DeadlineKind.ANSWER, ctx.now + timedelta(milliseconds=after.rules.answer_timeout_ms)
        )
        return (*head, started, ev.QuestionPresented(question, deadline))

    if attacker_right:
        won = ev.DuelResolved(turn.attacker_id)
        after = evolve(state, won)
        return (*head, won, *_resolve_capture(
            after, turn.attacker_id, turn.defender_id, turn.region_id, ctx))

    if defender_right:
        won = ev.DuelResolved(turn.defender_id)
        held = ev.DefenseHeld(turn.region_id, turn.defender_id)
        after = fold(state, (won, held))
        score = ev.ScoreChanged(
            turn.defender_id, state.rules.pts_defense, ev.ScoreReason.DEFENSE,
            new_total=expected_score(after, turn.defender_id) + state.rules.pts_defense,
        )
        after = evolve(after, score)
        return (*head, won, held, score, *_next_battle_turn(after, ctx))

    nobody = ev.DuelResolved(None)
    return (*head, nobody, *_next_battle_turn(evolve(state, nobody), ctx))
```

`_close_tiebreak` mirrors this using `_rank_numeric`: the attacker wins only if
they rank strictly first and actually answered; otherwise the defender holds and
earns `pts_defense`.

Add `_apply` branches for `DuelResolved`, `DefenseHeld` and `TiebreakStarted`
(all of which only clear or reshape `turn`).

- [ ] **Step 4: Run tests and linters**

Run: `cd backend && uv run pytest tests/domain/game -v && uv run ruff check . && uv run mypy`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/domain/game backend/tests/domain/game
git commit -m "feat(domain): implement duel and tiebreak resolution"
```

---

### Task 17: Capture, base damage and elimination

**Files:**
- Modify: `backend/src/triviador/domain/game/reducer.py`
- Test: `backend/tests/domain/game/test_capture.py`

**Interfaces:**
- Consumes: `_close_duel` (Task 16).
- Produces: `_resolve_capture(...)`, `_eliminate(state, player_id, ctx)`.

The exact sequences from spec §3.4, which the tests assert literally:

```
normal region   TerritoryCaptured(CONQUEST)
                ScoreChanged(attacker, +pts_conquered, CONQUEST)
                ScoreChanged(defender, −old_value,     TERRITORY_LOST)

base, hp > 1    BaseDamaged(hp − 1)                    ← region does NOT move

base, hp == 1   BaseDestroyed
                TerritoryCaptured(base → attacker, BASE)
                ScoreChanged(attacker, +pts_base, BASE)
                ScoreChanged(defender, −pts_base, BASE_LOST)
                PlayerEliminated(defender)
                per remaining region: TerritoryNeutralized + ScoreChanged(TERRITORY_LOST)
```

Two invariants the tests must pin: the destroyed base **transfers**, every other
holding **neutralizes** (no inheritance — it would make one base kill decide the
match), and `bonus_score` is untouched by elimination.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/game/test_capture.py`:

```python
from dataclasses import replace

from tests.domain.game.test_duel import CORRECT, WRONG, mc
from tests.domain.game.test_target_select import CTX, P1, P2, battle_state, open_turn
from triviador.domain.game import events as ev
from triviador.domain.game.actions import SelectAttackTarget
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.state import (
    AcquisitionKind,
    GameState,
    Phase,
    TerritoryKind,
)
from triviador.domain.ids import RegionId

TARGET = RegionId("r2")


def with_target(kind: TerritoryKind, hp: int | None,
                acquisition: AcquisitionKind) -> GameState:
    state = battle_state()
    territory = replace(
        state.territories[TARGET], kind=kind, base_owner_id=P2 if hp else None,
        base_hp=hp, acquisition=acquisition,
    )
    state = replace(state, territories={**state.territories, TARGET: territory})
    p2 = state.players[P2]
    from triviador.domain.game.scoring import expected_score

    state = replace(state, players={**state.players,
                                    P2: replace(p2, score=expected_score(state, P2))})
    state = open_turn(state)
    assert state.turn is not None
    return fold(state, decide(
        state, SelectAttackTarget(P1, state.turn.deadline.id, TARGET), CTX))


def win_the_duel(state: GameState) -> tuple[GameState, tuple]:
    state = fold(state, decide(state, mc(state, P1, CORRECT), CTX))
    events = decide(state, mc(state, P2, WRONG), CTX)
    return fold(state, events), events


def test_normal_region_capture_emits_the_exact_sequence() -> None:
    state = with_target(TerritoryKind.NORMAL, None, AcquisitionKind.CLAIMED)
    after, events = win_the_duel(state)
    kinds = [type(e) for e in events]
    head = kinds.index(ev.TerritoryCaptured)
    assert kinds[head:head + 3] == [ev.TerritoryCaptured, ev.ScoreChanged, ev.ScoreChanged]
    gain, loss = events[head + 1], events[head + 2]
    assert isinstance(gain, ev.ScoreChanged) and isinstance(loss, ev.ScoreChanged)
    assert (gain.player_id, gain.delta, gain.reason) == (
        P1, after.rules.pts_conquered, ev.ScoreReason.CONQUEST)
    assert (loss.player_id, loss.delta, loss.reason) == (
        P2, -after.rules.pts_territory, ev.ScoreReason.TERRITORY_LOST)


def test_a_captured_region_is_worth_more_to_its_conqueror() -> None:
    state = with_target(TerritoryKind.NORMAL, None, AcquisitionKind.CLAIMED)
    after, _ = win_the_duel(state)
    assert after.territories[TARGET].acquisition is AcquisitionKind.CONQUEST
    from triviador.domain.game.scoring import holding_value

    assert holding_value(after.territories[TARGET], after.rules) == after.rules.pts_conquered


def test_damaging_a_base_does_not_move_the_region() -> None:
    state = with_target(TerritoryKind.BASE, 3, AcquisitionKind.BASE)
    after, events = win_the_duel(state)
    damaged = next(e for e in events if isinstance(e, ev.BaseDamaged))
    assert damaged.hp_remaining == 2
    assert not any(isinstance(e, ev.TerritoryCaptured) for e in events)
    assert after.territories[TARGET].owner_id == P2
    assert after.territories[TARGET].base_hp == 2


def test_destroying_the_last_tower_transfers_the_base_and_eliminates() -> None:
    state = with_target(TerritoryKind.BASE, 1, AcquisitionKind.BASE)
    after, events = win_the_duel(state)
    kinds = [type(e) for e in events]
    assert ev.BaseDestroyed in kinds
    assert kinds.index(ev.BaseDestroyed) < kinds.index(ev.TerritoryCaptured)
    assert ev.PlayerEliminated in kinds
    assert after.territories[TARGET].owner_id == P1
    assert after.players[P2].is_eliminated is True


def test_every_other_holding_of_the_eliminated_player_becomes_neutral() -> None:
    state = with_target(TerritoryKind.BASE, 1, AcquisitionKind.BASE)
    after, events = win_the_duel(state)
    # p2 also owned r5; it must be neutral, not inherited by p1.
    assert any(isinstance(e, ev.TerritoryNeutralized) for e in events)
    assert after.territories[RegionId("r5")].owner_id is None
    assert after.territories[RegionId("r5")].acquisition is None
    assert after.owned_by(P2) == ()


def test_elimination_never_removes_accumulated_bonuses() -> None:
    state = with_target(TerritoryKind.BASE, 1, AcquisitionKind.BASE)
    p2 = state.players[P2]
    state = replace(state, players={**state.players,
                                    P2: replace(p2, bonus_score=300, score=p2.score + 300)})
    after, _ = win_the_duel(state)
    assert after.players[P2].bonus_score == 300
    assert after.players[P2].score == 300


def test_one_active_player_remaining_finishes_the_game() -> None:
    layout = {"r0": "p1", "r1": "p1", "r3": "p1", "r4": "p1",
              "r6": "p1", "r7": "p1", "r8": "p1", "r2": "p2", "r5": "p2"}
    state = battle_state(layout)
    territory = replace(state.territories[TARGET], kind=TerritoryKind.BASE,
                        base_owner_id=P2, base_hp=1, acquisition=AcquisitionKind.BASE)
    state = replace(state, territories={**state.territories, TARGET: territory},
                    turn_order=(P1, P2),
                    players={P1: state.players[P1], P2: state.players[P2]})
    state = open_turn(state)
    assert state.turn is not None
    state = fold(state, decide(state, SelectAttackTarget(
        P1, state.turn.deadline.id, TARGET), CTX))
    after, events = win_the_duel(state)
    assert any(isinstance(e, ev.GameFinished) for e in events)
    assert after.phase is Phase.FINISHED
    assert after.winner_id == P1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_capture.py -v`
Expected: FAIL with `NotImplementedError: _resolve_capture`

- [ ] **Step 3: Implement**

Every handler in this task threads *both* an accumulating event list and the
state those events have already been folded into. A tiny accumulator keeps that
honest instead of re-folding from scratch:

```python
@dataclass
class _Emitter:
    """Accumulates events while keeping `state` folded up to date."""

    state: GameState
    events: list[ev.GameEvent] = field(default_factory=list)

    def emit(self, *new_events: ev.GameEvent) -> None:
        for event in new_events:
            self.state = evolve(self.state, event)
            self.events.append(event)

    def score(self, player_id: PlayerId, delta: int, reason: ev.ScoreReason) -> None:
        """Emit a ScoreChanged whose new_total reflects the state after the delta."""
        bonus = delta if reason in (ev.ScoreReason.DEFENSE, ev.ScoreReason.BONUS) else 0
        new_total = expected_score(self.state, player_id) + bonus
        self.emit(ev.ScoreChanged(player_id, delta, reason, new_total))


def _resolve_capture(
    state: GameState,
    attacker_id: PlayerId,
    defender_id: PlayerId,
    region_id: RegionId,
    ctx: DecisionContext,
) -> tuple[ev.GameEvent, ...]:
    territory = state.territories[region_id]
    rules = state.rules
    out = _Emitter(state)

    # A base with towers left absorbs the hit; the region does not change hands.
    if territory.kind is TerritoryKind.BASE and (territory.base_hp or 0) > 1:
        out.emit(ev.BaseDamaged(region_id, (territory.base_hp or 0) - 1))
        return (*out.events, *_next_battle_turn(out.state, ctx))

    old_value = holding_value(territory, rules)
    is_base = territory.kind is TerritoryKind.BASE

    if is_base:
        out.emit(
            ev.BaseDestroyed(region_id, defender_id),
            ev.TerritoryCaptured(region_id, defender_id, attacker_id, AcquisitionKind.BASE),
        )
        out.score(attacker_id, rules.pts_base, ev.ScoreReason.BASE)
        out.score(defender_id, -old_value, ev.ScoreReason.BASE_LOST)
        _eliminate(out, defender_id, keep_base=False)
    else:
        out.emit(
            ev.TerritoryCaptured(region_id, defender_id, attacker_id, AcquisitionKind.CONQUEST)
        )
        out.score(attacker_id, rules.pts_conquered, ev.ScoreReason.CONQUEST)
        out.score(defender_id, -old_value, ev.ScoreReason.TERRITORY_LOST)

    return (*out.events, *_next_battle_turn(out.state, ctx))


def _eliminate(out: _Emitter, player_id: PlayerId, *, keep_base: bool) -> None:
    """Eliminate a player and neutralize everything they still hold.

    The base that was just destroyed has already transferred to the attacker, so
    it is not in owned_by() any more. On surrender (keep_base=False as well) the
    player's own base is still theirs and neutralizes with the rest.
    Accumulated bonuses are never touched.
    """
    out.emit(ev.PlayerEliminated(player_id))
    for region_id in out.state.owned_by(player_id):
        value = holding_value(out.state.territories[region_id], out.state.rules)
        out.emit(ev.TerritoryNeutralized(region_id, player_id))
        out.score(player_id, -value, ev.ScoreReason.TERRITORY_LOST)
```

`keep_base` exists only to document the two call sites; both neutralize whatever
the player still owns at that point, which is why capture must emit
`TerritoryCaptured` for the base *before* calling `_eliminate`.

Add `from dataclasses import field` to the imports.

`_apply` branches: `TerritoryCaptured` moves ownership and sets `acquisition`;
`BaseDamaged` decrements `base_hp`; `BaseDestroyed` sets `kind=NORMAL`,
`base_owner_id=None`, `base_hp=None`; `PlayerEliminated` sets `is_eliminated`
and clears `base_region`; `TerritoryNeutralized` sets `owner_id=None`,
`acquisition=None`.

- [ ] **Step 4: Run tests and linters**

Run: `cd backend && uv run pytest tests/domain/game -v && uv run ruff check . && uv run mypy`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/domain/game backend/tests/domain/game
git commit -m "feat(domain): implement capture, base sieges and elimination"
```

---

### Task 18: Turn advance, game end and the final tiebreak

**Files:**
- Modify: `backend/src/triviador/domain/game/reducer.py`
- Modify: `backend/src/triviador/domain/game/state.py` — adds `GameState.last_attacker_id`
- Test: `backend/tests/domain/game/test_endgame.py`

**Interfaces:**
- Consumes: `_open_battle_turn` (Task 14), `_rank_numeric` (Task 12).
- Produces: the real `_next_battle_turn`, `_finish(state, ctx)`, and `SubmitAnswer`/`ExpireDeadline` handling for `FinalTiebreak`.

`_next_battle_turn` advances to the next active attacker in `turn_order`; when
the round's attackers are exhausted it emits `BattleRoundCompleted` and either
starts the next round or finishes. One active player remaining also finishes.

Finishing compares scores: a unique maximum wins immediately; a tie opens a
`FinalTiebreak` numeric question among the tied players only.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/game/test_endgame.py`:

```python
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

from tests.conftest import NOW
from tests.domain.game.test_target_select import CTX, P1, P2, P3, battle_state, open_turn
from triviador.domain.game import events as ev
from triviador.domain.game.actions import DecisionContext, ExpireDeadline, SubmitAnswer
from triviador.domain.game.reducer import _next_battle_turn, decide, fold
from triviador.domain.game.state import (
    BattleTargetSelect,
    FinalTiebreak,
    GameState,
    NumericAnswer,
    Phase,
)

LATE = DecisionContext(now=NOW + timedelta(seconds=60))


def skip_turn(state: GameState) -> GameState:
    """Let the current attacker time out without acting."""
    assert isinstance(state.turn, BattleTargetSelect)
    return fold(state, decide(state, ExpireDeadline(state.turn.deadline.id), LATE))


def test_turns_cycle_through_active_players_in_turn_order() -> None:
    state = open_turn(battle_state())
    seen = []
    for _ in range(3):
        assert isinstance(state.turn, BattleTargetSelect)
        seen.append(state.turn.attacker_id)
        state = skip_turn(state)
    assert seen == [P1, P2, P3]


def test_eliminated_players_are_skipped_as_attackers() -> None:
    state = battle_state()
    p2 = state.players[P2]
    state = replace(state, players={**state.players, P2: replace(p2, is_eliminated=True)})
    state = open_turn(state)
    assert isinstance(state.turn, BattleTargetSelect)
    assert state.turn.attacker_id == P1
    state = skip_turn(state)
    assert isinstance(state.turn, BattleTargetSelect)
    assert state.turn.attacker_id == P3


def test_the_last_attacker_of_a_round_starts_the_next_round() -> None:
    state = open_turn(battle_state())
    for _ in range(2):
        state = skip_turn(state)
    assert isinstance(state.turn, BattleTargetSelect)
    events = decide(state, ExpireDeadline(state.turn.deadline.id), LATE)
    kinds = [type(e) for e in events]
    assert ev.BattleRoundCompleted in kinds
    assert ev.BattleRoundStarted in kinds
    assert fold(state, events).round_no == 2


def test_exhausting_battle_rounds_finishes_the_game() -> None:
    state = replace(battle_state(), rules=replace(battle_state().rules, battle_rounds=1))
    state = open_turn(state)
    for _ in range(2):
        state = skip_turn(state)
    assert isinstance(state.turn, BattleTargetSelect)
    events = decide(state, ExpireDeadline(state.turn.deadline.id), LATE)
    assert any(isinstance(e, ev.GameFinished) for e in events)
    after = fold(state, events)
    assert after.phase is Phase.FINISHED


def test_the_highest_scorer_wins_outright() -> None:
    state = battle_state()
    boosted = replace(state.players[P3], score=99_999)
    state = replace(state, players={**state.players, P3: boosted},
                    rules=replace(state.rules, battle_rounds=1))
    state = open_turn(state)
    for _ in range(2):
        state = skip_turn(state)
    assert isinstance(state.turn, BattleTargetSelect)
    events = decide(state, ExpireDeadline(state.turn.deadline.id), LATE)
    finished = next(e for e in events if isinstance(e, ev.GameFinished))
    assert finished.winner_id == P3


def test_a_score_tie_opens_a_final_tiebreak_among_the_tied_only() -> None:
    state = battle_state()
    tied = {P1: 500, P2: 500, P3: 100}
    state = replace(state, players={
        p: replace(s, score=tied[p]) for p, s in state.players.items()})
    events = _next_battle_turn(
        replace(state, round_no=state.rules.battle_rounds,
                turn_order=(), players=state.players), CTX)
    started = next(e for e in events if isinstance(e, ev.FinalTiebreakStarted))
    assert set(started.contenders) == {P1, P2}


def test_the_tiebreak_winner_becomes_the_winner() -> None:
    state = battle_state()
    tied = {P1: 500, P2: 500, P3: 100}
    state = replace(state, pool=state.pool,
                    players={p: replace(s, score=tied[p]) for p, s in state.players.items()},
                    round_no=state.rules.battle_rounds)
    state = fold(state, _next_battle_turn(state, CTX))
    assert isinstance(state.turn, FinalTiebreak)
    correct = int(state.turn.question.numeric_answer)  # type: ignore[arg-type]
    window = state.turn.deadline.id
    state = fold(state, decide(
        state, SubmitAnswer(P1, window, NumericAnswer(Decimal(correct + 100)), 300), CTX))
    events = decide(state, SubmitAnswer(P2, window, NumericAnswer(Decimal(correct)), 300), CTX)
    finished = next(e for e in events if isinstance(e, ev.GameFinished))
    assert finished.winner_id == P2
    after = fold(state, events)
    assert after.phase is Phase.FINISHED
    assert after.winner_id == P2
```

`test_a_score_tie_opens_a_final_tiebreak_among_the_tied_only` drives
`_next_battle_turn` directly because reaching a genuine score tie through play
is slow and brittle; it is the round-exhaustion branch under test, not the path
to it.

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_endgame.py -v`
Expected: FAIL with `NotImplementedError: completed in Task 18`

- [ ] **Step 3: Implement**

```python
def _next_battle_turn(state: GameState, ctx: DecisionContext) -> tuple[ev.GameEvent, ...]:
    active = state.active_players()
    if len(active) <= 1:
        return _finish(state, ctx)

    # Rotate on `turn_order`, which retains eliminated players in their
    # original seats — NOT on the pre-filtered `active`. `last` is the
    # attacker whose turn just ended, and that includes one who surrendered
    # *during* their own open turn, in which case they are already eliminated
    # and do not appear in `active` at all. Anchoring on `active` would make
    # the lookup miss, fall through to "round over", and cut every remaining
    # player's turn short — in the final round that ends the game outright,
    # letting a player out of contention surrender to freeze the standings.
    order = state.turn_order
    last = state.last_attacker_id
    start = order.index(last) + 1 if last in order else len(order)
    nxt = next((p for p in order[start:] if not state.players[p].is_eliminated), None)
    if nxt is not None:
        return _open_battle_turn(state, nxt, ctx)

    completed = ev.BattleRoundCompleted(state.round_no)
    after = evolve(state, completed)
    if after.round_no >= after.rules.battle_rounds:
        return (completed, *_finish(after, ctx))
    started = ev.BattleRoundStarted(after.round_no + 1)
    after = evolve(after, started)
    return (completed, started, *_open_battle_turn(after, after.active_players()[0], ctx))


def _finish(state: GameState, ctx: DecisionContext) -> tuple[ev.GameEvent, ...]:
    scores = {p: state.players[p].score for p in state.active_players()}
    if not scores:
        return (ev.GameFinished(None, {p: s.score for p, s in state.players.items()}),)

    best = max(scores.values())
    leaders = tuple(p for p, s in scores.items() if s == best)
    if len(leaders) == 1:
        return (
            ev.GameFinished(leaders[0], {p: s.score for p, s in state.players.items()}),
        )

    started = ev.FinalTiebreakStarted(leaders)
    after = evolve(state, started)
    question, _ = after.pool.next_numeric()
    deadline, _ = after.allocate_deadline(
        DeadlineKind.ANSWER, ctx.now + timedelta(milliseconds=after.rules.answer_timeout_ms)
    )
    return (started, ev.QuestionPresented(question, deadline))
```

Add `last_attacker_id: PlayerId | None` to `GameState` (set by the `TurnStarted`
and `TurnSkipped` `_apply` branches) so turn rotation needs no extra context.

`_close_final_tiebreak` ranks with `_rank_numeric` over `turn.contenders` and
emits `GameFinished(ranking[0], …)`.

`_apply` branches: `FinalTiebreakStarted` sets `turn=None` and stores the
contenders for `_present_question`; `GameFinished` sets `phase=FINISHED`,
`winner_id`, `turn=None`.

- [ ] **Step 4: Run tests and linters**

Run: `cd backend && uv run pytest tests/domain/game -v && uv run ruff check . && uv run mypy`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/domain/game backend/tests/domain/game
git commit -m "feat(domain): implement turn rotation, game end and final tiebreak"
```

---

### Task 19: Surrender and abort

**Files:**
- Modify: `backend/src/triviador/domain/game/reducer.py`
- Test: `backend/tests/domain/game/test_surrender.py`

**Interfaces:**
- Consumes: `_eliminate` (Task 17), `_next_battle_turn` (Task 18).
- Produces: `Surrender` and `AbortGame` handling in every non-terminal phase.

Surrender emits `PlayerSurrendered`, then the elimination sequence — **including
the surrendering player's own base**, since no attacker earned it — then, if the
surrendering player was the current actor or a duel participant, `TurnAborted`
and advance. Cancelling the whole turn is simpler and less exploitable than
trying to award the contested region. In `LOBBY`, surrender is a plain
`PlayerLeft`.

- [ ] **Step 1: Write the failing test**

Create `backend/tests/domain/game/test_surrender.py`:

```python
from dataclasses import replace

from tests.conftest import lobby_state
from tests.domain.game.test_duel import dueling
from tests.domain.game.test_target_select import CTX, P1, P2, P3, battle_state, open_turn
from triviador.domain.game import events as ev
from triviador.domain.game.actions import AbortGame, Surrender
from triviador.domain.game.reducer import decide, fold
from triviador.domain.game.state import (
    AcquisitionKind,
    BattleTargetSelect,
    Phase,
    TerritoryKind,
)
from triviador.domain.ids import RegionId


def with_p1_base() -> object:
    state = battle_state()
    base = replace(state.territories[RegionId("r0")], kind=TerritoryKind.BASE,
                   base_owner_id=P1, base_hp=3, acquisition=AcquisitionKind.BASE)
    state = replace(state, territories={**state.territories, RegionId("r0"): base})
    p1 = state.players[P1]
    from triviador.domain.game.scoring import expected_score

    state = replace(state, players={**state.players,
                                    P1: replace(p1, base_region=RegionId("r0"),
                                                score=expected_score(state, P1))})
    return open_turn(state)


def test_surrender_in_the_lobby_is_just_leaving() -> None:
    state = lobby_state()
    assert decide(state, Surrender(P1), CTX) == (ev.PlayerLeft(P1),)


def test_the_current_attacker_surrendering_aborts_the_turn_and_advances() -> None:
    state = with_p1_base()
    events = decide(state, Surrender(P1), CTX)  # type: ignore[arg-type]
    kinds = [type(e) for e in events]
    assert kinds[0] is ev.PlayerSurrendered
    assert ev.PlayerEliminated in kinds
    assert ev.TerritoryNeutralized in kinds
    assert ev.TurnAborted in kinds
    assert kinds.index(ev.TurnAborted) < kinds.index(ev.TurnStarted)
    after = fold(state, events)  # type: ignore[arg-type]
    assert isinstance(after.turn, BattleTargetSelect)
    assert after.turn.attacker_id == P2


def test_a_surrendering_players_own_base_is_neutralized_not_awarded() -> None:
    state = with_p1_base()
    after = fold(state, decide(state, Surrender(P1), CTX))  # type: ignore[arg-type]
    base = after.territories[RegionId("r0")]
    assert base.owner_id is None
    assert base.acquisition is None
    assert after.owned_by(P1) == ()


def test_a_duel_defender_surrendering_discards_the_question() -> None:
    state = dueling()  # p1 attacking p2's r2
    events = decide(state, Surrender(P2), CTX)
    assert not any(isinstance(e, ev.DuelResolved | ev.TerritoryCaptured) for e in events)
    assert any(isinstance(e, ev.TurnAborted) for e in events)


def test_surrender_keeps_accumulated_bonuses() -> None:
    state = with_p1_base()
    p1 = state.players[P1]  # type: ignore[union-attr]
    state = replace(state, players={**state.players,  # type: ignore[union-attr]
                                    P1: replace(p1, bonus_score=300, score=p1.score + 300)})
    after = fold(state, decide(state, Surrender(P1), CTX))
    assert after.players[P1].bonus_score == 300
    assert after.players[P1].score == 300


def test_surrender_leaving_one_active_player_finishes_the_game() -> None:
    state = battle_state()
    p3 = state.players[P3]
    state = replace(state, players={**state.players, P3: replace(p3, is_eliminated=True)})
    state = open_turn(state)
    events = decide(state, Surrender(P1), CTX)
    assert any(isinstance(e, ev.GameFinished) for e in events)
    after = fold(state, events)
    assert after.phase is Phase.FINISHED
    assert after.winner_id == P2


def test_abort_works_from_any_non_terminal_phase() -> None:
    for state in (lobby_state(), open_turn(battle_state())):
        events = decide(state, AbortGame(P1), CTX)
        assert [type(e) for e in events] == [ev.GameAborted]
        after = fold(state, events)
        assert after.phase is Phase.ABORTED
        assert after.winner_id is None
        assert after.turn is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd backend && uv run pytest tests/domain/game/test_surrender.py -v`
Expected: FAIL with `NotImplementedError: no handler for Surrender`

- [ ] **Step 3: Implement**

Reuse the `_Emitter` and `_eliminate` from Task 17 — `_eliminate` already
neutralizes *everything the player still owns*, and on surrender that includes
their own base, because no attacker took it first.

```python
def _decide_surrender(
    state: GameState, command: Surrender, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    if state.phase is Phase.LOBBY:
        return (ev.PlayerLeft(command.actor_id),)

    out = _Emitter(state)
    out.emit(ev.PlayerSurrendered(command.actor_id))
    _eliminate(out, command.actor_id, keep_base=False)

    if _is_involved_in_turn(state.turn, command.actor_id):
        out.emit(ev.TurnAborted(f"{command.actor_id} surrendered"))
        return (*out.events, *_next_battle_turn(out.state, ctx))
    return tuple(out.events)


def _decide_abort(state: GameState, command: AbortGame) -> tuple[ev.GameEvent, ...]:
    return (ev.GameAborted(f"aborted by {command.actor_id}"),)


def _is_involved_in_turn(turn: Turn | None, player_id: PlayerId) -> bool:
    match turn:
        case BattleTargetSelect(attacker_id=a):
            return a == player_id
        case BattleDuel(attacker_id=a, defender_id=d) | BattleTiebreak(attacker_id=a, defender_id=d):
            return player_id in (a, d)
        case NeutralChallenge(attacker_id=a):
            return a == player_id
        case _:
            return False
```

Add the `_apply` branches for `PlayerSurrendered` (no state change of its own —
the following `PlayerEliminated` does the work), `TurnAborted` (clears `turn`),
and `GameAborted` (`phase=ABORTED`, `turn=None`, `winner_id=None`).

- [ ] **Step 4: Run tests and linters**

Run: `cd backend && uv run pytest tests/domain/game -v && uv run ruff check . && uv run mypy`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add backend/src/triviador/domain/game backend/tests/domain/game
git commit -m "feat(domain): implement surrender and abort"
```

---

### Task 20: The transition matrix as an executable artifact

**Files:**
- Test: `backend/tests/domain/game/test_matrix.py`

**Interfaces:**
- Consumes: `decide`, every turn variant, every command.
- Produces: nothing importable — this task is pure verification.

All 80 cells of spec §6.3, table-driven. Adding a turn variant or a command
turns this red until the matrix is extended, which is exactly the point.

- [ ] **Step 1: Write the matrix test**

Create `backend/tests/domain/game/test_matrix.py`:

```python
"""Spec §6.3 as an executable artifact. 10 turn states x 8 commands = 80 cells."""

import pytest

from triviador.domain.game.actions import RejectedCommand
from triviador.domain.game.reducer import decide

ACCEPT, IGNORE, REJECT = "accept", "ignore", "reject"

MATRIX: dict[str, dict[str, str]] = {
    "lobby":            {"join": ACCEPT, "start": ACCEPT, "answer": REJECT, "pick": REJECT,
                         "target": REJECT, "expire": IGNORE, "surrender": ACCEPT, "abort": ACCEPT},
    "expansion_question": {"join": REJECT, "start": REJECT, "answer": ACCEPT, "pick": REJECT,
                         "target": REJECT, "expire": ACCEPT, "surrender": ACCEPT, "abort": ACCEPT},
    "expansion_picking": {"join": REJECT, "start": REJECT, "answer": REJECT, "pick": ACCEPT,
                         "target": REJECT, "expire": ACCEPT, "surrender": ACCEPT, "abort": ACCEPT},
    "battle_target":    {"join": REJECT, "start": REJECT, "answer": REJECT, "pick": REJECT,
                         "target": ACCEPT, "expire": ACCEPT, "surrender": ACCEPT, "abort": ACCEPT},
    "battle_duel":      {"join": REJECT, "start": REJECT, "answer": ACCEPT, "pick": REJECT,
                         "target": REJECT, "expire": ACCEPT, "surrender": ACCEPT, "abort": ACCEPT},
    "battle_tiebreak":  {"join": REJECT, "start": REJECT, "answer": ACCEPT, "pick": REJECT,
                         "target": REJECT, "expire": ACCEPT, "surrender": ACCEPT, "abort": ACCEPT},
    "neutral_challenge": {"join": REJECT, "start": REJECT, "answer": ACCEPT, "pick": REJECT,
                         "target": REJECT, "expire": ACCEPT, "surrender": ACCEPT, "abort": ACCEPT},
    "final_tiebreak":   {"join": REJECT, "start": REJECT, "answer": ACCEPT, "pick": REJECT,
                         "target": REJECT, "expire": ACCEPT, "surrender": IGNORE, "abort": ACCEPT},
    "finished":         {"join": REJECT, "start": REJECT, "answer": IGNORE, "pick": IGNORE,
                         "target": IGNORE, "expire": IGNORE, "surrender": IGNORE, "abort": REJECT},
    "aborted":          {"join": REJECT, "start": REJECT, "answer": IGNORE, "pick": IGNORE,
                         "target": IGNORE, "expire": IGNORE, "surrender": IGNORE, "abort": REJECT},
}


def test_the_matrix_is_complete() -> None:
    assert len(MATRIX) == 10
    assert all(len(row) == 8 for row in MATRIX.values())
    assert sum(len(row) for row in MATRIX.values()) == 80


@pytest.mark.parametrize("turn_name", sorted(MATRIX))
@pytest.mark.parametrize("command_name", ["join", "start", "answer", "pick", "target",
                                          "expire", "surrender", "abort"])
def test_cell(turn_name: str, command_name: str, states, commands) -> None:
    expected = MATRIX[turn_name][command_name]
    state = states[turn_name]
    command = commands[command_name](state)

    # A timer must be past its deadline to be accepted (guard 4), so the expire
    # column runs on a late clock. Every other column runs on the normal one.
    ctx = LATE_CTX if command_name == "expire" else states.ctx

    if expected == IGNORE:
        assert decide(state, command, ctx) == ()
    elif expected == REJECT:
        with pytest.raises(RejectedCommand):
            decide(state, command, ctx)
    else:
        assert decide(state, command, ctx) != ()
```

Add the late clock next to the imports:

```python
from datetime import timedelta

from tests.conftest import NOW
from triviador.domain.game.actions import DecisionContext

LATE_CTX = DecisionContext(now=NOW + timedelta(hours=1))
```

- [ ] **Step 2: Write the state and command fixtures**

Create `backend/tests/domain/game/conftest.py`:

```python
"""Fixtures for the transition matrix: one state per turn variant, one command
per column. Commands are always built against the *current* window so guard 2
never masks the cell under test."""

from dataclasses import replace
from decimal import Decimal

import pytest

from tests.conftest import NOW, full_pool, lobby_state, own
from tests.domain.game.test_duel import dueling, mc
from tests.domain.game.test_expansion_picking import picking_state
from tests.domain.game.test_neutral import challenging
from tests.domain.game.test_start import P1, P2, P3, start_ctx
from tests.domain.game.test_target_select import battle_state, open_turn
from triviador.domain.game.actions import (
    AbortGame,
    Command,
    DecisionContext,
    ExpireDeadline,
    JoinGame,
    PickRegion,
    SelectAttackTarget,
    StartGame,
    SubmitAnswer,
    Surrender,
)
from triviador.domain.game.reducer import _next_battle_turn, decide, fold
from triviador.domain.game.state import ChoiceAnswer, GameState, NumericAnswer, Phase
from triviador.domain.ids import DeadlineId, PlayerId, RegionId


class States(dict[str, GameState]):
    ctx = DecisionContext(now=NOW)


def _expansion_question() -> GameState:
    base = lobby_state()
    return fold(base, decide(base, StartGame(P1), start_ctx()))


def _battle_tiebreak() -> GameState:
    state = dueling()
    state = fold(state, decide(state, mc(state, P1, 0), States.ctx))
    return fold(state, decide(state, mc(state, P2, 0), States.ctx))


def _final_tiebreak() -> GameState:
    state = battle_state()
    tied = {P1: 500, P2: 500, P3: 100}
    state = replace(
        state,
        players={p: replace(s, score=tied[p]) for p, s in state.players.items()},
        round_no=state.rules.battle_rounds,
        pool=full_pool(),
    )
    return fold(state, _next_battle_turn(state, States.ctx))


@pytest.fixture
def states() -> States:
    out = States()
    out["lobby"] = lobby_state()
    out["expansion_question"] = _expansion_question()
    out["expansion_picking"] = picking_state()
    out["battle_target"] = open_turn(battle_state())
    out["battle_duel"] = dueling()
    out["battle_tiebreak"] = _battle_tiebreak()
    out["neutral_challenge"] = challenging()
    out["final_tiebreak"] = _final_tiebreak()
    out["finished"] = replace(lobby_state(), phase=Phase.FINISHED, turn=None)
    out["aborted"] = replace(lobby_state(), phase=Phase.ABORTED, turn=None)
    return out


def _window(state: GameState) -> DeadlineId:
    """The open window, or a sentinel when there is none (terminal/lobby rows)."""
    deadline = state.current_deadline()
    return deadline.id if deadline is not None else DeadlineId(0)


def _actor(state: GameState) -> PlayerId:
    """Whoever is legitimately expected to act right now."""
    turn = state.turn
    for attr in ("current_picker", "attacker_id"):
        if turn is not None and hasattr(turn, attr):
            return getattr(turn, attr)
    active = state.active_players()
    return active[0] if active else P1


def _free_or_any(state: GameState) -> RegionId:
    free = state.free_regions()
    return free[0] if free else RegionId("r4")


@pytest.fixture
def commands() -> dict[str, object]:
    return {
        "join": lambda s: JoinGame(PlayerId("newcomer"), "New"),
        "start": lambda s: StartGame(_actor(s)),
        "answer": lambda s: SubmitAnswer(
            _actor(s), _window(s),
            NumericAnswer(Decimal(100)) if _is_numeric(s) else ChoiceAnswer(0),
            300,
        ),
        "pick": lambda s: PickRegion(_actor(s), _window(s), _free_or_any(s)),
        "target": lambda s: SelectAttackTarget(_actor(s), _window(s), RegionId("r4")),
        "expire": lambda s: ExpireDeadline(_window(s)),
        "surrender": lambda s: Surrender(_actor(s)),
        "abort": lambda s: AbortGame(_actor(s)),
    }


def _is_numeric(state: GameState) -> bool:
    turn = state.turn
    if turn is None or not hasattr(turn, "question"):
        return True
    from triviador.domain.questions.types import QuestionKind

    return turn.question.kind is QuestionKind.NUMERIC
```

One detail the matrix depends on: `expire` for the terminal and lobby rows uses
the sentinel `DeadlineId(0)`, which those rows expect to be **ignored** anyway,
so no real window is needed there. The late clock in `test_cell` is what lets
the accepting `expire` cells past guard 4.

- [ ] **Step 3: Run the matrix**

Run: `cd backend && uv run pytest tests/domain/game/test_matrix.py -v`
Expected: 81 tests PASS (80 cells plus the completeness check)

- [ ] **Step 4: Commit**

```bash
git add backend/tests/domain/game/test_matrix.py backend/tests/domain/game/conftest.py
git commit -m "test(domain): make the transition matrix an executable artifact"
```

---

### Task 21: Property tests and the model-based state machine

**Files:**
- Test: `backend/tests/domain/game/test_properties.py`
- Modify: `backend/pyproject.toml` (coverage gate)

**Interfaces:**
- Consumes: everything.
- Produces: nothing importable.

Seven properties from spec §12.1. The `RuleBasedStateMachine` is the highest-value
test in the suite — it is what finds dead ends in the turn machine that
hand-written tests never do.

Two formulations that must not be simplified:

- **Budget** is an assertion about `required_question_budget` being an *upper
  bound over every trajectory*, generated pools sized exactly to it. It tests
  the formula, not whichever bank happens to exist.
- **Progress** is bounded over **accepted, state-advancing** commands plus
  deadline expirations — never raw command count, because a client can send
  unboundedly many ignored commands.

- [ ] **Step 1: Write the property tests**

Create `backend/tests/domain/game/test_properties.py`:

```python
from dataclasses import replace

from hypothesis import HealthCheck, given, settings
from hypothesis import strategies as st
from hypothesis.stateful import RuleBasedStateMachine, invariant, precondition, rule

from tests.conftest import NOW, full_pool, lobby_state
from triviador.domain.game.rules import DEFAULT_RULES, required_question_budget, validate_rules
from triviador.domain.game.scoring import expected_score
from triviador.domain.game.state import TERMINAL_PHASES, Phase
from triviador.domain.ids import RegionId


@st.composite
def valid_rules(draw: st.DrawFn):
    players = draw(st.integers(min_value=2, max_value=4))
    claims = draw(st.lists(st.integers(0, 3), min_size=players, max_size=players).filter(sum))
    return replace(
        DEFAULT_RULES,
        player_count=players,
        claims_by_rank=tuple(claims),
        expansion_rounds=draw(st.integers(1, 5)),
        battle_rounds=draw(st.integers(1, 5)),
    )


@given(valid_rules())
def test_generated_rules_are_valid(rules) -> None:
    assert validate_rules(rules) == ()


@given(valid_rules())
def test_budget_is_monotonic_in_rounds(rules) -> None:
    bigger = replace(rules, battle_rounds=rules.battle_rounds + 1)
    assert required_question_budget(bigger).numeric > required_question_budget(rules).numeric
    assert (
        required_question_budget(bigger).multiple_choice
        > required_question_budget(rules).multiple_choice
    )


class GameMachine(RuleBasedStateMachine):
    """Drive random legal commands and assert the invariants after every step."""

    def __init__(self) -> None:
        super().__init__()
        self.state = lobby_state()
        self.events: list[object] = []
        self.accepted = 0
        self.budget = required_question_budget(self.state.rules)

    def _apply(self, command) -> None:
        from triviador.domain.game.actions import RejectedCommand
        from triviador.domain.game.reducer import decide, fold

        try:
            events = decide(self.state, command, self._ctx())
        except RejectedCommand:
            return  # rejections change nothing; that is itself under test
        if events:
            self.accepted += 1
            self.events.extend(events)
            self.state = fold(self.state, events)

    def _ctx(self, late: bool = False):
        from datetime import timedelta

        from triviador.domain.game.actions import DecisionContext

        deadline = self.state.current_deadline()
        now = deadline.deadline_at + timedelta(seconds=1) if late and deadline else NOW
        return DecisionContext(
            now=now,
            shuffled_player_ids=tuple(self.state.players),
            base_regions=(RegionId("r0"), RegionId("r2"), RegionId("r6")),
            shuffled_region_ids=self.state.free_regions(),
            drawn_pool=full_pool(),
        )

    @precondition(lambda self: self.state.phase is Phase.LOBBY)
    @rule()
    def start(self) -> None:
        from triviador.domain.game.actions import StartGame

        self._apply(StartGame(next(iter(self.state.players))))

    @precondition(lambda self: self.state.current_deadline() is not None)
    @rule(player_index=st.integers(0, 3), choice=st.integers(0, 3),
          guess=st.integers(0, 300), elapsed=st.integers(0, 20_000))
    def answer(self, player_index: int, choice: int, guess: int, elapsed: int) -> None:
        from decimal import Decimal

        from triviador.domain.game.actions import SubmitAnswer
        from triviador.domain.game.state import ChoiceAnswer, NumericAnswer
        from triviador.domain.questions.types import QuestionKind

        turn = self.state.turn
        active = self.state.active_players()
        if not active or turn is None or not hasattr(turn, "question"):
            return
        player = active[player_index % len(active)]
        value = (
            NumericAnswer(Decimal(guess))
            if turn.question.kind is QuestionKind.NUMERIC
            else ChoiceAnswer(choice)
        )
        window = self.state.current_deadline()
        assert window is not None
        self._apply(SubmitAnswer(player, window.id, value, elapsed))

    @precondition(lambda self: self.state.current_deadline() is not None)
    @rule(region_index=st.integers(0, 8))
    def pick_or_target(self, region_index: int) -> None:
        from triviador.domain.game.actions import PickRegion, SelectAttackTarget
        from triviador.domain.game.reducer import legal_targets
        from triviador.domain.game.state import BattleTargetSelect, ExpansionPicking

        turn = self.state.turn
        window = self.state.current_deadline()
        assert window is not None
        if isinstance(turn, ExpansionPicking):
            free = self.state.free_regions()
            if free:
                self._apply(PickRegion(turn.current_picker, window.id,
                                       free[region_index % len(free)]))
        elif isinstance(turn, BattleTargetSelect):
            targets = legal_targets(self.state, turn.attacker_id)
            if targets:
                self._apply(SelectAttackTarget(turn.attacker_id, window.id,
                                               targets[region_index % len(targets)]))

    @precondition(lambda self: self.state.current_deadline() is not None)
    @rule()
    def expire(self) -> None:
        from triviador.domain.game.actions import ExpireDeadline
        from triviador.domain.game.actions import RejectedCommand
        from triviador.domain.game.reducer import decide, fold

        window = self.state.current_deadline()
        assert window is not None
        try:
            events = decide(self.state, ExpireDeadline(window.id), self._ctx(late=True))
        except RejectedCommand:
            return
        if events:
            self.accepted += 1
            self.events.extend(events)
            self.state = fold(self.state, events)

    @invariant()
    def score_matches_holdings_plus_bonus(self) -> None:
        for player_id in self.state.players:
            assert self.state.players[player_id].score == expected_score(self.state, player_id)

    @invariant()
    def score_log_reconstructs_the_score(self) -> None:
        from triviador.domain.game.events import ScoreChanged

        for player_id in self.state.players:
            total = sum(
                e.delta for e in self.events
                if isinstance(e, ScoreChanged) and e.player_id == player_id
            )
            assert total == self.state.players[player_id].score

    @invariant()
    def replay_reproduces_the_state(self) -> None:
        from triviador.domain.game.reducer import fold

        assert fold(lobby_state(), self.events) == self.state  # type: ignore[arg-type]

    @invariant()
    def a_turn_has_exactly_one_deadline(self) -> None:
        if self.state.turn is None:
            assert self.state.current_deadline() is None
        else:
            assert self.state.current_deadline() is not None

    @invariant()
    def terminal_phases_have_no_open_turn(self) -> None:
        if self.state.phase in TERMINAL_PHASES:
            assert self.state.turn is None

    @invariant()
    def eliminated_players_own_nothing(self) -> None:
        for player_id, player in self.state.players.items():
            if player.is_eliminated:
                assert self.state.owned_by(player_id) == ()

    @invariant()
    def the_pool_is_never_exhausted(self) -> None:
        assert self.state.pool.numeric_used <= self.budget.numeric
        assert self.state.pool.mc_used <= self.budget.multiple_choice

    @invariant()
    def progress_is_bounded(self) -> None:
        rules = self.state.rules
        ceiling = (
            rules.expansion_rounds * (1 + sum(rules.claims_by_rank))
            + rules.battle_rounds * rules.player_count * 4
            + len(self.state.map.regions) * 3
            + 50
        )
        assert self.accepted <= ceiling, "state machine has an unbounded cycle"

    @precondition(lambda self: self.state.phase in TERMINAL_PHASES)
    @rule()
    def terminal_is_absorbing(self) -> None:
        assert self.state.phase in (Phase.FINISHED, Phase.ABORTED)


TestGameMachine = GameMachine.TestCase
TestGameMachine.settings = settings(
    max_examples=200, stateful_step_count=120,
    suppress_health_check=[HealthCheck.too_slow],
)
```

Every `@rule` picks only currently-legal actions from `self.state`, so the
machine explores real trajectories rather than bouncing off guards. Rejections
are swallowed on purpose: a `RejectedCommand` must leave the state untouched,
which the invariants then re-verify.

- [ ] **Step 2: Run the properties**

Run: `cd backend && uv run pytest tests/domain/game/test_properties.py -v`
Expected: PASS. If `progress_is_bounded` fails, the turn machine has a cycle —
fix the reducer, never the ceiling.

- [ ] **Step 3: Add the reducer coverage gate**

Append to `backend/pyproject.toml`:

```toml
[tool.coverage.run]
branch = true
source = ["src/triviador"]
```

Then verify the reducer is fully covered:

Run: `cd backend && uv run pytest --cov --cov-report=term-missing --cov-fail-under=0 && uv run coverage report --include='*/domain/game/reducer.py' --fail-under=100`
Expected: `reducer.py` at 100 % branch coverage. Any uncovered branch is a
reachable state nothing tests — add the test, do not lower the number.

- [ ] **Step 4: Run the whole suite**

Run: `cd backend && uv run pytest -v && uv run ruff check . && uv run ruff format --check . && uv run mypy`
Expected: everything PASS

- [ ] **Step 5: Commit**

```bash
git add backend/tests/domain/game/test_properties.py backend/pyproject.toml
git commit -m "test(domain): add property tests, state machine and reducer coverage gate"
```

---

## Plan 1 completion criteria

- `uv run pytest` green, including 80 matrix cells and the Hypothesis machine
- `reducer.py` at 100 % branch coverage
- `ruff check`, `ruff format --check`, `mypy --strict` clean
- No import from `domain/` reaches `services/`, `api/`, `db/`, or the filesystem
- `grep -rn "random\.\|datetime.now\|uuid4" backend/src/triviador/domain/` returns nothing

## What Plan 2 consumes from this

`decide`, `evolve`, `fold`, `GameState`, `Command`, `GameEvent`, `DecisionContext`,
`RejectedCommand`, `RejectCode`, `required_question_budget`, `legal_targets`,
`MapRegistry`. Plan 2 adds the event log, `GameRuntime`, recovery, quarantine and
the watchdog around this library without modifying it.
