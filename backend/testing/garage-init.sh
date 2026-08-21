#!/usr/bin/env bash
# Initialise the test Garage: layout, buckets, key, website.
#
# Runs on the host, because `dxflrs/garage:v1.1.0` contains no shell —
# `/bin/sh` does not exist in that image, so an init *service* running a
# script inside it is not possible. Every command here is instead
# `docker compose exec` of the `/garage` binary, which needs no shell.
#
# Idempotent throughout: the harness has no way to know whether a previous
# `docker compose up` already initialised this node, and re-running must be
# free. Verified against dxflrs/garage:v1.1.0 (see Task 2 Step 1) — every
# flag below was checked against that image's `--help`.
#
# Usage, after `docker compose -f docker-compose.test.yml up -d`:
#     ./testing/garage-init.sh
set -euo pipefail

COMPOSE=(docker compose -f "$(dirname "$0")/../docker-compose.test.yml")
# `TRIVIADOR_TEST_S3_*`, matching `tests/storage/conftest.py`'s spelling —
# the two used to disagree (`TEST_S3_*` here, `TRIVIADOR_TEST_S3_*` there)
# with identical defaults, which made the mismatch invisible until someone
# overrode one and got a Garage auth error in the file they were not
# editing.
KEY_ID="${TRIVIADOR_TEST_S3_KEY_ID:-GK111111111111111111111111}"
KEY_SECRET="${TRIVIADOR_TEST_S3_KEY_SECRET:-2222222222222222222222222222222222222222222222222222222222222222}"

garage() { "${COMPOSE[@]}" exec -T garage-test /garage "$@"; }

# Wait for the daemon: `up -d` returns as soon as the container starts, and
# the first `garage` call can beat the RPC listener by a second or two.
for _ in $(seq 1 30); do
  if garage status >/dev/null 2>&1; then break; fi
  sleep 1
done

# One node, one zone, 1 GB of nominal capacity ("1G" is not a valid suffix —
# the accepted set is B, KB, MB, GB, TB, PB). `layout apply` fails once the
# layout is already at that version, which is what `|| true` absorbs on a
# re-run.
NODE_ID="$(garage node id -q | cut -d@ -f1 | tr -d '\r')"
garage layout assign -z dc1 -c 1GB "$NODE_ID" || true
garage layout apply --version 1 || true

for bucket in triviador-media triviador-staging; do
  garage bucket create "$bucket" || true
done

garage key import --yes -n test "$KEY_ID" "$KEY_SECRET" || true

for bucket in triviador-media triviador-staging; do
  garage bucket allow --read --write --owner "$bucket" --key test
done

# Website-enabled, anonymous read — §9.1's media bucket, and only it. If
# this line ever names the staging bucket, raw import uploads (answer keys
# included) become anonymously readable.
garage bucket website --allow triviador-media

garage bucket info triviador-media
garage bucket info triviador-staging
