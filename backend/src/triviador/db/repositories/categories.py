"""`categories`, which nothing else may write.

`QuestionSeeder.ensure_category` (Plan 6) also inserts categories, and
deliberately stays where it is: it is idempotent seeding, not an admin
write path, and it never renames. Both go through the same UNIQUE
constraint, which is what keeps them honest.
"""

from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from triviador.db.models.content import Category
from triviador.services.admin import CategoryRecord, SlugTaken


class CategoryRepository:
    """Implements `services.admin.CategoryPort`."""

    def __init__(self, sessionmaker: async_sessionmaker[AsyncSession]) -> None:
        self._sessionmaker = sessionmaker

    async def list(self) -> tuple[CategoryRecord, ...]:
        async with self._sessionmaker() as session:
            rows = (await session.execute(select(Category).order_by(Category.slug))).scalars().all()
        return tuple(CategoryRecord(r.id, r.slug, r.name) for r in rows)

    async def create(self, *, slug: str, name: str) -> CategoryRecord:
        category = Category(id=str(uuid4()), slug=slug, name=name)
        try:
            async with self._sessionmaker() as session, session.begin():
                session.add(category)
        except IntegrityError as exc:
            # `categories.slug` is the only UNIQUE constraint on this
            # table, so this cannot mean anything else.
            raise SlugTaken(slug) from exc
        return CategoryRecord(category.id, slug, name)

    async def rename(self, category_id: str, *, name: str) -> CategoryRecord | None:
        async with self._sessionmaker() as session, session.begin():
            row = await session.get(Category, category_id)
            if row is None:
                return None
            row.name = name
            return CategoryRecord(row.id, row.slug, name)
