"""The pure game reducer.

    events    = decide(state, command, ctx)
    new_state = evolve(state, event)

`decide` answers *what happened*; `evolve` answers *what the state becomes*.
Replay is therefore fold(evolve, events) and needs no context at all.
"""

from collections.abc import Iterable, Mapping
from dataclasses import replace
from datetime import timedelta
from decimal import Decimal

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
from triviador.domain.game.rules import required_question_budget
from triviador.domain.game.state import (
    TERMINAL_PHASES,
    AcquisitionKind,
    BattleDuel,
    BattleTargetSelect,
    BattleTiebreak,
    Deadline,
    DeadlineKind,
    ExpansionPicking,
    ExpansionQuestion,
    FinalTiebreak,
    GameState,
    NeutralChallenge,
    NumericAnswer,
    Phase,
    PlayerState,
    SubmittedAnswer,
    Territory,
    TerritoryKind,
    Turn,
)
from triviador.domain.ids import PlayerId
from triviador.domain.questions.types import QuestionKind, QuestionSnapshot

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
    match command:
        case JoinGame():
            return _decide_join(state, command)
        case StartGame():
            return _decide_start(state, ctx)
        case SubmitAnswer() if isinstance(state.turn, ExpansionQuestion):
            return _decide_expansion_answer(state, command, ctx)
        case ExpireDeadline() if isinstance(state.turn, ExpansionQuestion):
            return _close_expansion_question(state, state.turn, ctx)
    raise NotImplementedError(f"no handler for {type(command).__name__}")


def _decide_join(state: GameState, command: JoinGame) -> tuple[ev.GameEvent, ...]:
    if command.actor_id in state.players:
        raise RejectedCommand(RejectCode.ALREADY_JOINED, f"{command.actor_id!r} already joined")
    if len(state.players) >= state.rules.player_count:
        raise RejectedCommand(RejectCode.GAME_FULL, "lobby is full")
    return (ev.PlayerJoined(command.actor_id, command.display_name, seat=len(state.players)),)


def _decide_start(state: GameState, ctx: DecisionContext) -> tuple[ev.GameEvent, ...]:
    if len(state.players) != state.rules.player_count:
        raise RejectedCommand(
            RejectCode.NOT_ENOUGH_PLAYERS,
            f"need {state.rules.player_count} players, have {len(state.players)}",
        )

    pool = ctx.drawn_pool
    if pool is None or not pool.covers(required_question_budget(state.rules)):
        raise RejectedCommand(
            RejectCode.QUESTION_POOL_INSUFFICIENT, "question bank cannot cover this preset"
        )

    order = ctx.shuffled_player_ids
    bases = ctx.base_regions
    if order is None or bases is None or len(bases) != len(order):
        raise RejectedCommand(RejectCode.WRONG_TURN_STATE, "start context is incomplete")

    assignments = dict(zip(order, bases, strict=True))
    events: list[ev.GameEvent] = [ev.GameStarted(order), ev.BasesAssigned(assignments)]
    for player_id in order:
        events.append(
            ev.ScoreChanged(
                player_id, state.rules.pts_base, ev.ScoreReason.BASE, new_total=state.rules.pts_base
            )
        )
    events.append(ev.QuestionPoolDrawn(pool))

    # Fold what we have so the question window is opened against real state.
    seeded = fold(state, events)
    events.append(ev.ExpansionRoundStarted(1))
    seeded = evolve(seeded, events[-1])
    question_events, _ = _open_expansion_question(seeded, ctx)
    events.extend(question_events)
    return tuple(events)


def _open_expansion_question(
    state: GameState, ctx: DecisionContext
) -> tuple[tuple[ev.GameEvent, ...], GameState]:
    question, _ = state.pool.next_numeric()
    deadline, _ = state.allocate_deadline(
        DeadlineKind.ANSWER, ctx.now + timedelta(milliseconds=state.rules.answer_timeout_ms)
    )
    event = ev.QuestionPresented(question, deadline)
    return (event,), evolve(state, event)


def _record_answer(
    turn: ExpansionQuestion | BattleDuel | BattleTiebreak | NeutralChallenge | FinalTiebreak,
    command: SubmitAnswer,
) -> ev.AnswerSubmitted | None:
    """None means 'ignore' — an identical resubmission."""
    existing = turn.answers.get(command.actor_id)
    submitted = SubmittedAnswer(command.value, command.elapsed_ms)
    if existing is not None:
        if existing.value == submitted.value:
            return None
        raise RejectedCommand(
            RejectCode.ALREADY_ANSWERED, f"{command.actor_id!r} already answered this window"
        )
    expected_numeric = turn.question.kind is QuestionKind.NUMERIC
    if expected_numeric != isinstance(command.value, NumericAnswer):
        raise RejectedCommand(
            RejectCode.ANSWER_KIND_MISMATCH,
            f"question is {turn.question.kind}, answer was {type(command.value).__name__}",
        )
    return ev.AnswerSubmitted(command.actor_id, submitted)


