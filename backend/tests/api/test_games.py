"""§6.1 and §6.2. The important test is the two-commit one."""

import httpx

from tests.api.fakes import FakeGameCatalog, FakePresets
from tests.runtime.conftest import warmup_state
from triviador.api.deps import AppDependencies
from triviador.api.errors import ApiErrorCode
from triviador.domain.game.actions import RejectCode
from triviador.domain.ids import GameId, MapId


async def create(client: httpx.AsyncClient, **body: object) -> httpx.Response:
    return await client.post("/api/games", json={"map_id": "grid", **body})


async def _force_started(deps: AppDependencies, game_id: str) -> None:
    """Replaces the resident runtime's state with a started one, through
    Plan 4's own test seam, rather than playing a game to a started phase
    through this suite's fakes."""
    runtime = await deps.manager.get(GameId(game_id))
    runtime.replace_state_for_test(warmup_state())


async def test_creating_a_game_returns_the_snapshot_with_the_host_already_seated(
    signed_in: httpx.AsyncClient,
) -> None:
    """§6.2: `tx1` writes the row and the genesis event; the host then joins
    *through the runtime*, because putting seat allocation on a second
    mutation path is what §8.2 forbids."""
    response = await create(signed_in)
    assert response.status_code == 201
    state = response.json()["state"]
    assert [p["player_id"] for p in state["players"]] == ["u1"]
    assert state["players"][0]["seat"] == 0
    assert response.json()["seq"] >= 2  # genesis at 1, PlayerJoined after it


