"""ORM models for identity: users, sessions, invite codes.

Spec 1 §7 plus Spec 1B §4.1 (A-4: `invite_codes` gets a surrogate `id` so the
secret `code_hash` never doubles as an admin-facing identifier).
"""

from datetime import datetime
from uuid import uuid4

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy.orm import Mapped, mapped_column

from triviador.db.base import Base


class User(Base):
    __tablename__ = "users"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=lambda: str(uuid4()))
    username: Mapped[str] = mapped_column(Text, unique=True)
    password_hash: Mapped[str] = mapped_column(Text)
    display_name: Mapped[str] = mapped_column(Text)
    # No CheckConstraint: the domain has no `UserRole` enum yet to mirror (unlike
    # `games.status` / domain `Phase`), so pinning literal values here would be a
    # guess this model would then have to police alone.
    role: Mapped[str] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


class Session(Base):
    """Opaque server-side session, not a JWT (Spec 1 §7).

    Deactivating a user must log them out now, which a stateless token
    cannot do without a denylist — i.e. this table.
    """

    __tablename__ = "sessions"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=lambda: str(uuid4()))
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id"))
    # Unique: two live sessions sharing a token hash would make the token
    # ambiguous as an identity lookup key.
    token_hash: Mapped[str] = mapped_column(Text, unique=True)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class InviteCode(Base):
    """Amendment A-4: a surrogate `id` primary key, `code_hash` unique.

    A secret must not double as its own admin-facing identifier, or every
    admin URL that names an invite by its primary key leaks a live invite.
    """

    __tablename__ = "invite_codes"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=lambda: str(uuid4()))
    code_hash: Mapped[str] = mapped_column(Text, unique=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    used_by: Mapped[str | None] = mapped_column(ForeignKey("users.id"))
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
