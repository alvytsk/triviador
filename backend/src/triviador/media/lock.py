"""The one path §10.8's backup and `media-gc` must both flock.

`media-gc` deletes unreferenced media objects (`triviador.media.gc`) and
retires staging uploads (`triviador.imports.retire`); `infra/backup.sh`
`rclone copy`s the media bucket and then verifies it with `rclone check`.
If those two ever run concurrently without excluding each other,
`media-gc` can delete an object between the copy and the check and turn a
healthy backup into a spurious failure — or, worse, delete an object a
running backup has not copied yet.

The exclusion only holds if both sides `flock` the *same host path* — two
container-local paths of the same name resolve to different inodes and do
not exclude each other. So this constant is deliberately a fixed host
path, not a setting: `compose.prod.yaml` bind-mounts it, unchanged, into
both the `backend` service (where `triviador media-gc` runs) and the
`backup` service.

`infra/backup.sh` cannot import this module — it is a POSIX shell script,
not Python — so its `LOCK` variable duplicates this literal by hand. Keep
the two in sync; `infra/backup.sh` points back here in a comment.
"""

from pathlib import Path

MEDIA_LOCK_PATH = Path("/var/lock/triviador-media.lock")
