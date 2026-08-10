"""The pure game reducer.

    events    = decide(state, command, ctx)
    new_state = evolve(state, event)

`decide` answers *what happened*; `evolve` answers *what the state becomes*.
Replay is therefore fold(evolve, events) and needs no context at all.
"""

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
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
from triviador.domain.game.scoring import expected_score, holding_value
from triviador.domain.game.state import (
    TERMINAL_PHASES,
    AcquisitionKind,
    BattleDuel,
    BattleTargetSelect,
    BattleTiebreak,
    ChoiceAnswer,
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
from triviador.domain.ids import PlayerId, RegionId
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
        case Surrender():
            return _decide_surrender(state, command, ctx)
        case AbortGame():
            return _decide_abort(state, command)
        case SubmitAnswer() if isinstance(state.turn, ExpansionQuestion):
            return _decide_expansion_answer(state, command, ctx)
        case ExpireDeadline() if isinstance(state.turn, ExpansionQuestion):
            return _close_expansion_question(state, state.turn, ctx)
        case PickRegion() if isinstance(state.turn, ExpansionPicking):
            return _decide_pick(state, state.turn, command, ctx)
        case ExpireDeadline() if isinstance(state.turn, ExpansionPicking):
            return _decide_auto_pick(state, state.turn, ctx)
        case SelectAttackTarget() if isinstance(state.turn, BattleTargetSelect):
            return _decide_target(state, state.turn, command, ctx)
        case ExpireDeadline() if isinstance(state.turn, BattleTargetSelect):
            return _decide_target_timeout(state, state.turn, ctx)
        case SubmitAnswer() if isinstance(state.turn, NeutralChallenge):
            return _decide_neutral_answer(state, command, ctx)
        case ExpireDeadline() if isinstance(state.turn, NeutralChallenge):
            return _close_neutral_challenge(state, state.turn, ctx)
        case SubmitAnswer() if isinstance(state.turn, BattleDuel):
            return _decide_duel_answer(state, state.turn, command, ctx)
        case ExpireDeadline() if isinstance(state.turn, BattleDuel):
            return _close_duel(state, state.turn, ctx)
        case SubmitAnswer() if isinstance(state.turn, BattleTiebreak):
            return _decide_tiebreak_answer(state, state.turn, command, ctx)
        case ExpireDeadline() if isinstance(state.turn, BattleTiebreak):
            return _close_tiebreak(state, state.turn, ctx)
        case SubmitAnswer() if isinstance(state.turn, FinalTiebreak):
            return _decide_final_tiebreak_answer(state, state.turn, command, ctx)
        case ExpireDeadline() if isinstance(state.turn, FinalTiebreak):
            return _close_final_tiebreak(state, state.turn, ctx)
    raise NotImplementedError(f"no handler for {type(command).__name__}")  # pragma: no cover


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
    if (
        order is None
        or bases is None
        or len(bases) != len(order)
        or set(order) != set(state.players)
        or len(set(bases)) != len(bases)
        or not set(bases) <= set(state.map.region_ids())
    ):
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
    # A BattleTiebreak ranks only the two combatants: everyone else is a
    # bystander whose guess must never veto a capture the attacker or
    # defender legitimately won or lost.
    contenders = (
        (turn.attacker_id, turn.defender_id)
        if isinstance(turn, BattleTiebreak)
        else turn.contenders
        if isinstance(turn, FinalTiebreak)
        else state.active_players()
    )

    def key(player_id: PlayerId) -> tuple[int, Decimal, int, int]:
        submitted = turn.answers.get(player_id)
        seat = state.players[player_id].seat
        if submitted is None or not isinstance(submitted.value, NumericAnswer):
            return (1, Decimal(0), 0, seat)
        return (0, abs(submitted.value.value - correct), submitted.elapsed_ms, seat)

    return tuple(sorted(contenders, key=key))


def _decide_pick(
    state: GameState, turn: ExpansionPicking, command: PickRegion, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    if command.actor_id != turn.current_picker:
        raise RejectedCommand(RejectCode.NOT_YOUR_TURN, f"{turn.current_picker!r} is picking")
    if command.region_id not in state.territories:
        raise RejectedCommand(RejectCode.UNKNOWN_REGION, f"{command.region_id!r} is not on the map")
    if state.territories[command.region_id].owner_id is not None:
        raise RejectedCommand(RejectCode.REGION_NOT_FREE, f"{command.region_id!r} is taken")
    return _claim(state, turn, command.region_id, automatic=False, ctx=ctx)


def _decide_auto_pick(
    state: GameState, turn: ExpansionPicking, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    if state.players[turn.current_picker].is_eliminated:
        # The picker surrendered (or was otherwise eliminated) after this
        # window opened. Elimination during EXPANSION never touches the open
        # turn the way `_next_battle_turn` does for BATTLE — `current_picker`
        # is a snapshot taken when `PicksGranted` fired and nothing revisits
        # it — so on timeout it can point at someone who no longer owns
        # anything. Forfeit their remaining grants instead of handing a
        # region to an eliminated player. `_next_picker` (below) skips
        # eliminated candidates when choosing who is up next, but it does
        # *not* scrub `remaining` itself — other entries in this dict can
        # still carry stale positive grants for players eliminated earlier
        # in the round. That's fine only because `_apply`'s `PicksGranted`
        # handler re-derives `current_picker` through this same
        # `_next_picker` call rather than trusting the raw grants; this line
        # just needs to zero this picker's own remaining count and let
        # `_next_picker` decide what happens next.
        remaining = {**turn.grants_remaining, turn.current_picker: 0}
        next_picker = _next_picker(turn.pick_order, remaining, state)
        if next_picker is None:
            return _advance_expansion(state, ctx)
        deadline, _ = state.allocate_deadline(
            DeadlineKind.PICK, ctx.now + timedelta(milliseconds=state.rules.pick_timeout_ms)
        )
        return (ev.PicksGranted(turn.pick_order, remaining, deadline),)
    free = set(state.free_regions())
    order = ctx.shuffled_region_ids or state.free_regions()
    region_id = next((r for r in order if r in free), None)
    if region_id is None:
        return _advance_expansion(state, ctx)
    return _claim(state, turn, region_id, automatic=True, ctx=ctx)


def _claim(
    state: GameState,
    turn: ExpansionPicking,
    region_id: RegionId,
    *,
    automatic: bool,
    ctx: DecisionContext,
) -> tuple[ev.GameEvent, ...]:
    picker = turn.current_picker
    claimed = ev.TerritoryClaimed(picker, region_id, AcquisitionKind.CLAIMED, automatic)
    after = evolve(state, claimed)
    score = ev.ScoreChanged(
        picker,
        state.rules.pts_territory,
        ev.ScoreReason.TERRITORY,
        new_total=expected_score(after, picker),
    )
    after = evolve(after, score)

    remaining = {**turn.grants_remaining, picker: turn.grants_remaining[picker] - 1}
    next_picker = _next_picker(turn.pick_order, remaining, after)
    if next_picker is None:
        return (claimed, score, *_advance_expansion(after, ctx))

    deadline, _ = after.allocate_deadline(
        DeadlineKind.PICK, ctx.now + timedelta(milliseconds=after.rules.pick_timeout_ms)
    )
    return (claimed, score, ev.PicksGranted(turn.pick_order, remaining, deadline))


def _next_picker(
    order: tuple[PlayerId, ...], remaining: Mapping[PlayerId, int], state: GameState
) -> PlayerId | None:
    if not state.free_regions():
        return None
    return next(
        (p for p in order if remaining.get(p, 0) > 0 and not state.players[p].is_eliminated),
        None,
    )


def _advance_expansion(state: GameState, ctx: DecisionContext) -> tuple[ev.GameEvent, ...]:
    done = ev.ExpansionRoundCompleted(state.round_no)
    after = evolve(state, done)
    rounds_left = after.round_no < after.rules.expansion_rounds
    if rounds_left and after.free_regions():
        started = ev.ExpansionRoundStarted(after.round_no + 1)
        after = evolve(after, started)
        question, _ = _open_expansion_question(after, ctx)
        return (done, started, *question)
    # Mirrors `_next_battle_turn`'s own "one active player left ends the
    # game" check (there, "before anything about turn shape or rotation is
    # even looked at"). EXPANSION-phase elimination via surrender never
    # revisits the open turn the way BATTLE's rotation does, so it is only
    # here — the one place EXPANSION hands control to BATTLE — that a
    # trajectory eliminating everyone (or all but one) during EXPANSION
    # would otherwise crash on `active_players()[0]` instead of ending the
    # game. Skip opening a battle round altogether rather than start one
    # with no legal first attacker.
    active = after.active_players()
    if len(active) <= 1:
        return (done, *_finish(after, ctx))
    battle = ev.BattleRoundStarted(1)
    after = evolve(after, battle)
    return (done, battle, *_open_battle_turn(after, active[0], ctx))


def legal_targets(state: GameState, attacker_id: PlayerId) -> tuple[RegionId, ...]:
    """The single source of the adjacency rule: `turn.your_options` in Plan 3's
    projection is derived from this, never recomputed by the client."""
    mine = set(state.owned_by(attacker_id))
    reachable: set[RegionId] = set()
    for region_id in mine:
        reachable |= state.map.neighbours(region_id)
    return tuple(r for r in state.map.region_ids() if r in reachable and r not in mine)


def _open_battle_turn(
    state: GameState,
    attacker_id: PlayerId,
    ctx: DecisionContext,
) -> tuple[ev.GameEvent, ...]:
    if not legal_targets(state, attacker_id):
        skipped = ev.TurnSkipped(attacker_id, "no adjacent target")
        return (skipped, *_next_battle_turn(evolve(state, skipped), ctx))
    deadline, _ = state.allocate_deadline(
        DeadlineKind.TARGET_SELECT,
        ctx.now + timedelta(milliseconds=state.rules.answer_timeout_ms),
    )
    return (ev.TurnStarted(attacker_id, deadline),)


def _decide_target(
    state: GameState, turn: BattleTargetSelect, command: SelectAttackTarget, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    if command.actor_id != turn.attacker_id:
        raise RejectedCommand(RejectCode.NOT_YOUR_TURN, f"{turn.attacker_id!r} is attacking")
    if command.region_id not in state.territories:
        raise RejectedCommand(RejectCode.UNKNOWN_REGION, f"{command.region_id!r} is not on the map")
    target = state.territories[command.region_id]
    if target.owner_id == command.actor_id:
        raise RejectedCommand(RejectCode.OWN_TERRITORY, "cannot attack your own region")
    if command.region_id not in legal_targets(state, command.actor_id):
        raise RejectedCommand(RejectCode.NOT_ADJACENT, f"{command.region_id!r} is not adjacent")

    declared = ev.AttackDeclared(command.actor_id, target.owner_id, command.region_id)
    after = evolve(state, declared)
    question, _ = after.pool.next_multiple_choice()
    deadline, _ = after.allocate_deadline(
        DeadlineKind.ANSWER, ctx.now + timedelta(milliseconds=after.rules.answer_timeout_ms)
    )
    return (declared, ev.QuestionPresented(question, deadline))


def _decide_target_timeout(
    state: GameState, turn: BattleTargetSelect, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    skipped = ev.TurnSkipped(turn.attacker_id, "no target selected in time")
    return (skipped, *_next_battle_turn(evolve(state, skipped), ctx))


def _decide_neutral_answer(
    state: GameState, command: SubmitAnswer, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    turn = state.turn
    assert isinstance(turn, NeutralChallenge)
    if command.actor_id != turn.attacker_id:
        raise RejectedCommand(RejectCode.NOT_YOUR_TURN, f"{turn.attacker_id!r} is attacking")
    recorded = _record_answer(turn, command)
    if recorded is None:
        return ()
    after = evolve(state, recorded)
    assert isinstance(after.turn, NeutralChallenge)
    return (recorded, *_close_neutral_challenge(after, after.turn, ctx))


def _close_neutral_challenge(
    state: GameState, turn: NeutralChallenge, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    submitted = turn.answers.get(turn.attacker_id)
    correct_idx = turn.question.correct_choice_index()
    won = (
        submitted is not None
        and isinstance(submitted.value, ChoiceAnswer)
        and submitted.value.idx == correct_idx
    )
    resolved = ev.QuestionResolved(
        correct_choice_index=correct_idx,
        correct_value=None,
        ranking=(turn.attacker_id,),
        correct_players=(turn.attacker_id,) if won else (),
    )
    head: tuple[ev.GameEvent, ...] = (ev.AnswerWindowClosed(turn.deadline), resolved)

    if not won:
        failed = ev.NeutralAttackFailed(turn.region_id, turn.attacker_id)
        return (*head, failed, *_next_battle_turn(evolve(state, failed), ctx))

    captured = ev.NeutralTerritoryCaptured(turn.region_id, turn.attacker_id)
    after = evolve(state, captured)
    score = ev.ScoreChanged(
        turn.attacker_id,
        state.rules.pts_territory,
        ev.ScoreReason.TERRITORY,
        new_total=expected_score(after, turn.attacker_id),
    )
    after = evolve(after, score)
    return (*head, captured, score, *_next_battle_turn(after, ctx))


def _decide_duel_answer(
    state: GameState, turn: BattleDuel, command: SubmitAnswer, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    if command.actor_id not in (turn.attacker_id, turn.defender_id):
        raise RejectedCommand(
            RejectCode.NOT_YOUR_TURN, f"{command.actor_id!r} is not part of this duel"
        )
    recorded = _record_answer(turn, command)
    if recorded is None:
        return ()
    after = evolve(state, recorded)
    assert isinstance(after.turn, BattleDuel)
    if len(after.turn.answers) < 2:
        return (recorded,)
    return (recorded, *_close_duel(after, after.turn, ctx))


def _close_duel(
    state: GameState, turn: BattleDuel, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    correct_idx = turn.question.correct_choice_index()

    def is_right(player_id: PlayerId) -> bool:
        submitted = turn.answers.get(player_id)
        return (
            submitted is not None
            and isinstance(submitted.value, ChoiceAnswer)
            and submitted.value.idx == correct_idx
        )

    attacker_right, defender_right = is_right(turn.attacker_id), is_right(turn.defender_id)
    correct = tuple(p for p in (turn.attacker_id, turn.defender_id) if is_right(p))
    resolved = ev.QuestionResolved(correct_idx, None, (turn.attacker_id, turn.defender_id), correct)
    head: tuple[ev.GameEvent, ...] = (ev.AnswerWindowClosed(turn.deadline), resolved)

    if attacker_right and defender_right:
        started = ev.TiebreakStarted(turn.region_id)
        after = evolve(state, started)
        question, _ = after.pool.next_numeric()
        deadline, _ = after.allocate_deadline(
            DeadlineKind.ANSWER, ctx.now + timedelta(milliseconds=after.rules.answer_timeout_ms)
        )
        return (*head, started, ev.QuestionPresented(question, deadline))

    if attacker_right:
        won = ev.DuelResolved(turn.attacker_id)
        after = evolve(state, won)
        return (
            *head,
            won,
            *_resolve_capture(after, turn.attacker_id, turn.defender_id, turn.region_id, ctx),
        )

    if defender_right:
        won = ev.DuelResolved(turn.defender_id)
        held = ev.DefenseHeld(turn.region_id, turn.defender_id)
        after = fold(state, (won, held))
        score = ev.ScoreChanged(
            turn.defender_id,
            state.rules.pts_defense,
            ev.ScoreReason.DEFENSE,
            new_total=expected_score(after, turn.defender_id) + state.rules.pts_defense,
        )
        after = evolve(after, score)
        return (*head, won, held, score, *_next_battle_turn(after, ctx))

    nobody = ev.DuelResolved(None)
    return (*head, nobody, *_next_battle_turn(evolve(state, nobody), ctx))


def _decide_tiebreak_answer(
    state: GameState, turn: BattleTiebreak, command: SubmitAnswer, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    if command.actor_id not in (turn.attacker_id, turn.defender_id):
        raise RejectedCommand(
            RejectCode.NOT_YOUR_TURN, f"{command.actor_id!r} is not part of this tiebreak"
        )
    recorded = _record_answer(turn, command)
    if recorded is None:
        return ()
    after = evolve(state, recorded)
    assert isinstance(after.turn, BattleTiebreak)
    if len(after.turn.answers) < 2:
        return (recorded,)
    return (recorded, *_close_tiebreak(after, after.turn, ctx))


def _close_tiebreak(
    state: GameState, turn: BattleTiebreak, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    ranking = _rank_numeric(turn, state)
    resolved = ev.QuestionResolved(
        correct_choice_index=None,
        correct_value=turn.question.numeric_answer,
        ranking=ranking,
        correct_players=(),
    )
    head: tuple[ev.GameEvent, ...] = (ev.AnswerWindowClosed(turn.deadline), resolved)

    # The attacker wins only if they rank strictly first AND actually
    # answered — under mutual silence everyone ties and sorts by seat, which
    # would otherwise hand the attacker the region for free.
    attacker_wins = turn.attacker_id in turn.answers and ranking[0] == turn.attacker_id

    if attacker_wins:
        won = ev.DuelResolved(turn.attacker_id)
        after = evolve(state, won)
        return (
            *head,
            won,
            *_resolve_capture(after, turn.attacker_id, turn.defender_id, turn.region_id, ctx),
        )

    won = ev.DuelResolved(turn.defender_id)
    held = ev.DefenseHeld(turn.region_id, turn.defender_id)
    after = fold(state, (won, held))
    score = ev.ScoreChanged(
        turn.defender_id,
        state.rules.pts_defense,
        ev.ScoreReason.DEFENSE,
        new_total=expected_score(after, turn.defender_id) + state.rules.pts_defense,
    )
    after = evolve(after, score)
    return (*head, won, held, score, *_next_battle_turn(after, ctx))


@dataclass
class _Emitter:
    """Accumulates events while keeping `state` folded up to date."""

    state: GameState
    events: list[ev.GameEvent] = field(default_factory=list)

    def emit(self, *new_events: ev.GameEvent) -> None:
        for event in new_events:
            self.state = evolve(self.state, event)
            self.events.append(event)

    def score(self, player_id: PlayerId, delta: int, reason: ev.ScoreReason) -> None:
        """Emit a ScoreChanged whose new_total reflects the state after the delta."""
        bonus = delta if reason in (ev.ScoreReason.DEFENSE, ev.ScoreReason.BONUS) else 0
        new_total = expected_score(self.state, player_id) + bonus
        self.emit(ev.ScoreChanged(player_id, delta, reason, new_total))


def _resolve_capture(
    state: GameState,
    attacker_id: PlayerId,
    defender_id: PlayerId,
    region_id: RegionId,
    ctx: DecisionContext,
) -> tuple[ev.GameEvent, ...]:
    """A duel or tiebreak the attacker won, resolved into a capture."""
    territory = state.territories[region_id]
    rules = state.rules
    out = _Emitter(state)

    # A base with towers left absorbs the hit; the region does not change hands.
    if territory.kind is TerritoryKind.BASE and (territory.base_hp or 0) > 1:
        out.emit(ev.BaseDamaged(region_id, (territory.base_hp or 0) - 1))
        return (*out.events, *_next_battle_turn(out.state, ctx))

    old_value = holding_value(territory, rules)
    is_base = territory.kind is TerritoryKind.BASE

    if is_base:
        out.emit(
            ev.BaseDestroyed(region_id, defender_id),
            ev.TerritoryCaptured(region_id, defender_id, attacker_id, AcquisitionKind.BASE),
        )
        out.score(attacker_id, rules.pts_base, ev.ScoreReason.BASE)
        out.score(defender_id, -old_value, ev.ScoreReason.BASE_LOST)
        _eliminate(out, defender_id, keep_base=False)
    else:
        out.emit(
            ev.TerritoryCaptured(region_id, defender_id, attacker_id, AcquisitionKind.CONQUEST)
        )
        out.score(attacker_id, rules.pts_conquered, ev.ScoreReason.CONQUEST)
        out.score(defender_id, -old_value, ev.ScoreReason.TERRITORY_LOST)

    return (*out.events, *_next_battle_turn(out.state, ctx))


def _eliminate(out: _Emitter, player_id: PlayerId, *, keep_base: bool) -> None:
    """Eliminate a player and neutralize everything they still hold.

    The base that was just destroyed has already transferred to the attacker, so
    it is not in owned_by() any more. On surrender (keep_base=False as well) the
    player's own base is still theirs and neutralizes with the rest.
    Accumulated bonuses are never touched.
    """
    out.emit(ev.PlayerEliminated(player_id))
    for region_id in out.state.owned_by(player_id):
        value = holding_value(out.state.territories[region_id], out.state.rules)
        out.emit(ev.TerritoryNeutralized(region_id, player_id))
        out.score(player_id, -value, ev.ScoreReason.TERRITORY_LOST)


def _decide_surrender(
    state: GameState, command: Surrender, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    if state.phase is Phase.LOBBY:
        return (ev.PlayerLeft(command.actor_id),)

    # HUMAN RULING: a contender in a FinalTiebreak cannot eliminate themselves
    # by surrendering — that would be exploitable (surrender to hand the win
    # to a specific rival). `Surrender` stays in LEGAL_COMMANDS[FinalTiebreak]
    # (Task 10) so the command is accepted rather than rejected, but it is
    # silently dropped here: no events, no elimination.
    if isinstance(state.turn, FinalTiebreak):
        return ()

    out = _Emitter(state)
    out.emit(ev.PlayerSurrendered(command.actor_id))
    _eliminate(out, command.actor_id, keep_base=False)

    if _is_involved_in_turn(state.turn, command.actor_id):
        out.emit(ev.TurnAborted(f"{command.actor_id} surrendered"))
        return (*out.events, *_next_battle_turn(out.state, ctx))
    return tuple(out.events)


def _decide_abort(state: GameState, command: AbortGame) -> tuple[ev.GameEvent, ...]:
    return (ev.GameAborted(f"aborted by {command.actor_id}"),)


def _is_involved_in_turn(turn: Turn | None, player_id: PlayerId) -> bool:
    match turn:
        case BattleTargetSelect(attacker_id=a):
            return a == player_id
        case (
            BattleDuel(attacker_id=a, defender_id=d) | BattleTiebreak(attacker_id=a, defender_id=d)
        ):
            return player_id in (a, d)
        case NeutralChallenge(attacker_id=a):
            return a == player_id
        case _:
            return False


def _next_battle_turn(state: GameState, ctx: DecisionContext) -> tuple[ev.GameEvent, ...]:
    """Advance to the next attacker, the next round, or the end of the game.

    `state.last_attacker_id` is the rotation anchor: it is updated by both the
    `TurnStarted` and `TurnSkipped` `_apply` branches, so it advances on every
    single hop through this function — a skip chain can never recompute the
    same "next attacker" twice, which is what makes this recursion (via
    `_open_battle_turn` for an attacker with no legal target) provably
    terminating without needing to track visited attackers explicitly:
    the search position in `turn_order` strictly increases each call until
    it reaches the end, at which point the round is over.

    Rotation is anchored on `turn_order` (which retains eliminated players in
    their original seats) rather than the pre-filtered `active` list. `last`
    is the attacker whose turn just ended — including one who surrendered
    *during* their own open turn, in which case `last` is no longer active
    and would not appear in `active` at all. Anchoring on `turn_order` still
    finds the correct next active player after them; anchoring on `active`
    would silently skip straight to "round over" and cut every remaining
    player's turn short.

    One active player remaining also ends the game — before anything about
    turn shape or rotation is even looked at, since `state.turn` is typically
    already `None` at that point, cleared by the `DuelResolved` that preceded
    the capture.
    """
    active = state.active_players()
    if len(active) <= 1:
        return _finish(state, ctx)

    order = state.turn_order
    last = state.last_attacker_id
    start = order.index(last) + 1 if last in order else len(order)
    nxt = next((p for p in order[start:] if not state.players[p].is_eliminated), None)
    if nxt is not None:
        return _open_battle_turn(state, nxt, ctx)

    completed = ev.BattleRoundCompleted(state.round_no)
    after = evolve(state, completed)
    if after.round_no >= after.rules.battle_rounds:
        return (completed, *_finish(after, ctx))
    started = ev.BattleRoundStarted(after.round_no + 1)
    after = evolve(after, started)
    return (completed, started, *_open_battle_turn(after, after.active_players()[0], ctx))


def _finish(state: GameState, ctx: DecisionContext) -> tuple[ev.GameEvent, ...]:
    """Compare scores among non-eliminated players and end the game.

    Deliberately keyed off `state.players` (filtered by `is_eliminated`)
    rather than `state.active_players()`: winner determination is a property
    of who is still in the game, not of `turn_order`'s rotation bookkeeping.
    """
    final_scores = {p: s.score for p, s in state.players.items()}
    scores = {p: s.score for p, s in state.players.items() if not s.is_eliminated}
    if not scores:
        return (ev.GameFinished(None, final_scores),)

    best = max(scores.values())
    leaders = tuple(p for p, s in scores.items() if s == best)
    if len(leaders) == 1:
        return (ev.GameFinished(leaders[0], final_scores),)

    started = ev.FinalTiebreakStarted(leaders)
    after = evolve(state, started)
    question, _ = after.pool.next_numeric()
    deadline, _ = after.allocate_deadline(
        DeadlineKind.ANSWER, ctx.now + timedelta(milliseconds=after.rules.answer_timeout_ms)
    )
    return (started, ev.QuestionPresented(question, deadline))


def _decide_final_tiebreak_answer(
    state: GameState, turn: FinalTiebreak, command: SubmitAnswer, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    if command.actor_id not in turn.contenders:
        raise RejectedCommand(
            RejectCode.NOT_YOUR_TURN, f"{command.actor_id!r} is not part of this tiebreak"
        )
    recorded = _record_answer(turn, command)
    if recorded is None:
        return ()
    after = evolve(state, recorded)
    assert isinstance(after.turn, FinalTiebreak)
    if len(after.turn.answers) < len(after.turn.contenders):
        return (recorded,)
    return (recorded, *_close_final_tiebreak(after, after.turn, ctx))


def _close_final_tiebreak(
    state: GameState, turn: FinalTiebreak, ctx: DecisionContext
) -> tuple[ev.GameEvent, ...]:
    ranking = _rank_numeric(turn, state)
    resolved = ev.QuestionResolved(
        correct_choice_index=None,
        correct_value=turn.question.numeric_answer,
        ranking=ranking,
        correct_players=(),
    )
    finished = ev.GameFinished(ranking[0], {p: s.score for p, s in state.players.items()})
    return (ev.AnswerWindowClosed(turn.deadline), resolved, finished)


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

        case ev.PlayerLeft(player_id=pid):
            # Only reachable from a LOBBY surrender (`_decide_surrender`'s
            # `Phase.LOBBY` branch) — the player never got far enough to hold
            # a base, a score, or a seat in an open turn, so undoing
            # `PlayerJoined` is exactly its inverse: drop them from both
            # `players` and `turn_order`. NOTE: seats are not renumbered, so
            # a later `JoinGame` (`seat=len(players)`) can re-mint a seat
            # number still held by a remaining player — a pre-existing gap
            # in seat allocation, not introduced or fixed here; out of scope
            # for this arm.
            return replace(
                state,
                players={p: s for p, s in state.players.items() if p != pid},
                turn_order=tuple(p for p in state.turn_order if p != pid),
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
            # `order` is the round's fixed rank order; the picker due next is
            # the earliest-ranked player who still has a grant and hasn't
            # been eliminated since the grant was computed (surrender during
            # EXPANSION never revisits the open turn, so `grants` can carry a
            # stale positive entry for a player eliminated after this event
            # was decided). Re-grants (Task 13) reuse this same event/branch
            # mid-round, once some entries in `order` are already exhausted,
            # so this cannot simply take `order[0]`.
            #
            # Delegate to `_next_picker` rather than re-deriving the same
            # rule here: it's the function decide() itself used (on the same
            # state, by construction — `state` is this event's predecessor
            # state, threaded through unchanged or via events already folded
            # earlier in the same batch) to decide whether to emit this event
            # at all, so decide-time and apply-time can never disagree about
            # who picks next.
            current_picker = _next_picker(order, grants, state)
            assert current_picker is not None, (
                "PicksGranted is only emitted once _next_picker confirms a "
                "candidate exists; state must have changed between decide() "
                "and apply() for this to fire"
            )
            return replace(
                state,
                next_deadline_id=max(state.next_deadline_id, deadline.id + 1),
                turn=ExpansionPicking(deadline, order, dict(grants), current_picker),
            )

        case ev.TerritoryClaimed(player_id=pid, region_id=rid, acquisition=acq):
            territory = replace(state.territories[rid], owner_id=pid, acquisition=acq)
            return replace(state, territories={**state.territories, rid: territory})

        case ev.ExpansionRoundCompleted():
            return replace(state, turn=None)

        case ev.BattleRoundStarted(round_no=round_no):
            return replace(state, phase=Phase.BATTLE, round_no=round_no, turn=None)

        case ev.TurnStarted(attacker_id=attacker, deadline=deadline):
            return replace(
                state,
                next_deadline_id=max(state.next_deadline_id, deadline.id + 1),
                turn=BattleTargetSelect(deadline, attacker),
                last_attacker_id=attacker,
            )

        case ev.TurnSkipped(attacker_id=attacker):
            # `turn` itself is left untouched (still the just-skipped
            # `BattleTargetSelect`, or unrelated) — only the rotation anchor
            # advances, which is all `_next_battle_turn` needs to find the
            # next active attacker.
            return replace(state, last_attacker_id=attacker)

        case ev.AttackDeclared():
            # `turn` stays the BattleTargetSelect it already was — the following
            # `QuestionPresented` reads this back to build the BattleDuel or
            # NeutralChallenge turn shape.
            return replace(state, pending_attack=event)

        case ev.NeutralTerritoryCaptured(region_id=rid, player_id=pid):
            territory = replace(
                state.territories[rid], owner_id=pid, acquisition=AcquisitionKind.CLAIMED
            )
            return replace(state, territories={**state.territories, rid: territory}, turn=None)

        case ev.NeutralAttackFailed():
            return replace(state, turn=None)

        case ev.DuelResolved():
            # Win, loss, or draw, the duel/tiebreak window is over. Whatever
            # comes next (a capture, a held defense, or nothing) starts from
            # a clean slate — `_next_battle_turn` seeing `turn=None` here is
            # exactly why it returns `()` on every path through `_close_duel`
            # and `_close_tiebreak`; opening the next attacker's turn is
            # Task 18's job.
            return replace(state, turn=None)

        case ev.DefenseHeld():
            # The territory already belongs to the defender; nothing about
            # ownership changes. `DuelResolved` already cleared `turn`.
            return state

        case ev.TiebreakStarted():
            # `turn` is left as the just-resolved `BattleDuel` — attacker_id,
            # defender_id and region_id are read back off it by
            # `_present_question` when the numeric question that follows
            # reshapes `turn` into a `BattleTiebreak`.
            return state

        case ev.TerritoryCaptured(region_id=rid, to_player_id=pid, acquisition=acq):
            territory = replace(state.territories[rid], owner_id=pid, acquisition=acq)
            return replace(state, territories={**state.territories, rid: territory})

        case ev.BaseDamaged(region_id=rid, hp_remaining=hp):
            territory = replace(state.territories[rid], base_hp=hp)
            return replace(state, territories={**state.territories, rid: territory})

        case ev.BaseDestroyed(region_id=rid):
            # Ownership itself moves via the `TerritoryCaptured` that follows
            # this event; here only the base's own identity is torn down.
            territory = replace(
                state.territories[rid], kind=TerritoryKind.NORMAL, base_owner_id=None, base_hp=None
            )
            return replace(state, territories={**state.territories, rid: territory})

        case ev.PlayerEliminated(player_id=pid):
            player = state.players[pid]
            return replace(
                state,
                players={
                    **state.players,
                    pid: replace(player, is_eliminated=True, base_region=None),
                },
            )

        case ev.TerritoryNeutralized(region_id=rid):
            territory = replace(state.territories[rid], owner_id=None, acquisition=None)
            return replace(state, territories={**state.territories, rid: territory})

        case ev.BattleRoundCompleted():
            # `round_no` itself only advances on the `BattleRoundStarted` that
            # follows when there is a next round; this event is purely a
            # boundary marker.
            return state

        case ev.FinalTiebreakStarted(contenders=contenders):
            return replace(state, turn=None, pending_final_contenders=contenders)

        case ev.GameFinished(winner_id=winner_id):
            return replace(state, phase=Phase.FINISHED, winner_id=winner_id, turn=None)

        case ev.PlayerSurrendered():
            # No state change of its own — the `PlayerEliminated` that always
            # follows it does the work.
            return state

        case ev.TurnAborted():
            return replace(state, turn=None)

        case ev.GameAborted():
            return replace(state, phase=Phase.ABORTED, turn=None, winner_id=None)

    # Every `ev.X(...)` construction site in this module (i.e. every event
    # `decide()` can actually emit) has a `case` above — cross-checked by
    # grepping every emission site against this match's arms; `PlayerLeft`
    # was missing that arm until a review caught `fold()` crashing on a real
    # lobby-surrender trajectory (`decide()` emits it, `_apply` had no case).
    # The one `GameEvent` member with no arm, `GameCreated`, is never
    # constructed anywhere in `decide()`/`_dispatch` — the initial `GameState`
    # is materialised directly by whatever constructs it (`lobby_state()`
    # here; presumably a runtime-level "create game" step in Plan 2), never
    # folded from a `GameCreated` event. So this fallthrough is unreachable
    # for any event sequence `decide()` could have produced; it only fires if
    # `evolve`/`fold` is handed a fabricated or foreign event directly.
    raise NotImplementedError(f"no evolve branch for {type(event).__name__}")  # pragma: no cover


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
    if state.phase is Phase.BATTLE:
        turn: Turn
        if state.pending_final_contenders:
            turn = FinalTiebreak(deadline, state.pending_final_contenders, question, answers={})
            return replace(base, turn=turn, pending_final_contenders=())
        attack = state.pending_attack
        if attack is not None:
            if attack.defender_id is not None:
                turn = BattleDuel(
                    deadline,
                    attack.attacker_id,
                    attack.defender_id,
                    attack.region_id,
                    question,
                    answers={},
                )
            else:
                turn = NeutralChallenge(
                    deadline, attack.attacker_id, attack.region_id, question, answers={}
                )
            return replace(base, turn=turn, pending_attack=None)
        # No pending attack: this question window continues a duel that just
        # tied, reshaping `turn` from the resolved `BattleDuel` (or a chained
        # `BattleTiebreak`, on another tie) into a fresh numeric tiebreak that
        # only the two original combatants play.
        current = state.turn
        assert isinstance(current, BattleDuel | BattleTiebreak), (
            "battle question window opened without a declared attack or a pending tiebreak"
        )
        turn = BattleTiebreak(
            deadline,
            current.attacker_id,
            current.defender_id,
            current.region_id,
            question,
            answers={},
        )
        return replace(base, turn=turn)
    raise NotImplementedError(f"no question window shape for phase {state.phase}")


def fold(state: GameState, events: Iterable[ev.GameEvent]) -> GameState:
    for event in events:
        state = evolve(state, event)
    return state
