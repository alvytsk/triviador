"""`prompt_digest`: the one function both the seed path and the import path
need to agree on what counts as "the same question".

Lives here, in `imports/`, rather than in `db/repositories/questions.py`
where it originated, because `imports/parse.py` needs it and
`tests/test_layering.py` forbids `imports/` from naming `triviador.db`.
`db/repositories/questions.py` re-exports it (see its own `__all__`) so
every existing caller — `QuestionSeeder`, `cli.py`'s `parse_seed_csv`,
`db/repositories/question_admin.py`, `tests/api/fakes.py` — keeps working
unchanged. One definition; nobody re-implements it.
"""

import hashlib


def prompt_digest(prompt: str) -> str:
    """Whitespace- and case-insensitive.

    Re-running the seed after reflowing a line in the CSV must not insert a
    second copy of a question the bank already has, and `questions.prompt_hash`
    is the only column that could tell the two apart.
    """
    return hashlib.sha256(" ".join(prompt.split()).casefold().encode("utf-8")).hexdigest()
