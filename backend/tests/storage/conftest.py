"""Fixtures for the object-store suite: a real Garage, per §9.1's two buckets.

Not MinIO. Production runs Garage (Spec 1B §10.3), and the behaviours this
suite pins — a 404 on a missing key, an idempotent delete, `Cache-Control`
surviving a round trip as object metadata — are exactly the ones an
S3-compatible stand-in is entitled to get subtly right in a different way.

Keys are namespaced per test with a `uuid4` prefix rather than cleaned up
between tests: the store is content-addressed in production and the bucket
is tmpfs here, so collision-avoidance is worth more than tidiness.
"""

import os
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
import pytest_asyncio

from triviador.storage.s3 import S3GarageProbe, S3ImportStagingStore, S3MediaStore

HERE = Path(__file__).parent

ENDPOINT = os.environ.get("TRIVIADOR_TEST_S3_ENDPOINT", "http://127.0.0.1:3900")
# 24 hex-encoded bytes after `GK`, the exact format `garage key import`
# validates against on the pinned v1.1.0 image (Task 2 Step 1) — a
# same-length-but-different string is rejected before it ever reaches a
# bucket check, which is why this must stay byte-for-byte in step with
# `testing/garage-init.sh`'s default.
KEY_ID = os.environ.get("TRIVIADOR_TEST_S3_KEY_ID", "GK111111111111111111111111")
KEY_SECRET = os.environ.get("TRIVIADOR_TEST_S3_KEY_SECRET", "2" * 64)

pytestmark = pytest.mark.integration


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Same gate as `tests/db/conftest.py`: a module here without the mark
    would be deselected by `-m "not integration"` and still require Garage.

    A conftest.py hook is registered for the whole pytest session once it is
    loaded, not scoped to this directory — `items` here is every item
    collected anywhere under `testpaths`. Filtered to this directory's own
    items for the same reason `tests/db/conftest.py` filters: without it,
    this hook rejects the entire suite the moment collection touches
    `tests/storage`, whether or not anything under it actually ran.
    """
    ours = [item for item in items if item.path.is_relative_to(HERE)]
    for item in ours:
        if "integration" not in item.keywords:
            raise pytest.UsageError(f"{item.nodeid}: tests/storage requires the integration mark")


@pytest_asyncio.fixture
async def prefix() -> str:
    return f"t-{uuid.uuid4().hex}"


@pytest_asyncio.fixture
async def media_store() -> AsyncIterator[S3MediaStore]:
    yield S3MediaStore(
        endpoint_url=ENDPOINT,
        region="garage",
        access_key_id=KEY_ID,
        secret_access_key=KEY_SECRET,
        bucket="triviador-media",
    )


@pytest_asyncio.fixture
async def staging_store() -> AsyncIterator[S3ImportStagingStore]:
    yield S3ImportStagingStore(
        endpoint_url=ENDPOINT,
        region="garage",
        access_key_id=KEY_ID,
        secret_access_key=KEY_SECRET,
        bucket="triviador-staging",
    )


@pytest_asyncio.fixture
async def garage_probe() -> AsyncIterator[S3GarageProbe]:
    yield S3GarageProbe(
        endpoint_url=ENDPOINT,
        region="garage",
        access_key_id=KEY_ID,
        secret_access_key=KEY_SECRET,
        media_bucket="triviador-media",
        staging_bucket="triviador-staging",
    )
