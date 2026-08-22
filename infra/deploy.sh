#!/bin/sh
# The ONLY supported way to deploy (§10.11).
#
# `up -d` alone is not enough: Compose does not reliably re-run a completed
# one-shot container just because the code inside it changed, so a deploy
# that skips the explicit `run` steps can silently skip a migration.
set -eu
cd "$(dirname "$0")/.."

COMPOSE="docker compose -f compose.yaml -f compose.prod.yaml"

./infra/render-secrets.sh
# A pure `docker compose config` check — no service needs to be running for
# it to work — so it runs here, before `build`/`up`, not after `up -d`
# below. Checked after `up -d` once published a debug `ports:` entry to the
# LAN (Postgres, in the case that was caught) before this script printed
# FATAL and exited, with nothing to tear the exposure back down. Here, a
# failure never got as far as publishing anything.
./infra/assert-ports.sh
# Must run before ANY service that binds the media lock path starts —
# `backend` (media-gc) and `backup` — or Docker auto-vivifies a missing
# host path as a root-owned directory, and both sides then fail to open
# it (see infra/provision-media-lock.sh for the full mechanism, and
# backend/src/triviador/media/lock.py for what the lock protects).
#
# Alternatives considered and rejected for the accompanying uid mismatch
# (backend: uid 10001, backup: root):
#   - `USER 10001` on infra/backup.Dockerfile would make the two share a
#     uid, but backup also writes into ${BACKUP_DEST} — an arbitrary
#     host-mounted destination (NAS/external drive/Windows disk) whose
#     ownership this repo does not control. Root maximizes the chance
#     that write succeeds across arbitrary host filesystems; giving that
#     up would trade a fixable permissions bug for a silent, harder one.
#     It would also only fix *this* lock issue as a side effect of a
#     shared uid — chmod 666 fixes it directly, and keeps working even
#     if the uids ever diverge again.
#   - A named Docker volume instead of the raw host bind path would not
#     remove this provisioning gap — the file inside the volume still
#     needs to be created by something before either service starts, so
#     it only relocates the problem. It would also give up the
#     operator-visible, stable host path §10.8 requires (needed so an
#     operator can inspect/clear the lock directly from the host).
#   - A fourth, standing one-shot compose service (like garage-init) would
#     need its own volume wiring in compose.yaml for what is pure
#     filesystem setup, and would still run through Compose's own
#     dependency graph rather than unconditionally first — no different
#     in kind from render-secrets.sh just above, which is already a
#     plain script run once before the stack builds. Instead
#     provision-media-lock.sh reaches for the middle ground: it stays a
#     script invoked here, but does the actual touch/chmod inside a
#     single disposable `docker run` rather than directly on the host
#     shell — see that script's own comment for why (a real
#     cross-runtime gap between a host-authored and a container-authored
#     file, found while proving this fix, not a hypothetical).
./infra/provision-media-lock.sh
$COMPOSE build
$COMPOSE up -d db garage
# One-shots, explicitly, in dependency order. Either failing aborts the
# deploy before anything serves traffic — which is the point.
$COMPOSE run --rm garage-init
$COMPOSE run --rm migrate
$COMPOSE up -d --remove-orphans
$COMPOSE ps
