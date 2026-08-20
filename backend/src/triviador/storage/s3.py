"""One client factory, two thin stores over it.

**Path addressing, always.** Garage serves buckets at
`http://host:3900/<bucket>/<key>`; virtual-host addressing would resolve
`triviador-media.<host>`, which does not exist on a LAN and fails as a DNS
error rather than as anything an operator can read.

**A client per call.** `aioboto3`'s client is an async context manager
holding a connection pool, and holding one open for the process lifetime
means owning its lifecycle across the app's own startup and shutdown for
no gain: admin traffic is a handful of requests from one or two people
(§1.1), and `aiohttp`'s connector setup is microseconds against a LAN
round trip. If that ever stops being true, the fix is one shared client
opened in `lifespan`, not a cache keyed on nothing.
"""

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import aioboto3
from botocore.config import Config as BotoConfig
from botocore.exceptions import ClientError

from triviador.services.storage import ObjectHead, StoredObject

_MISSING = {"404", "NoSuchKey", "NotFound"}


class _S3Base:
    def __init__(
        self,
        *,
        endpoint_url: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        bucket: str,
    ) -> None:
        self._session = aioboto3.Session()
        self._bucket = bucket
        self._client_kwargs: dict[str, Any] = {
            "endpoint_url": endpoint_url,
            "region_name": region,
            "aws_access_key_id": access_key_id,
            "aws_secret_access_key": secret_access_key,
            "config": BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                retries={"max_attempts": 3, "mode": "standard"},
            ),
        }

    @property
    def bucket(self) -> str:
        """Read by the wiring test, which asserts the two stores differ."""
        return self._bucket

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Any]:
        async with self._session.client("s3", **self._client_kwargs) as client:
            yield client

    async def _put(self, key: str, data: bytes, extra: dict[str, Any]) -> None:
        async with self._client() as client:
            await client.put_object(Bucket=self._bucket, Key=key, Body=data, **extra)

    async def open(self, key: str) -> bytes | None:
        async with self._client() as client:
            try:
                response = await client.get_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in _MISSING:
                    return None
                raise
            body: bytes = await response["Body"].read()
            return body

    async def delete(self, key: str) -> None:
        # S3 `DeleteObject` is already idempotent — deleting an absent key
        # is a 204 — so this needs no `try`. Asserted by
        # `test_delete_is_idempotent` rather than assumed, because it is
        # the property §9.3's retryable state machine rests on.
        async with self._client() as client:
            await client.delete_object(Bucket=self._bucket, Key=key)


class S3ImportStagingStore(_S3Base):
    """Implements `services.storage.ImportStagingStore`."""

    async def put(self, key: str, data: bytes, *, content_type: str) -> None:
        await self._put(key, data, {"ContentType": content_type})


class S3MediaStore(_S3Base):
    """Implements `services.storage.MediaStore`."""

    async def put(
        self, key: str, data: bytes, *, content_type: str, cache_control: str | None = None
    ) -> None:
        extra: dict[str, Any] = {"ContentType": content_type}
        if cache_control is not None:
            # §9.2: set at PUT time as object metadata, so a 404 does not
            # inherit a one-year cache lifetime the way a blanket proxy
            # header would give it one.
            extra["CacheControl"] = cache_control
        await self._put(key, data, extra)

    async def head(self, key: str) -> ObjectHead | None:
        async with self._client() as client:
            try:
                response = await client.head_object(Bucket=self._bucket, Key=key)
            except ClientError as exc:
                if exc.response.get("Error", {}).get("Code") in _MISSING:
                    return None
                raise
        return ObjectHead(
            byte_size=int(response["ContentLength"]),
            content_type=str(response.get("ContentType", "")),
            cache_control=response.get("CacheControl"),
            last_modified=response["LastModified"],
        )

    async def list_objects(self, *, prefix: str = "") -> tuple[StoredObject, ...]:
        objects: list[StoredObject] = []
        async with self._client() as client:
            paginator = client.get_paginator("list_objects_v2")
            async for page in paginator.paginate(Bucket=self._bucket, Prefix=prefix):
                objects.extend(
                    StoredObject(
                        key=item["Key"],
                        byte_size=int(item["Size"]),
                        # botocore parses this into an aware datetime; the
                        # grace period compares it against `clock.now()`,
                        # which is also aware (§8.6 has no naive datetimes
                        # anywhere in this system).
                        last_modified=item["LastModified"],
                    )
                    for item in page.get("Contents", ())
                )
        return tuple(objects)
