from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRIVIADOR_", extra="forbid")

    # No default. A default here — even a test-database one — is a footgun
    # that eventually points a migration or a running service at the wrong
    # database when `TRIVIADOR_DATABASE_URL` is simply unset; Pydantic
    # raising `ValidationError` on missing config is exactly the loud
    # failure we want instead. Tests own their own database URL (see
    # `TEST_DATABASE_URL` in `tests/db/conftest.py`) rather than borrowing
    # one from here.
    database_url: str


@lru_cache
def get_settings() -> Settings:
    # `database_url` has no default (see above), so pydantic-settings sources
    # it from `TRIVIADOR_DATABASE_URL` at runtime and raises `ValidationError`
    # if that's unset — but mypy, without the `pydantic.mypy` plugin enabled
    # in this project, sees a plain required `__init__` argument here and
    # can't model "populated from the environment" statically.
    return Settings()  # type: ignore[call-arg]
