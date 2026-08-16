"""The event codec: pure translation between `GameEvent` instances and the
`(type, schema_version, payload)` triple stored in `game_events`.

Imports `triviador.domain.game.events` and nothing from the database — no
session, no engine, no SQLAlchemy. That purity is what lets its tests live
in `tests/codec/` and run with PostgreSQL stopped.
"""

from triviador.db.codec.codec import decode, encode
from triviador.db.codec.registry import CURRENT_VERSION, WIRE_NAMES

__all__ = ["CURRENT_VERSION", "WIRE_NAMES", "decode", "encode"]
