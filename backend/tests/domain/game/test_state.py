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
            PlayerId("p1"): PlayerState(
                PlayerId("p1"),
                "One",
                seat=0,
                score=0,
                bonus_score=0,
                base_region=RegionId("a"),
                is_eliminated=False,
            ),
            PlayerId("p2"): PlayerState(
                PlayerId("p2"),
                "Two",
                seat=1,
                score=0,
                bonus_score=0,
                base_region=RegionId("c"),
                is_eliminated=True,
            ),
        },
        territories={
            RegionId("a"): Territory(
                RegionId("a"),
                PlayerId("p1"),
                TerritoryKind.BASE,
                PlayerId("p1"),
                3,
                AcquisitionKind.BASE,
            ),
            RegionId("b"): Territory(RegionId("b"), None, TerritoryKind.NORMAL, None, None, None),
            RegionId("c"): Territory(
                RegionId("c"),
                PlayerId("p2"),
                TerritoryKind.BASE,
                PlayerId("p2"),
                3,
                AcquisitionKind.BASE,
            ),
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
