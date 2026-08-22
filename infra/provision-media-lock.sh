#!/bin/sh
# Pre-creates the host media-lock file BEFORE any container that binds it
# starts — `backend` (where `triviador media-gc` runs) and `backup`
# (§10.8) both bind-mount this exact host path; see
# backend/src/triviador/media/lock.py for what it protects and why it
# must be the identical host inode on both sides.
#
# Two bugs, compounding, on a freshly booted or freshly restored host:
#
#   1. `/var/lock` is a symlink to `/run/lock`, which is tmpfs on both
#      this repo's base images (python:3.13-slim, alpine) — so
#      `triviador-media.lock` does NOT survive a reboot. The first
#      `docker compose up` after a reboot then bind-mounts a source path
#      that does not exist, and Docker's bind-mount behaviour creates the
#      missing source as a DIRECTORY, not a file. Nothing at container
#      startup opens it, so nothing catches this at boot — it only
#      surfaces later as `IsADirectoryError` (media-gc's
#      `open(MEDIA_LOCK_PATH, "a")`) or `flock: Is a directory` (from
#      backup.sh's `exec 9>"$LOCK"`).
#
#   2. `backend` runs as uid 10001 (infra/backend.Dockerfile); `backup`
#      runs as root (infra/backup.Dockerfile has no USER). Whichever side
#      creates the file first fixes its ownership. If a directory never
#      got auto-vivified and root creates the plain file first (default
#      umask, mode 644), a later uid-10001 `open()` fails with
#      `PermissionError` — a real, one-directional lockout that only an
#      operator's manual chmod can clear.
#
# Fix: create the file explicitly, as a REGULAR file, before either
# container can auto-vivify it, and make it permissive enough (mode 666)
# for both uids to open regardless of which one gets there first.
#
# This runs the actual touch/chmod INSIDE a disposable container, not as
# a plain shell command on the host. That is deliberate, not decoration:
# while developing this fix, a host-shell-created file (`: > "$LOCK"`,
# `chmod 666`) worked on a plain Linux Docker Engine but was *not*
# reliably reachable from a container on every runtime tested — one
# Docker Desktop configuration's bind-mount layer kept a host-shell
# authored file readable only by the exact host uid that created it,
# chmod notwithstanding, while a file the container runtime itself
# authored honoured normal mode bits for any uid. Creating the file from
# inside a container sidesteps that gap entirely: it behaves identically
# on a plain Docker Engine (where either approach was already fine) and
# also works on the runtime where the two paths diverged. `alpine:3.21`
# matches infra/backup.Dockerfile's own pin — nothing repo-specific is
# needed for a touch and a chmod, so no dedicated Dockerfile was added
# for this (see infra/deploy.sh's call site for the fuller comparison
# against a real one-shot compose service and the other alternatives).
set -eu

LOCK=/var/lock/triviador-media.lock
LOCKDIR=$(dirname "$LOCK")

docker run --rm --user 0:0 -e LOCK="$LOCK" -v "$LOCKDIR:$LOCKDIR" alpine:3.21 sh -c '
set -eu
if [ -d "$LOCK" ]; then
  echo "FATAL: $LOCK is a directory — Docker auto-vivified it on a prior" >&2
  echo "       run that skipped this script. Remove it (rmdir \"\$LOCK\") and re-run." >&2
  exit 1
fi
rm -f "$LOCK"
: > "$LOCK"
chmod 666 "$LOCK"
'

echo "provision-media-lock: ok ($LOCK)"
