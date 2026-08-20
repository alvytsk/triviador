"""Trigram index for §10.2's prompt search.

`pg_trgm` + `ILIKE '%needle%'` rather than a `tsvector`: PostgreSQL ships
no Czech text-search configuration, this deployment's map is Czechia and
its seed bank is English, so any stemming configuration chosen here is the
wrong one for half the bank. Trigrams are language-independent, and
substring is what an admin who half-remembers a question actually types.

The index is on `lower(prompt)`, and the query must be
`lower(prompt) LIKE lower(:needle)` for the planner to use it — `ILIKE`
against the bare column would not match this expression index.

`CREATE EXTENSION` needs privileges an unprivileged application role does
not have. In this deployment the migration runs as the owner of its own
database (§10.5's `migrate` service), which does. If a future deployment
splits those roles, this line moves to a provisioning step and the
migration keeps only the index.

Revision ID: 0004_question_search
Revises: 0003_repair_default_preset_rules
"""

from alembic import op

revision = "0004_question_search"
down_revision = "0003_repair_default_preset_rules"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS pg_trgm")
    op.execute(
        "CREATE INDEX ix_questions_prompt_trgm ON questions "
        "USING gin (lower(prompt) gin_trgm_ops)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_questions_prompt_trgm")
    # The extension is deliberately not dropped: another schema may be
    # using it, and `DROP EXTENSION` would take their indexes with it.
