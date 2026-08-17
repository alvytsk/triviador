"""`UnitOfWork` and `TransactionContext`: the optimistic event append and the
read-model projection, sharing one transaction (Spec 1B §4.2, §4.4, §5.2).

Two properties this module exists to guarantee:

1. **The optimistic check is one statement.** `TransactionContext.append`
   issues `UPDATE games SET last_seq = :new WHERE id = :gid AND last_seq =
   :expected RETURNING id` before any insert. That statement takes the row
   lock and performs the check together, so two concurrent appends against
   the same game serialize on the `games` row rather than racing on
   `game_events`'s primary key. No returned row means someone else already
   advanced `last_seq`; `append` raises `ConcurrentModification`, which the
   runtime quarantines on and never retries.

2. **The read model is projected in the same transaction.** `append` also
   applies `_project`'s effects on `games` and `game_players` before
   returning — no asynchronous projector, no backfill, no separate method
   a caller could forget to invoke.

`append` does not handle genesis: `UPDATE ... WHERE last_seq = :expected`
has no row to match before the `games` row exists. `GameRepository.create`
(a later task) writes the `games` row and the seq-1 `GameCreated` row
directly, in its own transaction, before any game's stream reaches this
module.
"""

from collections.abc import AsyncIterator, Sequence
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import assert_never

from sqlalchemy import delete, func, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.codec.codec import decode
from triviador.db.errors import ConcurrentModification
from triviador.db.models.games import Game, GamePlayer
from triviador.db.repositories.events import (
    insert_event_rows,
    select_event_refs_for_operation,
    select_events_ordered,
)
from triviador.db.repositories.questions import QuestionBank
from triviador.domain.game import events as ev
from triviador.domain.game.events import GameEvent
from triviador.domain.ids import GameId


@dataclass(frozen=True)
class PersistedEventRef:
    """One committed row's identity, without its payload.

    `type` is the wire name (e.g. `"battle.territory_captured"`), not the
    Python class. Plan 4's ambiguous-commit reconciliation (§5.5) verifies
    the exact seq range, the row count, *and* the ordered types against the
    batch it still holds in memory — a bare `tuple[int, ...]` would make the
    third check impossible.
    """

    seq: int
    type: str


