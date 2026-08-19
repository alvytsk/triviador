import re
from functools import lru_cache
from pathlib import Path
from typing import Annotated, Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

PLACEHOLDER = "CHANGE_ME"

# A browser's Origin header is `scheme://host[:port]` — no path, no trailing
# slash. Matching is exact string equality against this list, so an entry in
# any other shape is dead weight that can only ever produce a 403.
_ORIGIN_RE = re.compile(r"^https?://[A-Za-z0-9.\-]+(:\d+)?$")


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

    # --- API (Spec 1B §6, §10.4) ------------------------------------------
    # `NoDecode`: without it, `EnvSettingsSource` tries `json.loads()` on the
    # raw env string *before* `_split_csv` below ever sees it, and a plain
    # (non-Union) complex type has `allow_parse_failure=False` — so
    # `TRIVIADOR_ALLOWED_ORIGINS=http://localhost:5173` fails to parse as
    # JSON and the process refuses to boot on the one value `.env.example`
    # actually ships. `NoDecode` hands the source's raw string straight to
    # the validator instead of decoding it first.
    allowed_origins: Annotated[tuple[str, ...], NoDecode] = ()
    allowed_hosts: Annotated[tuple[str, ...], NoDecode] = ("*",)
    cookie_secure: bool = False
    session_cookie_name: str = "triviador_session"
    session_ttl_days: int = 30
    maps_root: Path = Path("/data/maps")
    media_public_base: str = "/media"
    # Where Caddy serves `data/maps/<id>/map.svg` from (§10.2). The API
    # names the URL; it never serves the bytes.
    maps_public_base: str = "/maps"
    log_level: str = "INFO"
    log_format: Literal["json", "console"] = "json"
    # 413 above this. Spec 1's largest player-facing body is a login form;
    # Plan 7's media upload sets its own, larger limit on its own route.
    max_body_bytes: int = 1_048_576
    # §8.6's bounded outbound queue. Overflow closes that subscriber (4408).
    ws_outbound_queue_size: int = 64
    # §8.6: "ping every 15 s, socket considered dead after 30 s of silence."
    # Both ends apply it. Without the server half, a half-open TCP
    # connection — a laptop lid closing, a Wi-Fi handover — leaves a
    # `Connection`, a sender task and a presence entry behind forever.
    ws_idle_timeout_s: float = 30.0

    @field_validator("allowed_origins", "allowed_hosts", mode="before")
    @classmethod
    def _split_csv(cls, value: object) -> object:
        """`TRIVIADOR_ALLOWED_ORIGINS=http://a,http://b`.

        pydantic-settings parses a complex annotation from JSON by default,
        which would make the natural env-file form a startup crash with a
        JSON decode error pointing at nothing useful. The `NoDecode` marker
        on both fields is what makes this validator reachable at all: it
        stops `EnvSettingsSource` from attempting that JSON decode itself
        and handing this function the raw string instead — remove it and
        the JSON decode error is back, silently.
        """
        if isinstance(value, str):
            return tuple(part.strip() for part in value.split(",") if part.strip())
        return value


@lru_cache
def get_settings() -> Settings:
    # `database_url` has no default (see above), so pydantic-settings sources
    # it from `TRIVIADOR_DATABASE_URL` at runtime and raises `ValidationError`
    # if that's unset — but mypy, without the `pydantic.mypy` plugin enabled
    # in this project, sees a plain required `__init__` argument here and
    # can't model "populated from the environment" statically.
    return Settings()  # type: ignore[call-arg]


def startup_problems(settings: Settings) -> tuple[str, ...]:
    """§10.4's two assertions, as a list rather than a raise.

    A list, so a misconfigured deploy is told about *every* problem at once
    instead of one per restart.
    """
    problems: list[str] = []

    if not settings.allowed_origins:
        problems.append("ALLOWED_ORIGINS is empty: no request and no socket could be accepted")
    malformed = [o for o in settings.allowed_origins if not _ORIGIN_RE.match(o)]
    if malformed:
        problems.append(f"ALLOWED_ORIGINS entries must be scheme://host[:port]: {malformed}")

    wanted = "https" if settings.cookie_secure else "http"
    wrong = [o for o in settings.allowed_origins if not o.startswith(f"{wanted}://")]
    if wrong:
        problems.append(
            f"COOKIE_SECURE={settings.cookie_secure} requires every ALLOWED_ORIGINS entry "
            f"to use {wanted}://; these do not: {wrong}"
        )

    for name, value in settings.model_dump().items():
        if isinstance(value, str) and PLACEHOLDER in value:
            problems.append(f"{name} still holds its .env.example placeholder")

    return tuple(problems)
