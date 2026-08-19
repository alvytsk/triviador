"""What the client is *told happened*, as distinct from what is true.

Spec 1 §9.1: state is transported, events narrate. So these carry only
what an animation or a log line needs — never a field the snapshot already
holds authoritatively, and never a field §8.7 withholds.
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from triviador.api.schemas.games import SubmittedValue
from triviador.domain.game.events import ScoreReason
from triviador.domain.game.state import AcquisitionKind


class _Event(BaseModel):
    model_config = ConfigDict(extra="forbid")


class PlayerJoinedEvent(_Event):
    type: Literal["player_joined"] = "player_joined"
    player_id: str
    display_name: str
    seat: int


class PlayerLeftEvent(_Event):
    type: Literal["player_left"] = "player_left"
    player_id: str


class GameStartedEvent(_Event):
    type: Literal["game_started"] = "game_started"
    turn_order: tuple[str, ...]


class BasesAssignedEvent(_Event):
    type: Literal["bases_assigned"] = "bases_assigned"
    assignments: dict[str, str]


class WarmupStartedEvent(_Event):
    type: Literal["warmup_started"] = "warmup_started"
    deadline_id: int


class GameFinishedEvent(_Event):
    type: Literal["game_finished"] = "game_finished"
    winner_id: str | None
    final_scores: dict[str, int]


class GameAbortedEvent(_Event):
    type: Literal["game_aborted"] = "game_aborted"
    reason: str


class QuestionPresentedEvent(_Event):
    """The question itself is in the snapshot's turn; this is the cue."""

    type: Literal["question_presented"] = "question_presented"
    deadline_id: int


class PlayerAnsweredEvent(_Event):
    """§8.7: the fact to everyone, the value to its author only."""

    type: Literal["player_answered"] = "player_answered"
    player_id: str
    your_answer: SubmittedValue | None = None


class QuestionResolvedEvent(_Event):
    """`correct_value` is a decimal string for the reason every number on
    this API is: JSON's only number type is a float."""

    type: Literal["question_resolved"] = "question_resolved"
    correct_choice_index: int | None
    correct_value: str | None
    ranking: tuple[str, ...]
    correct_players: tuple[str, ...]


class RoundEvent(_Event):
    type: Literal["round_started", "round_completed"]
    phase: Literal["expansion", "battle"]
    round_no: int


class PicksGrantedEvent(_Event):
    type: Literal["picks_granted"] = "picks_granted"
    pick_order: tuple[str, ...]
    grants: dict[str, int]
    deadline_id: int


class TerritoryClaimedEvent(_Event):
    type: Literal["territory_claimed"] = "territory_claimed"
    player_id: str
    region_id: str
    acquisition: AcquisitionKind
    automatic: bool


class TurnStartedEvent(_Event):
    type: Literal["turn_started"] = "turn_started"
    attacker_id: str
    deadline_id: int


class TurnEndedEvent(_Event):
    type: Literal["turn_skipped", "turn_aborted"]
    attacker_id: str | None
    reason: str


class AttackDeclaredEvent(_Event):
    type: Literal["attack_declared"] = "attack_declared"
    attacker_id: str
    defender_id: str | None
    region_id: str


class DuelResolvedEvent(_Event):
    type: Literal["duel_resolved"] = "duel_resolved"
    winner_id: str | None


class TiebreakStartedEvent(_Event):
    type: Literal["tiebreak_started"] = "tiebreak_started"
    region_id: str


class TerritoryCapturedEvent(_Event):
    type: Literal["territory_captured"] = "territory_captured"
    region_id: str
    from_player_id: str | None
    to_player_id: str
    acquisition: AcquisitionKind


class NeutralCapturedEvent(_Event):
    type: Literal["neutral_captured"] = "neutral_captured"
    region_id: str
    player_id: str


class NeutralAttackFailedEvent(_Event):
    type: Literal["neutral_attack_failed"] = "neutral_attack_failed"
    region_id: str
    attacker_id: str


class DefenseHeldEvent(_Event):
    type: Literal["defense_held"] = "defense_held"
    region_id: str
    defender_id: str


class BaseDamagedEvent(_Event):
    type: Literal["base_damaged"] = "base_damaged"
    region_id: str
    hp_remaining: int


class BaseDestroyedEvent(_Event):
    type: Literal["base_destroyed"] = "base_destroyed"
    region_id: str
    owner_id: str


class ScoreChangedEvent(_Event):
    type: Literal["score_changed"] = "score_changed"
    player_id: str
    delta: int
    reason: ScoreReason
    new_total: int


class PlayerGoneEvent(_Event):
    type: Literal["player_eliminated", "player_surrendered"]
    player_id: str


class TerritoryNeutralizedEvent(_Event):
    type: Literal["territory_neutralized"] = "territory_neutralized"
    region_id: str
    former_owner_id: str


class FinalTiebreakStartedEvent(_Event):
    type: Literal["final_tiebreak_started"] = "final_tiebreak_started"
    contenders: tuple[str, ...]


ClientEvent = Annotated[
    PlayerJoinedEvent
    | PlayerLeftEvent
    | GameStartedEvent
    | BasesAssignedEvent
    | WarmupStartedEvent
    | GameFinishedEvent
    | GameAbortedEvent
    | QuestionPresentedEvent
    | PlayerAnsweredEvent
    | QuestionResolvedEvent
    | RoundEvent
    | PicksGrantedEvent
    | TerritoryClaimedEvent
    | TurnStartedEvent
    | TurnEndedEvent
    | AttackDeclaredEvent
    | DuelResolvedEvent
    | TiebreakStartedEvent
    | TerritoryCapturedEvent
    | NeutralCapturedEvent
    | NeutralAttackFailedEvent
    | DefenseHeldEvent
    | BaseDamagedEvent
    | BaseDestroyedEvent
    | ScoreChangedEvent
    | PlayerGoneEvent
    | TerritoryNeutralizedEvent
    | FinalTiebreakStartedEvent,
    Field(discriminator="type"),
]
