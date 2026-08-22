#!/bin/sh
# Garage does NOT interpolate environment variables inside a mounted TOML
# (§10.3), and its rpc secret must therefore arrive as a file. Every other
# value in garage.toml is static, so this renders exactly one thing.
set -eu
cd "$(dirname "$0")/.."
[ -f .env ] || { echo "FATAL: no .env — copy .env.example and fill it in" >&2; exit 1; }

# `.env` is Compose's env-file format, not shell — `docker compose config`
# reads it as plain `KEY=VALUE` lines with no quoting, escaping or
# expansion. `. ./.env` (the previous version of this script) fed it to
# `/bin/sh` instead, which chokes on exactly the values `.env.example`
# tells an operator to put here: a `BACKUP_DEST` like
# `/mnt/c/Users/Alexey/My Backups/triviador` (unquoted spaces — "command
# not found") or a password containing `#` (starts a shell comment mid-line
# — everything after it silently vanishes instead of erroring). Only one
# variable is ever needed, so parse that one line instead of sourcing the
# whole file. `tail -1`: the last matching line wins, the same rule Compose
# itself applies to a repeated key.
GARAGE_RPC_SECRET="$(sed -n 's/^GARAGE_RPC_SECRET=//p' .env | tail -1)"
: "${GARAGE_RPC_SECRET:?set GARAGE_RPC_SECRET in .env}"
case "$GARAGE_RPC_SECRET" in
  CHANGE_ME) echo "FATAL: GARAGE_RPC_SECRET still holds its placeholder" >&2; exit 1 ;;
esac
umask 077
printf '%s' "$GARAGE_RPC_SECRET" > infra/garage/rpc_secret
echo "render-secrets: ok"
