# Restore drill (§10.9)

Single-node Garage runs `replication_factor = 1` — zero redundancy. `infra/backup.sh`'s
output (`backups/db/<ts>.dump` and `backups/media/`) is the *only* copy of anything in
the system. This document is the runnable procedure for turning that copy back into a
working deploy, and the three checks that prove it actually worked.

**Status: not yet exercised.** Do not treat this procedure as proven until someone runs
it end to end and appends a dated line to the "Exercised" section at the bottom — do
not add that line speculatively.

Run this against a *disposable* host or a second checkout, never against the live
deploy: every step below assumes fresh, empty `db`/`garage` volumes, and step 1
destroys whatever was in them.

## Prerequisites

- A backup produced by `infra/backup.sh` — `backups/db/<ts>.dump` and
  `backups/media/*`, reachable from the restore host.
- The same `.env` the backed-up deploy used (or a fresh one with the same
  `TRIVIADOR_S3_ACCESS_KEY_ID`/`TRIVIADOR_S3_SECRET_ACCESS_KEY` — Garage v1.1.0 refuses
  to re-import a key ID with a different secret, so a mismatched `.env` fails loudly at
  `garage-init` rather than silently).
- `docker`, `docker compose`, `psql`/`rclone` on the restore host (or run the DB/media
  restore steps through one-off containers, as below — no host install required).

Set once, both as **absolute** paths — never repo-relative. `.env.example` and
`compose.prod.yaml`'s `BACKUP_DEST` are both explicit that the backup destination is a
NAS/Windows/external-drive path, NEVER inside the WSL vhdx (that vhdx is the thing being
backed up), so the dump and media backup this drill restores from live outside this
checkout. A repo-relative `DUMP`/`MEDIA` here would only work by accident, on the one
host where someone happened to run `infra/backup.sh` with `BACKUP_DEST` pointed at this
checkout — which §10.8 and `.env.example` both say not to do:

```sh
COMPOSE="docker compose -f compose.yaml -f compose.prod.yaml"
DUMP=/mnt/d/backups/db/20260821T030000Z.dump   # the chosen dump
MEDIA=/mnt/d/backups/media                     # the append-only media backup
```

## The seven steps

### 1. Start fresh db + garage

```sh
./infra/render-secrets.sh
./infra/provision-media-lock.sh
$COMPOSE build
$COMPOSE up -d db garage
```

Wait for both healthy (`$COMPOSE ps`) before continuing — the same precondition
`infra/deploy.sh` waits on.

`provision-media-lock.sh` matters here specifically: a "disposable host or a second
checkout" (this drill's own precondition, above) is exactly the freshly-booted case
where `/var/lock/triviador-media.lock` does not exist yet — `/var/lock` is tmpfs, so it
never survives a reboot. Skip this and Step 6 below (`triviador media-gc
--after-restore`) is the step that walks straight into it: Docker auto-vivifies the
missing bind-mount source as a root-owned directory, and `media-gc` fails with
`IsADirectoryError` instead of expiring anything. See
`infra/provision-media-lock.sh` for the full mechanism, including the backend/backup
uid mismatch it also has to avoid re-introducing.

### 2. Run garage-init

```sh
$COMPOSE run --rm garage-init
```

