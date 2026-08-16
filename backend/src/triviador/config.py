from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

TEST_DATABASE_URL = "postgresql+asyncpg://triviador:triviador@127.0.0.1:5433/triviador_test"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="TRIVIADOR_", extra="forbid")

    database_url: str = Field(default=TEST_DATABASE_URL)


@lru_cache
def get_settings() -> Settings:
    return Settings()
