"""One domain event, one client event or nothing — decided per viewer.

`PROJECTED` and `WITHHELD` are declared rather than inferred, and a test
asserts their union is exactly the `GameEvent` union. The failure that
guards against is silent: a new event type that fell through to a `None`
default would simply never appear in any client, with nothing reporting it.
"""

from typing import assert_never

from triviador.api.projection.viewer import ViewerContext
from triviador.api.schemas.events import (
    AttackDeclaredEvent,
    BaseDamagedEvent,
    BaseDestroyedEvent,
    BasesAssignedEvent,
    ClientEvent,
    DefenseHeldEvent,
    DuelResolvedEvent,
    FinalTiebreakStartedEvent,
    GameAbortedEvent,
    GameFinishedEvent,
    GameStartedEvent,
    NeutralAttackFailedEvent,
    NeutralCapturedEvent,
    PicksGrantedEvent,
    PlayerAnsweredEvent,
    PlayerGoneEvent,
    PlayerJoinedEvent,
    PlayerLeftEvent,
    QuestionPresentedEvent,
    QuestionResolvedEvent,
    RoundEvent,
    ScoreChangedEvent,
    TerritoryCapturedEvent,
    TerritoryClaimedEvent,
    TerritoryNeutralizedEvent,
    TiebreakStartedEvent,
    TurnEndedEvent,
    TurnStartedEvent,
    WarmupStartedEvent,
)
from triviador.api.schemas.games import SubmittedValue
from triviador.domain.game import events as ev
from triviador.domain.game.state import ChoiceAnswer

# Nothing derived: both are written out, and `test_projection_events.py`
# asserts their union is the whole `GameEvent` union.
WITHHELD = {"GameCreated", "QuestionPoolDrawn", "AnswerWindowClosed"}
PROJECTED = {
    "PlayerJoined",
    "PlayerLeft",
    "GameStarted",
    "BasesAssigned",
    "MediaWarmupStarted",
    "GameFinished",
    "GameAborted",
    "QuestionPresented",
    "AnswerSubmitted",
    "QuestionResolved",
    "ExpansionRoundStarted",
    "PicksGranted",
    "TerritoryClaimed",
    "ExpansionRoundCompleted",
    "BattleRoundStarted",
    "TurnStarted",
    "TurnSkipped",
    "TurnAborted",
    "AttackDeclared",
    "DuelResolved",
    "TiebreakStarted",
    "TerritoryCaptured",
    "NeutralTerritoryCaptured",
    "NeutralAttackFailed",
    "DefenseHeld",
    "BaseDamaged",
    "BaseDestroyed",
    "BattleRoundCompleted",
    "ScoreChanged",
    "PlayerEliminated",
    "PlayerSurrendered",
    "TerritoryNeutralized",
    "FinalTiebreakStarted",
}


def _own_value(event: ev.AnswerSubmitted, viewer: ViewerContext) -> SubmittedValue | None:
    if viewer.player_id != event.player_id:
        return None
    value = event.answer.value
    if isinstance(value, ChoiceAnswer):
        return SubmittedValue(kind="choice", idx=value.idx)
    return SubmittedValue(kind="numeric", value=str(value.value))


