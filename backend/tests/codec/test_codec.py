"""The event codec: encode/decode round trips, and the four traps called out
in the task brief (Decimal-as-string, the UTC invariant, the undiscriminated
AnswerValue union, and the upcaster chain).

Pure and PostgreSQL-free: no `integration` marker, no asyncio marks.
"""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import pytest
from hypothesis import given
from hypothesis import strategies as st

from triviador.db.codec.codec import decode, encode
from triviador.db.codec.registry import CURRENT_VERSION
from triviador.db.codec.upcasters import _compose
from triviador.db.errors import NaiveDatetime, UnknownEventType, UnknownSchemaVersion
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
    GameEvent,
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
    ScoreReason,
    TerritoryCaptured,
    TerritoryClaimed,
    TerritoryNeutralized,
    TiebreakStarted,
    TurnAborted,
    TurnSkipped,
    TurnStarted,
)
from triviador.domain.game.rules import DEFAULT_RULES
from triviador.domain.game.state import (
    AcquisitionKind,
    ChoiceAnswer,
    Deadline,
    DeadlineKind,
    NumericAnswer,
    SubmittedAnswer,
)
from triviador.domain.ids import (
    CategoryId,
    DeadlineId,
    MapId,
    MediaAssetId,
    PlayerId,
    QuestionId,
    RegionId,
)
from triviador.domain.questions.types import (
    CategorySnapshot,
    ChoiceSnapshot,
    Difficulty,
    QuestionKind,
    QuestionPool,
    QuestionSnapshot,
)

NOW = datetime(2026, 8, 16, 12, 0, tzinfo=UTC)
CATEGORY = CategorySnapshot(CategoryId("c1"), "general", "General")


def a_deadline(deadline_id: int = 1, kind: DeadlineKind = DeadlineKind.ANSWER) -> Deadline:
    return Deadline(DeadlineId(deadline_id), kind, NOW)


def a_mc_question() -> QuestionSnapshot:
    return QuestionSnapshot(
        question_id=QuestionId("q1"),
        version=1,
        kind=QuestionKind.MULTIPLE_CHOICE,
        prompt="prompt?",
        category=CATEGORY,
        difficulty=Difficulty.EASY,
        choices=(
            ChoiceSnapshot(0, "a", True, None),
            ChoiceSnapshot(1, "b", False, MediaAssetId("m1")),
        ),
        numeric_answer=None,
        unit=None,
        media_asset_id=None,
    )


def a_numeric_question() -> QuestionSnapshot:
    return QuestionSnapshot(
        question_id=QuestionId("q2"),
        version=1,
        kind=QuestionKind.NUMERIC,
        prompt="num?",
        category=CATEGORY,
        difficulty=Difficulty.HARD,
        choices=None,
        numeric_answer=Decimal("3.14"),
        unit="kg",
        media_asset_id=None,
    )


def a_pool() -> QuestionPool:
    return QuestionPool(
        numeric=(a_numeric_question(),),
        multiple_choice=(a_mc_question(),),
        numeric_used=1,
        mc_used=0,
    )


