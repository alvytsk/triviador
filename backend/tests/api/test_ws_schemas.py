"""§6.5: frames are strict, and they carry no actor.

Two separate properties, both asserted. The field is *unacceptable* — a
frame carrying `actor_id` is rejected before anything reads it — and
identity comes from the principal, which Task 16 asserts at the endpoint.
An earlier formulation of this ("a frame naming another player still acts
as the session's user") contradicted strict validation and is not what
either test says.
"""

import json
from typing import get_args

import pytest
from pydantic import ValidationError

from triviador.api.schemas.ws import (
    CLIENT_MESSAGE_ADAPTER,
    ClientMessage,
    NumericAnswerPayload,
    ServerMessage,
    SubmitAnswerFrame,
    game_topic,
)

WINDOWED = {"submit_answer", "pick_region", "select_attack_target"}


def frame(**kw: object) -> str:
    return json.dumps(kw)


def parse(**kw: object) -> ClientMessage:
    return CLIENT_MESSAGE_ADAPTER.validate_json(frame(**kw))


def test_a_valid_answer_frame_parses() -> None:
    message = parse(
        type="submit_answer",
        command_id="c1",
        game_id="g1",
        deadline_id=7,
        payload={"kind": "numeric", "value": "42.5"},
    )
    assert isinstance(message, SubmitAnswerFrame)
    assert isinstance(message.payload, NumericAnswerPayload)
    assert message.payload.value == "42.5"


def test_a_numeric_answer_arrives_as_a_string() -> None:
    """JSON has one number type and it is a float: `0.1` does not survive
    the trip as a `Decimal`. Every number this API compares for equality is
    a decimal string."""
    with pytest.raises(ValidationError):
        parse(
            type="submit_answer",
            command_id="c1",
            game_id="g1",
            deadline_id=7,
            payload={"kind": "numeric", "value": 42.5},
        )


@pytest.mark.parametrize(
    "value",
    ["forty-two", "NaN", "nan", "-NaN", "sNaN", "Infinity", "-Infinity", "inf", "-inf"],
)
def test_a_numeric_value_that_is_not_a_finite_number_is_rejected(value: str) -> None:
    """`Decimal("NaN")` parses. It then reaches `_rank_numeric`, where an
    ordering comparison against it raises `InvalidOperation` *inside*
    `decide` — which §5.5 treats as a fault and quarantines the game for.
    One frame, one dead game, from an authenticated player who only had to
    type four characters."""
    with pytest.raises(ValidationError):
        parse(
            type="submit_answer",
            command_id="c1",
            game_id="g1",
            deadline_id=7,
            payload={"kind": "numeric", "value": value},
        )


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "ping", "unexpected": "x"},
        {"type": "subscribe", "topic": "lobby", "unexpected": "x"},
        {"type": "surrender", "command_id": "c1", "game_id": "g1", "unexpected": "x"},
        # Nested: the payload models (`NumericAnswerPayload`, `RegionPayload`)
        # are separate classes from the frame that carries them — each has to
        # inherit `extra="forbid"` on its own for this to hold.
        {
            "type": "submit_answer",
            "command_id": "c1",
            "game_id": "g1",
            "deadline_id": 7,
            "payload": {"kind": "numeric", "value": "1", "unexpected": "x"},
        },
        {
            "type": "pick_region",
            "command_id": "c1",
            "game_id": "g1",
            "deadline_id": 7,
            "payload": {"region_id": "r3", "unexpected": "x"},
        },
    ],
)
def test_an_extra_field_on_any_frame_is_rejected(bad: dict[str, object]) -> None:
    """`extra="forbid"` everywhere, including nested payload models — not
    only the outer frame. Omitting `actor_id` from a schema is worth
    nothing if unknown keys are silently ignored anywhere in the frame
    (§6.5)."""
    with pytest.raises(ValidationError):
        parse(**bad)


@pytest.mark.parametrize(
    "bad",
    [
        {"type": "surrender", "command_id": "c1", "game_id": "g1", "actor_id": "somebody-else"},
        {
            "type": "submit_answer",
            "command_id": "c1",
            "game_id": "g1",
            "deadline_id": 7,
            "payload": {"kind": "choice", "idx": 1},
            "actor_id": "somebody-else",
        },
        # Nested: an actor named one level deeper, inside the payload
        # rather than the frame, must be refused just as outright.
        {
            "type": "submit_answer",
            "command_id": "c1",
            "game_id": "g1",
            "deadline_id": 7,
            "payload": {"kind": "choice", "idx": 1, "actor_id": "somebody-else"},
        },
        {
            "type": "pick_region",
            "command_id": "c1",
            "game_id": "g1",
            "deadline_id": 7,
            "payload": {"region_id": "r3", "actor_id": "somebody-else"},
        },
    ],
)
def test_a_frame_carrying_an_actor_is_rejected_outright(bad: dict[str, object]) -> None:
    """The one that matters. Identity is derived from the session, and a
    frame that even mentions an actor is refused rather than sanitized —
    whether the actor is named at the top level or inside a nested
    payload."""
    with pytest.raises(ValidationError):
        parse(**bad)


def test_only_the_windowed_commands_declare_a_deadline() -> None:
    """§6.5, checked against the models rather than by inspection: a
    `deadline_id` on surrender would be a window identity for a command
    that has no window, and the guard pipeline would have to decide what to
    do with it."""
    for model in get_args(get_args(ClientMessage)[0]):
        kind = model.model_fields["type"].default
        has_deadline = "deadline_id" in model.model_fields
        assert has_deadline == (kind in WINDOWED), kind


def test_expire_deadline_is_not_a_client_frame() -> None:
    """§6.5: server-internal. A client that could expire its own window
    could end an opponent's answer time."""
    kinds = {m.model_fields["type"].default for m in get_args(get_args(ClientMessage)[0])}
    assert "expire_deadline" not in kinds
    assert "abort_game" not in kinds
    assert "join_game" not in kinds  # REST, per §8.2
    assert "start_game" not in kinds


def test_a_missing_deadline_on_a_windowed_command_is_rejected() -> None:
    with pytest.raises(ValidationError):
        parse(type="pick_region", command_id="c1", game_id="g1", payload={"region_id": "r3"})


@pytest.mark.parametrize(
    "topic", ["lobby", "game:0123abcd", "admin:games", "game:", "game:../x", ""]
)
def test_only_the_two_spec_1_topics_are_accepted(topic: str) -> None:
    """`admin:*` is Spec 2 (§8.1). Accepting it now would mean a topic with
    no authorization rule behind it."""
    ok = topic in {"lobby", "game:0123abcd"}
    try:
        parse(type="subscribe", topic=topic)
    except ValidationError:
        assert not ok
    else:
        assert ok


def test_a_game_topic_is_built_the_one_way() -> None:
    assert game_topic("g1") == "game:g1"


def test_no_server_message_shares_a_base_class_with_a_domain_type() -> None:
    for model in get_args(get_args(ServerMessage)[0]):
        assert not any(b.__module__.startswith("triviador.domain") for b in model.__mro__[1:])
