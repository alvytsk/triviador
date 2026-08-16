"""Exceptions raised by the persistence layer.

Everything here is a shared home for `db/`-level errors, not just the
codec's: Task 6 appends `ConcurrentModification` for the optimistic-append
guard, and Task 8 appends `InsufficientQuestions` for `QuestionBank`, and
both belong in this module rather than a new file so the exception surface
stays in one place.
"""

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
