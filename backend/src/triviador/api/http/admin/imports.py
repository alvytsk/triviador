"""§10.3's two phases. This module is phase one; Task 8 appends phase two.

**The row is written before the object is staged.** The two stores share
no transaction, so one of them is first, and the choice is not arbitrary:
staging the object first and then failing to insert the row would leave an
untracked upload — full of correct answers — in a bucket nothing will ever
sweep, because every sweep starts from a `question_imports` row. Row
first, object second means the worst case is a row whose staged object is
missing, which `confirm` refuses with a reason and the expiry machine
retires on schedule.

**The filename arrives in `X-Filename`,** because the body is the file
(the same raw-body decision the media route documents). It decides only
`.zip` versus `.csv` parsing and what the staged object is called; it
never becomes a path.
"""

import csv
import hashlib
import io
import uuid
from collections.abc import Sequence
from datetime import datetime, timedelta

from fastapi import APIRouter, Header, Request
from fastapi.responses import PlainTextResponse

from triviador.api.deps import AdminPrincipal, Deps
from triviador.api.errors import ApiError, ApiErrorCode
from triviador.api.http.admin.media import CACHE_CONTROL, read_capped, repair_blob
from triviador.api.schemas.admin.imports import ImportNotice, ImportRejection, ImportSummary
from triviador.imports.digest import prompt_digest
from triviador.imports.parse import (
    Notice,
    ParsedImport,
    ParsedRow,
    Rejection,
    UploadRejected,
    parse_upload,
)
from triviador.media.pipeline import MediaRejected, NormalizedImage
from triviador.services.admin import ImportedImage, ImportedQuestion, ImportRecord, ImportStatus

router = APIRouter(prefix="/questions/import", tags=["admin"])


def _summary(record: ImportRecord, *, now: datetime) -> ImportSummary:
    """`confirmable` is three facts, not two.

    Status and rejection count are §10.3's rule; `expires_at` is §9.3's,
    and leaving it out would show a green CONFIRM button on an import the
    server will refuse — the client would then be the only place the
    expiry rule was *not* applied.
    """
    rejections = [
        ImportRejection(line=int(item["line"]), reason=str(item["reason"]))
        for item in record.report.get("rejections", ())
    ]
    notices = [
        ImportNotice(line=int(item["line"]), reason=str(item["reason"]))
        for item in record.report.get("notices", ())
    ]
    return ImportSummary(
        import_id=record.import_id,
        upload_sha256=record.upload_sha256,
        filename=record.filename,
        staged_key=record.staged_key,
        row_count=record.row_count,
        rejected_count=record.rejected_count,
        rejections=rejections,
        notices=notices,
        status=record.status,
        confirmable=(
            record.status is ImportStatus.VALIDATED
            and record.rejected_count == 0
            and record.expires_at > now
        ),
        expires_at=record.expires_at,
    )


async def _bank_duplicates(deps: Deps, rows: Sequence[ParsedRow]) -> tuple[Notice, ...]:
    """§10.2's other half: a prompt the bank already has is a warning here
    too, and the dry-run report is the only screen that can show it before
    the rows are applied.

    One query for the whole file, not one per row: a 500-row import would
    otherwise open 500 round trips to answer a question that is a single
    `WHERE prompt_hash IN (...)`.
    """
    digests = {row.line: prompt_digest(row.prompt) for row in rows}
    known = await deps.questions_admin.existing_prompt_digests(frozenset(digests.values()))
    return tuple(
        Notice(line=line, reason="a question with this prompt is already in the bank")
        for line, digest in sorted(digests.items())
        if digest in known
    )


async def _reject_unusable_media(deps: Deps, parsed: ParsedImport) -> tuple[Rejection, ...]:
    """Validate every referenced image now, and throw the result away.

    §9.3 re-encodes at confirm time, so this is the same work twice — and
    it is the price of §10.3's promise that `rejected == 0` means the
    confirm will not fail halfway. An import that discovered a corrupt
    JPEG during phase two would have to roll back a transaction the admin
    was told was safe.
    """
    extra: list[Rejection] = []
    for row in parsed.rows:
        if row.media_file is None:
            continue
        try:
            await deps.normalizer.normalize(parsed.media[row.media_file])
        except MediaRejected as exc:
            extra.append(
                Rejection(line=row.line, reason=f"{row.media_file}: {exc.reason}", raw=row.raw)
            )
    return tuple(extra)


