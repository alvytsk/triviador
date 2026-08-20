"""Repair any `rule_presets` row that 0002 double-JSON-encoded.

0002's original `upgrade()` bound `json.dumps(DEFAULT_PRESET_RULES)` — already
a string — with `type_=sa.JSON`, which serialized it a second time. On a
database that ran that version of 0002, `rule_presets.rules` holds a JSONB
*string* scalar containing the intended object's JSON text, not the object
itself, and `PresetRepository.get_default()` raises on it — the exact call
`POST /api/games` makes for `preset_id: null`, so an affected database can
create no games at all.

0002 itself has since been fixed in place (bind the dict once, no manual
`json.dumps`), so a fresh install now gets a correct row directly from that
migration. This migration exists for the other case: a database that already
ran the old, broken 0002 before that fix landed. Alembic tracks 0002 as
applied and will never re-run it, so nothing short of a forward migration
reaches that row. Both migrations are needed together — 0002 so the source is
correct, 0003 so a database that already has the defect gets fixed instead of
carrying it forward silently.

`upgrade()` targets every row with the defect, not just `id = 'default'`: the
same double-encode bug would corrupt any row written through the same broken
bind, and there is nothing about the repair that is specific to the default
preset. Guarded by `WHERE jsonb_typeof(rules) = 'string'`, so it is a no-op on
a database where 0002 already wrote (or has been repaired to hold) a proper
object — safe to run on a fresh install where 0002 was already correct, and
safe to run twice.

Revision ID: 0003_repair_default_preset_rules
Revises: 0002_default_preset
"""

import sqlalchemy as sa
from alembic import op

revision = "0003_repair_default_preset_rules"
down_revision = "0002_default_preset"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # `rules #>> '{}'` extracts the string scalar's text content — the
    # double-encoded row's *inner* JSON text, unquoted. That text is itself
    # valid JSON (it's what a correct row's `rules` should have been all
    # along), so casting it back to `jsonb` recovers the intended object.
    op.execute(
        sa.text(
            "UPDATE rule_presets SET rules = (rules #>> '{}')::jsonb "
            "WHERE jsonb_typeof(rules) = 'string'"
        )
    )


def downgrade() -> None:
    # This migration repairs corrupted data; there is no meaningful inverse
    # to "un-repair" a row back into a broken shape. A no-op rather than
    # re-corrupting it.
    pass
