"""§6.1's admin surface: six resources, one guard, one prefix.

The guard is declared on this router rather than on each route below it.
That is the whole reason this package has an `__init__.py` with code in
it: a new module under `http/admin/` inherits the guard by being included,
and "forgot the dependency" stops being a thing that can happen.
`tests/api/test_admin_guard.py` walks the built app and fails on any
`/api/admin` route whose dependency tree does not contain `current_admin`.
"""

from fastapi import APIRouter, Depends

from triviador.api.deps import current_admin

# The two routes that take a body larger than `max_body_bytes`, and so opt
# out of `BodyLimitMiddleware`'s buffering. Each imposes its own cap while
# reading — `media_max_bytes` and `import_max_bytes` respectively. Exempt
# *paths*, not "anything under /api/admin": an exemption is a hole, and a
# hole the width of a whole router is one nobody would notice widening.
UPLOAD_PATHS = ("/api/admin/media", "/api/admin/questions/import/dry-run")


def build_admin_router(*routers: APIRouter) -> APIRouter:
    router = APIRouter(prefix="/api/admin", tags=["admin"], dependencies=[Depends(current_admin)])
    for sub in routers:
        router.include_router(sub)
    return router


# Sub-routers are added to this call as the tasks that create them land.
router = build_admin_router()
