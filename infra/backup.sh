#!/bin/sh
# §10.8. Everything below runs inside ONE flock, shared with media-gc via
# an identical HOST path — two container-local paths would resolve to
# different inodes and would not exclude each other.
#
# LOCK below must stay byte-identical to `MEDIA_LOCK_PATH` in
# backend/src/triviador/media/lock.py (the Python-side source of truth
# for the same path — a shell script cannot import it) and to the volume
# mounts on the `backend` and `backup` services in compose.prod.yaml.
set -eu

LOCK=/var/lock/triviador-media.lock

# Retention rule for `backups/db/*.dump` (deliberately not sketched by the
# plan): keep every dump whose timestamp is within the last 7 days, PLUS
# the newest dump in each of the last 4 ISO weeks — the ISO 8601
# year-week (`%G-%V`) containing "now", and the three that precede it —
# then delete everything else. Media is append-only and never pruned
# here: `copy` (not `sync`, see below) already guarantees an old dump's
# objects are never removed just because a newer run ran.
#
# A pure function of `$1` (the directory) and `$PRUNE_NOW` (an epoch
# override, unset in production) so infra/backup-prune-dumps.test.sh can
# source this file with TRIVIADOR_BACKUP_SOURCED=1 and exercise it
# directly, against a fixture directory, without touching a database or
# Garage.
prune_dumps() {
    dumpdir="$1"
    [ -d "$dumpdir" ] || return 0

    prune_now="${PRUNE_NOW:-$(date -u +%s)}"
    prune_cutoff=$((prune_now - 7 * 86400))

    prune_weeks=""
    prune_w=0
    while [ "$prune_w" -lt 4 ]; do
        prune_wk_epoch=$((prune_now - prune_w * 7 * 86400))
        prune_weeks="$prune_weeks $(date -u -d "@$prune_wk_epoch" +%G-%V)"
        prune_w=$((prune_w + 1))
    done

    prune_index="$(mktemp)"
    for prune_f in "$dumpdir"/*.dump; do
        [ -e "$prune_f" ] || continue
        prune_base=$(basename "$prune_f" .dump)
        prune_epoch=$(date -u -D "%Y%m%dT%H%M%SZ" -d "$prune_base" +%s 2>/dev/null) || continue
        printf '%s %s %s\n' \
            "$prune_epoch" "$(date -u -d "@$prune_epoch" +%G-%V)" "$prune_base" \
            >> "$prune_index"
    done

    # For each name that survives, `daily[]` is set when its own
    # timestamp is inside the last 7 days; `weekly[]` is set on the one
    # row per interesting week whose epoch is the largest in that week
    # (the "newest dump in that ISO week"). Anything in neither set is
    # printed for deletion below.
    prune_victims="$(awk -v cutoff="$prune_cutoff" -v weeks="$prune_weeks" '
        BEGIN {
            n = split(weeks, w, " ")
            for (i = 1; i <= n; i++) interesting[w[i]] = 1
        }
        {
            epoch = $1; wk = $2; base = $3
            row[NR] = base
            if (epoch >= cutoff) daily[NR] = 1
            if (wk in interesting && (!(wk in bestepoch) || epoch > bestepoch[wk])) {
                bestepoch[wk] = epoch
                bestidx[wk] = NR
            }
        }
        END {
            for (wk in bestidx) weekly[bestidx[wk]] = 1
            for (i = 1; i <= NR; i++)
                if (!(i in daily) && !(i in weekly)) print row[i]
        }
    ' "$prune_index")"
    rm -f "$prune_index"

    [ -z "$prune_victims" ] && return 0
    printf '%s\n' "$prune_victims" | while IFS= read -r prune_victim; do
        rm -f "$dumpdir/$prune_victim.dump"
    done
}

# Sourced by the retention test with TRIVIADOR_BACKUP_SOURCED=1 to get
# `prune_dumps` without running pg_dump/rclone/flock against a real stack.
if [ "${TRIVIADOR_BACKUP_SOURCED:-0}" = "1" ]; then
    return 0
fi

DEST="${BACKUP_DEST:?set BACKUP_DEST — a Windows disk, external drive or NAS, NEVER inside the WSL vhdx}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

exec 9>"$LOCK"
flock 9

mkdir -p "$DEST/db" "$DEST/media"

# 1. Database first, so media is always a SUPERSET of what this snapshot
#    references. Verification therefore asserts coverage, not equality.
#
# Dump to `.partial` and `mv` into place only once `pg_dump` has actually
# succeeded: `> "$DEST/db/$TS.dump"` opens (creates) the destination file
# before the command that fills it can fail, so a `pg_dump` that dies
# partway still leaves a truncated `.dump` behind — one retention (below)
# keeps and an operator restoring later would pick as "the newest",
# because nothing about its name or presence says it is broken. `set -eu`
# stops the script at the failed `pg_dump` before the `mv` ever runs, so
# a `.partial` left on disk after a failed backup is itself the signal
# something went wrong, not a file retention would ever treat as a dump.
pg_dump -Fc -h db -U triviador triviador > "$DEST/db/$TS.dump.partial"
mv "$DEST/db/$TS.dump.partial" "$DEST/db/$TS.dump"

# 2. copy, NOT sync: a sync maintains one mutable mirror, so a retained
#    weekly dump can reference an asset a later run deleted. Keys are
#    content-addressed, so copy deduplicates naturally and never removes
#    an asset an older dump still needs.
rclone copy "garage:${S3_MEDIA_BUCKET:?}" "$DEST/media/"

# 3. Verify both halves, still inside the flock — so media-gc cannot
#    delete an object between the copy and the check and turn a healthy
#    backup into a spurious failure. `rclone check --one-way` proves the
#    property that matters: every object currently in Garage exists in
#    the append-only backup. Not a per-object manifest walk: nothing
#    generates such a manifest, and `pg_dump -Fc` does not contain one.
pg_restore --list "$DEST/db/$TS.dump" > /dev/null
rclone check "garage:${S3_MEDIA_BUCKET:?}" "$DEST/media/" --one-way

# 4. Retention: keep every dump from the last 7 days, PLUS the newest
#    dump in each of the last 4 ISO weeks; delete the rest. Media is
#    append-only and never pruned — an old dump may still reference an
#    object no live row does.
prune_dumps "$DEST/db"

echo "backup: ok $TS"
