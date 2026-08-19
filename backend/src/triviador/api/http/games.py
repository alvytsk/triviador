"""§6.1's game surface, and §6.2's deliberate two commits.

Every mutation here goes through the one serialised queue. There is no
second route by which state can change (§8.2) — including creation, where
the `games` row and its genesis event are written directly *because there
is no runtime before the game exists*, and the host's `PlayerJoined` then
goes through the queue like everything else.
"""

import uuid
from typing import assert_never

from fastapi import APIRouter

from triviador.api.deps import Deps, Principal
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.projection.snapshot import project_snapshot
from triviador.api.projection.viewer import viewer_for
from triviador.api.schemas.games import CreateGameRequest, GameSnapshot, LobbyGameSummary
from triviador.domain.game.actions import Command, JoinGame, RejectedCommand, StartGame
from triviador.domain.game.state import Phase
from triviador.domain.ids import GameId, MapId, PlayerId
from triviador.maps.registry import InvalidMapError
from triviador.runtime.origins import Accepted, Failed, FutureOrigin, Ignored, Rejected
from triviador.runtime.runtime import GameRuntime, QueuedCommand
from triviador.services.identity import AuthenticatedPrincipal

router = APIRouter(prefix="/api/games", tags=["games"])


async def _display_name(deps: Deps, principal: AuthenticatedPrincipal) -> str:
    user = await deps.users.get(principal.user_id)
    if user is None:
        raise ApiError(ApiErrorCode.UNAUTHENTICATED, 401, "not signed in")
    return user.display_name


def _snapshot(deps: Deps, runtime: GameRuntime, principal: AuthenticatedPrincipal) -> GameSnapshot:
    state = runtime.state
    return project_snapshot(
        state, viewer_for(state, principal), media_base=deps.settings.media_public_base
    )


async def _submit(deps: Deps, game_id: GameId, command: Command) -> GameRuntime:
    """One command, awaited to its outcome.

    REST genuinely awaits its result (§8.2), so this is the one origin in
    the system that holds a future. A cancelled request leaves that future
    settled by nobody — `FutureOrigin` already treats an `InvalidStateError`
    as a logged non-event, because the batch is durable and destroying a
    healthy game over a dead HTTP connection would be the actual bug.
    """
    runtime = await deps.manager.get(game_id)
    origin = FutureOrigin()
    runtime.submit(QueuedCommand(command=command, operation_id=uuid.uuid4().hex, origin=origin))
    outcome = await origin.future
    match outcome:
        case Accepted() | Ignored():
            return runtime
        case Rejected(code=code, message=message):
            raise RejectedCommand(code, message)
        case Failed(code=code, message=message):
            raise ApiError(ApiErrorCode(code.value), 503, message)
        case _:
            assert_never(outcome)


async def _publish_lobby(deps: Deps) -> None:
    message = await deps.lobby_message("lobby.update")
    for connection in tuple(deps.hub.subscribers("lobby")):
        connection.send(message)


@router.get("")
async def list_games(deps: Deps, principal: Principal) -> list[LobbyGameSummary]:
    return [LobbyGameSummary.of(s) for s in await deps.games.list_joinable()]


@router.post("", status_code=201)
async def create_game(body: CreateGameRequest, deps: Deps, principal: Principal) -> GameSnapshot:
    try:
        loaded = deps.maps.load_with_digest(MapId(body.map_id))
    except InvalidMapError as exc:
        raise ApiError(ApiErrorCode.MAP_UNKNOWN, 404, "no such map") from exc

    if body.preset_id is None:
        preset = await deps.presets.get_default()
        if preset is None:
            raise ApiError(ApiErrorCode.NO_DEFAULT_PRESET, 409, "no default preset is configured")
    else:
        preset = await deps.presets.get(body.preset_id)
        if preset is None:
            raise ApiError(ApiErrorCode.PRESET_UNKNOWN, 404, "no such preset")

    game_id = GameId(uuid.uuid4().hex)
    host = PlayerId(principal.user_id)
    # §6.2's `tx1`. Not routed through `TransactionContext.append`: the
    # optimistic check is `UPDATE games ... WHERE last_seq = :expected`, and
    # at genesis there is no row for it to match.
    await deps.games.create(
        game_id=game_id,
        map_id=MapId(body.map_id),
        rules=preset.rules,
        host_id=host,
        map_sha256=loaded.sha256,
        preset_id=preset.preset_id,
        operation_id=f"genesis:{game_id}",
    )
    # ...and then the host joins through the runtime, like anyone else. The
    # crash window between the two is a player-less lobby, which §5.6's
    # sweep collects after five minutes and `list_joinable` hides meanwhile.
    runtime = await _submit(deps, game_id, JoinGame(host, await _display_name(deps, principal)))
    await _publish_lobby(deps)
    return _snapshot(deps, runtime, principal)


@router.get("/{game_id}")
async def get_game(game_id: str, deps: Deps, principal: Principal) -> GameSnapshot:
    """§9.3's first paint: the same projection the socket sends, so the page
    renders while the socket is still connecting."""
    if await deps.games.get_summary(GameId(game_id)) is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such game")
    runtime = await deps.manager.get(GameId(game_id))
    state = runtime.state
    if state.phase is not Phase.LOBBY and PlayerId(principal.user_id) not in state.players:
        # A live game's snapshot carries the open question. Spectating is
        # Spec 2; a lobby has no question and is readable by anyone signed
        # in, which is how a player decides whether to join it.
        raise ApiError(ApiErrorCode.FORBIDDEN, 403, "not a participant in that game")
    return _snapshot(deps, runtime, principal)


@router.post("/{game_id}/join")
async def join_game(game_id: str, deps: Deps, principal: Principal) -> GameSnapshot:
    if await deps.games.get_summary(GameId(game_id)) is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such game")
    runtime = await _submit(
        deps,
        GameId(game_id),
        JoinGame(PlayerId(principal.user_id), await _display_name(deps, principal)),
    )
    await _publish_lobby(deps)
    return _snapshot(deps, runtime, principal)


@router.post("/{game_id}/start")
async def start_game(game_id: str, deps: Deps, principal: Principal) -> GameSnapshot:
    if await deps.games.get_summary(GameId(game_id)) is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such game")
    runtime = await _submit(deps, GameId(game_id), StartGame(PlayerId(principal.user_id)))
    await _publish_lobby(deps)
    return _snapshot(deps, runtime, principal)