# One constructed instance per `GameEvent` union member. A new event type
# must be given a sample here before it can be merged — this table, plus
# `test_wire_names.py`'s exhaustiveness check, is what keeps that honest.
SAMPLE_EVENTS: tuple[GameEvent, ...] = (
    GameCreated(MapId("m1"), DEFAULT_RULES, PlayerId("p1"), "a" * 64),
    PlayerJoined(PlayerId("p1"), "Alice", 0),
    PlayerLeft(PlayerId("p1")),
    GameStarted((PlayerId("p1"), PlayerId("p2"))),
    BasesAssigned({PlayerId("p1"): RegionId("r1"), PlayerId("p2"): RegionId("r2")}),
    QuestionPoolDrawn(a_pool()),
    MediaWarmupStarted(a_deadline(kind=DeadlineKind.WARMUP)),
    GameFinished(PlayerId("p1"), {PlayerId("p1"): 100, PlayerId("p2"): 50}),
    GameAborted("host left"),
    QuestionPresented(a_mc_question(), a_deadline()),
    AnswerSubmitted(PlayerId("p1"), SubmittedAnswer(ChoiceAnswer(idx=0), elapsed_ms=1200)),
    AnswerWindowClosed(a_deadline()),
    QuestionResolved(0, None, (PlayerId("p1"), PlayerId("p2")), (PlayerId("p1"),)),
    ExpansionRoundStarted(1),
    PicksGranted(
        (PlayerId("p1"), PlayerId("p2")),
        {PlayerId("p1"): 2, PlayerId("p2"): 1},
        a_deadline(kind=DeadlineKind.PICK),
    ),
    TerritoryClaimed(PlayerId("p1"), RegionId("r1"), AcquisitionKind.CLAIMED, False),
    ExpansionRoundCompleted(1),
    BattleRoundStarted(1),
    TurnStarted(PlayerId("p1"), a_deadline(kind=DeadlineKind.TARGET_SELECT)),
    TurnSkipped(PlayerId("p1"), "timed out"),
    TurnAborted("player surrendered"),
    AttackDeclared(PlayerId("p1"), PlayerId("p2"), RegionId("r3")),
    DuelResolved(PlayerId("p1")),
    TiebreakStarted(RegionId("r3")),
    TerritoryCaptured(RegionId("r3"), PlayerId("p2"), PlayerId("p1"), AcquisitionKind.CONQUEST),
    NeutralTerritoryCaptured(RegionId("r4"), PlayerId("p1")),
    NeutralAttackFailed(RegionId("r4"), PlayerId("p1")),
    DefenseHeld(RegionId("r3"), PlayerId("p2")),
    BaseDamaged(RegionId("r5"), 2),
    BaseDestroyed(RegionId("r5"), PlayerId("p2")),
    BattleRoundCompleted(1),
    ScoreChanged(PlayerId("p1"), 200, ScoreReason.TERRITORY, 400),
    PlayerEliminated(PlayerId("p2")),
    PlayerSurrendered(PlayerId("p2")),
    TerritoryNeutralized(RegionId("r6"), PlayerId("p2")),
    FinalTiebreakStarted((PlayerId("p1"), PlayerId("p3"))),
)


def test_sample_table_covers_every_event_type() -> None:
    from typing import get_args

    covered = {type(e) for e in SAMPLE_EVENTS}
    assert covered == set(get_args(GameEvent))


@pytest.mark.parametrize("event", SAMPLE_EVENTS, ids=lambda e: type(e).__name__)
def test_every_event_type_round_trips(event: GameEvent) -> None:
    wire_type, version, payload = encode(event)
    decoded = decode(wire_type, version, payload)
    assert decoded == event
    assert type(decoded) is type(event)


def test_decimal_survives_as_a_string() -> None:
    """A numeric answer of 0.1 through an IEEE double is a wrong answer."""
    event = AnswerSubmitted(PlayerId("p1"), SubmittedAnswer(NumericAnswer(Decimal("0.1")), 500))
    _wire_type, _version, payload = encode(event)
    assert payload["answer"]["value"]["value"] == "0.1"
    assert isinstance(payload["answer"]["value"]["value"], str)
    decoded = decode(*encode(event))
    assert isinstance(decoded, AnswerSubmitted)
    assert isinstance(decoded.answer.value, NumericAnswer)
    assert decoded.answer.value.value == Decimal("0.1")


def test_datetime_is_iso_8601_utc_and_aware() -> None:
    """Deadlines are absolute (ADR-001) and compared across a restart."""
    event = MediaWarmupStarted(a_deadline(kind=DeadlineKind.WARMUP))
    _wire_type, _version, payload = encode(event)
    raw = payload["deadline"]["deadline_at"]
    assert raw == "2026-08-16T12:00:00Z"
    decoded = decode(*encode(event))
    assert isinstance(decoded, MediaWarmupStarted)
    assert decoded.deadline.deadline_at.tzinfo is not None
    assert decoded.deadline.deadline_at == NOW


