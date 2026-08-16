"""Exceptions raised by the persistence layer.

Everything here is a shared home for `db/`-level errors, not just the
codec's: Task 6 appends `ConcurrentModification` for the optimistic-append
guard, and Task 8 appends `InsufficientQuestions` for `QuestionBank`, and
both belong in this module rather than a new file so the exception surface
stays in one place.
"""

from triviador.domain.ids import QuestionId
from triviador.domain.questions.types import QuestionKind


class UnknownEventType(Exception):
    """`decode` was given a wire `type` absent from the registry.

    Raised with the unrecognized wire type as the argument.
    """


class UnknownSchemaVersion(Exception):
    """`decode` was given a `schema_version` the upcaster chain cannot reach.

    Either the requested version is newer than `CURRENT_VERSION` for that
    wire type, or an intermediate upcaster step is missing from the chain.
    """


class NaiveDatetime(Exception):
    """A datetime reachable from an event has no `tzinfo`.

    There is no correct instant to recover from a naive value, so the codec
    refuses it outright rather than guessing a zone. Raised with a dotted
    path to the offending field as the argument.
    """


class ConcurrentModification(Exception):
    """`TransactionContext.append`'s optimistic `UPDATE games ... WHERE
    last_seq = :expected` matched zero rows.

    Raised with `(game_id, expected_last_seq)`. Someone else already
    advanced this game's `last_seq` past what this attempt's `decide()` call
    saw, so the runtime quarantines on this and never retries — retrying
    would append events decided against state that is no longer current.
    """


class InsufficientQuestions(Exception):
    """`QuestionBank.select_pool` found fewer than `required` active
    questions of `kind` to draw from.

    `kind`, `required`, and `available` are all set as attributes (not just
    positional `args`) — the operator needs to know which bank is short and
    by how much. Plan 4 maps this directly to
    `RejectedCommand(QUESTION_POOL_INSUFFICIENT)`, after which the
    transaction rolls back and the game stays in `LOBBY`.
    """

    def __init__(self, *, kind: QuestionKind, required: int, available: int) -> None:
        super().__init__(kind, required, available)
        self.kind = kind
        self.required = required
        self.available = available


class MalformedQuestion(Exception):
    """`QuestionBank._materialize` found a `Question` row missing the child
    data its `kind` requires: a `multiple_choice` row with zero
    `question_choices` rows, or a `numeric` row with no `question_numeric`
    row.

    Raised while a `StartGame` pool draw is still inside its transaction —
    the same place `InsufficientQuestions` is raised — so a malformed row is
    a pre-game failure while the game is still in `LOBBY`. Without this
    check, the same bad row would instead be baked into a committed
    `QuestionPoolDrawn` event and only surface later, mid-game, as a
    `ValueError` out of `QuestionSnapshot.correct_choice_index()` — a
    failure that reproduces identically on every recovery replay, since the
    bad snapshot is now part of the durable log. `question_id` and `kind`
    are set as attributes so the operator can locate and fix the source row.
    """

    def __init__(self, *, question_id: QuestionId, kind: QuestionKind) -> None:
        super().__init__(question_id, kind)
        self.question_id = question_id
        self.kind = kind
