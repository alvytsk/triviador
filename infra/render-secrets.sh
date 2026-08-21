#!/bin/sh
# Garage does NOT interpolate environment variables inside a mounted TOML
# (§10.3), and its rpc secret must therefore arrive as a file. Every other
# value in garage.toml is static, so this renders exactly one thing.
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "FATAL: no .env — copy .env.example and fill it in" >&2; exit 1; }
# shellcheck disable=SC1091
. ./.env
: "${GARAGE_RPC_SECRET:?set GARAGE_RPC_SECRET in .env}"
case "$GARAGE_RPC_SECRET" in
  CHANGE_ME) echo "FATAL: GARAGE_RPC_SECRET still holds its placeholder" >&2; exit 1 ;;
esac
umask 077
printf '%s' "$GARAGE_RPC_SECRET" > infra/garage/rpc_secret
echo "render-secrets: ok"
