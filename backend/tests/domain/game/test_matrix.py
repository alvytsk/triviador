"""Spec §6.3 as an executable artifact. 10 turn states x 8 commands = 80 cells.

`test_the_matrix_is_complete` doesn't just check MATRIX's own shape — it
cross-references MATRIX's row and column labels against the live `Turn` and
`Command` unions (via `TURN_ROWS`/`COMMAND_COLUMNS` below), so adding or
removing a turn variant or a command type turns this red before anyone
remembers to touch this file, not just after.
"""

from datetime import timedelta
from typing import get_args

import pytest

from tests.conftest import NOW, lobby_state
from tests.domain.game.conftest import CommandBuilder, States
from tests.domain.game.test_start import start_ctx
from triviador.domain.game.actions import (
    AbortGame,
    Command,
    DecisionContext,
    ExpireDeadline,
    JoinGame,
    PickRegion,
    RejectedCommand,
    SelectAttackTarget,
    StartGame,
    SubmitAnswer,
    Surrender,
)
from triviador.domain.game.reducer import decide
from triviador.domain.game.state import (
    TERMINAL_PHASES,
    BattleDuel,
    BattleTargetSelect,
    BattleTiebreak,
    ExpansionPicking,
    ExpansionQuestion,
    FinalTiebreak,
    NeutralChallenge,
    Turn,
)

ACCEPT, IGNORE, REJECT = "accept", "ignore", "reject"

LATE_CTX = DecisionContext(now=NOW + timedelta(hours=1))

MATRIX: dict[str, dict[str, str]] = {
    "lobby": {
        "join": ACCEPT,
        "start": ACCEPT,
        "answer": IGNORE,
        "pick": IGNORE,
        "target": IGNORE,
        "expire": IGNORE,
        "surrender": ACCEPT,
        "abort": ACCEPT,
    },
    "expansion_question": {
        "join": REJECT,
        "start": REJECT,
        "answer": ACCEPT,
        "pick": REJECT,
        "target": REJECT,
        "expire": ACCEPT,
        "surrender": ACCEPT,
        "abort": ACCEPT,
    },
    "expansion_picking": {
        "join": REJECT,
        "start": REJECT,
        "answer": REJECT,
        "pick": ACCEPT,
        "target": REJECT,
        "expire": ACCEPT,
        "surrender": ACCEPT,
        "abort": ACCEPT,
    },
    "battle_target": {
        "join": REJECT,
        "start": REJECT,
        "answer": REJECT,
        "pick": REJECT,
        "target": ACCEPT,
        "expire": ACCEPT,
        "surrender": ACCEPT,
        "abort": ACCEPT,
    },
    "battle_duel": {
        "join": REJECT,
        "start": REJECT,
        "answer": ACCEPT,
        "pick": REJECT,
        "target": REJECT,
        "expire": ACCEPT,
        "surrender": ACCEPT,
        "abort": ACCEPT,
    },
    "battle_tiebreak": {
        "join": REJECT,
        "start": REJECT,
        "answer": ACCEPT,
        "pick": REJECT,
        "target": REJECT,
        "expire": ACCEPT,
        "surrender": ACCEPT,
        "abort": ACCEPT,
    },
    "neutral_challenge": {
        "join": REJECT,
        "start": REJECT,
        "answer": ACCEPT,
        "pick": REJECT,
        "target": REJECT,
        "expire": ACCEPT,
        "surrender": ACCEPT,
        "abort": ACCEPT,
    },
    "final_tiebreak": {
        "join": REJECT,
        "start": REJECT,
        "answer": ACCEPT,
        "pick": REJECT,
        "target": REJECT,
        "expire": ACCEPT,
        "surrender": IGNORE,
        "abort": ACCEPT,
    },
    "finished": {
        "join": REJECT,
        "start": REJECT,
        "answer": IGNORE,
        "pick": IGNORE,
        "target": IGNORE,
        "expire": IGNORE,
        "surrender": IGNORE,
        "abort": REJECT,
    },
    "aborted": {
        "join": REJECT,
        "start": REJECT,
        "answer": IGNORE,
        "pick": IGNORE,
        "target": IGNORE,
        "expire": IGNORE,
        "surrender": IGNORE,
        "abort": REJECT,
    },
}


