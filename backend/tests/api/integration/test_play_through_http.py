"""Create → join → start → FINISHED, over HTTP and one real socket each.

Not a suite: one scenario proving the seams line up. Everything it asserts
has been asserted in isolation somewhere above; what is new here is that
the composition root, PostgreSQL, the runtime, the hub and the projection
are all the real ones.
"""

import itertools
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

pytestmark = pytest.mark.integration

# Every frame in and out of the socket is a JSON object; `Any` for the
# values is unavoidable here — these are wire payloads read back with
# `.json()`/`json.loads`, not typed models.
JSON = dict[str, Any]

# The four turn kinds that present a question and expect an answer from
# every player. `expansion_picking` and `battle_target_select` are the
# other two live turn kinds and are handled separately below — they take
# a region pick, not an answer.
QUESTION_TURN_KINDS = frozenset(
    {"expansion_question", "battle_duel", "neutral_challenge", "final_tiebreak"}
)

_command_ids = itertools.count()


def _command_id() -> str:
    return f"cmd-{next(_command_ids)}"


def sign_in(client: TestClient, username: str) -> JSON:
    response = client.post(
        "/api/auth/login", json={"username": username, "password": "correct horse"}
    )
    assert response.status_code == 200, response.text
    result: JSON = response.json()
    return result


def until(socket: WebSocketTestSession, *types: str, limit: int = 40) -> list[JSON]:
    """Read frames until one of `types` arrives, returning everything read.

    Bounded and raising rather than looping: a wedged server must fail the
    test that provoked it, not hang CI with no indication of where.
    """
    seen: list[JSON] = []
    for _ in range(limit):
        message = json.loads(socket.receive_text())
        seen.append(message)
        if message["type"] in types:
            return seen
    raise AssertionError(f"never saw {types}; saw {[m['type'] for m in seen]}")


def answer(socket: WebSocketTestSession, game_id: str, turn: JSON, command_id: str) -> None:
    """Answer whatever kind of question is open.

    Switching on `turn["question"]["kind"]` is not defensive coding — it is
    required. `FAST_RULES` needs two multiple-choice questions
    (`required_question_budget` gives `multiple_choice = battle_rounds *
    player_count`), every battle duel presents one, and a numeric payload
    sent to an MC window is rejected with `ANSWER_KIND_MISMATCH`. The
    window would then close on its own 3 s deadline instead of on a
    player's answer — both slower and a different scenario from the one
    this test claims to run.
    """
    if turn["question"]["kind"] == "multiple_choice":
        payload: dict[str, object] = {"kind": "choice", "idx": 0}
    else:
        payload = {"kind": "numeric", "value": "1"}
    socket.send_json(
        {
            "type": "submit_answer",
            "command_id": command_id,
            "game_id": game_id,
            "deadline_id": turn["deadline_id"],
            "payload": payload,
        }
    )


def _pick_region(socket: WebSocketTestSession, game_id: str, turn: JSON, region_id: str) -> None:
    socket.send_json(
        {
            "type": "pick_region",
            "command_id": _command_id(),
            "game_id": game_id,
            "deadline_id": turn["deadline_id"],
            "payload": {"region_id": region_id},
        }
    )


def _select_attack_target(
    socket: WebSocketTestSession, game_id: str, turn: JSON, region_id: str
) -> None:
    socket.send_json(
        {
            "type": "select_attack_target",
            "command_id": _command_id(),
            "game_id": game_id,
            "deadline_id": turn["deadline_id"],
            "payload": {"region_id": region_id},
        }
    )


