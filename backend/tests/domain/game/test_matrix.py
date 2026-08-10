"""Spec §6.3 as an executable artifact. 10 turn states x 8 commands = 80 cells."""

from datetime import timedelta

import pytest

from tests.conftest import NOW, lobby_state
from tests.domain.game.conftest import CommandBuilder, States
from tests.domain.game.test_start import start_ctx
from triviador.domain.game.actions import DecisionContext, RejectedCommand
from triviador.domain.game.reducer import decide

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


def test_the_matrix_is_complete() -> None:
    assert len(MATRIX) == 10
    assert all(len(row) == 8 for row in MATRIX.values())
    assert sum(len(row) for row in MATRIX.values()) == 80


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
