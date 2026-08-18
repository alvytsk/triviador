"""The socket envelope, both directions.

One flat discriminated union per direction. §6.5 enumerates "surrender,
subscribe, unsubscribe, or ping" in one breath when saying where
`deadline_id` may not appear, which only parses if transport frames and
commands share a `type` discriminator — so they do.
"""

from datetime import datetime
from decimal import Decimal, InvalidOperation
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, field_validator

from triviador.api.errors import ApiErrorCode
from triviador.api.schemas.events import ClientEvent
from triviador.api.schemas.games import ClientGameState
from triviador.domain.game.actions import RejectCode

LOBBY_TOPIC = "lobby"
# `admin:*` is Spec 2 (§8.1) and is deliberately unmatched: a topic the
# parser accepts is a topic something has to authorize.
TOPIC_PATTERN = r"^(lobby|game:[A-Za-z0-9_-]{1,64})$"

Topic = Annotated[str, Field(pattern=TOPIC_PATTERN)]
CommandId = Annotated[str, Field(min_length=1, max_length=64)]
GameIdField = Annotated[str, Field(min_length=1, max_length=64)]


def game_topic(game_id: str) -> str:
    return f"game:{game_id}"


class _Frame(BaseModel):
    """Every client frame. `extra="forbid"` is the property §6.5 requires,
    and it is what makes `actor_id` unacceptable rather than ignored."""

    model_config = ConfigDict(extra="forbid")


class SubscribeFrame(_Frame):
    type: Literal["subscribe"] = "subscribe"
    topic: Topic


class UnsubscribeFrame(_Frame):
    type: Literal["unsubscribe"] = "unsubscribe"
    topic: Topic


class ResyncFrame(_Frame):
    """§8.5: the client asks for a fresh snapshot rather than catching up on
    events. A whole game state is a couple of kilobytes."""

    type: Literal["resync"] = "resync"
    topic: Topic


class PingFrame(_Frame):
    type: Literal["ping"] = "ping"


class ChoiceAnswerPayload(_Frame):
    kind: Literal["choice"] = "choice"
    idx: int = Field(ge=0, le=15)


class NumericAnswerPayload(_Frame):
    kind: Literal["numeric"] = "numeric"
    value: str = Field(min_length=1, max_length=40)

    @field_validator("value")
    @classmethod
    def _decimal(cls, value: str) -> str:
        """Finite, not merely parseable.

        `Decimal("NaN")` and `Decimal("Infinity")` both construct without
        raising, and they do not stay harmless: `_rank_numeric` sorts on
        `(wrong?, abs(value - correct), elapsed_ms, seat)`, and an ordering
        comparison against a `Decimal` NaN raises `InvalidOperation` — from
        inside `decide`, which §5.5 classifies as a fault and quarantines
        the game for. One client frame would take a healthy game down.
        Infinity does not raise, but it sorts last-but-one forever and is
        not an answer to any question.
        """
        try:
            parsed = Decimal(value)
        except InvalidOperation as exc:
            raise ValueError("not a decimal number") from exc
        if not parsed.is_finite():
            raise ValueError("must be a finite number")
        return value


AnswerPayload = Annotated[ChoiceAnswerPayload | NumericAnswerPayload, Field(discriminator="kind")]


class RegionPayload(_Frame):
    region_id: str = Field(min_length=1, max_length=64)


class _Command(_Frame):
    command_id: CommandId
    game_id: GameIdField


class SubmitAnswerFrame(_Command):
    type: Literal["submit_answer"] = "submit_answer"
    deadline_id: int
    payload: AnswerPayload


class PickRegionFrame(_Command):
    type: Literal["pick_region"] = "pick_region"
    deadline_id: int
    payload: RegionPayload


class SelectTargetFrame(_Command):
    type: Literal["select_attack_target"] = "select_attack_target"
    deadline_id: int
    payload: RegionPayload


class SurrenderFrame(_Command):
    """No `deadline_id` and no `payload`: surrender is not windowed and
    carries nothing. An empty `payload: {}` would be a field whose only
    possible value is one the server ignores."""

    type: Literal["surrender"] = "surrender"


ClientMessage = Annotated[
    SubscribeFrame
    | UnsubscribeFrame
    | ResyncFrame
    | PingFrame
    | SubmitAnswerFrame
    | PickRegionFrame
    | SelectTargetFrame
    | SurrenderFrame,
    Field(discriminator="type"),
]

CLIENT_MESSAGE_ADAPTER: TypeAdapter[ClientMessage] = TypeAdapter(ClientMessage)


# --- server → client --------------------------------------------------------


class _Message(BaseModel):
    model_config = ConfigDict(extra="forbid")


class HelloMessage(_Message):
    """§8.6: the client refines its clock offset from ping/pong, not from
    this — `server_time` alone embeds one-way network delay."""

    type: Literal["hello"] = "hello"
    server_time: datetime


class PongMessage(_Message):
    type: Literal["pong"] = "pong"
    server_time: datetime


class LobbyGame(_Message):
    game_id: str
    map_id: str
    host_id: str
    status: str
    player_count: int
    max_players: int


class LobbyMessage(_Message):
    type: Literal["lobby.snapshot", "lobby.update"]
    games: tuple[LobbyGame, ...]


class SnapshotMessage(_Message):
    type: Literal["game.snapshot"] = "game.snapshot"
    game_id: str
    seq: int
    state: ClientGameState


class UpdateMessage(_Message):
    """§8.4: the transport unit is the whole committed batch, so the client
    can sequence on `base_seq`/`seq` even though projection drops events."""

    type: Literal["game.update"] = "game.update"
    game_id: str
    base_seq: int
    seq: int
    state: ClientGameState
    events: tuple[ClientEvent, ...]


class PresenceMessage(_Message):
    """§8.3: deliberately not a domain event — no `seq`, not persisted,
    absent from replay."""

    type: Literal["game.presence"] = "game.presence"
    game_id: str
    connected: tuple[str, ...]


class ErrorMessage(_Message):
    """`command_id` is transport correlation only (§8.3): with several
    actions pending the client cannot otherwise tell which one a
    `REGION_NOT_FREE` belongs to. It is never used to retry."""

    type: Literal["error"] = "error"
    command_id: str | None
    code: ApiErrorCode | RejectCode
    message: str


ServerMessage = Annotated[
    HelloMessage
    | PongMessage
    | LobbyMessage
    | SnapshotMessage
    | UpdateMessage
    | PresenceMessage
    | ErrorMessage,
    Field(discriminator="type"),
]
