from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum

from triviador.domain.game.state import AnswerValue
from triviador.domain.ids import DeadlineId, PlayerId, RegionId
from triviador.domain.questions.types import QuestionPool


@dataclass(frozen=True)
class JoinGame:
    actor_id: PlayerId
    display_name: str


@dataclass(frozen=True)
class StartGame:
    actor_id: PlayerId


@dataclass(frozen=True)
class SubmitAnswer:
    actor_id: PlayerId
    deadline_id: DeadlineId
    value: AnswerValue
    elapsed_ms: int


@dataclass(frozen=True)
class PickRegion:
    actor_id: PlayerId
    deadline_id: DeadlineId
    region_id: RegionId


@dataclass(frozen=True)
class SelectAttackTarget:
    actor_id: PlayerId
    deadline_id: DeadlineId
    region_id: RegionId


@dataclass(frozen=True)
class ExpireDeadline:
    deadline_id: DeadlineId


@dataclass(frozen=True)
class Surrender:
    actor_id: PlayerId


@dataclass(frozen=True)
class AbortGame:
    """`actor_id is None` means a system-issued abort.

    Guard 3 validates the actor only when one is present, so a system abort is
    legal even in a lobby with no participants — which is exactly the reaper's
    case (an abandoned, empty lobby has no actor that could pass guard 3).
    """

    actor_id: PlayerId | None = None


Command = (
    JoinGame
    | StartGame
    | SubmitAnswer
    | PickRegion
    | SelectAttackTarget
    | ExpireDeadline
    | Surrender
    | AbortGame
)

WINDOWED_COMMANDS = (SubmitAnswer, PickRegion, SelectAttackTarget, ExpireDeadline)


class RejectCode(StrEnum):
    NOT_A_PARTICIPANT = "not_a_participant"
    WRONG_TURN_STATE = "wrong_turn_state"
    NOT_YOUR_TURN = "not_your_turn"
    ALREADY_ANSWERED = "already_answered"
    ALREADY_JOINED = "already_joined"
    GAME_FULL = "game_full"
    NOT_ENOUGH_PLAYERS = "not_enough_players"
    QUESTION_POOL_INSUFFICIENT = "question_pool_insufficient"
    UNKNOWN_REGION = "unknown_region"
    REGION_NOT_FREE = "region_not_free"
    OWN_TERRITORY = "own_territory"
    NOT_ADJACENT = "not_adjacent"
    ANSWER_KIND_MISMATCH = "answer_kind_mismatch"


class RejectedCommand(Exception):
    """A command the client should not have sent. Nothing is persisted or broadcast."""

    def __init__(self, code: RejectCode, message: str) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message


@dataclass(frozen=True)
class DecisionContext:
    """Materialised values, never capabilities.

    The runtime resolves every non-deterministic input before enqueueing, so
    `decide` stays a mathematical function and replay never diverges.
    """

    now: datetime
    shuffled_player_ids: tuple[PlayerId, ...] | None = None
    base_regions: tuple[RegionId, ...] | None = None
    shuffled_region_ids: tuple[RegionId, ...] | None = None
    drawn_pool: QuestionPool | None = None
