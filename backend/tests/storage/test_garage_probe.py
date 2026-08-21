"""§10.6's fourth readiness check, proven against a real Garage rather
than trusted from the implementation's `ClientError` handling alone.

The trap this suite exists to catch: Garage v1.1.0's `get_bucket_website`
does **not** behave like real AWS S3. AWS raises `NoSuchWebsiteConfiguration`
for a bucket with no website config; Garage answers with an ordinary 200/204
and an *empty* body instead (confirmed against the running `garage-test`
container — see task report). A probe written against the AWS-shaped
contract — "not configured" means "it raised" — would report `ready() is
True` unconditionally against Garage, website-enabled staging bucket or
not: exactly the kind of guard that cannot fire this plan has shipped
before. `test_website_enabled_staging_bucket_is_not_ready` is the tripwire.
"""

import os

import aioboto3
import pytest
from botocore.config import Config as BotoConfig

from triviador.storage.s3 import S3GarageProbe

pytestmark = [pytest.mark.integration, pytest.mark.asyncio]

ENDPOINT = os.environ.get("TRIVIADOR_TEST_S3_ENDPOINT", "http://127.0.0.1:3900")
KEY_ID = os.environ.get("TRIVIADOR_TEST_S3_KEY_ID", "GK111111111111111111111111")
KEY_SECRET = os.environ.get(
    "TRIVIADOR_TEST_S3_KEY_SECRET",
    "2222222222222222222222222222222222222222222222222222222222222222",
)


async def test_the_seeded_state_is_ready(garage_probe: S3GarageProbe) -> None:
    """`testing/garage-init.sh` seeds exactly the state §10.6 wants: both
    buckets exist, and only the media bucket is website-enabled."""
    assert await garage_probe.ready() is True


async def test_an_unreachable_garage_is_not_ready() -> None:
    """The Critical finding this test pins: `ready()` must return `False`
    for an unreachable Garage — connection refused, not started yet, a
    network blip — not raise. `EndpointConnectionError` (what botocore
    raises when nothing answers at `endpoint_url`) is a `BotoCoreError`,
    not a `ClientError`: Garage never gets the chance to answer with an
    S3 error response, so a probe that only caught `ClientError` let this
    propagate straight through `_lifespan` and crashed the process at
    startup instead of setting `readiness.garage_ready = False` — exactly
    the crash-loop `app.py`'s own comment says this check exists to avoid.

    No live Garage needed either way: `garage-test` being up or down does
    not matter here, because port 1 on loopback refuses the connection
    deterministically regardless. Carries the module's `integration` mark
    only because `tests/storage/conftest.py` requires it of every test in
    this directory, not because this test depends on the container."""
    probe = S3GarageProbe(
        endpoint_url="http://127.0.0.1:1",
        region="garage",
        access_key_id=KEY_ID,
        secret_access_key=KEY_SECRET,
        media_bucket="triviador-media",
        staging_bucket="triviador-staging",
    )
    assert await probe.ready() is False


async def test_a_missing_bucket_is_not_ready() -> None:
    probe = S3GarageProbe(
        endpoint_url=ENDPOINT,
        region="garage",
        access_key_id=KEY_ID,
        secret_access_key=KEY_SECRET,
        media_bucket="triviador-media",
        staging_bucket="bucket-that-was-never-created",
    )
    assert await probe.ready() is False


async def test_website_enabled_staging_bucket_is_not_ready(garage_probe: S3GarageProbe) -> None:
    """The assertion that matters, mirrored from `infra/garage/init.sh`'s
    own guard: a website-enabled staging bucket publishes raw import
    uploads, answer keys included. Toggled directly through the S3 API
    (Garage v1.1.0 supports `Put`/`DeleteBucketWebsite` there — verified
    empirically) and restored in `finally`, so this test does not leave
    the shared `garage-test` container's staging bucket in the unsafe
    state for whatever runs after it."""
    session = aioboto3.Session()
    client_kwargs = {
        "endpoint_url": ENDPOINT,
        "region_name": "garage",
        "aws_access_key_id": KEY_ID,
        "aws_secret_access_key": KEY_SECRET,
        "config": BotoConfig(signature_version="s3v4", s3={"addressing_style": "path"}),
    }
    async with session.client("s3", **client_kwargs) as client:
        await client.put_bucket_website(
            Bucket="triviador-staging",
            WebsiteConfiguration={"IndexDocument": {"Suffix": "index.html"}},
        )
        try:
            assert await garage_probe.ready() is False
        finally:
            await client.delete_bucket_website(Bucket="triviador-staging")
    # Restored: the seeded state is ready again for whatever test runs next.
    assert await garage_probe.ready() is True
