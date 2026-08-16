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
