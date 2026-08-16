import pytest

from triviador.domain.game.actions import (
    WINDOWED_COMMANDS,
    AbortGame,
    ExpireDeadline,
    JoinGame,
    PickRegion,
    RejectCode,
    RejectedCommand,
    SelectAttackTarget,
    SubmitAnswer,
    Surrender,
)
from triviador.domain.game.state import ChoiceAnswer
from triviador.domain.ids import DeadlineId, PlayerId, RegionId


def test_windowed_commands_all_carry_a_deadline_id() -> None:
    assert (SubmitAnswer, PickRegion, SelectAttackTarget, ExpireDeadline) == WINDOWED_COMMANDS
    for cls in WINDOWED_COMMANDS:
        assert "deadline_id" in cls.__dataclass_fields__


def test_non_windowed_commands_do_not_carry_one() -> None:
    for cls in (JoinGame, Surrender, AbortGame):
        assert "deadline_id" not in cls.__dataclass_fields__


def test_rejected_command_exposes_its_code() -> None:
    error = RejectedCommand(RejectCode.NOT_ADJACENT, "region R9 is not adjacent")
    assert error.code is RejectCode.NOT_ADJACENT
    assert "R9" in str(error)


def test_commands_compare_by_value_for_idempotency_checks() -> None:
    a = SubmitAnswer(PlayerId("p1"), DeadlineId(4), ChoiceAnswer(2), elapsed_ms=900)
    b = SubmitAnswer(PlayerId("p1"), DeadlineId(4), ChoiceAnswer(2), elapsed_ms=900)
    assert a == b


def test_rejected_command_is_an_exception() -> None:
    with pytest.raises(RejectedCommand):
        raise RejectedCommand(RejectCode.WRONG_TURN_STATE, "nope")


def test_pick_and_target_carry_a_region() -> None:
    assert PickRegion(PlayerId("p1"), DeadlineId(1), RegionId("a")).region_id == RegionId("a")
    assert SelectAttackTarget(PlayerId("p1"), DeadlineId(1), RegionId("b")).region_id == RegionId(
        "b"
    )
