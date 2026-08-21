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
# `layout apply` fails once the layout is already at that version, which is
# what the `|| true` absorbs on every deploy after the first.
NODE_ID="$($GARAGE node id -q | cut -d@ -f1 | tr -d '\r')"
$GARAGE layout assign -z dc1 -c "${GARAGE_CAPACITY:-100GB}" "$NODE_ID" || true
$GARAGE layout apply --version 1 || true

for bucket in "$S3_MEDIA_BUCKET" "$S3_STAGING_BUCKET"; do
  $GARAGE bucket create "$bucket" || true
done

# Imported, not created: credentials come from configuration and stay stable
# across rebuilds instead of being generated inside a container.
$GARAGE key import --yes -n triviador-backend \
  "$S3_ACCESS_KEY_ID" "$S3_SECRET_ACCESS_KEY" || true

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
if $GARAGE bucket info "$S3_STAGING_BUCKET" | grep -qi "website access: true"; then
  echo "FATAL: $S3_STAGING_BUCKET is website-enabled; raw imports would be public" >&2
  exit 1
fi

echo "garage-init: ok"
