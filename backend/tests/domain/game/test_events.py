import dataclasses

from triviador.domain.game import events as ev


def test_no_gameplay_event_embeds_score() -> None:
    banned = {"score_delta", "new_score", "new_total_score", "points"}
    for name in dir(ev):
        cls = getattr(ev, name)
        if not dataclasses.is_dataclass(cls) or cls is ev.ScoreChanged:
            continue
        fields = set(getattr(cls, "__dataclass_fields__", {}))
        assert not (fields & banned), f"{name} embeds scoring: {fields & banned}"


def test_score_changed_carries_reason_and_new_total() -> None:
    fields = set(ev.ScoreChanged.__dataclass_fields__)
    assert {"player_id", "delta", "reason", "new_total"} <= fields


def test_question_pool_drawn_carries_snapshots_not_ids() -> None:
    fields = ev.QuestionPoolDrawn.__dataclass_fields__
    assert "pool" in fields
    assert not any("id" in f for f in fields), "the pool must be snapshots, never ids"


def test_question_presented_carries_a_full_snapshot_and_window() -> None:
    fields = set(ev.QuestionPresented.__dataclass_fields__)
    assert {"question", "deadline"} <= fields


def test_every_event_is_frozen() -> None:
    for name in dir(ev):
        cls = getattr(ev, name)
        if dataclasses.is_dataclass(cls):
            assert cls.__dataclass_params__.frozen, f"{name} must be frozen"  # type: ignore[union-attr]
