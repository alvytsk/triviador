"""The frozen wire-name registry.

`WIRE_NAMES` maps each `GameEvent` union member to the string stored in
`game_events.type`. It is a module-level literal — not derived from
`cls.__name__` — because the wire name and the Python class name are
allowed to diverge: a class can be renamed in a refactor without that being
a data migration, precisely because nothing here reads `__name__`. Changing
a *value* in this dict, on the other hand, is a data migration over
`game_events.type`.

`CURRENT_VERSION` gives every registered wire type a starting schema
version of 1. It is derived from `WIRE_NAMES`' keys (themselves a literal),
not from any external source, so every registered event is guaranteed an
entry and none can be forgotten.
"""

from collections.abc import Mapping
from typing import Any

from triviador.domain.game.events import (
    AnswerSubmitted,
    AnswerWindowClosed,
    AttackDeclared,
    BaseDamaged,
    BaseDestroyed,
    BasesAssigned,
    BattleRoundCompleted,
    BattleRoundStarted,
    DefenseHeld,
    DuelResolved,
    ExpansionRoundCompleted,
    ExpansionRoundStarted,
    FinalTiebreakStarted,
    GameAborted,
    GameCreated,
    GameFinished,
    GameStarted,
    MediaWarmupStarted,
    NeutralAttackFailed,
    NeutralTerritoryCaptured,
    PicksGranted,
    PlayerEliminated,
    PlayerJoined,
    PlayerLeft,
    PlayerSurrendered,
    QuestionPoolDrawn,
    QuestionPresented,
    QuestionResolved,
    ScoreChanged,
    TerritoryCaptured,
    TerritoryClaimed,
    TerritoryNeutralized,
    TiebreakStarted,
    TurnAborted,
    TurnSkipped,
    TurnStarted,
)

WIRE_NAMES: Mapping[type[Any], str] = {
    GameCreated: "game.created",
    PlayerJoined: "game.player_joined",
    PlayerLeft: "game.player_left",
    GameStarted: "game.started",
    BasesAssigned: "game.bases_assigned",
    QuestionPoolDrawn: "game.question_pool_drawn",
    MediaWarmupStarted: "game.media_warmup_started",
    GameFinished: "game.finished",
    GameAborted: "game.aborted",
    TerritoryNeutralized: "game.territory_neutralized",
    QuestionPresented: "question.presented",
    AnswerSubmitted: "question.answer_submitted",
    AnswerWindowClosed: "question.window_closed",
    QuestionResolved: "question.resolved",
    ExpansionRoundStarted: "expansion.round_started",
    PicksGranted: "expansion.picks_granted",
    TerritoryClaimed: "expansion.territory_claimed",
    ExpansionRoundCompleted: "expansion.round_completed",
    BattleRoundStarted: "battle.round_started",
    TurnStarted: "battle.turn_started",
    TurnSkipped: "battle.turn_skipped",
    TurnAborted: "battle.turn_aborted",
    AttackDeclared: "battle.attack_declared",
    DuelResolved: "battle.duel_resolved",
    TiebreakStarted: "battle.tiebreak_started",
    TerritoryCaptured: "battle.territory_captured",
    NeutralTerritoryCaptured: "battle.neutral_territory_captured",
    NeutralAttackFailed: "battle.neutral_attack_failed",
    DefenseHeld: "battle.defense_held",
    BaseDamaged: "battle.base_damaged",
    BaseDestroyed: "battle.base_destroyed",
    BattleRoundCompleted: "battle.round_completed",
    FinalTiebreakStarted: "battle.final_tiebreak_started",
    ScoreChanged: "player.score_changed",
    PlayerEliminated: "player.eliminated",
    PlayerSurrendered: "player.surrendered",
}

CLASSES_BY_WIRE_NAME: Mapping[str, type[Any]] = {name: cls for cls, name in WIRE_NAMES.items()}

CURRENT_VERSION: Mapping[str, int] = dict.fromkeys(WIRE_NAMES.values(), 1)
