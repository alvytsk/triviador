#!/bin/sh
# Fixture test for infra/backup.sh's `prune_dumps`. Run inside the same
# base image `infra/backup.Dockerfile` uses, since the retention rule
# depends on busybox `date -D` (parsing) and `%G-%V` (ISO week) — a
# GNU-date host would silently exercise different code paths:
#
#   docker run --rm -v "$(pwd)/infra:/infra" alpine:3.21 sh /infra/backup-prune-dumps.test.sh
#
# Builds a fixture of one dump per day for 61 days ending "now", asserts
# the exact survivor set against the real `prune_dumps`, and then repeats
# the same assertion against a deliberately inverted rule to confirm the
# fixture can actually detect a wrong implementation (a retention bug
# that silently deletes the wrong file is not observable until the day
# someone needs the file it deleted — this is the check that would have
# caught it).
set -eu

SCRIPT_DIR=$(cd -- "$(dirname -- "$0")" && pwd)

# `now`: 2026-08-21T12:00:00Z, a fixed Friday in ISO week 2026-W34 — see
# the report for the by-hand calculation this asserts against.
NOW=$(date -u -D "%Y-%m-%d %H:%M:%S" -d "2026-08-21 12:00:00" +%s)

# days-ago -> expected to survive: {0..7} (last 7 days, daily) plus the
# newest-in-week for 2026-W32 (day 12) and 2026-W31 (day 19). W34's and
# W33's "newest in week" are day 0 and day 5, already covered by the
# daily rule, so they add nothing extra.
EXPECT_SURVIVE="0 1 2 3 4 5 6 7 12 19"

make_fixture() {
    fixture_dir="$1"
    rm -rf "$fixture_dir"
    mkdir -p "$fixture_dir"
    d=0
    while [ "$d" -le 60 ]; do
        e=$((NOW - d * 86400))
        ts=$(date -u -d "@$e" +%Y%m%dT%H%M%SZ)
        touch "$fixture_dir/$ts.dump"
        d=$((d + 1))
    done
}

survivor_days() {
    # Prints, one per line, the "days ago" value of every surviving file.
    fixture_dir="$1"
    for f in "$fixture_dir"/*.dump; do
        [ -e "$f" ] || continue
        base=$(basename "$f" .dump)
        epoch=$(date -u -D "%Y%m%dT%H%M%SZ" -d "$base" +%s)
        echo $(( (NOW - epoch) / 86400 ))
    done | sort -n
}

assert_survivors() {
    label="$1"
    fixture_dir="$2"
    got=$(survivor_days "$fixture_dir" | tr '\n' ' ' | sed 's/ $//')
    want=$(printf '%s\n' "$EXPECT_SURVIVE" | tr ' ' '\n' | sort -n | tr '\n' ' ' | sed 's/ $//')
    if [ "$got" = "$want" ]; then
        echo "PASS: $label survivors = [$got]"
        return 0
    else
        echo "FAIL: $label survivors = [$got], expected [$want]"
        return 1
    fi
}

FAILURES=0

echo "=== real prune_dumps: must match the hand-computed survivor set ==="
FIXTURE_REAL=/tmp/prune-fixture-real
TRIVIADOR_BACKUP_SOURCED=1
export TRIVIADOR_BACKUP_SOURCED
# shellcheck source=/dev/null
. "$SCRIPT_DIR/backup.sh"
make_fixture "$FIXTURE_REAL"
PRUNE_NOW=$NOW prune_dumps "$FIXTURE_REAL"
assert_survivors "real rule" "$FIXTURE_REAL" || FAILURES=$((FAILURES + 1))

echo
echo "=== inverted rule: same fixture, same assertion, must FAIL ==="
echo "    (proves the fixture actually detects a wrong retention rule,"
echo "    not just that some file happened to remain)"
FIXTURE_INV=/tmp/prune-fixture-inverted

# The mutation: daily keeps what's OLD instead of recent, and the weekly
# slot keeps the OLDEST in each week instead of the newest — the same
# rule, with both comparisons flipped.
prune_dumps_inverted() {
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
    prune_victims="$(awk -v cutoff="$prune_cutoff" -v weeks="$prune_weeks" '
        BEGIN {
            n = split(weeks, w, " ")
            for (i = 1; i <= n; i++) interesting[w[i]] = 1
        }
        {
            epoch = $1; wk = $2; base = $3
            row[NR] = base
            if (epoch < cutoff) daily[NR] = 1
            if (wk in interesting && (!(wk in bestepoch) || epoch < bestepoch[wk])) {
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

make_fixture "$FIXTURE_INV"
PRUNE_NOW=$NOW prune_dumps_inverted "$FIXTURE_INV"
if assert_survivors "inverted rule" "$FIXTURE_INV"; then
    echo "FAIL: the inverted rule passed the same assertion — the fixture" \
        "does not actually distinguish a correct retention rule from a" \
        "wrong one"
    FAILURES=$((FAILURES + 1))
else
    echo "PASS: inverted rule correctly failed the assertion, as expected"
fi

echo
if [ "$FAILURES" -eq 0 ]; then
    echo "backup-prune-dumps.test.sh: ok"
    exit 0
else
    echo "backup-prune-dumps.test.sh: $FAILURES failure(s)"
    exit 1
fi