Creates `triviador-media`/`triviador-staging`, imports the S3 key from `.env`, and
fails the drill immediately (§10.3's assertion) if the staging bucket would come up
website-enabled.

### 3. Restore the database

```sh
set -a; . ./.env; set +a
docker run --rm --network triviador_default \
  -e PGPASSWORD="$POSTGRES_PASSWORD" \
  -v "$(dirname "$DUMP")":/dump:ro \
  postgres:17-alpine \
  pg_restore -h db -U triviador -d triviador --no-owner --exit-on-error \
    "/dump/$(basename "$DUMP")"
```

`db` is fresh and empty (step 1), so no `--clean` is needed — `pg_restore` is loading
into an empty schema-less database, and `--exit-on-error` turns a partial restore into
a hard failure instead of a database that looks restored but is missing rows.

### 4. Restore media, re-applying Content-Type and Cache-Control

`backups/media/` is a **plain local directory**, not another S3 bucket — copying files
back into Garage with `rclone copy` sets no object metadata at all, and the app depends
on both: `Content-Type` for the browser to render an image as an image, and the fixed
`Cache-Control: public, max-age=31536000, immutable` (`api/http/admin/media.py`) that
makes a content-addressed key cacheable forever. Both are already restored *into the
database* by step 3 (`media_assets.storage_key`, `media_assets.mime_type`) — re-derive
them from there instead of guessing from a file extension:

```sh
docker run --rm --network triviador_default \
  -e PGPASSWORD="$POSTGRES_PASSWORD" \
  postgres:17-alpine \
  psql -h db -U triviador -d triviador -Atc \
    "SELECT storage_key || ',' || mime_type FROM media_assets" \
  > /tmp/media_manifest.csv

while IFS=, read -r key mime; do
  docker run --rm --network triviador_default \
    -e RCLONE_CONFIG_GARAGE_TYPE=s3 \
    -e RCLONE_CONFIG_GARAGE_PROVIDER=Other \
    -e RCLONE_CONFIG_GARAGE_ENV_AUTH=false \
    -e RCLONE_CONFIG_GARAGE_ACCESS_KEY_ID="$TRIVIADOR_S3_ACCESS_KEY_ID" \
    -e RCLONE_CONFIG_GARAGE_SECRET_ACCESS_KEY="$TRIVIADOR_S3_SECRET_ACCESS_KEY" \
    -e RCLONE_CONFIG_GARAGE_ENDPOINT=http://garage:3900 \
    -e RCLONE_CONFIG_GARAGE_REGION=garage \
    -e RCLONE_CONFIG_GARAGE_FORCE_PATH_STYLE=true \
    -v "$MEDIA":/media:ro \
    rclone/rclone:1.68 copyto \
      "/media/$key" "garage:${TRIVIADOR_MEDIA_BUCKET}/$key" \
      --header-upload "Content-Type: $mime" \
      --header-upload "Cache-Control: public, max-age=31536000, immutable"
done < /tmp/media_manifest.csv
```

A row in `media_assets` with no matching file under `backups/media/` means the object
was created after the last backup ran — report it, but it is not this drill's job to
paper over data the backup genuinely never had.

### 5. Run migrations required by the current application

```sh
$COMPOSE run --rm migrate
```

The restored dump was taken by whatever schema version was live *then*; the code about
to serve traffic is whatever is checked out *now*. This is the step that reconciles
them — never skip it because "the dump already has the data."

### 6. Expire every non-confirmed import (§9.3)

Staging is deliberately not backed up (see below), so any import still `validated` at
backup time has no upload to confirm against. Expire it explicitly, before `backend`
starts and a confirm attempt could race this cleanup:

```sh
$COMPOSE run --rm backend triviador media-gc --after-restore
```

### 7. Start backend and caddy

```sh
$COMPOSE up -d --remove-orphans
./infra/assert-ports.sh
$COMPOSE ps
```

## Verification

Three checks, one per failure surface a partial or silently-wrong restore could hit.
Run them all — passing two out of three is not a passing drill.

### A. A finished game replays from the log

`GET /api/games/{id}` never trusts an in-memory cache after a fresh process start; it
always rebuilds `GameState` by folding the persisted event log
(`triviador.runtime.loader.GameLoader.load`). Pick a game that was finished before the
backup, and confirm it comes back intact:

Set `$FINISHED_GAME_ID` to that game's id (from your own notes made before the backup,
or `SELECT id FROM games WHERE status = 'finished' LIMIT 1` against the restored
database), and `$COOKIE` to a session cookie for a user allowed to read it — the value
of the `session` cookie in `Set-Cookie` from a `POST /api/auth/login` against this same
restored deploy (any account with access to the game; §6 has no separate "read-only"
role).

```sh
curl -fsS -b "session=$COOKIE" "http://localhost/api/games/$FINISHED_GAME_ID" \
  | python3 -c 'import json,sys
s = json.load(sys.stdin)["state"]
print("phase:", s["phase"], "winner:", s["winner_id"])
for p in s["players"]:
    print(" ", p["display_name"], "score:", p["score"] + p["bonus_score"])'
```

Pass: `phase` is `finished`, `winner_id` is set, and every player's score matches what
the game had before the backup — proof the event log survived the dump and restore
byte-for-byte, not just that some rows exist.

### B. An active game with a persisted deadline resumes and expires at its original absolute time

Pick a game that was mid-round, with a live deadline, at backup time. `state.turn`
carries that deadline as an absolute timestamp (`deadline_at`), reconstructed from the
log the same way as check A — not recomputed relative to "now":

```sh
curl -fsS -b "session=$COOKIE" "http://localhost/api/games/$ACTIVE_GAME_ID" \
  | python3 -c 'import json,sys; print(json.load(sys.stdin)["state"]["turn"]["deadline_at"])'
```

Pass: this equals the deadline the game had *before* the backup was taken (compare
against a value noted before starting the drill) — not "now plus the round's normal
duration". Then either wait for it or let the drill run past it, and confirm the game
actually transitions (round resolves / game advances) at that same absolute instant,
not early and not never — proof the restored deadline drives the watchdog, not a
freshly-started timer.

### C. A question image loads through Caddy → Garage

Set `$SOME_STORAGE_KEY` to any `storage_key` from `media_assets` (the same manifest
query step 4 already ran: `SELECT storage_key FROM media_assets LIMIT 1`) restored
against the database.

```sh
curl -fsS -o /dev/null -w '%{http_code} %{content_type}\n' \
  "http://localhost/media/$SOME_STORAGE_KEY"
```

Pass: `200` with the `Content-Type` recorded in `media_assets` for that key — proof step
4's metadata re-application worked and Caddy's `/media/*` proxy reaches the restored
Garage bucket, not just that the file exists on disk.

## Staging is not backed up, by design

`infra/backup.sh` only ever touches the **media** bucket
(`rclone copy garage:$S3_MEDIA_BUCKET`). The **staging** bucket — raw, unconfirmed
import uploads, answer keys included — is never copied. This is deliberate, not an
oversight: staging is expiring-by-design (§9.3, `TRIVIADOR_IMPORT_TTL_HOURS`), and
backing up a bucket whose whole purpose is "temporary and about to be deleted anyway"
buys nothing. The consequence is step 6: after any restore, every import that was still
`validated` (uploaded, not yet confirmed) simply becomes `expired` — its upload is
gone, and there is nothing to confirm. An admin re-uploads and re-validates it. This is
expected behavior, not data loss in the sense the rest of this drill cares about.

## Exercised

Not yet. Whoever runs this drill for the first time replaces this line with the date it
was run, the dump used, and the outcome of all three verifications.
