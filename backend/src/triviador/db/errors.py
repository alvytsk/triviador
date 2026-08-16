"""Exceptions raised by the persistence layer.

Everything here is a shared home for `db/`-level errors, not just the
codec's: Task 6 appends `ConcurrentModification` for the optimistic-append
guard, and that belongs in this module rather than a new file so the
exception surface stays in one place.
"""


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
