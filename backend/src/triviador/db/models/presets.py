"""ORM model for rule presets.

Spec 1 §7 plus Spec 1B §4.1 (`is_active`, a soft delete: an admin retiring a
preset must not perturb `games.preset_id` on games already in flight).
"""

from typing import Any
from uuid import uuid4

from sqlalchemy import Boolean, Index, Integer, Text, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from triviador.db.base import Base


class RulePreset(Base):
    __tablename__ = "rule_presets"

    id: Mapped[str] = mapped_column(Text, primary_key=True, default=lambda: str(uuid4()))
    name: Mapped[str] = mapped_column(Text)
    is_default: Mapped[bool] = mapped_column(Boolean, default=False)
    rules: Mapped[dict[str, Any]] = mapped_column(JSONB)
    version: Mapped[int] = mapped_column(Integer, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))

    __table_args__ = (
        # Exactly one default preset, enforced in the database rather than
        # only in application logic: a PostgreSQL partial unique index on
        # `is_default WHERE is_default` (Spec 1 §7). Application logic still
        # has to ensure there is never zero.
        Index(
            "uq_rule_presets_single_default",
            "is_default",
            unique=True,
            postgresql_where=text("is_default"),
        ),
    )
