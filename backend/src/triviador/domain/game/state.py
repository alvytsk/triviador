from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import TYPE_CHECKING

from triviador.domain.game.rules import GameRules
from triviador.domain.ids import DeadlineId, GameId, PlayerId, RegionId
from triviador.domain.maps.definition import MapDefinition
from triviador.domain.questions.types import QuestionPool, QuestionSnapshot

if TYPE_CHECKING:
    # `events.py` imports from this module, so importing it back here at
    # runtime would be circular. `GameState.pending_attack` only needs the
    # name for static typing, resolved via the quoted annotation below.
    from triviador.domain.game.events import AttackDeclared


class Phase(StrEnum):
    LOBBY = "lobby"
    EXPANSION = "expansion"
    BATTLE = "battle"
    FINISHED = "finished"
    ABORTED = "aborted"


TERMINAL_PHASES = frozenset({Phase.FINISHED, Phase.ABORTED})


class TerritoryKind(StrEnum):
    NORMAL = "normal"
    BASE = "base"


class AcquisitionKind(StrEnum):
    CLAIMED = "claimed"  # taken while unowned: expansion pick or neutral challenge
    CONQUEST = "conquest"  # taken from another player
    BASE = "base"


class DeadlineKind(StrEnum):
    ANSWER = "answer"
    PICK = "pick"
    TARGET_SELECT = "target_select"


@dataclass(frozen=True)
class Deadline:
    id: DeadlineId
    kind: DeadlineKind
    deadline_at: datetime


@dataclass(frozen=True)
class Territory:
    region_id: RegionId
    owner_id: PlayerId | None
    kind: TerritoryKind
    base_owner_id: PlayerId | None
    base_hp: int | None
    acquisition: AcquisitionKind | None


@dataclass(frozen=True)
class PlayerState:
    player_id: PlayerId
    display_name: str
    seat: int
    score: int
    bonus_score: int
    base_region: RegionId | None
    is_eliminated: bool


@dataclass(frozen=True)
class ChoiceAnswer:
    idx: int


@dataclass(frozen=True)
class NumericAnswer:
    value: Decimal


AnswerValue = ChoiceAnswer | NumericAnswer


@dataclass(frozen=True)
class SubmittedAnswer:
    value: AnswerValue
    elapsed_ms: int


@dataclass(frozen=True)
class ExpansionQuestion:
    deadline: Deadline
    question: QuestionSnapshot
    answers: Mapping[PlayerId, SubmittedAnswer]


@dataclass(frozen=True)
class ExpansionPicking:
    deadline: Deadline
    pick_order: tuple[PlayerId, ...]
    grants_remaining: Mapping[PlayerId, int]
    current_picker: PlayerId


@dataclass(frozen=True)
class BattleTargetSelect:
    deadline: Deadline
    attacker_id: PlayerId


@dataclass(frozen=True)
class BattleDuel:
    deadline: Deadline
    attacker_id: PlayerId
    defender_id: PlayerId
    region_id: RegionId
    question: QuestionSnapshot
    answers: Mapping[PlayerId, SubmittedAnswer]


@dataclass(frozen=True)
class BattleTiebreak:
    deadline: Deadline
    attacker_id: PlayerId
    defender_id: PlayerId
    region_id: RegionId
    question: QuestionSnapshot
    answers: Mapping[PlayerId, SubmittedAnswer]


@dataclass(frozen=True)
class NeutralChallenge:
    deadline: Deadline
    attacker_id: PlayerId
    region_id: RegionId
    question: QuestionSnapshot
    answers: Mapping[PlayerId, SubmittedAnswer]


@dataclass(frozen=True)
class FinalTiebreak:
    deadline: Deadline
    contenders: tuple[PlayerId, ...]
    question: QuestionSnapshot
    answers: Mapping[PlayerId, SubmittedAnswer]


Turn = (
    ExpansionQuestion
    | ExpansionPicking
    | BattleTargetSelect
    | BattleDuel
    | BattleTiebreak
    | NeutralChallenge
    | FinalTiebreak
)


@dataclass(frozen=True)
class GameState:
    game_id: GameId
    seq: int
    next_deadline_id: int
    map: MapDefinition
    rules: GameRules
    phase: Phase
    round_no: int
    turn_order: tuple[PlayerId, ...]
    players: Mapping[PlayerId, PlayerState]
    territories: Mapping[RegionId, Territory]
    turn: Turn | None
    pool: QuestionPool
    winner_id: PlayerId | None
    # Bridges `AttackDeclared` to the `QuestionPresented` that follows it: `evolve`
    # sees them as two separate events and needs somewhere to carry the declared
    # attack in between, since it builds the BattleDuel/NeutralChallenge turn.
    pending_attack: "AttackDeclared | None" = None
    # The rotation anchor for `_next_battle_turn`: the attacker who was most
    # recently started or skipped. Set by both the `TurnStarted` and
    # `TurnSkipped` `_apply` branches so the anchor advances on every skip,
    # never just on a completed turn — that's what lets turn rotation find
    # the next active player in `turn_order` without recursing.
    last_attacker_id: PlayerId | None = None
    # Bridges `FinalTiebreakStarted` to the `QuestionPresented` that follows
    # it, mirroring `pending_attack` for the final-tiebreak case.
    pending_final_contenders: tuple[PlayerId, ...] = ()

    def active_players(self) -> tuple[PlayerId, ...]:
        return tuple(p for p in self.turn_order if not self.players[p].is_eliminated)

    def current_deadline(self) -> Deadline | None:
        return None if self.turn is None else self.turn.deadline

    def free_regions(self) -> tuple[RegionId, ...]:
        return tuple(r for r in self.map.region_ids() if self.territories[r].owner_id is None)

    def owned_by(self, player_id: PlayerId) -> tuple[RegionId, ...]:
        return tuple(r for r in self.map.region_ids() if self.territories[r].owner_id == player_id)

    def allocate_deadline(
        self, kind: DeadlineKind, deadline_at: datetime
    ) -> tuple[Deadline, "GameState"]:
        deadline = Deadline(DeadlineId(self.next_deadline_id), kind, deadline_at)
        return deadline, replace(self, next_deadline_id=self.next_deadline_id + 1)
