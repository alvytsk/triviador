"""`.csv` or `.zip` in, rows and rejections out. Nothing is written here.

**Row rejections versus upload rejections.** A row that fails its own
rules is reported by line number and the rest of the file is still
parsed — §10.3's workflow is "download the rejected rows, fix them,
repeat", which needs the good rows counted. A file whose *header* is
wrong, or whose archive has no `questions.csv`, has no rows to report at
all, so it raises `UploadRejected` and the request fails as a whole.

**Everything is held in memory, deliberately.** The upload is capped at
`import_max_bytes` by the route, the archive is capped again here by
expanded size, and the alternative — spooling to a temp file — would put
answer keys on the application container's disk, which nothing else in
this system does.
"""

import csv
import io
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from triviador.domain.questions.types import Difficulty, QuestionKind
from triviador.imports.digest import prompt_digest

COLUMNS = (
    "kind",
    "prompt",
    "category",
    "difficulty",
    "choice_1",
    "choice_2",
    "choice_3",
    "choice_4",
    "correct_index",
    "numeric_answer",
    "unit",
    "media_file",
)

CHOICE_COUNT = 4
MAX_EXPANDED_BYTES = 128 * 1024 * 1024


class UploadRejected(Exception):
    """The upload as a whole is unusable; there is nothing to report per row."""


@dataclass(frozen=True)
class ParsedRow:
    line: int
    kind: str
    prompt: str
    category_slug: str
    difficulty: str
    choices: tuple[tuple[str, bool], ...] | None
    numeric_answer: Decimal | None
    unit: str | None
    media_file: str | None
    raw: Mapping[str, str]


@dataclass(frozen=True)
class Rejection:
    line: int
    reason: str
    raw: Mapping[str, str]


@dataclass(frozen=True)
class Notice:
    """Something the admin should see and may ignore.

    Separate from `Rejection` because the two have opposite consequences:
    a rejection makes the upload unconfirmable (§10.3), a notice does not.
    Collapsing them is how §10.2's "warning, not a block" quietly becomes
    a block.
    """

    line: int
    reason: str


@dataclass(frozen=True)
class ParsedImport:
    rows: tuple[ParsedRow, ...] = ()
    rejections: tuple[Rejection, ...] = ()
    notices: tuple[Notice, ...] = ()
    media: Mapping[str, bytes] = field(default_factory=dict)


def parse_upload(data: bytes, *, filename: str) -> ParsedImport:
    if filename.lower().endswith(".zip"):
        text, media = _open_archive(data)
    else:
        text, media = data.decode("utf-8-sig", errors="replace"), {}
    return _parse_rows(text, media, archive=filename.lower().endswith(".zip"))


def _open_archive(data: bytes) -> tuple[str, dict[str, bytes]]:
    try:
        archive = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile as exc:
        raise UploadRejected("that file is not a readable .zip archive") from exc

    with archive:
        names = archive.namelist()
        if any(name.startswith("/") or ".." in name.split("/") for name in names):
            raise UploadRejected("the archive contains an unsafe path")
        if sum(info.file_size for info in archive.infolist()) > MAX_EXPANDED_BYTES:
            raise UploadRejected(
                f"the archive expands to more than {MAX_EXPANDED_BYTES} bytes"
            )
        if "questions.csv" not in names:
            raise UploadRejected("the archive must contain questions.csv")
        text = archive.read("questions.csv").decode("utf-8-sig", errors="replace")
        media = {
            name.removeprefix("media/"): archive.read(name)
            for name in names
            if name.startswith("media/") and not name.endswith("/")
        }
    return text, media


def _parse_rows(text: str, media: Mapping[str, bytes], *, archive: bool) -> ParsedImport:
    reader = csv.DictReader(io.StringIO(text))
    if tuple(reader.fieldnames or ()) != COLUMNS:
        raise UploadRejected(f"header must be exactly {','.join(COLUMNS)}")

    rows: list[ParsedRow] = []
    rejections: list[Rejection] = []
    notices: list[Notice] = []
    seen: dict[str, int] = {}
    for line, raw in enumerate(reader, start=2):
        try:
            row = _parse_row(line, raw, media, archive=archive)
        except ValueError as exc:
            rejections.append(Rejection(line=line, reason=str(exc), raw=raw))
            continue
        digest = prompt_digest(row.prompt)
        if digest in seen:
            # A notice, and the row is still imported: §10.2 says a digest
            # match is a warning on save *and on import*, and legitimately
            # similar phrasings normalise to the same digest.
            notices.append(
                Notice(
                    line=line,
                    reason=f"duplicate prompt: same as line {seen[digest]} of this upload",
                )
            )
        seen.setdefault(digest, line)
        rows.append(row)
    return ParsedImport(
        rows=tuple(rows),
        rejections=tuple(rejections),
        notices=tuple(notices),
        media=media,
    )


def _parse_row(
    line: int, raw: Mapping[str, str], media: Mapping[str, bytes], *, archive: bool
) -> ParsedRow:
    def cell(name: str) -> str:
        return (raw.get(name) or "").strip()

    kind = cell("kind")
    if kind not in {k.value for k in QuestionKind}:
        raise ValueError(f"unknown kind {kind!r}")
    difficulty = cell("difficulty")
    if difficulty not in {d.value for d in Difficulty}:
        raise ValueError(f"unknown difficulty {difficulty!r}")
    prompt = cell("prompt")
    if not prompt:
        raise ValueError("empty prompt")
    category = cell("category")
    if not category:
        raise ValueError("empty category")

    media_file = cell("media_file") or None
    if media_file is not None:
        if not archive:
            raise ValueError("a plain .csv cannot reference media; upload a .zip instead")
        if media_file not in media:
            raise ValueError(f"media file {media_file!r} is not in the archive")

    choices = tuple(cell(f"choice_{i}") for i in (1, 2, 3, 4))
    answer = cell("numeric_answer")
    unit = cell("unit") or None
    index_raw = cell("correct_index")

    if kind == QuestionKind.NUMERIC.value:
        if any(choices) or index_raw:
            raise ValueError("a numeric question carries no choices")
        if not answer:
            raise ValueError("a numeric question needs a numeric_answer")
        try:
            value = Decimal(answer)
        except InvalidOperation as exc:
            raise ValueError(f"numeric_answer {answer!r} is not a decimal number") from exc
        if not value.is_finite():
            raise ValueError("numeric_answer must be finite")
        return ParsedRow(line, kind, prompt, category, difficulty, None, value, unit,
                         media_file, raw)

    if answer or unit:
        raise ValueError("a multiple-choice question carries no numeric_answer or unit")
    if sum(1 for c in choices if c) != CHOICE_COUNT:
        raise ValueError("a multiple-choice question needs exactly four choices")
    if not index_raw.isdigit() or int(index_raw) >= CHOICE_COUNT:
        raise ValueError(f"correct_index {index_raw!r} is not 0..3")
    correct = int(index_raw)
    return ParsedRow(
        line,
        kind,
        prompt,
        category,
        difficulty,
        tuple((text, idx == correct) for idx, text in enumerate(choices)),
        None,
        None,
        media_file,
        raw,
    )