def _play_to_finish(
    client: TestClient,
    game_id: str,
    alice_ws: WebSocketTestSession,
    bob_ws: WebSocketTestSession,
    *,
    limit: int = 300,
) -> None:
    """Drive the game generically rather than scripting a fixed sequence —
    base placement and pick order are randomised. Loop reading a matched
    pair of `game.update`s (one per socket) and act on `state.turn`,
    dispatching on `turn["kind"]`.

    **Read from both sockets.** Each receives its own projection and
    `your_options` (and `your_answer`) is populated only on the socket
    belonging to the player whose move it is. A loop reading only Alice's
    stream never sees which socket is the one that must act when Bob is
    the picker or the attacker.

    **Both players answer every question window**, checked via each
    socket's own `your_answer` — `None` means that viewer has not answered
    yet. A window resolves when everyone has answered or when it expires,
    and the whole claim of this scenario is that none expires.

    **Both question kinds must appear** — tracked into `kinds_seen`, which
    the caller asserts against once the match reaches FINISHED.

    Bounded and raising, like `until`: a wedged server must fail the test
    that provoked it, not hang CI with no indication of where.
    """
    kinds_seen: set[str] = set()

    # Alice's caller already consumed her own `media_warmup` `game.update`;
    # Bob has not read his yet — both sockets receive their own copy of
    # every broadcast, so bring Bob's stream even with Alice's before
    # driving the rest of the match from both, in lockstep, from here.
    bob_update = until(bob_ws, "game.update")[-1]
    assert bob_update["state"]["turn"]["kind"] == "media_warmup", bob_update["state"]["turn"]
    last_seq = bob_update["seq"]
    resolution_checked = False

    for _ in range(limit):
        alice_update = until(alice_ws, "game.update")[-1]
        bob_update = until(bob_ws, "game.update")[-1]

        # §8.4's batch sequencing holds for every update either socket
        # applies, and both sockets are watching the same game, so the
        # same committed batch reaches both.
        for who, update in (("alice", alice_update), ("bob", bob_update)):
            seen_turn = update["state"]["turn"]
            assert update["base_seq"] == last_seq, (
                f"{who}: base_seq {update['base_seq']} != last applied seq {last_seq} "
                f"(turn kind {seen_turn['kind'] if seen_turn else None})"
            )
            assert update["seq"] > update["base_seq"]
        assert alice_update["seq"] == bob_update["seq"], "both sockets watch the same game"
        last_seq = alice_update["seq"]

        # §8.7: before resolution, neither socket has been told the
        # answer — `correct_value` never appears anywhere in a live turn.
        assert "correct_value" not in json.dumps(alice_update["state"]["turn"])
        assert "correct_value" not in json.dumps(bob_update["state"]["turn"])

        if not resolution_checked:
            resolved = next(
                (e for e in alice_update["events"] if e["type"] == "question_resolved"), None
            )
            if resolved is not None:
                # ...and after it, both have.
                assert resolved["correct_value"] is not None
                resolution_checked = True

        state = alice_update["state"]
        if state["phase"] == "finished":
            assert kinds_seen == {"numeric", "multiple_choice"}, kinds_seen
            assert state["winner_id"] is not None
            return

        turn = state["turn"]
        assert turn is not None
        kind = turn["kind"]

        if kind == "media_warmup":
            continue  # the only real wait in the run; nothing to send

        if kind in QUESTION_TURN_KINDS:
            kinds_seen.add(turn["question"]["kind"])
            if alice_update["state"]["turn"]["your_answer"] is None:
                answer(alice_ws, game_id, turn, _command_id())
            if bob_update["state"]["turn"]["your_answer"] is None:
                answer(bob_ws, game_id, turn, _command_id())
        elif kind == "expansion_picking":
            for update, socket in ((alice_update, alice_ws), (bob_update, bob_ws)):
                pick = update["state"]["turn"]["your_options"]["pick"]
                if pick:
                    _pick_region(socket, game_id, turn, pick[0])
                    break
            else:
                raise AssertionError(f"expansion_picking turn with no picker options: {turn}")
        elif kind == "battle_target_select":
            for update, socket in ((alice_update, alice_ws), (bob_update, bob_ws)):
                attack = update["state"]["turn"]["your_options"]["attack"]
                if attack:
                    _select_attack_target(socket, game_id, turn, attack[0])
                    break
            else:
                raise AssertionError(f"battle_target_select turn with no attacker options: {turn}")
        else:
            raise AssertionError(f"unexpected turn kind: {kind}")

    raise AssertionError(
        f"game never reached FINISHED after {limit} updates; kinds_seen={kinds_seen}"
    )


def test_two_players_play_a_whole_game(client: TestClient) -> None:
    alice = sign_in(client, "alice")
    alice_cookies = dict(client.cookies)

    created = client.post("/api/games", json={"map_id": "grid", "preset_id": "fast"})
    assert created.status_code == 201, created.text
    game_id = created.json()["state"]["game_id"]
    assert [p["player_id"] for p in created.json()["state"]["players"]] == [alice["user_id"]]

    # Bob signs in on the same client, which replaces the cookie.
    sign_in(client, "bob")
    bob_cookies = dict(client.cookies)
    joined = client.post(f"/api/games/{game_id}/join")
    assert joined.status_code == 200, joined.text
    assert len(joined.json()["state"]["players"]) == 2

    with client.websocket_connect("/ws", cookies=bob_cookies) as bob_ws:
        client.cookies.update(alice_cookies)
        with client.websocket_connect("/ws", cookies=alice_cookies) as alice_ws:
            for socket in (alice_ws, bob_ws):
                assert json.loads(socket.receive_text())["type"] == "hello"
                socket.send_json({"type": "subscribe", "topic": f"game:{game_id}"})
                until(socket, "game.snapshot")

            started = client.post(f"/api/games/{game_id}/start")
            assert started.status_code == 200, started.text

            # §9.6: the warmup window opens before any question, and the
            # snapshot already carries every image to prefetch.
            warmup = until(alice_ws, "game.update")[-1]
            assert warmup["state"]["turn"]["kind"] == "media_warmup"

            _play_to_finish(client, game_id, alice_ws, bob_ws)

    final = client.get(f"/api/games/{game_id}")
    assert final.json()["state"]["phase"] == "finished"
    assert final.json()["state"]["winner_id"] is not None
