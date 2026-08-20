"""The composition root is the only thing that tells the two object stores
apart (§9.1). `services/storage.py`'s docstring says so; this file is what
makes the claim true.
"""

import pytest
from pydantic import SecretStr

from triviador.api.app import build_dependencies
from triviador.config import Settings
from triviador.storage.s3 import S3MediaStore


@pytest.fixture
def wired_settings() -> Settings:
    return Settings(
        database_url="postgresql+asyncpg://unused/unused",
        allowed_origins=("http://box.lan",),
        s3_access_key_id="GK111111111111111111111111",
        s3_secret_access_key=SecretStr("2" * 64),
    )


def test_the_media_store_is_bound_to_the_media_bucket(wired_settings: Settings) -> None:
    """Half of the claim `services/storage.py`'s docstring makes: the
    other half (`staging_store`, and that the two buckets differ) arrives
    in Task 7, once `staging_store` exists to assert against.
    """
    built = build_dependencies(wired_settings)
    assert isinstance(built.deps.media_store, S3MediaStore)
    assert built.deps.media_store.bucket == wired_settings.media_bucket
