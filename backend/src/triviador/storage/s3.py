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
from botocore.exceptions import BotoCoreError, ClientError

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


class S3GarageProbe:
    """Implements `services.ports.GarageProbe`, over the plain S3 API — the
    backend holds an S3 access key, not the Garage admin RPC socket
    `infra/garage/init.sh` talks to, so this checks the same two facts
    that script's own guard checks, through the only door the backend has.

    `head_bucket` answers "does this bucket exist" the ordinary way: a
    `ClientError` (404 for a missing bucket, but any `ClientError` is
    treated the same — an unreachable Garage is exactly as not-ready as a
    missing bucket).

    "Unreachable" is not itself a `ClientError`, though — that class covers
    Garage *answering* with an S3 error response. A Garage that never
    accepted the connection (not started yet, network blip, wrong port)
    raises a `botocore.exceptions.BotoCoreError` instead — sibling to, not
    a subclass of, `ClientError` — so `ready()` catches both, around the
    whole `head_bucket`/`get_bucket_website` sequence. Every `BotoCoreError`
    reachable from here is transport/config level (connection refused, DNS
    or endpoint resolution failure, TLS handshake failure, a timeout) —
    this class never builds a call whose *parameters* botocore could reject
    on their own merits (`ParamValidationError` and its relatives): the
    bucket names and credentials are opaque strings handed in once, from
    `Settings`, and never inspected or reshaped here. So there is no
    `BotoCoreError` subclass this method can raise that indicates a bug in
    this class's own logic rather than an environment problem, and none is
    special-cased out of the catch. A defect in a *caller* (e.g. a Python
    `TypeError` from a genuine bug elsewhere) is not a `BotoCoreError` and
    still propagates — this stays narrower than `except Exception`.

    The website check does **not** work the way it would against real
    AWS S3, where `get_bucket_website` raises `NoSuchWebsiteConfiguration`
    for an unconfigured bucket. Verified empirically against a running
    `dxflrs/garage:v1.1.0`: Garage's `get_bucket_website` returns success —
    HTTP 204, an *empty* body — for a bucket with no website configuration,
    and only populates `IndexDocument` (or the other website fields) once
    one is actually set. Treating "it didn't raise" as "it's the good
    state" the way the AWS-shaped API suggests would make this guard
    report ready unconditionally, on every Garage version this runs
    against — so the signal used here is the response's *content*, not
    whether the call raised.
    """

    def __init__(
        self,
        *,
        endpoint_url: str,
        region: str,
        access_key_id: str,
        secret_access_key: str,
        media_bucket: str,
        staging_bucket: str,
    ) -> None:
        self._session = aioboto3.Session()
        self._media_bucket = media_bucket
        self._staging_bucket = staging_bucket
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

    @asynccontextmanager
    async def _client(self) -> AsyncIterator[Any]:
        async with self._session.client("s3", **self._client_kwargs) as client:
            yield client

    async def ready(self) -> bool:
        try:
            async with self._client() as client:
                for bucket in (self._media_bucket, self._staging_bucket):
                    try:
                        await client.head_bucket(Bucket=bucket)
                    except ClientError:
                        return False
                try:
                    website = await client.get_bucket_website(Bucket=self._staging_bucket)
                except ClientError:
                    # Real S3's "not configured" answer. Never observed against
                    # Garage v1.1.0 (see class docstring), kept for the API
                    # contract `get_bucket_website` documents.
                    return True
                # Garage's "not configured" answer: success, with nothing in
                # the body beyond `ResponseMetadata`.
                return not any(key != "ResponseMetadata" for key in website)
        except BotoCoreError:
            # Garage never answered at all — connection refused, not yet
            # started, a network blip, a timeout. Not a `ClientError`
            # (see class docstring), so it needs its own handler; wrapped
            # around the whole sequence, not one call, because any of the
            # three requests above can be the one that fails to connect.
            return False
