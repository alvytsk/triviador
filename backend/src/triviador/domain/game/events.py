from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum

from triviador.domain.game.rules import GameRules
from triviador.domain.game.state import AcquisitionKind, Deadline, SubmittedAnswer
from triviador.domain.ids import MapId, PlayerId, RegionId
from triviador.domain.questions.types import QuestionPool, QuestionSnapshot


class ScoreReason(StrEnum):
    BASE = "base"
    TERRITORY = "territory"
    CONQUEST = "conquest"
    DEFENSE = "defense"
    TERRITORY_LOST = "territory_lost"
    BASE_LOST = "base_lost"
    BONUS = "bonus"


# --- lifecycle -------------------------------------------------------------


@dataclass(frozen=True)
class GameCreated:
    map_id: MapId
    rules: GameRules
    host_id: PlayerId


@dataclass(frozen=True)
class PlayerJoined:
    player_id: PlayerId
    display_name: str
    seat: int


@dataclass(frozen=True)
class PlayerLeft:
    player_id: PlayerId


@dataclass(frozen=True)
class GameStarted:
    turn_order: tuple[PlayerId, ...]


@dataclass(frozen=True)
class BasesAssigned:
    assignments: Mapping[PlayerId, RegionId]


@dataclass(frozen=True)
class QuestionPoolDrawn:
    pool: QuestionPool


@dataclass(frozen=True)
class GameFinished:
    winner_id: PlayerId | None
    final_scores: Mapping[PlayerId, int]


@dataclass(frozen=True)
class GameAborted:
    reason: str


# --- questions -------------------------------------------------------------


@dataclass(frozen=True)
class QuestionPresented:
    question: QuestionSnapshot
    deadline: Deadline


@dataclass(frozen=True)
class AnswerSubmitted:
    player_id: PlayerId
    answer: SubmittedAnswer


@dataclass(frozen=True)
class AnswerWindowClosed:
    deadline: Deadline


@dataclass(frozen=True)
class QuestionResolved:
    correct_choice_index: int | None
    correct_value: Decimal | None
    ranking: tuple[PlayerId, ...]
    correct_players: tuple[PlayerId, ...]


# --- expansion -------------------------------------------------------------


@dataclass(frozen=True)
class ExpansionRoundStarted:
    round_no: int


@dataclass(frozen=True)
class PicksGranted:
    pick_order: tuple[PlayerId, ...]
    grants: Mapping[PlayerId, int]
    deadline: Deadline


@dataclass(frozen=True)
class TerritoryClaimed:
    player_id: PlayerId
    region_id: RegionId
    acquisition: AcquisitionKind
    automatic: bool


@dataclass(frozen=True)
class ExpansionRoundCompleted:
    round_no: int


# --- battle ----------------------------------------------------------------


@dataclass(frozen=True)
class BattleRoundStarted:
    round_no: int


@dataclass(frozen=True)
class TurnStarted:
    attacker_id: PlayerId
    deadline: Deadline


@dataclass(frozen=True)
class TurnSkipped:
    attacker_id: PlayerId
    reason: str


@dataclass(frozen=True)
class TurnAborted:
    reason: str


@dataclass(frozen=True)
class AttackDeclared:
    attacker_id: PlayerId
    defender_id: PlayerId | None
    region_id: RegionId


@dataclass(frozen=True)
class DuelResolved:
    winner_id: PlayerId | None


@dataclass(frozen=True)
class TiebreakStarted:
    region_id: RegionId


@dataclass(frozen=True)
class TerritoryCaptured:
    region_id: RegionId
    from_player_id: PlayerId | None
    to_player_id: PlayerId
    acquisition: AcquisitionKind


@dataclass(frozen=True)
class NeutralTerritoryCaptured:
    region_id: RegionId
    player_id: PlayerId


@dataclass(frozen=True)
class NeutralAttackFailed:
    region_id: RegionId
    attacker_id: PlayerId


@dataclass(frozen=True)
class DefenseHeld:
    region_id: RegionId
    defender_id: PlayerId


@dataclass(frozen=True)
class BaseDamaged:
    region_id: RegionId
    hp_remaining: int


@dataclass(frozen=True)
class BaseDestroyed:
    region_id: RegionId
    owner_id: PlayerId


@dataclass(frozen=True)
class BattleRoundCompleted:
    round_no: int


# --- scoring and terminal --------------------------------------------------


@dataclass(frozen=True)
class ScoreChanged:
    player_id: PlayerId
    delta: int
    reason: ScoreReason
    new_total: int


@dataclass(frozen=True)
class PlayerEliminated:
    player_id: PlayerId


@dataclass(frozen=True)
class PlayerSurrendered:
    player_id: PlayerId


@dataclass(frozen=True)
class TerritoryNeutralized:
    region_id: RegionId
    former_owner_id: PlayerId


@dataclass(frozen=True)
class FinalTiebreakStarted:
    contenders: tuple[PlayerId, ...]


GameEvent = (
    GameCreated
    | PlayerJoined
    | PlayerLeft
    | GameStarted
    | BasesAssigned
    | QuestionPoolDrawn
    | GameFinished
    | GameAborted
    | QuestionPresented
    | AnswerSubmitted
    | AnswerWindowClosed
    | QuestionResolved
    | ExpansionRoundStarted
    | PicksGranted
    | TerritoryClaimed
    | ExpansionRoundCompleted
    | BattleRoundStarted
    | TurnStarted
    | TurnSkipped
    | TurnAborted
    | AttackDeclared
    | DuelResolved
    | TiebreakStarted
    | TerritoryCaptured
    | NeutralTerritoryCaptured
    | NeutralAttackFailed
    | DefenseHeld
    | BaseDamaged
    | BaseDestroyed
    | BattleRoundCompleted
    | ScoreChanged
    | PlayerEliminated
    | PlayerSurrendered
    | TerritoryNeutralized
    | FinalTiebreakStarted
)