class TransactionContext:
    """Everything one command does to the database, inside one transaction.

    `session` is exposed so a materialiser (Plan 4's `StartGame`, via §5.3)
    can run its own `FOR SHARE` selection inside the same transaction that
    later appends — selection and append share one unit of work for every
    command, not as a special case.

    No `commit()` here: the transaction boundary belongs to
    `UnitOfWork.begin`'s context manager. §5.2 requires that an origin
    resolve only after that context exits, so nothing on this class may end
    the transaction from inside the `async with` block — that would let the
    runtime produce an external response while locks are still held.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @property
    def questions(self) -> QuestionBank:
        """A `QuestionBank` bound to *this* transaction's session.

        Selection and append share one unit of work for every command
        (§5.3), so the `FOR SHARE` locks the bank takes are still held when
        the resulting `QuestionPoolDrawn` event is inserted. Exposing the
        bank rather than the raw `session` is what keeps `AsyncSession` out
        of `services.ports.Transaction` — and therefore out of every
        signature `runtime/` can see.
        """
        return QuestionBank(self.session)

    async def append(
        self,
        game_id: GameId,
        *,
        expected_last_seq: int,
        events: Sequence[GameEvent],
        operation_id: str,
    ) -> None:
        """§4.4. The `UPDATE` runs first, before any insert: it takes the
        `games` row lock and performs the optimistic check in one statement,
        so two concurrent appends against the same game serialize on that
        row rather than racing on `game_events`'s primary key.

        An empty `events` is a caller bug, not silently accepted: §5.2's
        consumer loop resolves a no-op before ever reaching `append`, so an
        empty append here would silently advance `last_seq` for nothing.
        """
        if not events:
            raise ValueError("append requires at least one event; a no-op resolves earlier")

        new_seq = expected_last_seq + len(events)
        result = await self.session.execute(
            update(Game)
            .where(Game.id == game_id, Game.last_seq == expected_last_seq)
            .values(last_seq=new_seq)
            .returning(Game.id)
        )
        # `.returning(Game.id)` in place of a bare rowcount check: `Result`
        # (the type `session.execute` is annotated to return) has no
        # `.rowcount` under mypy strict — only `CursorResult`, which is what
        # this statement actually returns at runtime, does — so a `.returning`
        # clause plus `.first() is None` gets the same single-statement
        # lock-and-check with no cast needed to satisfy the type checker.
        if result.first() is None:
            raise ConcurrentModification(game_id, expected_last_seq)

        insert_event_rows(self.session, game_id, expected_last_seq, events, operation_id)
        await self._project(game_id, events)

    async def load_stream(self, game_id: GameId) -> tuple[GameEvent, ...]:
        rows = await select_events_ordered(self.session, game_id)
        return tuple(decode(row.type, row.schema_version, row.payload) for row in rows)

    async def events_for_operation(
        self, game_id: GameId, operation_id: str
    ) -> tuple[PersistedEventRef, ...]:
        """§5.5's reconciliation query verbatim. Deliberately does not
        decode `payload`: reconciliation only asks "did my batch commit?",
        and decoding rows a different code path may have written is both
        slower and a second chance to fail."""
        rows = await select_event_refs_for_operation(self.session, game_id, operation_id)
        return tuple(PersistedEventRef(seq=seq, type=type_) for seq, type_ in rows)

    async def _project(self, game_id: GameId, events: Sequence[GameEvent]) -> None:
        """§4.2's read model, applied event by event.

        Structured as a `match` over every event type in the union, not a
        generic dispatch keyed by wire name or a narrow `if` chain: a future
        event type that ought to affect `games` or `game_players` and isn't
        given a branch here falls into `case _` and raises loudly, instead
        of silently doing nothing to the read model.
        """
        for event in events:
            match event:
                case ev.GameCreated():
                    # Genesis. `append` never receives this — see the module
                    # docstring — so reaching here is a caller bug, not a
                    # normal event stream.
                    raise AssertionError(
                        "GameCreated is genesis; GameRepository.create writes the "
                        "games row and this event directly, append() never receives it"
                    )

                case ev.PlayerJoined(player_id=player_id, seat=seat):
                    self.session.add(GamePlayer(game_id=game_id, user_id=player_id, seat=seat))

                case ev.PlayerLeft(player_id=player_id):
                    # The DELETE half of Plan 2's seat fix: `_decide_join`
                    # allocates the lowest unused seat, which only stays
                    # correct if a departure actually frees its row here.
                    # Leaving it behind would collide with
                    # UNIQUE(game_id, seat) the next time that seat is taken.
                    await self.session.execute(
                        delete(GamePlayer).where(
                            GamePlayer.game_id == game_id,
                            GamePlayer.user_id == player_id,
                        )
                    )

                case ev.GameStarted():
                    # `func.now()` here is transaction-*start* time in
                    # PostgreSQL, stable for the whole transaction — and this
                    # projection runs in the same transaction as the INSERT
                    # of the `GameStarted` row itself. So `games.started_at`
                    # is exactly `game_events.created_at` of the causing row,
                    # derivable from the log rather than floating free of
                    # it: no domain event carries its own timestamp today
                    # (`GameStarted(turn_order)` has none), so there is no
                    # "decision time" to project instead. The replay-safe
                    # shape is `_project(game_id, events, occurred_at)` —
                    # live `append` passing `func.now()`, a future replay
                    # passing the stored row's `created_at` — but no replay
                    # path exists yet; that's Plan 4's change. Once a
                    # `Clock` gives events their own decision time, this and
                    # the row's `created_at` can diverge, and that's the
                    # trigger to switch this projection to the event's time.
                    await self.session.execute(
                        update(Game)
                        .where(Game.id == game_id)
                        .values(status="expansion", started_at=func.now())
                    )

                case ev.ExpansionRoundStarted():
                    # Also phase-bearing (`reducer.py` sets `Phase.EXPANSION`
                    # here too) — redundant with `GameStarted` on the first
                    # round, a no-op re-assertion on any round after it.
                    await self.session.execute(
                        update(Game).where(Game.id == game_id).values(status="expansion")
                    )

                case ev.BattleRoundStarted():
                    await self.session.execute(
                        update(Game).where(Game.id == game_id).values(status="battle")
                    )

                case ev.GameFinished(winner_id=winner_id, final_scores=final_scores):
                    await self.session.execute(
                        update(Game)
                        .where(Game.id == game_id)
                        .values(status="finished", finished_at=func.now(), winner_id=winner_id)
                    )
                    for player_id, score in final_scores.items():
                        await self.session.execute(
                            update(GamePlayer)
                            .where(
                                GamePlayer.game_id == game_id,
                                GamePlayer.user_id == player_id,
                            )
                            .values(final_score=score)
                        )

                case ev.GameAborted():
                    await self.session.execute(
                        update(Game)
                        .where(Game.id == game_id)
                        .values(status="aborted", finished_at=func.now())
                    )

                case (
                    ev.BasesAssigned()
                    | ev.QuestionPoolDrawn()
                    | ev.MediaWarmupStarted()
                    | ev.QuestionPresented()
                    | ev.AnswerSubmitted()
                    | ev.AnswerWindowClosed()
                    | ev.QuestionResolved()
                    | ev.PicksGranted()
                    | ev.TerritoryClaimed()
                    | ev.ExpansionRoundCompleted()
                    | ev.TurnStarted()
                    | ev.TurnSkipped()
                    | ev.TurnAborted()
                    | ev.AttackDeclared()
                    | ev.DuelResolved()
                    | ev.TiebreakStarted()
                    | ev.TerritoryCaptured()
                    | ev.NeutralTerritoryCaptured()
                    | ev.NeutralAttackFailed()
                    | ev.DefenseHeld()
                    | ev.BaseDamaged()
                    | ev.BaseDestroyed()
                    | ev.BattleRoundCompleted()
                    | ev.ScoreChanged()
                    | ev.PlayerEliminated()
                    | ev.PlayerSurrendered()
                    | ev.TerritoryNeutralized()
                    | ev.FinalTiebreakStarted()
                ):
                    # No effect on `games` or `game_players` — territory,
                    # score, and turn state live only in the folded
                    # `GameState`, not in the read model this projects.
                    pass

                case _:  # pragma: no cover — unreachable while the branches
                    # above cover every member of the `GameEvent` union.
                    # `assert_never`, not a bare raise: mypy narrows `event`
                    # to `Never` here as long as every branch above is
                    # exhaustive, so a 37th event type added to the union
                    # without a branch fails *type-checking* (CI), not the
                    # first production append of that event.
                    assert_never(event)


class UnitOfWork:
    """One transaction per `begin()`. No autocommit, no nested `begin`."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[TransactionContext]:
        async with self._sessionmaker() as session, session.begin():
            yield TransactionContext(session)
