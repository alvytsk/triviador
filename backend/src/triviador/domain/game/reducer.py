"""The pure game reducer.

    events    = decide(state, command, ctx)
    new_state = evolve(state, event)

`decide` answers *what happened*; `evolve` answers *what the state becomes*.
Replay is therefore fold(evolve, events) and needs no context at all.
"""

from collections.abc import Iterable, Mapping
from dataclasses import replace

from triviador.domain.game import events as ev
from triviador.domain.game.actions import (
    WINDOWED_COMMANDS,
    AbortGame,
    Command,
    DecisionContext,
    ExpireDeadline,
    JoinGame,
    PickRegion,
    RejectCode,
    RejectedCommand,
    SelectAttackTarget,
    StartGame,
    SubmitAnswer,
    Surrender,
)
from triviador.domain.game.state import (
    TERMINAL_PHASES,
    BattleDuel,
    BattleTargetSelect,
    BattleTiebreak,
    ExpansionPicking,
    ExpansionQuestion,
    FinalTiebreak,
    GameState,
    NeutralChallenge,
    Turn,
)

# Which commands are legal for which turn variant. `None` means "no open turn",
# which in a non-terminal phase can only be LOBBY.
LEGAL_COMMANDS: Mapping[type[Turn] | None, frozenset[type[Command]]] = {
    None: frozenset({JoinGame, StartGame, Surrender, AbortGame}),
    ExpansionQuestion: frozenset({SubmitAnswer, ExpireDeadline, Surrender, AbortGame}),
    ExpansionPicking: frozenset({PickRegion, ExpireDeadline, Surrender, AbortGame}),
    BattleTargetSelect: frozenset({SelectAttackTarget, ExpireDeadline, Surrender, AbortGame}),
    BattleDuel: frozenset({SubmitAnswer, ExpireDeadline, Surrender, AbortGame}),
    BattleTiebreak: frozenset({SubmitAnswer, ExpireDeadline, Surrender, AbortGame}),
    NeutralChallenge: frozenset({SubmitAnswer, ExpireDeadline, Surrender, AbortGame}),
    FinalTiebreak: frozenset({SubmitAnswer, ExpireDeadline, Surrender, AbortGame}),
}


def decide(state: GameState, command: Command, ctx: DecisionContext) -> tuple[ev.GameEvent, ...]:
    # Guard 1 — terminal phases accept nothing.
    if state.phase in TERMINAL_PHASES:
        if isinstance(command, (JoinGame, StartGame, AbortGame)):
            raise RejectedCommand(RejectCode.WRONG_TURN_STATE, f"game is {state.phase}")
        return ()

    # Guard 2 — stale window. Deliberately before actor validation: a packet
    # from a window that has already closed is a benign race, never an error.
    if isinstance(command, WINDOWED_COMMANDS):
        current = state.current_deadline()
        if current is None or current.id != command.deadline_id:
            return ()

    # Guard 3 — actor validity.
    actor_id = getattr(command, "actor_id", None)
    if actor_id is not None and not isinstance(command, JoinGame):
        player = state.players.get(actor_id)
        if player is None or player.is_eliminated:
            raise RejectedCommand(
                RejectCode.NOT_A_PARTICIPANT, f"{actor_id!r} is not an active player"
            )

    # Guard 4 — a timer that fired early.
    if isinstance(command, ExpireDeadline):
        current = state.current_deadline()
        assert current is not None  # guaranteed by guard 2
        if ctx.now < current.deadline_at:
            return ()

    # Guard 5 — command legality for this turn.
    turn_key = type(state.turn) if state.turn is not None else None
    if type(command) not in LEGAL_COMMANDS[turn_key]:
        raise RejectedCommand(
            RejectCode.WRONG_TURN_STATE,
            f"{type(command).__name__} is not legal in {turn_key and turn_key.__name__}",
        )

    # Guards 6-7 — domain constraints and event production, per turn.
    return _dispatch(state, command, ctx)


def _dispatch(state: GameState, command: Command, ctx: DecisionContext) -> tuple[ev.GameEvent, ...]:
    raise NotImplementedError("filled in by later tasks")


def evolve(state: GameState, event: ev.GameEvent) -> GameState:
    """Apply one event. Always advances seq; never consults anything but the event."""
    return replace(_apply(state, event), seq=state.seq + 1)


def _apply(state: GameState, event: ev.GameEvent) -> GameState:
    raise NotImplementedError("filled in by later tasks")


def fold(state: GameState, events: Iterable[ev.GameEvent]) -> GameState:
    for event in events:
        state = evolve(state, event)
    return state
