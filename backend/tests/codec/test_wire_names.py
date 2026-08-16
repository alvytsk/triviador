"""The wire-name registry, frozen against a literal.

Pure and PostgreSQL-free: no `integration` marker, no asyncio marks. `WIRE_NAMES`
is derived from nothing but itself, so this file is the sole source of truth
that every union member is registered and that a value here has not drifted.
"""

from typing import get_args

from triviador.db.codec.registry import WIRE_NAMES
from triviador.domain.game.events import GameEvent

EXPECTED = {
    "GameCreated": "game.created",
    "PlayerJoined": "game.player_joined",
    "PlayerLeft": "game.player_left",
    "GameStarted": "game.started",
    "BasesAssigned": "game.bases_assigned",
    "QuestionPoolDrawn": "game.question_pool_drawn",
    "MediaWarmupStarted": "game.media_warmup_started",
    "GameFinished": "game.finished",
    "GameAborted": "game.aborted",
    "TerritoryNeutralized": "game.territory_neutralized",
    "QuestionPresented": "question.presented",
    "AnswerSubmitted": "question.answer_submitted",
    "AnswerWindowClosed": "question.window_closed",
    "QuestionResolved": "question.resolved",
    "ExpansionRoundStarted": "expansion.round_started",
    "PicksGranted": "expansion.picks_granted",
    "TerritoryClaimed": "expansion.territory_claimed",
    "ExpansionRoundCompleted": "expansion.round_completed",
    "BattleRoundStarted": "battle.round_started",
    "TurnStarted": "battle.turn_started",
    "TurnSkipped": "battle.turn_skipped",
    "TurnAborted": "battle.turn_aborted",
    "AttackDeclared": "battle.attack_declared",
    "DuelResolved": "battle.duel_resolved",
    "TiebreakStarted": "battle.tiebreak_started",
    "TerritoryCaptured": "battle.territory_captured",
    "NeutralTerritoryCaptured": "battle.neutral_territory_captured",
    "NeutralAttackFailed": "battle.neutral_attack_failed",
    "DefenseHeld": "battle.defense_held",
    "BaseDamaged": "battle.base_damaged",
    "BaseDestroyed": "battle.base_destroyed",
    "BattleRoundCompleted": "battle.round_completed",
    "FinalTiebreakStarted": "battle.final_tiebreak_started",
    "ScoreChanged": "player.score_changed",
    "PlayerEliminated": "player.eliminated",
    "PlayerSurrendered": "player.surrendered",
}


def test_expected_table_has_36_entries() -> None:
    """Guards the literal itself: the union is what defines "36", not this
    number — but a mismatch here means the table below was hand-edited out
    of sync with `GameEvent` and the other two tests would give a confusing
    diff instead of a direct count mismatch."""
    assert len(EXPECTED) == len(get_args(GameEvent)) == 36


def test_every_event_has_a_wire_name() -> None:
    missing = {t.__name__ for t in get_args(GameEvent)} - set(EXPECTED)
    assert missing == set(), f"unregistered events: {sorted(missing)}"


def test_wire_names_are_frozen() -> None:
    """Changing a value here is a data migration over `game_events.type`, not
    an edit. If this fails because you renamed a Python class, map the old
    wire name to the new class instead."""
    assert {cls.__name__: name for cls, name in WIRE_NAMES.items()} == EXPECTED


def test_wire_names_are_unique() -> None:
    assert len(set(WIRE_NAMES.values())) == len(WIRE_NAMES)


def test_no_stray_registrations() -> None:
    """Every key in `WIRE_NAMES` is an actual `GameEvent` union member — the
    inverse of `test_every_event_has_a_wire_name`, catching a class that was
    registered but never added to (or removed from) the union."""
    union_members = set(get_args(GameEvent))
    assert set(WIRE_NAMES) == union_members
