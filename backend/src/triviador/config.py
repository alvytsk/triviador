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

    # Runtime tunables (Spec 1B §5.6). Every one has a default because,
    # unlike `database_url`, a wrong-but-plausible value here degrades
    # behaviour rather than pointing the process at the wrong data — and a
    # deployment that must set nine environment variables to boot is a
    # deployment that will set one of them wrong.
    command_queue_maxsize: int = 256
    commit_max_attempts: int = 3
    watchdog_interval_s: float = 5.0
    watchdog_grace_s: float = 5.0
    reaper_interval_s: float = 60.0
    empty_lobby_grace_minutes: int = 5
    lobby_max_age_hours: int = 6
    recovery_backoff_initial_s: float = 1.0
    recovery_backoff_max_s: float = 60.0


@lru_cache
def get_settings() -> Settings:
    # `database_url` has no default (see above), so pydantic-settings sources
    # it from `TRIVIADOR_DATABASE_URL` at runtime and raises `ValidationError`
    # if that's unset — but mypy, without the `pydantic.mypy` plugin enabled
    # in this project, sees a plain required `__init__` argument here and
    # can't model "populated from the environment" statically.
    return Settings()  # type: ignore[call-arg]