def test_encoding_a_naive_datetime_raises() -> None:
    """Pydantic accepts a naive `Deadline(deadline_at=...)` happily, which is
    the problem: a naive deadline persisted here is compared against an aware
    `now` after a restart and either crashes or silently means the wrong
    instant."""
    naive_deadline = Deadline(DeadlineId(1), DeadlineKind.WARMUP, datetime(2026, 8, 16, 12, 0))
    event = MediaWarmupStarted(naive_deadline)
    with pytest.raises(NaiveDatetime):
        encode(event)


def test_decoding_a_payload_without_an_offset_raises() -> None:
    """`"2026-08-16T12:00:00"` decodes to a naive datetime under Pydantic.
    This is the read-side half, for rows written by an older or buggier
    version."""
    payload = {"deadline": {"id": 1, "kind": "warmup", "deadline_at": "2026-08-16T12:00:00"}}
    with pytest.raises(NaiveDatetime):
        decode("game.media_warmup_started", 1, payload)


def test_a_non_utc_offset_is_normalized_to_utc() -> None:
    """`+02:00` in, UTC out, same instant."""
    payload = {"deadline": {"id": 1, "kind": "warmup", "deadline_at": "2026-08-16T14:00:00+02:00"}}
    decoded = decode("game.media_warmup_started", 1, payload)
    assert isinstance(decoded, MediaWarmupStarted)
    assert decoded.deadline.deadline_at.tzinfo is UTC
    assert decoded.deadline.deadline_at == NOW


def test_a_utc_offset_from_json_is_also_normalized_to_a_real_utc_tzinfo() -> None:
    """Pydantic parses a `Z`-suffixed string into its own `TzInfo(0)`, not the
    stdlib `datetime.UTC` singleton — `== ` would pass on that alone, so this
    asserts identity, matching what `test_a_non_utc_offset_is_normalized_to_utc`
    checks for an actual offset."""
    payload = {"deadline": {"id": 1, "kind": "warmup", "deadline_at": "2026-08-16T12:00:00Z"}}
    decoded = decode("game.media_warmup_started", 1, payload)
    assert isinstance(decoded, MediaWarmupStarted)
    assert decoded.deadline.deadline_at.tzinfo is UTC


def test_answer_value_union_decodes_to_the_right_variant() -> None:
    """`ChoiceAnswer(idx=0)` and `NumericAnswer(Decimal(0))` are structurally
    distinct but both are bare dataclasses in an undiscriminated union."""
    choice_event = AnswerSubmitted(PlayerId("p1"), SubmittedAnswer(ChoiceAnswer(idx=2), 100))
    numeric_event = AnswerSubmitted(
        PlayerId("p1"), SubmittedAnswer(NumericAnswer(Decimal("0")), 100)
    )

    decoded_choice = decode(*encode(choice_event))
    decoded_numeric = decode(*encode(numeric_event))

    assert isinstance(decoded_choice, AnswerSubmitted)
    assert isinstance(decoded_numeric, AnswerSubmitted)
    assert type(decoded_choice.answer.value) is ChoiceAnswer
    assert type(decoded_numeric.answer.value) is NumericAnswer


@pytest.mark.parametrize("event", SAMPLE_EVENTS, ids=lambda e: type(e).__name__)
def test_payload_is_json_serializable(event: GameEvent) -> None:
    """`json.dumps(payload)` must succeed with no `default=` hook: this is
    what JSONB will receive, and a stray Decimal or datetime object would
    only fail at insert time."""
    _wire_type, _version, payload = encode(event)
    assert isinstance(json.dumps(payload), str)


def test_unknown_wire_type_raises() -> None:
    with pytest.raises(UnknownEventType):
        decode("nonexistent.type", 1, {})


def test_unknown_schema_version_raises() -> None:
    assert CURRENT_VERSION["game.created"] == 1
    with pytest.raises(UnknownSchemaVersion):
        decode(
            "game.created",
            2,
            {"map_id": "m1", "rules": {}, "host_id": "p1", "map_sha256": "a" * 64},
        )


# --- the upcaster chain, against a test-local synthetic registry -----------
#
# Production `UPCASTERS` is empty at v1 — nothing has been renamed, retyped,
# or removed from a real event yet — so running the composition machinery
# against it proves nothing about the loop, the missing-step guard, or the
# above-current guard. This table is a synthetic three-version event that
# exists only in this test file: v1 -> v2 renames a field, v2 -> v3 adds one
# with a default. It is never wired into the real registry, so it cannot be
# mistaken by anyone (or by Plan 4's recovery tooling) for a real version
# bump on a real event.

