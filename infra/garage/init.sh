#!/bin/sh
# §10.3's bootstrap, idempotent throughout: `infra/deploy.sh` runs it on
# every deploy and re-running must be free.
#
# Talks to the `garage` service over RPC using the same config file the
# server reads, so it needs the rendered rpc_secret mounted too.
set -eu

GARAGE="garage -c /etc/garage.toml"

# `up -d` returns when the container starts, which can precede the RPC
# listener by a second or two.
i=0
while [ "$i" -lt 60 ]; do
  if $GARAGE status >/dev/null 2>&1; then break; fi
  i=$((i + 1))
  sleep 1
done
$GARAGE status >/dev/null

# One node, one zone. `-c 1GB` — `1G` is NOT a valid suffix (B/KB/MB/GB/TB/PB).
#
# `assign` only *stages* a role — nothing is committed until `apply`, and a
# redundant `assign` (capacity unchanged from what's already live) stages
# nothing at all, so `assign` genuinely failing here is always a real error
# (bad node ID, bad capacity string), never "already done"; no `|| true`.
NODE_ID="$($GARAGE node id -q | cut -d@ -f1 | tr -d '\r')"
$GARAGE layout assign -z dc1 -c "${GARAGE_CAPACITY:-100GB}" "$NODE_ID"

# `apply` commits whatever `assign` just staged, at a specific version
# number that must be exactly current+1 — and fails with "Invalid new
# layout version" against any other number. Version 1 is only ever that
# number on the very first deploy; every deploy after that has already
# moved the layout past version 1, so a hardcoded `apply --version 1`
# fails from the second deploy onward, forever, and a bare `|| true` on it
# swallowed that: a later `GARAGE_CAPACITY` change would stage correctly
# and then never actually commit, deploy after deploy, with no error.
#
# Read the target version out of `layout show` instead of hardcoding it —
# it prints the exact `garage layout apply --version N` command needed —
# and only apply when something is actually staged (`assign` above may
# have been a no-op, in which case there is nothing to apply and `apply`
# itself would fail with nothing staged).
LAYOUT="$($GARAGE layout show)" || {
  echo "FATAL: could not query garage layout" >&2
  exit 1
}
if printf '%s\n' "$LAYOUT" | grep -q "STAGED ROLE CHANGES"; then
  LAYOUT_VERSION="$(printf '%s\n' "$LAYOUT" | sed -n 's/.*apply --version \([0-9][0-9]*\).*/\1/p')"
  if [ -z "$LAYOUT_VERSION" ]; then
    echo "FATAL: garage layout show staged changes but printed no 'apply --version' hint" >&2
    exit 1
  fi
  $GARAGE layout apply --version "$LAYOUT_VERSION"
fi

for bucket in "$S3_MEDIA_BUCKET" "$S3_STAGING_BUCKET"; do
  $GARAGE bucket create "$bucket" || true
done

# Imported, not created: credentials come from configuration and stay stable
# across rebuilds instead of being generated inside a container.
#
# `import` on a key ID that already exists always fails — Garage v1.1.0
# refuses to re-create a key ID unconditionally, whether or not the secret
# offered matches what it already holds (verified: same error either way,
# "Key ... already exists in data store"). A bare `|| true` here can't
# distinguish "already imported, nothing to do" from "the secret was
# rotated and Garage still has the old one" — the second case is a silent
# S3 auth failure at every request, with no signal from this script. There
# is no `garage key update`/`key rotate` in v1.1.0 to fix this cleanly, so
# the best available move is to detect the drift and fail loudly with
# instructions, rather than pretend nothing happened.
IMPORT_OUT="$($GARAGE key import --yes -n triviador-backend \
  "$S3_ACCESS_KEY_ID" "$S3_SECRET_ACCESS_KEY" 2>&1)" || {
  if printf '%s\n' "$IMPORT_OUT" | grep -qi "already exists"; then
    # `key info | sed` is a pipeline: without `pipefail`, its exit status is
    # `sed`'s, not `key info`'s, so a failed query would otherwise produce
    # an empty `STORED_SECRET` and *fail closed* here by accident (empty
    # never equals a real secret) rather than by a checked condition. Query
    # and check separately, the same fix as the website assertion below.
    KEY_INFO="$($GARAGE key info "$S3_ACCESS_KEY_ID" --show-secret)" || {
      echo "FATAL: could not query existing key $S3_ACCESS_KEY_ID" >&2
      exit 1
    }
    STORED_SECRET="$(printf '%s\n' "$KEY_INFO" | sed -n 's/^Secret key: //p')"
    if [ "$STORED_SECRET" != "$S3_SECRET_ACCESS_KEY" ]; then
      echo "FATAL: S3_SECRET_ACCESS_KEY does not match the secret Garage" \
        "already holds for key $S3_ACCESS_KEY_ID." >&2
      echo "Garage v1.1.0 has no key-rotation command: either restore the" \
        "original secret, or delete the key in Garage and re-import" \
        "(which mints a new key ID and requires updating configuration to" \
        "match)." >&2
      exit 1
    fi
  else
    echo "FATAL: garage key import failed: $IMPORT_OUT" >&2
    exit 1
  fi
}

for bucket in "$S3_MEDIA_BUCKET" "$S3_STAGING_BUCKET"; do
  $GARAGE bucket allow --read --write --owner "$bucket" --key triviador-backend
done

# Website-enabled, anonymous read — the media bucket, and only it.
$GARAGE bucket website --allow "$S3_MEDIA_BUCKET"

# THE ASSERTION THAT MATTERS. A staging bucket that ever becomes
# website-enabled publishes raw import uploads, answer keys included. Fail
# the job rather than let a deploy proceed past it.
#
# `garage bucket info` prints "Website access: true" or "Website access:
# false" — verified against a running dxflrs/garage:v1.1.0 (see task-4
# report). A pattern of "website.*enabled" never matches either line, which
# would make this guard report success unconditionally; match the real
# string instead.
#
# The query itself must be checked separately from the match: sitting
# inside `if $GARAGE ... | grep ...`, a failed `bucket info` (RPC hiccup, a
# bucket name from a misconfigured env var) writes its error to stderr,
# produces no stdout, and exits non-zero — `set -e` does not reach inside an
# `if`, and there is no `pipefail`, so `grep` against empty input simply
# doesn't match and the `if` is false. That reports "safe" without having
# checked anything, which is the same failure this guard exists to catch,
# reached by a second path. Capture the output and require the query to
# have actually succeeded before trusting a non-match.
INFO="$($GARAGE bucket info "$S3_STAGING_BUCKET")" || {
  echo "FATAL: could not query $S3_STAGING_BUCKET" >&2
  exit 1
}
if printf '%s\n' "$INFO" | grep -qi "website access: true"; then
  echo "FATAL: $S3_STAGING_BUCKET is website-enabled; raw imports would be public" >&2
  exit 1
fi

echo "garage-init: ok"
