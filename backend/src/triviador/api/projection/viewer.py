"""Who is looking, from the point of view of one game.

Spec 1B §6.5: a connection stores an `AuthenticatedPrincipal`, and a
`ViewerContext` is constructed per `(connection, game)` after membership
authorization. Keeping them separate types is what stops "authenticated"
from being mistaken for "a player in this game".
"""

from dataclasses import dataclass

from triviador.domain.game.state import GameState
from triviador.domain.ids import PlayerId, UserId
from triviador.services.identity import AuthenticatedPrincipal, UserRole


@dataclass(frozen=True)
class ViewerContext:
    user_id: UserId
    player_id: PlayerId | None
    role: UserRole


def viewer_for(state: GameState, principal: AuthenticatedPrincipal) -> ViewerContext:
    """`player_id` is a membership test, never a lookup.

    A user's `PlayerId` in a game *is* their `UserId` — `game_players.
    user_id` is a foreign key to `users.id` — so participation is exactly
    "is this id among the seated players", and there is no table that could
    disagree with the folded state.
    """
    player_id = PlayerId(principal.user_id)
    return ViewerContext(
        user_id=principal.user_id,
        player_id=player_id if player_id in state.players else None,
        role=principal.role,
    )