@router.post("/dry-run", status_code=201)
async def dry_run(
    request: Request,
    deps: Deps,
    principal: AdminPrincipal,
    x_filename: str = Header(default="upload.csv"),
) -> ImportSummary:
    raw = await read_capped(request, deps.settings.import_max_bytes)
    try:
        parsed = parse_upload(raw, filename=x_filename)
    except UploadRejected as exc:
        raise ApiError(ApiErrorCode.VALIDATION_FAILED, 422, str(exc)) from exc

    media_rejections = await _reject_unusable_media(deps, parsed)
    rejected = tuple(parsed.rejections) + media_rejections
    media_rejected_lines = {r.line for r in media_rejections}
    accepted = tuple(r for r in parsed.rows if r.line not in media_rejected_lines)
    notices = tuple(parsed.notices) + await _bank_duplicates(deps, accepted)

    import_id = uuid.uuid4().hex
    staged_key = f"{import_id}/{x_filename.rsplit('/', 1)[-1]}"
    now = deps.clock.now()
    record = await deps.imports.create(
        import_id=import_id,
        uploaded_by=str(principal.user_id),
        upload_sha256=hashlib.sha256(raw).hexdigest(),
        filename=x_filename,
        staged_key=staged_key,
        row_count=len(accepted),
        rejected_count=len(rejected),
        report={
            "columns": list(rejected[0].raw) if rejected else [],
            "rejections": [
                {"line": r.line, "reason": r.reason, "raw": dict(r.raw)} for r in rejected
            ],
            "notices": [{"line": n.line, "reason": n.reason} for n in notices],
        },
        expires_at=now + timedelta(hours=deps.settings.import_ttl_hours),
    )
    content_type = "application/zip" if x_filename.lower().endswith(".zip") else "text/csv"
    await deps.staging_store.put(staged_key, raw, content_type=content_type)
    return _summary(record, now=now)


@router.get("/{import_id}/rejected.csv", response_class=PlainTextResponse)
async def rejected_csv(import_id: str, deps: Deps, principal: AdminPrincipal) -> PlainTextResponse:
    """The original rows plus a `reason` column — §10.3's fix-and-repeat
    loop. Built from the stored report, not from the staged object: the
    object may already have been retired, and the report is what the
    verdict was actually computed from."""
    record = await deps.imports.get(import_id)
    if record is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such import")
    rejections = record.report.get("rejections", [])
    columns = list(record.report.get("columns") or [])
    buffer = io.StringIO()
    writer = csv.DictWriter(buffer, fieldnames=[*columns, "reason"], extrasaction="ignore")
    writer.writeheader()
    for item in rejections:
        writer.writerow({**item.get("raw", {}), "reason": item["reason"]})
    return PlainTextResponse(buffer.getvalue(), media_type="text/csv; charset=utf-8")


