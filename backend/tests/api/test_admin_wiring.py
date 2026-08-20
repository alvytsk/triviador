"""The composition root is the only thing that tells the two object stores
apart (§9.1). `services/storage.py`'s docstring says so; this file is what
makes the claim true.
"""

import pytest
from pydantic import SecretStr

from triviador.api.app import build_dependencies
from triviador.config import Settings
from triviador.storage.s3 import S3ImportStagingStore, S3MediaStore


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
    other half (`staging_store`, and that the two buckets differ) is
    asserted below.
    """
    built = build_dependencies(wired_settings)
    assert isinstance(built.deps.media_store, S3MediaStore)
    assert built.deps.media_store.bucket == wired_settings.media_bucket


def test_the_staging_store_is_bound_to_a_different_bucket_than_media(
    wired_settings: Settings,
) -> None:
    """The other half: `staging_store` is bound at all, and to a *different*
    bucket than `media_store` — §9.1's security boundary is the bucket, and
    a composition root that pointed both at the same one would publish
    unvalidated uploads to the world.
    """
    built = build_dependencies(wired_settings)
    assert isinstance(built.deps.staging_store, S3ImportStagingStore)
    assert isinstance(built.deps.media_store, S3MediaStore)
    assert built.deps.staging_store.bucket == wired_settings.staging_bucket
    assert built.deps.staging_store.bucket != built.deps.media_store.bucket