# MATRIX's row labels for every non-terminal turn shape `decide` switches on
# (`None` meaning "no open turn", i.e. the lobby) — this is the one place a
# new `Turn` variant must be named, so `test_the_matrix_is_complete` fails the
# moment `Turn` gains a member this dict doesn't know about yet.
TURN_ROWS: dict[type[Turn] | None, str] = {
    None: "lobby",
    ExpansionQuestion: "expansion_question",
    ExpansionPicking: "expansion_picking",
    BattleTargetSelect: "battle_target",
    BattleDuel: "battle_duel",
    BattleTiebreak: "battle_tiebreak",
    NeutralChallenge: "neutral_challenge",
    FinalTiebreak: "final_tiebreak",
}

# MATRIX's remaining two rows are terminal *phases*, not turn shapes: guard 1
# in `decide` short-circuits on `state.phase in TERMINAL_PHASES` before `Turn`
# ever enters the picture, so `finished`/`aborted` are cross-referenced
# against `TERMINAL_PHASES` instead of `Turn`.
TERMINAL_ROWS = {"finished", "aborted"}

# MATRIX's column labels, one per `Command` union member.
COMMAND_COLUMNS: dict[type[Command], str] = {
    JoinGame: "join",
    StartGame: "start",
    SubmitAnswer: "answer",
    PickRegion: "pick",
    SelectAttackTarget: "target",
    ExpireDeadline: "expire",
    Surrender: "surrender",
    AbortGame: "abort",
}


def test_the_matrix_is_complete() -> None:
    assert len(MATRIX) == 10
    assert all(len(row) == 8 for row in MATRIX.values())
    assert sum(len(row) for row in MATRIX.values()) == 80

    # Cross-reference against the live domain types, not just MATRIX's own
    # internal shape: this is what makes a new `Turn` variant or `Command`
    # type turn the suite red instead of silently going unchecked.
    live_turns = set(get_args(Turn)) | {None}
    named_turns = set(TURN_ROWS)
    assert live_turns == named_turns, (
        f"Turn union and TURN_ROWS disagree: "
        f"missing from TURN_ROWS={live_turns - named_turns}, "
        f"stale in TURN_ROWS={named_turns - live_turns}"
    )
    assert set(TURN_ROWS.values()) | TERMINAL_ROWS == set(MATRIX)

    terminal_phase_names = {phase.value for phase in TERMINAL_PHASES}
    assert terminal_phase_names == TERMINAL_ROWS, (
        f"TERMINAL_PHASES and TERMINAL_ROWS disagree: "
        f"missing from TERMINAL_ROWS={terminal_phase_names - TERMINAL_ROWS}, "
        f"stale in TERMINAL_ROWS={TERMINAL_ROWS - terminal_phase_names}"
    )

    live_commands = set(get_args(Command))
    named_commands = set(COMMAND_COLUMNS)
    assert live_commands == named_commands, (
        f"Command union and COMMAND_COLUMNS disagree: "
        f"missing from COMMAND_COLUMNS={live_commands - named_commands}, "
        f"stale in COMMAND_COLUMNS={named_commands - live_commands}"
    )
    column_names = set(COMMAND_COLUMNS.values())
    assert all(set(row) == column_names for row in MATRIX.values())


@pytest.mark.parametrize("turn_name", sorted(MATRIX))
@pytest.mark.parametrize(
    "command_name", ["join", "start", "answer", "pick", "target", "expire", "surrender", "abort"]
)
def test_cell(
    turn_name: str, command_name: str, states: States, commands: dict[str, CommandBuilder]
) -> None:
    expected = MATRIX[turn_name][command_name]
    state = states[turn_name]
    ctx = LATE_CTX if command_name == "expire" else states.ctx

    # The lobby row's `join` and `start` cells need fixtures the shared lobby
    # state can't supply at the same time: `join` needs room (the shared
    # lobby is full at 3/3) and `start` needs a context that actually carries
    # a drawn pool, player order and base assignment (states.ctx doesn't).
    # Every other lobby-row cell is indifferent to fullness or to ctx's extra
    # fields, so the shared fixtures stay as-is for them.
    if turn_name == "lobby" and command_name == "join":
        state = lobby_state(players={"p1": 0, "p2": 1})
    elif turn_name == "lobby" and command_name == "start":
        ctx = start_ctx()

    command = commands[command_name](state)

    if expected == IGNORE:
        assert decide(state, command, ctx) == ()
    elif expected == REJECT:
        with pytest.raises(RejectedCommand):
            decide(state, command, ctx)
    else:
        assert decide(state, command, ctx) != ()
