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
