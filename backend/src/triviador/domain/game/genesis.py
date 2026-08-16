"""Where a GameState comes from.

`GameCreated` is a genesis event: it is *consumed* to build the initial state,
never folded through `evolve`. Recovery is therefore

    create_initial_state(events[0], game_id, map_defn)
    fold(that, events[1:])

which is what makes ADR-004 ("the event log is the truth") read literally, with
the map registry supplying the one immutable input the log references by id.
"""

from triviador.domain.game import events as ev
from triviador.domain.game.state import GameState, Phase, Territory, TerritoryKind
from triviador.domain.ids import GameId
from triviador.domain.maps.definition import MapDefinition
from triviador.domain.questions.types import QuestionPool


class GenesisEventNotFoldable(Exception):
    """A genesis event was handed to `evolve`. Use `create_initial_state`."""


def create_initial_state(
    event: ev.GameCreated, game_id: GameId, map_defn: MapDefinition
) -> GameState:
    """Build the empty lobby a game starts from.

    `seq=1` because `GameCreated` *is* sequence 1: creation writes the `games`
    row and the genesis event in one transaction, so `last_seq=0` exists only
    as a pre-insert value and never as a persisted row.
    """
    return GameState(
        game_id=game_id,
        seq=1,
        next_deadline_id=1,
        map=map_defn,
        rules=event.rules,
        phase=Phase.LOBBY,
        round_no=0,
        turn_order=(),
        players={},
        territories={
            region_id: Territory(
                region_id=region_id,
                owner_id=None,
                kind=TerritoryKind.NORMAL,
                base_owner_id=None,
                base_hp=None,
                acquisition=None,
            )
            for region_id in map_defn.region_ids()
        },
        turn=None,
        pool=QuestionPool(numeric=(), multiple_choice=()),
        winner_id=None,
    )