async def test_the_created_row_records_the_maps_digest(
    signed_in: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """The pin recovery verifies before folding anything (Plan 4's loader).
    A creation path that wrote the wrong digest would make every one of its
    games unrecoverable after the first restart."""
    await create(signed_in)
    assert isinstance(deps.games, FakeGameCatalog)
    (created,) = deps.games.created
    assert created["map_sha256"] == deps.maps.load_with_digest(MapId("grid")).sha256


async def test_creating_without_a_preset_uses_the_default(
    signed_in: httpx.AsyncClient, deps: AppDependencies
) -> None:
    await create(signed_in)
    assert isinstance(deps.games, FakeGameCatalog)
    assert deps.games.created[0]["preset_id"] == "default"


async def test_an_unknown_preset_is_404(signed_in: httpx.AsyncClient) -> None:
    response = await create(signed_in, preset_id="nope")
    assert response.status_code == 404
    assert response.json()["code"] == ApiErrorCode.PRESET_UNKNOWN


async def test_no_default_preset_is_a_409_not_a_500(
    signed_in: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """§7 leaves "never zero" to application logic. When that logic has
    failed, the honest answer is a conflict naming the cause — not a
    `NoneType has no attribute rules` in a 500."""
    assert isinstance(deps.presets, FakePresets)
    deps.presets.presets.clear()
    response = await create(signed_in)
    assert response.status_code == 409
    assert response.json()["code"] == ApiErrorCode.NO_DEFAULT_PRESET


async def test_an_unknown_map_is_404(signed_in: httpx.AsyncClient) -> None:
    response = await create(signed_in, map_id="atlantis")
    assert response.status_code == 404
    assert response.json()["code"] == ApiErrorCode.MAP_UNKNOWN


async def test_creating_requires_a_session(client: httpx.AsyncClient) -> None:
    assert (await create(client)).status_code == 401


async def test_the_open_lobby_list_comes_from_the_catalog(
    signed_in: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """§6.1: "`GET /api/games` **excludes zero-player lobbies**", which the
    repository's inner JOIN already does — so this route must not
    reimplement the filter, only render what it is given."""
    await create(signed_in)
    assert isinstance(deps.games, FakeGameCatalog)
    response = await signed_in.get("/api/games")
    assert [g["game_id"] for g in response.json()] == [deps.games.created[0]["game_id"]]


async def test_a_lobby_snapshot_is_readable_by_anyone_signed_in(
    signed_in: httpx.AsyncClient, other_client: httpx.AsyncClient
) -> None:
    """Reading a lobby is how a player decides whether to join it."""
    game_id = (await create(signed_in)).json()["state"]["game_id"]
    response = await other_client.get(f"/api/games/{game_id}")
    assert response.status_code == 200
    assert response.json()["state"]["you"]["player_id"] is None


async def test_a_started_games_snapshot_is_refused_to_a_non_participant(
    signed_in: httpx.AsyncClient, other_client: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """Spectating is Spec 2. A snapshot of a live game carries the open
    question, so serving it to a stranger would be exactly the leak §8.7
    spends its whole length preventing — and the lobby exemption above is
    safe precisely because a lobby has no question."""
    game_id = (await create(signed_in)).json()["state"]["game_id"]
    await _force_started(deps, game_id)
    assert (await other_client.get(f"/api/games/{game_id}")).status_code == 403


async def test_an_unknown_game_is_404(signed_in: httpx.AsyncClient) -> None:
    assert (await signed_in.get("/api/games/nope")).status_code == 404


async def test_joining_seats_the_second_player(
    signed_in: httpx.AsyncClient, other_client: httpx.AsyncClient
) -> None:
    game_id = (await create(signed_in)).json()["state"]["game_id"]
    response = await other_client.post(f"/api/games/{game_id}/join")
    assert response.status_code == 200
    seats = {p["player_id"]: p["seat"] for p in response.json()["state"]["players"]}
    assert seats == {"u1": 0, "u2": 1}


async def test_joining_twice_is_409_carrying_the_domains_own_code(
    signed_in: httpx.AsyncClient,
) -> None:
    """§6.3: `RejectedCommand → 409 + its RejectCode`, and the code is the
    envelope's `code` rather than a nested detail."""
    game_id = (await create(signed_in)).json()["state"]["game_id"]
    response = await signed_in.post(f"/api/games/{game_id}/join")
    assert response.status_code == 409
    assert response.json()["code"] == RejectCode.ALREADY_JOINED


async def test_starting_with_too_few_players_is_409(signed_in: httpx.AsyncClient) -> None:
    game_id = (await create(signed_in)).json()["state"]["game_id"]
    response = await signed_in.post(f"/api/games/{game_id}/start")
    assert response.status_code == 409
    assert response.json()["code"] == RejectCode.NOT_ENOUGH_PLAYERS


async def test_a_stranger_cannot_start_a_game(
    signed_in: httpx.AsyncClient, stranger_client: httpx.AsyncClient
) -> None:
    """Guard 3, reached through HTTP: `StartGame` carries an actor, and an
    actor who is not an active player is rejected.

    Note what this does **not** assert. Any seated player may start, not
    only the host — see the note below the test list.
    """
    game_id = (await create(signed_in)).json()["state"]["game_id"]
    response = await stranger_client.post(f"/api/games/{game_id}/start")
    assert response.status_code == 409
    assert response.json()["code"] == RejectCode.NOT_A_PARTICIPANT


async def test_any_seated_player_may_start(
    signed_in: httpx.AsyncClient, other_client: httpx.AsyncClient
) -> None:
    """The positive half of the same decision, asserted so that adding a
    host-only rule later has to change a test that says what it is
    changing."""
    game_id = (await create(signed_in, preset_id="two-player")).json()["state"]["game_id"]
    await other_client.post(f"/api/games/{game_id}/join")
    response = await other_client.post(f"/api/games/{game_id}/start")
    assert response.status_code == 200
    assert response.json()["state"]["phase"] != "lobby"


async def test_a_recovering_game_answers_503_rather_than_409(
    signed_in: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """§6.3 keeps `RuntimeCode` and `RejectCode` on different status codes
    for a reason: a 409 tells the client "do not send that again", and a
    recovering game is precisely the case where it should."""
    from datetime import UTC, datetime

    from triviador.runtime.manager import Recovering

    game_id = (await create(signed_in)).json()["state"]["game_id"]
    deps.manager._entries[GameId(game_id)] = Recovering(
        attempt=1, next_at=datetime(2026, 8, 18, tzinfo=UTC)
    )
    response = await signed_in.post(f"/api/games/{game_id}/join")
    assert response.status_code == 503
    assert response.json()["code"] == ApiErrorCode.GAME_RECOVERING


async def test_a_lobby_subscriber_is_told_when_a_game_appears(
    signed_in: httpx.AsyncClient, deps: AppDependencies
) -> None:
    """The lobby list is a live view: a player sitting on `/` must see a
    new game without polling."""
    from tests.api.test_ws_hub import FakeSocket, a_connection, parsed

    watcher = a_connection(FakeSocket(), id="w", user_id="u2")
    deps.hub.add(watcher)
    deps.hub.subscribe(watcher, "lobby")
    await create(signed_in)
    assert [m["type"] for m in parsed(watcher)] == ["lobby.update"]
