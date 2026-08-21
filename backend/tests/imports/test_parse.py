"""Pure. No database, no bucket, no event loop — the format is the whole
subject, and every rejection here is a line an admin has to fix in a
spreadsheet, so each one names its line number.
"""

import io
import zipfile
from decimal import Decimal

import pytest

from triviador.imports.parse import UploadRejected, parse_upload

HEADER = (
    "kind,prompt,category,difficulty,"
    "choice_1,choice_2,choice_3,choice_4,correct_index,numeric_answer,unit,media_file"
)
MC = "multiple_choice,Which river runs through Prague?,geography,easy,Vltava,Elbe,Morava,Ohře,0,,,"
NUM = "numeric,In which year did the Velvet Revolution begin?,history,easy,,,,,,1989,,"


def csv_bytes(*lines: str) -> bytes:
    return "\n".join((HEADER, *lines)).encode("utf-8")


def zip_bytes(csv: bytes, media: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("questions.csv", csv)
        for name, blob in media.items():
            archive.writestr(f"media/{name}", blob)
    return buffer.getvalue()


def test_a_plain_csv_parses_both_kinds() -> None:
    parsed = parse_upload(csv_bytes(MC, NUM), filename="bank.csv")
    assert parsed.rejections == ()
    assert [r.kind for r in parsed.rows] == ["multiple_choice", "numeric"]
    assert parsed.rows[0].choices == (
        ("Vltava", True), ("Elbe", False), ("Morava", False), ("Ohře", False)
    )
    assert parsed.rows[1].numeric_answer == Decimal("1989")


def test_a_bad_row_is_rejected_by_line_number_and_the_rest_survive() -> None:
    """Row-level, not file-level: the admin gets a rejected-rows CSV to fix
    and re-upload, which is only useful if the good rows were accepted in
    the report."""
    parsed = parse_upload(
        csv_bytes(MC, "numeric,No answer here,history,easy,,,,,,,,"), filename="b.csv"
    )
    assert [r.line for r in parsed.rejections] == [3]
    assert "answer" in parsed.rejections[0].reason
    assert len(parsed.rows) == 1


@pytest.mark.parametrize(
    ("row", "reason"),
    [
        ("multiple_choice,Three choices,geography,easy,A,B,C,,0,,,", "four"),
        ("multiple_choice,Bad index,geography,easy,A,B,C,D,9,,,", "correct_index"),
        ("multiple_choice,With answer,geography,easy,A,B,C,D,0,12,,", "numeric"),
        ("numeric,With choices,history,easy,A,B,C,D,,1989,,", "choices"),
        ("numeric,Not a number,history,easy,,,,,,twelve,,", "decimal"),
        ("picture,Unknown kind,history,easy,,,,,,1,,", "kind"),
        ("numeric,Unknown difficulty,history,trivial,,,,,,1,,", "difficulty"),
        ("numeric,,history,easy,,,,,,1,,", "prompt"),
        # Important #2 of the Plan 7A review: `CreateCategoryRequest.slug`
        # enforces `^[a-z0-9]+(-[a-z0-9]+)*$` on the interactive route: an
        # importer that accepted anything non-empty here was the one path
        # that could still create "Pop Music" and "pop-music" as two
        # categories nobody could tell apart on screen.
        ("numeric,Capitalised category,Pop Music,easy,,,,,,1,,", "slug"),
        ("numeric,Underscore is not a dash,pop_music,easy,,,,,,1,,", "slug"),
    ],
)
def test_each_row_level_rule(row: str, reason: str) -> None:
    parsed = parse_upload(csv_bytes(row), filename="b.csv")
    assert parsed.rows == ()
    assert reason in parsed.rejections[0].reason


def test_a_duplicate_prompt_inside_one_file_is_a_warning_not_a_rejection() -> None:
    """§10.2 is unambiguous: a prompt-digest match "surfaces a warning,
    not a block", on save *and on import*. Rejecting here would also make
    the upload unconfirmable (§10.3 gates confirm on `rejected == 0`), so
    a file with one accidental repeat could never be applied at all —
    which is a block wearing a warning's name."""
    parsed = parse_upload(csv_bytes(NUM, NUM), filename="b.csv")
    assert len(parsed.rows) == 2
    assert parsed.rejections == ()
    assert [n.line for n in parsed.notices] == [3]
    assert "duplicate" in parsed.notices[0].reason


def test_a_wrong_header_is_a_whole_upload_rejection() -> None:
    """Not a row rejection: a file with the wrong columns has no rows to
    report on, and "1000 rejected rows" would bury the one fact that
    matters."""
    with pytest.raises(UploadRejected, match="header"):
        parse_upload(b"a,b,c\n1,2,3", filename="b.csv")


def test_a_zip_carries_its_media() -> None:
    parsed = parse_upload(zip_bytes(csv_bytes(MC.replace(",,,", ",,,river.png")),
                                    {"river.png": b"PNGDATA"}), filename="bank.zip")
    assert parsed.rows[0].media_file == "river.png"
    assert parsed.media["river.png"] == b"PNGDATA"


def test_a_row_naming_a_missing_media_file_is_rejected() -> None:
    parsed = parse_upload(
        zip_bytes(csv_bytes(MC.replace(",,,", ",,,absent.png")), {}), filename="b.zip"
    )
    assert parsed.rows == ()
    assert "absent.png" in parsed.rejections[0].reason


def test_a_csv_may_not_reference_media_at_all() -> None:
    """§10.3: "Plain `.csv` is accepted without images." A media reference
    with no archive to hold it is a mistake worth naming."""
    parsed = parse_upload(csv_bytes(MC.replace(",,,", ",,,river.png")), filename="b.csv")
    assert "media" in parsed.rejections[0].reason


def test_a_zip_without_questions_csv_is_rejected() -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("other.csv", csv_bytes(MC))
    with pytest.raises(UploadRejected, match=r"questions\.csv"):
        parse_upload(buffer.getvalue(), filename="b.zip")


def test_a_traversal_path_in_the_archive_is_refused() -> None:
    """`media/../../etc/passwd` never reaches a filesystem here — nothing
    is extracted to disk — but a name like that is either an attack or a
    corrupt archive, and treating it as an ordinary key would put it in an
    anonymously readable bucket."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("questions.csv", csv_bytes(MC))
        archive.writestr("media/../../escape.png", b"x")
    with pytest.raises(UploadRejected, match="path"):
        parse_upload(buffer.getvalue(), filename="b.zip")


def test_an_archive_that_expands_absurdly_is_refused() -> None:
    """A zip bomb: 40 MB of zeroes compresses to a few kilobytes, and this
    parser holds what it reads in memory."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("questions.csv", csv_bytes(MC))
        archive.writestr("media/huge.png", b"\0" * (200 * 1024 * 1024))
    with pytest.raises(UploadRejected, match="expands"):
        parse_upload(buffer.getvalue(), filename="b.zip")
