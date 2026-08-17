"""Conformance is a *typing* property, so mypy is the test.

`_conformance` never runs — it lives under `TYPE_CHECKING` and takes the
concrete Plan 3 classes as parameters. Assigning each to its Protocol is
what proves the adapter satisfies the port; `uv run mypy` failing with
"Incompatible types in assignment" is this module's red state. The two
runtime tests below cover the parts mypy cannot see: that the ports module
imports no persistence code, and that the exception hierarchy the
materialiser catches actually holds.
"""

from typing import TYPE_CHECKING

from triviador.db.errors import InsufficientQuestions, MalformedQuestion
from triviador.domain.ids import QuestionId
from triviador.domain.questions.types import QuestionKind
from triviador.services import ports

if TYPE_CHECKING:
    from triviador.db.repositories.games import GameRepository
    from triviador.db.repositories.questions import QuestionBank
    from triviador.db.unit_of_work import TransactionContext, UnitOfWork
    from triviador.maps.registry import MapRegistry

    def _conformance(
        uow: UnitOfWork,
        tx: TransactionContext,
        repo: GameRepository,
        bank: QuestionBank,
        registry: MapRegistry,
    ) -> None:
        _uow: ports.UnitOfWorkPort = uow
        _tx: ports.Transaction = tx
        _repo: ports.GameQueriesPort = repo
        _bank: ports.QuestionBankPort = bank
        _maps: ports.MapProvider = registry


def test_bank_shortfalls_are_catchable_as_one_port_exception() -> None:
    """The materialiser catches a single type. If either bank error stops
    subclassing `QuestionPoolUnavailable`, a content problem starts
    quarantining a healthy lobby instead of rejecting a StartGame."""
    insufficient = InsufficientQuestions(kind=QuestionKind.NUMERIC, required=17, available=3)
    malformed = MalformedQuestion(question_id=QuestionId("q1"), kind=QuestionKind.NUMERIC)

    assert isinstance(insufficient, ports.QuestionPoolUnavailable)
    assert isinstance(malformed, ports.QuestionPoolUnavailable)


def test_decode_failures_are_catchable_as_one_port_exception() -> None:
    """The loader's permanent/transient split hangs off this. If a decode
    error stops subclassing `EventStreamCorrupt`, an undecodable log gets
    classified transient and retried with backoff forever — an outage
    with no error to find."""
    from triviador.db.errors import NaiveDatetime, UnknownEventType, UnknownSchemaVersion

    for error in (
        UnknownEventType("battle.unheard_of"),
        UnknownSchemaVersion("battle.duel_resolved", 9),
        NaiveDatetime("turn.deadline.deadline_at"),
    ):
        assert isinstance(error, ports.EventStreamCorrupt)


def test_ports_module_imports_no_persistence_code() -> None:
    """A Protocol that mentions a SQLAlchemy type is not a port — it is a
    re-export of the adapter, and `runtime/` would inherit the dependency
    through it."""
    source = __import__("pathlib").Path(ports.__file__).read_text(encoding="utf-8")
    for forbidden in ("sqlalchemy", "asyncpg", "triviador.db", "triviador.runtime"):
        assert forbidden not in source, f"services/ports.py must not mention {forbidden}"


def test_runtime_settings_defaults() -> None:
    """§5.6's numbers, in one place. 256 sits far above any legitimate
    burst from four players; 5 s is the watchdog tick *and* its grace."""
    from triviador.config import Settings

    settings = Settings(database_url="postgresql+asyncpg://u:p@localhost/db")

    assert settings.command_queue_maxsize == 256
    assert settings.commit_max_attempts == 3
    assert settings.watchdog_interval_s == 5.0
    assert settings.watchdog_grace_s == 5.0
    assert settings.lobby_max_age_hours == 6
    assert settings.empty_lobby_grace_minutes == 5
