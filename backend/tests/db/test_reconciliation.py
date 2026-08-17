"""§5.5's ambiguous-commit reconciliation. "Any mismatch is quarantine,
never 'close enough'" — so each of the four ways a batch can fail to match
gets its own assertion."""

import pytest

from tests.db.conftest import LobbyGame
from triviador.domain.game.events import PlayerJoined, PlayerLeft
from triviador.domain.ids import PlayerId
from triviador.services.ports import ReconcileOutcome

pytestmark = [pytest.mark.integration, pytest.mark.asyncio(loop_scope="session")]


async def test_absent_when_nothing_with_that_operation_id_committed(lobby_game: LobbyGame) -> None:
    async with lobby_game.uow.begin() as tx:
        verdict = await tx.operation_matches(
            lobby_game.game_id,
            "op-never-ran",
            expected_base_seq=1,
            events=[PlayerJoined(PlayerId("p1"), "P1", seat=0)],
        )
    assert verdict is ReconcileOutcome.ABSENT


async def test_matched_for_the_exact_batch_that_committed(lobby_game: LobbyGame) -> None:
    events = [
        PlayerJoined(PlayerId("p1"), "P1", seat=0),
        PlayerJoined(PlayerId("p2"), "P2", seat=1),
    ]
    async with lobby_game.uow.begin() as tx:
        await tx.append(lobby_game.game_id, expected_last_seq=1, events=events, operation_id="op-1")

    async with lobby_game.uow.begin() as tx:
        verdict = await tx.operation_matches(
            lobby_game.game_id, "op-1", expected_base_seq=1, events=events
        )
    assert verdict is ReconcileOutcome.MATCHED


async def test_mismatch_when_the_row_count_differs(lobby_game: LobbyGame) -> None:
    committed = [
        PlayerJoined(PlayerId("p1"), "P1", seat=0),
        PlayerJoined(PlayerId("p2"), "P2", seat=1),
    ]
    async with lobby_game.uow.begin() as tx:
        await tx.append(
            lobby_game.game_id, expected_last_seq=1, events=committed, operation_id="op-1"
        )

    async with lobby_game.uow.begin() as tx:
        verdict = await tx.operation_matches(
            lobby_game.game_id, "op-1", expected_base_seq=1, events=committed[:1]
        )
    assert verdict is ReconcileOutcome.MISMATCH


async def test_mismatch_when_the_ordered_types_differ(lobby_game: LobbyGame) -> None:
    """Same count, same seq range, different batch. This is the case a
    bare `SELECT count(*)` would wave through — and the reason
    `events_for_operation` returns wire names rather than just seqs."""
    async with lobby_game.uow.begin() as tx:
        await tx.append(
            lobby_game.game_id,
            expected_last_seq=1,
            events=[
                PlayerJoined(PlayerId("p1"), "P1", seat=0),
                PlayerJoined(PlayerId("p2"), "P2", seat=1),
            ],
            operation_id="op-1",
        )

    async with lobby_game.uow.begin() as tx:
        verdict = await tx.operation_matches(
            lobby_game.game_id,
            "op-1",
            expected_base_seq=1,
            events=[PlayerJoined(PlayerId("p1"), "P1", seat=0), PlayerLeft(PlayerId("p2"))],
        )
    assert verdict is ReconcileOutcome.MISMATCH


async def test_mismatch_when_the_seq_range_is_not_the_expected_one(lobby_game: LobbyGame) -> None:
    """The batch committed at seq 2, but this attempt decided against
    state.seq = 5. Same rows, wrong place in history — accepting it would
    fold events onto a state they were never decided against."""
    events = [PlayerJoined(PlayerId("p1"), "P1", seat=0)]
    async with lobby_game.uow.begin() as tx:
        await tx.append(lobby_game.game_id, expected_last_seq=1, events=events, operation_id="op-1")

    async with lobby_game.uow.begin() as tx:
        verdict = await tx.operation_matches(
            lobby_game.game_id, "op-1", expected_base_seq=5, events=events
        )
    assert verdict is ReconcileOutcome.MISMATCH