@router.post("/{import_id}/confirm")
async def confirm_import(
    import_id: str, deps: Deps, principal: AdminPrincipal
) -> ImportSummary:
    """§9.3's order, and the reason each step is where it is.

        read staged object          — the upload, not what the client sent now
        recompute sha256            — and compare against the dry-run's
        validate + re-encode media  — CPU-bound, before any lock is taken
        write public blobs          — idempotent by content addressing
        BEGIN … FOR UPDATE … COMMIT — the only step that can lose a race

    Concurrent confirms duplicate the preprocessing and write the same
    blobs twice, which is safe precisely because the blobs are addressed
    by their content; only the transaction is serialised.
    """
    now = deps.clock.now()
    record = await deps.imports.get(import_id)
    if record is None:
        raise ApiError(ApiErrorCode.NOT_FOUND, 404, "no such import")
    if record.status is not ImportStatus.VALIDATED or record.rejected_count != 0:
        raise ApiError(
            ApiErrorCode.IMPORT_NOT_CONFIRMABLE,
            409,
            f"this import is {record.status.value} with {record.rejected_count} rejected rows",
        )
    if record.expires_at <= now:
        # Refused here for the message, and again under the lock for the
        # rule (`apply_if_confirmable`). §9.3 sets a TTL on the staged
        # upload; an import that outlived it must not be applicable just
        # because `media-gc` has not run since.
        raise ApiError(
            ApiErrorCode.IMPORT_NOT_CONFIRMABLE,
            409,
            "this import expired; upload it again",
        )
    if record.staged_key is None:
        raise ApiError(
            ApiErrorCode.IMPORT_NOT_CONFIRMABLE, 409, "the staged upload has been retired"
        )

    staged = await deps.staging_store.open(record.staged_key)
    if staged is None:
        raise ApiError(
            ApiErrorCode.IMPORT_NOT_CONFIRMABLE, 409, "the staged upload is no longer available"
        )
    if hashlib.sha256(staged).hexdigest() != record.upload_sha256:
        raise ApiError(
            ApiErrorCode.IMPORT_NOT_CONFIRMABLE,
            409,
            "the staged upload changed since it was validated; run the dry-run again",
        )

    parsed = parse_upload(staged, filename=record.filename)
    normalized: dict[str, NormalizedImage] = {}
    try:
        for row in parsed.rows:
            if row.media_file is not None and row.media_file not in normalized:
                normalized[row.media_file] = await deps.normalizer.normalize(
                    parsed.media[row.media_file]
                )
    except MediaRejected as exc:
        # Dry-run validated these exact bytes — the sha match above proves
        # they *are* the same bytes — so this is unreachable within one
        # running process. It is not unreachable across a deploy: the
        # limits live on `ImageNormalizer`, built from settings at process
        # start, and an operator who tightens `media_max_bytes` between an
        # admin's dry-run and their confirm (well inside `IMPORT_TTL_HOURS`)
        # makes an image that passed then fail now. That is an ordinary
        # "run the dry-run again", not a server fault, and letting it reach
        # the catch-all handler would report it as a 500.
        raise ApiError(
            ApiErrorCode.IMPORT_NOT_CONFIRMABLE,
            409,
            f"{exc.reason}; the media limits changed since this upload was validated — "
            "run the dry-run again",
        ) from exc
    for image in normalized.values():
        await deps.media_store.put(
            image.storage_key,
            image.data,
            content_type=image.mime_type,
            cache_control=CACHE_CONTROL,
        )

    applied = await deps.imports.apply_if_confirmable(
        import_id,
        rows=tuple(
            ImportedQuestion(
                category_slug=row.category_slug,
                kind=row.kind,
                prompt=row.prompt,
                difficulty=row.difficulty,
                media_file=row.media_file,
                choices=row.choices,
                numeric_answer=row.numeric_answer,
                unit=row.unit,
            )
            for row in parsed.rows
        ),
        images={
            name: ImportedImage(
                asset_id=image.sha256,
                mime_type=image.mime_type,
                width=image.width,
                height=image.height,
                byte_size=image.byte_size,
                storage_key=image.storage_key,
            )
            for name, image in normalized.items()
        },
        uploaded_by=str(principal.user_id),
        now=now,
    )
    if not applied:
        # Lost the `FOR UPDATE` race, or the row changed underneath. The
        # blobs written above stay; they are content-addressed, and
        # `media-gc` collects them if nothing ends up referencing them.
        raise ApiError(
            ApiErrorCode.IMPORT_NOT_CONFIRMABLE, 409, "this import was already confirmed"
        )
    # Same repair as the upload route, for the same window: the blobs were
    # written before the transaction (§9.3's order), so a sweep in between
    # could have taken one. The bytes are still in memory here.
    for image in normalized.values():
        await repair_blob(deps, image)

    confirmed = await deps.imports.get(import_id)
    assert confirmed is not None
    return _summary(confirmed, now=now)