WIDGET = "test.synthetic_widget"


def _rename_field_v1_to_v2(payload: dict[str, Any]) -> dict[str, Any]:
    payload = dict(payload)
    payload["label"] = payload.pop("name")
    return payload


def _add_priority_v2_to_v3(payload: dict[str, Any]) -> dict[str, Any]:
    return {**payload, "priority": 0}


SYNTHETIC_UPCASTERS = {
    (WIDGET, 1): _rename_field_v1_to_v2,
    (WIDGET, 2): _add_priority_v2_to_v3,
}
SYNTHETIC_CURRENT_VERSION = {WIDGET: 3}


def test_upcast_chain_composes_a_v1_payload_to_the_current_shape() -> None:
    chain = _compose(SYNTHETIC_UPCASTERS, SYNTHETIC_CURRENT_VERSION, WIDGET, 1)
    assert chain({"name": "foo"}) == {"label": "foo", "priority": 0}


def test_upcast_chain_is_a_no_op_when_already_current() -> None:
    chain = _compose(SYNTHETIC_UPCASTERS, SYNTHETIC_CURRENT_VERSION, WIDGET, 3)
    assert chain({"label": "foo", "priority": 5}) == {"label": "foo", "priority": 5}


def test_upcast_chain_rejects_a_version_above_current() -> None:
    with pytest.raises(UnknownSchemaVersion):
        _compose(SYNTHETIC_UPCASTERS, SYNTHETIC_CURRENT_VERSION, WIDGET, 4)


def test_upcast_chain_rejects_a_missing_intermediate_step() -> None:
    """An unregistered step must raise, not be silently skipped."""
    incomplete = {(WIDGET, 1): _rename_field_v1_to_v2}  # v2 -> v3 missing
    chain = _compose(incomplete, SYNTHETIC_CURRENT_VERSION, WIDGET, 1)
    with pytest.raises(UnknownSchemaVersion):
        chain({"name": "foo"})


# --- a cheap Hypothesis property over the flat (no Deadline/QuestionSnapshot)
# events. Building strategies for every one of the 36 event types — several
# nest `Deadline`, `QuestionSnapshot`/`QuestionPool`, or `GameRules` with
# their own validity constraints — is not cheap relative to what it would add
# over the golden parametrized table above, which already exercises every
# type once. This property adds fuzzed *values* (not fuzzed shapes) for the
# subset of events built entirely from scalars, tuples of ids, and mappings.

player_ids = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=8
).map(PlayerId)
region_ids = st.text(
    alphabet=st.characters(min_codepoint=97, max_codepoint=122), min_size=1, max_size=8
).map(RegionId)

flat_events = st.one_of(
    st.builds(PlayerJoined, player_ids, st.text(min_size=1, max_size=20), st.integers(0, 3)),
    st.builds(PlayerLeft, player_ids),
    st.builds(GameStarted, st.tuples(player_ids, player_ids)),
    st.builds(GameAborted, st.text(min_size=1, max_size=50)),
    st.builds(ExpansionRoundStarted, st.integers(1, 10)),
    st.builds(BattleRoundCompleted, st.integers(1, 10)),
    st.builds(
        ScoreChanged,
        player_ids,
        st.integers(-1000, 1000),
        st.sampled_from(ScoreReason),
        st.integers(0, 10000),
    ),
    st.builds(PlayerEliminated, player_ids),
    st.builds(PlayerSurrendered, player_ids),
    st.builds(TerritoryNeutralized, region_ids, player_ids),
    st.builds(DuelResolved, st.none() | player_ids),
    st.builds(AttackDeclared, player_ids, st.none() | player_ids, region_ids),
)


@given(event=flat_events)
def test_flat_events_round_trip_under_hypothesis(event: GameEvent) -> None:
    decoded = decode(*encode(event))
    assert decoded == event
    assert type(decoded) is type(event)
