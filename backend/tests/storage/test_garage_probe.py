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
