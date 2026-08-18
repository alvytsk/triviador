"""`configure_logging` mutates two pieces of process-global state:
`structlog.configure(...)` and `logging.basicConfig(...)`. Both persist
past the end of whichever test called them — there is no `structlog`
un-configure and `basicConfig` only ever adds a handler, never removes
one — so without this fixture, `test_logging.py`'s tests would leave every
later test in the session logging JSON to stdout through a leftover
`StreamHandler`, whether or not that later test wanted JSON logging at all.

This is a plain (non-yielding-until-teardown-matters) autouse fixture in
this directory's `conftest.py`, not in `test_logging.py` itself: pytest
instantiates a parent conftest's autouse fixture *before* a same-scope
autouse fixture declared in the test module, and tears it down *after* —
confirmed empirically, not assumed — so the snapshot taken here is the
pristine pre-`configure_logging` state, and the restore runs after
whatever `test_logging.py`'s own `json_logging` fixture did.
"""

import logging
from collections.abc import Iterator

import pytest
import structlog


@pytest.fixture(autouse=True)
def _restore_logging_globals() -> Iterator[None]:
    structlog_config = structlog.get_config()
    root = logging.getLogger()
    handlers = list(root.handlers)
    level = root.level
    yield
    structlog.configure(**structlog_config)
    root.handlers[:] = handlers
    root.setLevel(level)