def _decide_expansion_answer(
    state: GameState, command: SubmitAnswer, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    turn = state.turn
    assert isinstance(turn, ExpansionQuestion)
    recorded = _record_answer(turn, command)
    if recorded is None:
        return ()
    after = evolve(state, recorded)
    assert isinstance(after.turn, ExpansionQuestion)
    if len(after.turn.answers) < len(after.active_players()):
        return (recorded,)
    return (recorded, *_close_expansion_question(after, after.turn, ctx))


def _close_expansion_question(
    state: GameState, turn: ExpansionQuestion, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    ranking = _rank_numeric(turn, state)
    resolved = ev.QuestionResolved(
        correct_choice_index=None,
        correct_value=turn.question.numeric_answer,
        ranking=ranking,
        correct_players=(),
    )
    free = len(state.free_regions())
    grants: dict[PlayerId, int] = {}
    for rank, player_id in enumerate(ranking):
        want = state.rules.claims_by_rank[rank] if rank < len(state.rules.claims_by_rank) else 0
        take = min(want, free)
        grants[player_id] = take
        free -= take
    order = tuple(p for p in ranking if grants[p] > 0)

    if not order:
        return (ev.AnswerWindowClosed(turn.deadline), resolved, *_advance_expansion(state, ctx))

    # decide() owns the clock, so the pick deadline is allocated here and
    # carried on the event — evolve() never needs a timestamp of its own.
    deadline, _ = state.allocate_deadline(
        DeadlineKind.PICK, ctx.now + timedelta(milliseconds=state.rules.pick_timeout_ms)
    )
    return (
        ev.AnswerWindowClosed(turn.deadline),
        resolved,
        ev.PicksGranted(order, grants, deadline),
    )


def _rank_numeric(
    turn: ExpansionQuestion | BattleTiebreak | FinalTiebreak, state: GameState
) -> tuple[PlayerId, ...]:
    correct = turn.question.numeric_answer
    assert correct is not None
    contenders = turn.contenders if isinstance(turn, FinalTiebreak) else state.active_players()

    def key(player_id: PlayerId) -> tuple[int, Decimal, int, int]:
        submitted = turn.answers.get(player_id)
        seat = state.players[player_id].seat
        if submitted is None or not isinstance(submitted.value, NumericAnswer):
            return (1, Decimal(0), 0, seat)
        return (0, abs(submitted.value.value - correct), submitted.elapsed_ms, seat)

    return tuple(sorted(contenders, key=key))


def _advance_expansion(state: GameState, ctx: DecisionContext) -> tuple[ev.GameEvent, ...]:
    raise NotImplementedError("completed in Task 13")


def evolve(state: GameState, event: ev.GameEvent) -> GameState:
    """Apply one event. Always advances seq; never consults anything but the event."""
    return replace(_apply(state, event), seq=state.seq + 1)


def _apply(state: GameState, event: ev.GameEvent) -> GameState:
    match event:
        case ev.PlayerJoined(player_id=pid, display_name=name, seat=seat):
            player = PlayerState(
                pid, name, seat, score=0, bonus_score=0, base_region=None, is_eliminated=False
            )
            return replace(
                state,
                players={**state.players, pid: player},
                turn_order=(*state.turn_order, pid),
            )

        case ev.GameStarted(turn_order=order):
            return replace(state, turn_order=order, phase=Phase.EXPANSION)

        case ev.BasesAssigned(assignments=assignments):
            territories = dict(state.territories)
            players = dict(state.players)
            for player_id, region_id in assignments.items():
                territories[region_id] = Territory(
                    region_id=region_id,
                    owner_id=player_id,
                    kind=TerritoryKind.BASE,
                    base_owner_id=player_id,
                    base_hp=state.rules.base_hp,
                    acquisition=AcquisitionKind.BASE,
                )
                players[player_id] = replace(players[player_id], base_region=region_id)
            return replace(state, territories=territories, players=players)

        case ev.ScoreChanged(player_id=pid, reason=reason, delta=delta, new_total=total):
            player = state.players[pid]
            bonus = player.bonus_score
            if reason in (ev.ScoreReason.DEFENSE, ev.ScoreReason.BONUS):
                bonus += delta
            return replace(
                state,
                players={**state.players, pid: replace(player, score=total, bonus_score=bonus)},
            )

        case ev.QuestionPoolDrawn(pool=pool):
            return replace(state, pool=pool)

        case ev.ExpansionRoundStarted(round_no=round_no):
            return replace(state, phase=Phase.EXPANSION, round_no=round_no, turn=None)

        case ev.QuestionPresented(question=question, deadline=deadline):
            return _present_question(state, question, deadline)

        case ev.AnswerSubmitted(player_id=pid, answer=submitted):
            turn = state.turn
            assert turn is not None and hasattr(turn, "answers")
            return replace(
                state,
                turn=replace(turn, answers={**turn.answers, pid: submitted}),  # type: ignore[arg-type]
            )

        case ev.AnswerWindowClosed():
            return state

        case ev.QuestionResolved():
            return state

        case ev.PicksGranted(pick_order=order, grants=grants, deadline=deadline):
            return replace(
                state,
                next_deadline_id=max(state.next_deadline_id, deadline.id + 1),
                turn=ExpansionPicking(deadline, order, dict(grants), order[0]),
            )

    raise NotImplementedError(f"no evolve branch for {type(event).__name__}")


def _present_question(
    state: GameState, question: QuestionSnapshot, deadline: Deadline
) -> GameState:
    """Open a question window on whatever turn shape the phase calls for."""
    from triviador.domain.questions.types import QuestionKind

    if question.kind is QuestionKind.NUMERIC:
        _, pool = state.pool.next_numeric()
    else:
        _, pool = state.pool.next_multiple_choice()
    base = replace(state, pool=pool, next_deadline_id=max(state.next_deadline_id, deadline.id + 1))

    if state.phase is Phase.EXPANSION:
        return replace(base, turn=ExpansionQuestion(deadline, question, answers={}))
    raise NotImplementedError("battle question windows arrive in Task 15")


def fold(state: GameState, events: Iterable[ev.GameEvent]) -> GameState:
    for event in events:
        state = evolve(state, event)
    return state
