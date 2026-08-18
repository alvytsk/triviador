"""§10.4's two startup assertions, and the comma-separated list form.

These are *startup* assertions deliberately: a misconfigured origin list
fails authentication in a way that looks like a frontend bug, hours later
and on someone else's machine.
"""

from pathlib import Path

import pytest

from triviador.config import PLACEHOLDER, Settings, startup_problems


def settings(**overrides: object) -> Settings:
    base: dict[str, object] = {
        "database_url": "postgresql+asyncpg://u:p@localhost/db",
        "allowed_origins": ("http://box.lan",),
        "cookie_secure": False,
        "maps_root": Path("/data/maps"),
    }
    return Settings(**{**base, **overrides})  # type: ignore[arg-type]


def test_a_comma_separated_origin_list_parses_into_a_tuple() -> None:
    assert settings(allowed_origins="http://a.lan, http://b.lan").allowed_origins == (
        "http://a.lan",
        "http://b.lan",
    )


def test_a_single_origin_from_the_environment_parses_without_a_json_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The `.env.example` value, read the way a real process reads it: via
    `TRIVIADOR_ALLOWED_ORIGINS` and a bare `Settings()`, not via kwargs.

    `Settings(**overrides)` above never touches `EnvSettingsSource` — Python
    kwargs bypass it entirely — so it cannot catch a regression in the
    `NoDecode` annotation. Before that annotation was added,
    `EnvSettingsSource` tried `json.loads("http://localhost:5173")` before
    `_split_csv` ever ran, and that is not valid JSON even with no comma in
    sight: the process refused to boot on the one value `.env.example`
    actually ships.
    """
    monkeypatch.setenv("TRIVIADOR_DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("TRIVIADOR_ALLOWED_ORIGINS", "http://localhost:5173")
    assert Settings().allowed_origins == ("http://localhost:5173",)  # type: ignore[call-arg]


def test_a_comma_separated_origin_list_from_the_environment_parses_with_whitespace_stripped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Same environment path as above, with the comma-and-whitespace form
    `test_a_comma_separated_origin_list_parses_into_a_tuple` already covers
    through kwargs — this confirms it also survives `EnvSettingsSource`."""
    monkeypatch.setenv("TRIVIADOR_DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("TRIVIADOR_ALLOWED_ORIGINS", "http://a.lan, http://b.lan")
    assert Settings().allowed_origins == ("http://a.lan", "http://b.lan")  # type: ignore[call-arg]


def test_allowed_hosts_from_the_environment_parses_the_same_way(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`allowed_hosts` carries the same `NoDecode` annotation as
    `allowed_origins` and the same failure mode without it — this is that
    field's sibling regression test."""
    monkeypatch.setenv("TRIVIADOR_DATABASE_URL", "postgresql+asyncpg://u:p@localhost/db")
    monkeypatch.setenv("TRIVIADOR_ALLOWED_HOSTS", "localhost,127.0.0.1")
    assert Settings().allowed_hosts == ("localhost", "127.0.0.1")  # type: ignore[call-arg]


def test_a_consistent_configuration_has_no_problems() -> None:
    assert startup_problems(settings()) == ()


def test_an_https_origin_with_an_insecure_cookie_is_refused() -> None:
    problems = startup_problems(settings(allowed_origins=("https://box.lan",), cookie_secure=False))
    assert any("COOKIE_SECURE" in p for p in problems)


def test_an_http_origin_with_a_secure_cookie_is_refused() -> None:
    """The failure this catches is silent: a `Secure` cookie is simply never
    sent over plain HTTP, so every request arrives unauthenticated and the
    only symptom is a login that appears to succeed and then does nothing."""
    problems = startup_problems(settings(allowed_origins=("http://box.lan",), cookie_secure=True))
    assert any("COOKIE_SECURE" in p for p in problems)


def test_a_mixed_scheme_origin_list_is_refused_under_either_cookie_setting() -> None:
    mixed = ("http://box.lan", "https://box.lan")
    assert startup_problems(settings(allowed_origins=mixed, cookie_secure=False)) != ()
    assert startup_problems(settings(allowed_origins=mixed, cookie_secure=True)) != ()


def test_a_setting_still_holding_its_placeholder_is_refused() -> None:
    url = f"postgresql+asyncpg://u:{PLACEHOLDER}@localhost/db"
    problems = startup_problems(settings(database_url=url))
    assert any("database_url" in p for p in problems)


def test_an_empty_origin_list_is_refused() -> None:
    """Not a vacuous truth: with no origins every unsafe request is refused
    and every socket handshake fails, which reads as a broken deploy rather
    than as a missing variable."""
    assert startup_problems(settings(allowed_origins=())) != ()


@pytest.mark.parametrize("origin", ["box.lan", "http://box.lan/", "http://box.lan/app"])
def test_an_origin_that_is_not_a_bare_scheme_and_host_is_refused(origin: str) -> None:
    """A browser sends `Origin: scheme://host[:port]` with no path and no
    trailing slash. An entry with either can never match, so the mismatch
    must surface at startup rather than as a 403 nobody can explain."""
    assert startup_problems(settings(allowed_origins=(origin,))) != ()
