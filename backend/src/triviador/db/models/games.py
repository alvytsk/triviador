"""ORM models for games, seated players, and the event log.

Spec 1 §7 plus Spec 1B §4.1 (`game_events.schema_version`). The event-log
table is named `GameEventRow`, not `GameEvent`: the domain already has a
`GameEvent` union of 36 event dataclasses in
`triviador.domain.game.events`, and a module that imports both under one
name would produce a type error far from its cause.

`games` and `game_players` are projections of the event log maintained in
the same transaction as the event append (Spec 1B §4.2), never by an
asynchronous projector — but that write discipline lives in the
application/service layer, not in these declarations.

This module declares no update/delete-oriented machinery for `game_events`:
the event log is append-only.
"""

from datetime import datetime
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    SmallInteger,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from triviador.db.base import Base


class Game(Base):
    """`status` mirrors the domain's `Phase`, which has no `FINAL`.

    `FinalTiebreak` is a `Turn` variant inside `BATTLE` (Spec 1B §4.2), so
    there is no `final` status here either. A `TEXT` + `CheckConstraint`
    over literal strings is used instead of a PostgreSQL `ENUM` type:
    adding a value to a PG enum inside a transaction has historically been
    restricted, and this set is small and stable enough that the check
    constraint costs nothing.
    """

    __tablename__ = "games"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=lambda: str(uuid4()))
    map_id: Mapped[str] = mapped_column(Text)
    # Frozen at creation: editing a preset afterward must not perturb a
    # running game, which is why the rules are copied here rather than
    # dereferenced from `preset_id` on every read.
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB)
    preset_id: Mapped[str | None] = mapped_column(ForeignKey("rule_presets.id"))
    status: Mapped[str] = mapped_column(Text)
    host_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    winner_id: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    # No default: creation always sets this explicitly to 1, in the same
    # transaction that writes the genesis `game.created` event at seq=1
    # (Spec 1B §6.2) — a DB-side default of 0 would describe a row state
    # that is never actually persisted.
    last_seq: Mapped[int] = mapped_column(Integer)

    __table_args__ = (
        CheckConstraint(
            "status IN ('lobby', 'expansion', 'battle', 'finished', 'aborted')",
            name="status_valid",
        ),
    )


class GamePlayer(Base):
    """A seated player.

    Composite PK `(game_id, user_id)`; `seat` is unique per game so two
    players can never occupy the same slot.
    """

    __tablename__ = "game_players"

    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"), primary_key=True)
    seat: Mapped[int] = mapped_column(Integer)
    final_score: Mapped[int | None] = mapped_column(Integer)

    __table_args__ = (UniqueConstraint("game_id", "seat"),)


class GameEventRow(Base):
    """The append-only event log.

    PK `(game_id, seq)`; never updated or deleted after insert.
    `schema_version` (Spec 1B §4.1) lets the codec evolve `payload`'s shape
    per event type without a destructive rewrite of history.
    """

    __tablename__ = "game_events"

    game_id: Mapped[str] = mapped_column(ForeignKey("games.id"), primary_key=True)
    seq: Mapped[int] = mapped_column(primary_key=True)
    operation_id: Mapped[str] = mapped_column(index=False)
    type: Mapped[str]
    schema_version: Mapped[int] = mapped_column(SmallInteger)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (Index("ix_game_events_game_id_operation_id", "game_id", "operation_id"),)
