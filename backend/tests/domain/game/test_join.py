"""Seat allocation. Seats are an identity, not a counter."""

from tests.conftest import NOW, lobby_state
from triviador.domain.game import events as ev
from triviador.domain.game.actions import DecisionContext, JoinGame, Surrender
from triviador.domain.game.reducer import decide, fold
from triviador.domain.ids import PlayerId

CTX = DecisionContext(now=NOW)


def test_first_join_takes_seat_zero() -> None:
    state = lobby_state(players={})
    assert decide(state, JoinGame(PlayerId("p1"), "One"), CTX) == (
        ev.PlayerJoined(PlayerId("p1"), "One", seat=0),
    )


def test_join_takes_the_next_free_seat() -> None:
    state = lobby_state(players={"p1": 0, "p2": 1})
    assert decide(state, JoinGame(PlayerId("p3"), "Three"), CTX) == (
        ev.PlayerJoined(PlayerId("p3"), "Three", seat=2),
    )


def test_a_seat_freed_from_the_middle_is_reused() -> None:
    """The regression: p2 leaves seat 1, p4 joins and must take seat 1 — not
    seat 2, which p3 still holds. `UNIQUE(game_id, seat)` in Plan 3 makes the
    old `seat=len(players)` behaviour a hard failure."""
    state = lobby_state(players={"p1": 0, "p2": 1, "p3": 2})
    state = fold(state, decide(state, Surrender(PlayerId("p2")), CTX))

    events = decide(state, JoinGame(PlayerId("p4"), "Four"), CTX)

    assert events == (ev.PlayerJoined(PlayerId("p4"), "Four", seat=1),)
    after = fold(state, events)
    seats = sorted(p.seat for p in after.players.values())
    assert seats == [0, 1, 2], "seats must stay unique"
