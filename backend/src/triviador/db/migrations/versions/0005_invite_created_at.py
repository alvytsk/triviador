"""`invite_codes.created_at`, needed by Task 10's admin listing.

`0001_initial` gave `users` and `media_assets` a `created_at` but left
`invite_codes` without one — `expires_at` alone cannot answer "when was
this issued", which an admin auditing a leaked code needs and which
`expires_in_hours` (issued at request time, TTL from there) cannot be
derived from after the fact. Same shape as those two: `timestamptz`,
`server_default=now()`, `NOT NULL` — a backfill for any pre-existing row
that never had a "when issued" of its own, same as it would be for either
of the other two tables.

Revision ID: 0005_invite_created_at
Revises: 0004_question_search
"""

import sqlalchemy as sa
from alembic import op

revision = "0005_invite_created_at"
down_revision = "0004_question_search"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "invite_codes",
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
    )


def downgrade() -> None:
    op.drop_column("invite_codes", "created_at")
