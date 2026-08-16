"""ORM models for the question bank and its media/import pipeline.

Spec 1 §7 plus Spec 1B §4.1 (`question_imports`, closing the two-phase import
hole in §9.3/§10.3 of the app-architecture spec).
"""

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import uuid4

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    Text,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from triviador.db.base import Base


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=lambda: str(uuid4()))
    slug: Mapped[str] = mapped_column(Text, unique=True)
    name: Mapped[str] = mapped_column(Text)


class Question(Base):
    """A question is never physically deleted, only `is_active = False`.

    `game_events` snapshot questions by value at draw time, and Spec 2
    analytics reads historical questions — a hard delete would orphan both.
    `version` increments on any semantic edit (prompt, choices, correct
    answer, category, difficulty, media, unit); toggling `is_active` does
    not, or Spec 2 would silently merge the statistics of two materially
    different questions sharing an id.

    `kind` and `difficulty` are `TEXT` + `CheckConstraint`, mirroring the
    domain's closed `QuestionKind`/`Difficulty` `StrEnum`s the same way
    `games.status` mirrors `Phase` — the pattern applies wherever a closed
    domain enum already exists. Without it, `select_pool`'s
    `WHERE q.kind = :kind` would not error on a bad value written by an
    admin path; the row would just never be selected, surfacing much later
    as an unexplained `InsufficientQuestions`.
    """

    __tablename__ = "questions"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=lambda: str(uuid4()))
    version: Mapped[int] = mapped_column(Integer, default=1)
    kind: Mapped[str] = mapped_column(Text)
    prompt: Mapped[str] = mapped_column(Text)
    category_id: Mapped[str] = mapped_column(ForeignKey("categories.id"))
    difficulty: Mapped[str] = mapped_column(Text)
    media_asset_id: Mapped[str | None] = mapped_column(ForeignKey("media_assets.id"))
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    prompt_hash: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        CheckConstraint("kind IN ('multiple_choice', 'numeric')", name="kind_valid"),
        CheckConstraint("difficulty IN ('easy', 'medium', 'hard')", name="difficulty_valid"),
    )


class QuestionChoice(Base):
    """One multiple-choice option.

    Composite PK: a choice has no identity apart from its question and
    position.
    """

    __tablename__ = "question_choices"

    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"), primary_key=True)
    idx: Mapped[int] = mapped_column(Integer, primary_key=True)
    text: Mapped[str] = mapped_column(Text)
    is_correct: Mapped[bool] = mapped_column(Boolean)
    media_asset_id: Mapped[str | None] = mapped_column(ForeignKey("media_assets.id"))


class QuestionNumeric(Base):
    """The numeric answer for a `kind = 'numeric'` question.

    One row per question — `question_id` is both the PK and the FK, matching
    the 1:1 the domain's `QuestionSnapshot.numeric_answer` expects.
    """

    __tablename__ = "question_numeric"

    question_id: Mapped[str] = mapped_column(ForeignKey("questions.id"), primary_key=True)
    correct_value: Mapped[Decimal] = mapped_column(Numeric)
    unit: Mapped[str | None] = mapped_column(Text)


class MediaAsset(Base):
    """`id` is the sha256 of the normalized WebP content, not a generated key.

    Content addressing is what makes the media pipeline (re-upload,
    re-import, `media-gc`) idempotent.
    """

    __tablename__ = "media_assets"

    id: Mapped[str] = mapped_column(Text, primary_key=True)
    mime_type: Mapped[str] = mapped_column(Text)
    width: Mapped[int | None] = mapped_column(Integer)
    height: Mapped[int | None] = mapped_column(Integer)
    byte_size: Mapped[int] = mapped_column(BigInteger)
    storage_key: Mapped[str] = mapped_column(Text)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class QuestionImport(Base):
    """Staging metadata for the two-phase question import (§9.3).

    Persisted at dry-run time so the verdict and the uploaded bytes survive
    between the dry-run and confirm requests, which are two separate HTTP
    calls with nothing else to hold that state.
    """

    __tablename__ = "question_imports"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=lambda: str(uuid4()))
    uploaded_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    upload_sha256: Mapped[str] = mapped_column(Text)
    filename: Mapped[str] = mapped_column(Text)
    staged_key: Mapped[str | None] = mapped_column(
        Text,
        comment=(
            "Key into the private `triviador-staging` bucket, never the public "
            "`triviador-media` bucket (§9.1). Set NULL once the staged object is "
            "retired (expiry or post-confirm cleanup) — the row remains as an "
            "audit trail. The bucket, not a prefix, is the security boundary: "
            "staged uploads carry unpublished answer keys."
        ),
    )
    row_count: Mapped[int] = mapped_column(Integer, default=0)
    rejected_count: Mapped[int] = mapped_column(Integer, default=0)
    report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    # No CheckConstraint: the spec names the values this status machine passes
    # through in prose (validated / confirmed / expired / cleaned) but never
    # states them as an exhaustive, closed list the way it does for
    # `games.status`, so pinning a set here would be a guess.
    status: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
