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
$COMPOSE build
$COMPOSE up -d db garage
# One-shots, explicitly, in dependency order. Either failing aborts the
# deploy before anything serves traffic — which is the point.
$COMPOSE run --rm garage-init
$COMPOSE run --rm migrate
$COMPOSE up -d --remove-orphans
./infra/assert-ports.sh
$COMPOSE ps
