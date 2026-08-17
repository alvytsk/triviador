from sqlalchemy import MetaData
from sqlalchemy.orm import DeclarativeBase

# Explicit names for every constraint. Without this, Alembic emits migrations
# that drop unnamed constraints it cannot address, and a failing check reads as
# `CHECK constraint "ck_1a2b" violated` instead of naming the rule.
NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)