def project_event(event: ev.GameEvent, viewer: ViewerContext) -> ClientEvent | None:
    match event:
        # --- withheld ------------------------------------------------------
        case ev.GameCreated():
            # Never folded and never in a published batch (§6.2 writes it
            # directly at genesis); listed so the decision is explicit.
            return None
        case ev.QuestionPoolDrawn():
            # The whole match, answers included.
            return None
        case ev.AnswerWindowClosed():
            # Mechanical: the snapshot's turn already changed, and a
            # narration line for it would say nothing a player can see.
            return None

        # --- lifecycle -----------------------------------------------------
        case ev.PlayerJoined(player_id=pid, display_name=name, seat=seat):
            return PlayerJoinedEvent(player_id=str(pid), display_name=name, seat=seat)
        case ev.PlayerLeft(player_id=pid):
            return PlayerLeftEvent(player_id=str(pid))
        case ev.GameStarted(turn_order=order):
            return GameStartedEvent(turn_order=tuple(str(p) for p in order))
        case ev.BasesAssigned(assignments=assignments):
            return BasesAssignedEvent(assignments={str(p): str(r) for p, r in assignments.items()})
        case ev.MediaWarmupStarted(deadline=deadline):
            return WarmupStartedEvent(deadline_id=int(deadline.id))
        case ev.GameFinished(winner_id=winner, final_scores=scores):
            return GameFinishedEvent(
                winner_id=None if winner is None else str(winner),
                final_scores={str(p): s for p, s in scores.items()},
            )
        case ev.GameAborted(reason=reason):
            return GameAbortedEvent(reason=reason)

        # --- questions -----------------------------------------------------
        case ev.QuestionPresented(deadline=deadline):
            return QuestionPresentedEvent(deadline_id=int(deadline.id))
        case ev.AnswerSubmitted(player_id=pid):
            return PlayerAnsweredEvent(player_id=str(pid), your_answer=_own_value(event, viewer))
        case ev.QuestionResolved(
            correct_choice_index=idx,
            correct_value=value,
            ranking=ranking,
            correct_players=correct,
        ):
            return QuestionResolvedEvent(
                correct_choice_index=idx,
                correct_value=None if value is None else str(value),
                ranking=tuple(str(p) for p in ranking),
                correct_players=tuple(str(p) for p in correct),
            )

        # --- expansion -----------------------------------------------------
        case ev.ExpansionRoundStarted(round_no=n):
            return RoundEvent(type="round_started", phase="expansion", round_no=n)
        case ev.ExpansionRoundCompleted(round_no=n):
            return RoundEvent(type="round_completed", phase="expansion", round_no=n)
        case ev.PicksGranted(pick_order=order, grants=grants, deadline=deadline):
            return PicksGrantedEvent(
                pick_order=tuple(str(p) for p in order),
                grants={str(p): n for p, n in grants.items()},
                deadline_id=int(deadline.id),
            )
        case ev.TerritoryClaimed(
            player_id=pid, region_id=rid, acquisition=acq, automatic=automatic
        ):
            return TerritoryClaimedEvent(
                player_id=str(pid), region_id=str(rid), acquisition=acq, automatic=automatic
            )

        # --- battle --------------------------------------------------------
        case ev.BattleRoundStarted(round_no=n):
            return RoundEvent(type="round_started", phase="battle", round_no=n)
        case ev.BattleRoundCompleted(round_no=n):
            return RoundEvent(type="round_completed", phase="battle", round_no=n)
        case ev.TurnStarted(attacker_id=pid, deadline=deadline):
            return TurnStartedEvent(attacker_id=str(pid), deadline_id=int(deadline.id))
        case ev.TurnSkipped(attacker_id=pid, reason=reason):
            return TurnEndedEvent(type="turn_skipped", attacker_id=str(pid), reason=reason)
        case ev.TurnAborted(reason=reason):
            return TurnEndedEvent(type="turn_aborted", attacker_id=None, reason=reason)
        case ev.AttackDeclared(attacker_id=a, defender_id=d, region_id=rid):
            return AttackDeclaredEvent(
                attacker_id=str(a),
                defender_id=None if d is None else str(d),
                region_id=str(rid),
            )
        case ev.DuelResolved(winner_id=winner):
            return DuelResolvedEvent(winner_id=None if winner is None else str(winner))
        case ev.TiebreakStarted(region_id=rid):
            return TiebreakStartedEvent(region_id=str(rid))
        case ev.TerritoryCaptured(
            region_id=rid, from_player_id=src, to_player_id=dst, acquisition=acq
        ):
            return TerritoryCapturedEvent(
                region_id=str(rid),
                from_player_id=None if src is None else str(src),
                to_player_id=str(dst),
                acquisition=acq,
            )
        case ev.NeutralTerritoryCaptured(region_id=rid, player_id=pid):
            return NeutralCapturedEvent(region_id=str(rid), player_id=str(pid))
        case ev.NeutralAttackFailed(region_id=rid, attacker_id=pid):
            return NeutralAttackFailedEvent(region_id=str(rid), attacker_id=str(pid))
        case ev.DefenseHeld(region_id=rid, defender_id=pid):
            return DefenseHeldEvent(region_id=str(rid), defender_id=str(pid))
        case ev.BaseDamaged(region_id=rid, hp_remaining=hp):
            return BaseDamagedEvent(region_id=str(rid), hp_remaining=hp)
        case ev.BaseDestroyed(region_id=rid, owner_id=pid):
            return BaseDestroyedEvent(region_id=str(rid), owner_id=str(pid))

        # --- scoring and terminal ------------------------------------------
        case ev.ScoreChanged(player_id=pid, delta=delta, reason=reason, new_total=total):
            return ScoreChangedEvent(
                player_id=str(pid), delta=delta, reason=reason, new_total=total
            )
        case ev.PlayerEliminated(player_id=pid):
            return PlayerGoneEvent(type="player_eliminated", player_id=str(pid))
        case ev.PlayerSurrendered(player_id=pid):
            return PlayerGoneEvent(type="player_surrendered", player_id=str(pid))
        case ev.TerritoryNeutralized(region_id=rid, former_owner_id=pid):
            return TerritoryNeutralizedEvent(region_id=str(rid), former_owner_id=str(pid))
        case ev.FinalTiebreakStarted(contenders=contenders):
            return FinalTiebreakStartedEvent(contenders=tuple(str(p) for p in contenders))
        case _:
            assert_never(event)
