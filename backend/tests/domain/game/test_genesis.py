"""Genesis: GameCreated is consumed, never folded."""

import pytest

from tests.conftest import grid_map
from triviador.domain.game import events as ev
from triviador.domain.game.genesis import GenesisEventNotFoldable, create_initial_state
from triviador.domain.game.reducer import evolve, fold
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.game.state import GameState, Phase
from triviador.domain.ids import GameId, MapId, PlayerId

CREATED = ev.GameCreated(
    map_id=MapId("grid"),
    rules=DEFAULT_RULES,
    host_id=PlayerId("p1"),
    map_sha256="0" * 64,
)


def a_state() -> GameState:
    return create_initial_state(CREATED, GameId("g1"), grid_map())


def test_genesis_produces_an_empty_lobby_at_seq_one() -> None:
    state = a_state()
    assert state.seq == 1, "GameCreated is seq 1; last_seq=0 is only a pre-insert value"
    assert state.phase is Phase.LOBBY
    assert state.players == {}
    assert state.turn_order == ()
    assert state.turn is None
    assert state.winner_id is None
    assert state.round_no == 0
    assert state.next_deadline_id == 1


def test_genesis_seeds_one_unowned_territory_per_region() -> None:
    state = a_state()
    assert set(state.territories) == set(grid_map().region_ids())
    assert all(t.owner_id is None for t in state.territories.values())
    assert state.free_regions() == grid_map().region_ids()


def test_genesis_carries_the_rules_and_the_map() -> None:
    state = a_state()
    assert state.rules == DEFAULT_RULES
    assert state.map == grid_map()
    assert state.game_id == GameId("g1")


def test_the_pool_starts_empty() -> None:
    state = a_state()
    assert state.pool.numeric == ()
    assert state.pool.multiple_choice == ()


def test_folding_game_created_is_refused() -> None:
    """ADR-004 reads 'log + map registry -> state'. GameCreated is the genesis:
    consumed by create_initial_state, never replayed through evolve."""
    with pytest.raises(GenesisEventNotFoldable):
        evolve(a_state(), CREATED)


def test_recovery_is_genesis_then_fold() -> None:
    """The shape Plan 4's recovery uses: construct from events[0], fold the
    rest."""
    log: list[ev.GameEvent] = [CREATED, ev.PlayerJoined(PlayerId("p1"), "One", seat=0)]
    genesis = log[0]
    assert isinstance(genesis, ev.GameCreated)
    state = fold(create_initial_state(genesis, GameId("g1"), grid_map()), log[1:])
    assert state.players[PlayerId("p1")].display_name == "One"
    assert state.seq == 2
