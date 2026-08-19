"""Seed the one default rule preset.

Spec 1 §7 makes the database enforce "at most one default" with a partial
unique index and leaves "never zero" to application logic. This *is* that
logic, applied at the only moment the system is guaranteed quiescent.
Doing it lazily at first use instead would mean two concurrent creates
racing to insert a default, which the partial unique index would then
turn into a 500 on one of them.

Revision ID: 0002_default_preset
Revises: 0001_initial
"""

import json

import sqlalchemy as sa
from alembic import op

revision = "0002_default_preset"
down_revision = "0001_initial"
branch_labels = None
depends_on = None

# Frozen, and imported from a frozen module rather than from
# `domain.game.rules`. A migration is a historical record of what a
# database was made to contain at one moment; `from ...rules import
# DEFAULT_RULES` would make this already-applied migration seed a
# *different* preset the day someone tunes the defaults, so a fresh
# install and an upgraded install would silently disagree about what
# `default` means with nothing in either database saying which it got.
# Changing the default later is a new migration — which is also the only
# form in which existing installations can be told about it.
from triviador.db.seed import DEFAULT_PRESET_RULES  # noqa: E402


def upgrade() -> None:
    op.execute(
        sa.text(
            "INSERT INTO rule_presets (id, name, is_default, rules, version, is_active) "
            "VALUES ('default', 'Default', true, :rules, 1, true)"
        ).bindparams(sa.bindparam("rules", json.dumps(DEFAULT_PRESET_RULES), type_=sa.JSON))
    )


def downgrade() -> None:
    op.execute("DELETE FROM rule_presets WHERE id = 'default'")
