"""Encode/decode between `GameEvent` instances and JSONB-ready payloads.

Serialization is Pydantic's `TypeAdapter` per event class, not hand-rolled
reflection: the 36 dataclasses nest `GameRules`, `Deadline`,
`SubmittedAnswer`, `QuestionPool`, `Decimal`, `StrEnum`, `NewType` aliases,
tuples and mappings, which is exactly where a hand-written walker
accumulates quiet bugs. `TypeAdapter` instances are cached per class (see
`_adapter_for`) — building one per event during a 300-event replay would be
pure waste.

Two invariants Pydantic will not enforce on its own are handled explicitly:

- `Decimal` must round-trip exactly (a numeric answer that passes through an
  IEEE double is a wrong answer). `TypeAdapter.dump_python(mode="json")`
  already renders `Decimal` as a JSON string and validates a JSON string
  back into an exact `Decimal` — verified empirically before writing this
  module, see the task report — so no extra annotation is needed here.
- Every `datetime` reachable from an event must be aware and UTC. Pydantic
  accepts a naive `datetime` on both the Python side and the JSON side, so
  this is enforced structurally by `normalize_utc`, called on both `encode`
  and `decode`.
"""

from collections.abc import Mapping
from dataclasses import fields, is_dataclass, replace
from datetime import UTC, datetime
from typing import Any, cast

from pydantic import TypeAdapter

from triviador.db.codec.registry import CLASSES_BY_WIRE_NAME, CURRENT_VERSION, WIRE_NAMES
from triviador.db.codec.upcasters import upcast_chain
from triviador.db.errors import NaiveDatetime, UnknownEventType
from triviador.domain.game.events import GameEvent

# A manual dict cache rather than `functools.cache`: mypy strict rejects a
# `type[X] | type[Y] | ...` argument against `functools`'s `Hashable`-typed
# `_lru_cache_wrapper.__call__` (a typeshed quirk in how a metaclass's
# `__hash__` matches the `Hashable` protocol), which would otherwise force a
# `type: ignore` at every call site instead of once here.
_ADAPTERS: dict[type[Any], TypeAdapter[Any]] = {}


def _adapter_for(cls: type[Any]) -> TypeAdapter[Any]:
    adapter = _ADAPTERS.get(cls)
    if adapter is None:
        adapter = TypeAdapter(cls)
        _ADAPTERS[cls] = adapter
    return adapter


def _walk(value: Any, path: str) -> Any:
    """Every datetime reachable from `value` must be aware and UTC.

    The walk is structural — driven by `isinstance` on the actual value
    tree, not by a registry of "the fields known to carry a datetime" — so
    a new datetime field on any future event inherits the invariant without
    anyone needing to remember to add it here.

    A naive value has no correct instant to recover, so it is rejected
    outright (`NaiveDatetime`). An aware-but-not-UTC value (a `+02:00`
    offset, or Pydantic's own non-identical UTC tzinfo after JSON parsing)
    denotes the correct instant, so it is normalized via `astimezone(UTC)`
    rather than rejected — rejecting it would turn a harmless producer
    difference into a game that cannot load.

    `list`, `set`, and `frozenset` are deliberately not traversed silently:
    no event field uses them today, but `frozenset` is already in the
    domain's vocabulary elsewhere (`MapDefinition.adjacency`), so it is one
    refactor away from appearing in an event. A container this walk doesn't
    recognize raises `TypeError` rather than falling through to the leaf
    case, which would silently skip any datetime nested inside it.
    """
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise NaiveDatetime(path)
        return value.astimezone(UTC)
    if is_dataclass(value):
        updates = {f.name: _walk(getattr(value, f.name), f"{path}.{f.name}") for f in fields(value)}
        # `is_dataclass` narrows `value` to `DataclassInstance | type[DataclassInstance]`,
        # a union `replace`'s TypeVar can't bind to; it is always an instance here
        # since nothing in an event tree holds a bare class object.
        instance: Any = value
        return replace(instance, **updates)
    if isinstance(value, tuple):
        return tuple(_walk(item, f"{path}[{i}]") for i, item in enumerate(value))
    if isinstance(value, Mapping):
        return {key: _walk(item, f"{path}[{key!r}]") for key, item in value.items()}
    if isinstance(value, list | set | frozenset):
        raise TypeError(
            f"{path}: codec cannot walk a {type(value).__name__} — no event field uses one "
            "today, and this walk must not silently skip whatever it might contain"
        )
    return value


def normalize_utc[T](value: T) -> T:
    """Typed facade over `_walk`.

    `_walk` is untyped (`Any` in, `Any` out) because it recurses through
    heterogeneous dataclass fields, tuple elements, and mapping values —
    there is no single concrete type to give it. But `encode`/`decode` both
    want the *input* type back (`GameEvent` in, `GameEvent` out), not `Any`;
    without this facade, `decode`'s return needed a `type:
    ignore[no-any-return]`, and `encode` passed an unchecked `Any` into
    `dump_python` with no mypy diagnostic to show for it either.
    """
    return cast(T, _walk(value, "$"))


def encode(event: GameEvent) -> tuple[str, int, dict[str, Any]]:
    wire_type = WIRE_NAMES[type(event)]
    normalized = normalize_utc(event)
    adapter = _adapter_for(type(event))
    payload: dict[str, Any] = adapter.dump_python(normalized, mode="json")
    return wire_type, CURRENT_VERSION[wire_type], payload


def decode(wire_type: str, schema_version: int, payload: Mapping[str, Any]) -> GameEvent:
    cls = CLASSES_BY_WIRE_NAME.get(wire_type)
    if cls is None:
        raise UnknownEventType(wire_type)
    upcast = upcast_chain(wire_type, schema_version)  # raises UnknownSchemaVersion
    event: GameEvent = _adapter_for(cls).validate_python(upcast(dict(payload)))
    return normalize_utc(event)
