from pydantic import BaseModel, ConfigDict, Field

from triviador.services.admin import CATEGORY_SLUG_PATTERN


class CategoryView(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    slug: str
    name: str


class CreateCategoryRequest(BaseModel):
    """The slug pattern is the same shape the seed CSV's `category_slug`
    column uses, so a category created here can be referenced by a later
    import without transformation."""

    model_config = ConfigDict(extra="forbid")

    slug: str = Field(min_length=1, max_length=48, pattern=CATEGORY_SLUG_PATTERN)
    name: str = Field(min_length=1, max_length=64)


class RenameCategoryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=64)
