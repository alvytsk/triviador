# Triviador Infrastructure Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the working application into a deployable one — a Compose stack that
brings up PostgreSQL, Garage, migrations, the backend, the frontend and Caddy on a
LAN box, with CI, backups, and a written restore drill.

**Architecture:** Three Compose files (`compose.yaml` base + `compose.dev.yaml` /
`compose.prod.yaml` overlays) over a strict dependency graph: `db` healthy and
`garage` healthy gate the two one-shot services (`migrate`, `garage-init`), which
gate `backend`, which gates `caddy`. One-shots are dedicated services rather than
entrypoint steps, so a dev reload or a crash loop cannot re-run a migration. In
production Caddy is the single published origin; in development the Vite dev server
is. Nothing else is ever published.

**Tech Stack:** Docker Compose, PostgreSQL 17, Garage (S3-compatible, pinned by
digest), Caddy 2, Alembic, uv, pnpm, Playwright, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-08-16-triviador-app-architecture-design.md` §10
(infrastructure) and §12.4 of `docs/superpowers/specs/2026-08-07-triviador-spec1-design.md`
(the single E2E). Both travel with this plan; executors read the spec section their
task cites, not just the task.

## Global Constraints

- **LAN-only deployment.** Plain HTTP is a deliberate trust assumption (§10.11), not
  an oversight. Do not add TLS, secret managers, or internet-facing hardening.
- **Only Caddy is published in production**: `0.0.0.0:80:80` and nothing else. Not
  PostgreSQL, not the backend, not Garage's `3900`/`3902`/`3903`, not `migrate`.
  Development ports bind `127.0.0.1` unless a task says otherwise.
- **Garage is pinned to an exact version or digest**, never a floating tag —
  `garage-init` depends on CLI syntax and a silent bump breaks bootstrap.
  This repo already uses `dxflrs/garage:v1.1.0`; keep that exact tag.
- **`replication_factor = 1`.** Single node, zero redundancy. Backups are the only
  copy of anything.
- **Live data on local Linux filesystems only** — never SMB/NFS/NAS for PostgreSQL's
  data dir or Garage's meta/data dirs. Backup destinations may be a NAS.
- **Containers run UTC.** Absolute deadlines are persisted (ADR-001/5), so clock
  correctness is a correctness requirement, not hygiene.
- **Backend `stop_grace_period` must exceed the database statement timeout** (§10.12).
  A shorter grace kills the container mid-`COMMIT` and manufactures the ambiguous-commit
  case on every deploy — the exact case §5.6's graceful shutdown exists to avoid.
- **Config is `pydantic-settings` with the `TRIVIADOR_` env prefix.** It already
  exists in `backend/src/triviador/config.py`; this plan wires it, it does not
  redesign it.
- The repository already ships `backend/docker-compose.test.yml` for the **test**
  Postgres and Garage. It is a separate concern from this plan's stack and must keep
  working unchanged.

---

## Prior art in this repository — read before Task 1

Two existing files carry knowledge that was verified the hard way and must not be
rediscovered:

**`backend/testing/garage-init.sh`** — initialises the *test* Garage. Its header
records that `dxflrs/garage:v1.1.0` **contains no shell**: `/bin/sh` does not exist
in that image, so a script cannot run *inside* it. It works around that by running on
the host and calling `docker compose exec -T garage-test /garage …`. Every flag in it
was checked against that image's `--help`:

- capacity is `-c 1GB` — **`1G` is not a valid suffix**; the accepted set is
  `B, KB, MB, GB, TB, PB`
- `garage layout apply --version 1` fails once the layout is already at that version
  (absorbed with `|| true` on re-run)
- `garage key import --yes -n <name> <key-id> <secret>`
- `garage bucket allow --read --write --owner <bucket> --key <name>`
- `garage node id -q` prints `<id>@<addr>`; the id is the part before `@`

**`backend/testing/garage.toml`** — the test node's config, same shape as the
production one this plan writes.

---

## File Structure

```
compose.yaml                     db · garage · garage-init · migrate   (base)
compose.dev.yaml                 backend(dev) · frontend(vite)
compose.prod.yaml                backend(prod) · caddy
.env.example                     moved from backend/, repo root         (Task 1)
infra/
  backend.Dockerfile             multi-stage, uv, non-root
  frontend.Dockerfile            builds static output for Caddy
  garage-init.Dockerfile         alpine + the pinned garage binary
  garage/garage.toml             production node config (committed; no secrets)
  garage/init.sh                 idempotent bootstrap, the website assertion
  caddy/Caddyfile                the single prod origin
  deploy.sh                      the one supported deploy command
  backup.sh                      flock · pg_dump · rclone copy · verify · retention
  restore-drill.md               written procedure, exercised once
backend/src/triviador/
  api/http/health.py             MODIFY — readiness gains garage_ready
  storage/…                      MODIFY — record the startup Garage assertion
frontend/
  vite.config.ts                 MODIFY — /media must proxy to Garage, not the API
e2e/
  package.json · playwright.config.ts
  smoke.spec.ts                  §12.4's one scenario
  seed/                          fixture including at least one media question
.github/workflows/ci.yml         §10.7's seven jobs
```

---

## Task 1: The environment file moves to the repository root

**Files:**
- Create: `.env.example` (repo root)
- Delete: `backend/.env.example`
- Modify: `.gitignore` (confirm `.env` / `!.env.example` still apply at root)
- Test: `backend/tests/api/test_settings.py` (update the path it documents)

**Interfaces:**
- Produces: a single `.env` at the repository root, read by Compose for variable
  substitution *and* handed to the backend container via `env_file:`.

**Why this must happen first.** Compose substitutes `${POSTGRES_PASSWORD}` from a
`.env` **in the project directory** — the directory holding `compose.yaml`. With the
example file under `backend/`, an operator following it produces `backend/.env`,
which Compose never reads, and `db` comes up with an empty password. One file at the
root serves both consumers.

- [ ] **Step 1: Move the file and add the Compose-only variables**

```bash
git mv backend/.env.example .env.example
```

Then confirm it contains, at minimum, the §10.4 set. It already ends with:

```
# Plan 8 (compose, Garage) reads these; they are listed here so the
# placeholder assertion covers them from the first deploy.
POSTGRES_PASSWORD=CHANGE_ME
GARAGE_RPC_SECRET=CHANGE_ME
```

Rewrite that comment — Plan 8 is now this plan, and it is no longer "will read":

```
# Read by Compose itself (not by the backend): substituted into
# compose.yaml for the database password and rendered into Garage's
# rpc_secret_file before launch. They live here rather than in a second
# file so `startup_problems` covers them from the first deploy.
POSTGRES_PASSWORD=CHANGE_ME
GARAGE_RPC_SECRET=CHANGE_ME
```

Also change `TRIVIADOR_DATABASE_URL`'s host from `postgres` to `db`, matching this
plan's service name, and add the two values the dev overlay needs:

```
TRIVIADOR_ALLOWED_ORIGINS=http://localhost:5173
TRIVIADOR_ALLOWED_HOSTS=localhost,127.0.0.1
```

- [ ] **Step 2: Update the two comments that name the old path**

`backend/src/triviador/config.py:49` and `:156` and
`backend/tests/api/test_settings.py:38,46` refer to `.env.example`. The filename is
unchanged, so only check whether any of them state a *directory*. Fix any that do.

- [ ] **Step 3: Verify nothing reads the old path**

Run: `grep -rn "backend/.env.example" . --exclude-dir=node_modules --exclude-dir=.git`
Expected: no matches.

- [ ] **Step 4: Run the settings tests**

Run: `cd backend && uv run pytest tests/api/test_settings.py -v`
Expected: PASS (the tests exercise env vars, not the file's location).

- [ ] **Step 5: Commit**

```bash
git add .env.example backend/src/triviador/config.py backend/tests/api/test_settings.py
git commit -m "chore(infra): move .env.example to the repository root"
```

---

## Task 2: The backend image

**Files:**
- Create: `infra/backend.Dockerfile`, `.dockerignore` (repo root)

**Interfaces:**
- Produces: an image that runs `uvicorn triviador.api.app:app --host 0.0.0.0 --port 8000
  --workers 1` in production, and is reusable as the `migrate` one-shot's image.

Read `backend/pyproject.toml` first: `requires-python = ">=3.13"`, hatchling build
backend, `packages = ["src/triviador"]`, and a console script
`triviador = "triviador.cli:main"`.

**`--workers 1` is not a performance choice.** ADR-002 guarantees exactly one
application process; a second worker would give two processes ownership of the same
game runtimes. Do not make it configurable.

- [ ] **Step 1: Write `.dockerignore`**

```
.git
.venv
node_modules
**/__pycache__
**/.pytest_cache
**/.mypy_cache
**/.ruff_cache
**/.hypothesis
frontend/dist
backups
.env
.superpowers
docs
```

- [ ] **Step 2: Write `infra/backend.Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1

# uv's own image supplies the pinned binary; copying it into a plain python
# base keeps the runtime image free of build tooling.
FROM ghcr.io/astral-sh/uv:0.5.11 AS uv

FROM python:3.13-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PROJECT_ENVIRONMENT=/opt/venv
COPY --from=uv /uv /usr/local/bin/uv
WORKDIR /app

# Dependencies resolve from the lockfile alone, in their own layer, so a
# source edit does not re-resolve the whole dependency tree.
FROM base AS deps
COPY backend/pyproject.toml backend/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-install-project --no-dev

FROM base AS runtime
COPY --from=deps /opt/venv /opt/venv
COPY backend/ ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-editable --no-dev
ENV PATH="/opt/venv/bin:$PATH"

# Non-root. The container writes nothing outside /tmp: media goes to Garage,
# logs go to stdout, and the maps volume is mounted read-only.
RUN useradd --create-home --uid 10001 triviador
USER triviador

EXPOSE 8000
CMD ["uvicorn", "triviador.api.app:app", \
     "--host", "0.0.0.0", "--port", "8000", "--workers", "1"]
```

**Verify the app import path before writing it.** Run
`grep -rn "^app = \|FastAPI(" backend/src/triviador/api/app.py | head` and use the
module path that actually exists. If it is not `triviador.api.app:app`, fix the `CMD`
and say so in the report — do not leave a path that only looks right.

- [ ] **Step 3: Build it**

Run: `docker build -f infra/backend.Dockerfile -t triviador-backend:dev .`
Expected: builds clean.

- [ ] **Step 4: Prove the entrypoint resolves**

Run: `docker run --rm triviador-backend:dev python -c "import triviador.api.app; print('ok')"`
Expected: `ok`.

This is the check that catches a wrong `CMD` module path at build time rather than at
first deploy.

- [ ] **Step 5: Commit**

```bash
git add infra/backend.Dockerfile .dockerignore
git commit -m "feat(infra): backend image"
```

---

## Task 3: The frontend image

**Files:**
- Create: `infra/frontend.Dockerfile`

**Interfaces:**
- Produces: an image whose `/dist` holds the built SPA. Caddy copies from it; nothing
  runs at runtime in production.

The frontend has no server in production — §10.1's table says "built at image build;
static output served by Caddy". So this image exists only to produce artifacts.

- [ ] **Step 1: Write `infra/frontend.Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1
FROM node:22-alpine AS build
# corepack ships with node:22 and pins pnpm from packageManager in package.json,
# so the image cannot drift from what developers run locally.
RUN corepack enable
WORKDIR /app

COPY frontend/package.json frontend/pnpm-lock.yaml ./
RUN --mount=type=cache,target=/pnpm-store \
    pnpm config set store-dir /pnpm-store && pnpm install --frozen-lockfile

COPY frontend/ ./
# `contracts/` sits outside frontend/ and codegen reads it, so the build
# needs it present at the path the script expects.
COPY contracts/ ../contracts/
RUN pnpm build

# A scratch stage holding only the artifacts. `docker build --target dist
# --output` extracts them without running a container.
FROM scratch AS dist
COPY --from=build /app/dist /dist
```

**Check `frontend/package.json` for `packageManager` first.** If it is absent, add it
(matching the pnpm version in use) rather than letting corepack pick a default — an
unpinned pnpm is exactly the drift this stage exists to prevent.

**Check what `pnpm build` depends on.** If the build runs codegen and codegen reads
`../contracts`, the relative copy above is right; if it reads an env var or a
different path, fix the `COPY` to match and say so.

- [ ] **Step 2: Build it**

Run: `docker build -f infra/frontend.Dockerfile --target build -t triviador-frontend:dev .`
Expected: builds clean, `pnpm build` succeeds.

- [ ] **Step 3: Verify the artifacts exist and are hashed**

Run:
```bash
docker build -f infra/frontend.Dockerfile --target dist --output type=local,dest=/tmp/fe .
ls /tmp/fe/dist/assets | head
```
Expected: `index.html` at `/tmp/fe/dist/`, and hashed files under `assets/`.

The hashed-`assets/` layout is what Caddy's `immutable` matcher keys on (§10.2), so
confirm it here rather than discovering it when caching misbehaves.

- [ ] **Step 4: Commit**

```bash
git add infra/frontend.Dockerfile frontend/package.json
git commit -m "feat(infra): frontend build image"
```

---

## Task 4: Garage's production config and the init image

**Files:**
- Create: `infra/garage/garage.toml`, `infra/garage.Dockerfile` (the init image),
  `infra/garage/init.sh`
- Modify: `.gitignore`

**Interfaces:**
- Produces: an image `triviador-garage-init` containing a shell **and** the pinned
  `garage` binary, whose entrypoint is `init.sh`.
- Produces: `infra/garage/rpc_secret` (gitignored, rendered at deploy time).

**The design decision this task turns on.** §10.1 wants `garage-init` as a Compose
service, but `dxflrs/garage:v1.1.0` **has no shell** — `backend/testing/garage-init.sh`
records this, which is why the *test* bootstrap runs on the host via
`docker compose exec`. A service running a guarded, multi-step, conditional script
needs a shell. So build a tiny image that has both:

```dockerfile
COPY --from=dxflrs/garage:v1.1.0 /garage /usr/local/bin/garage
```

This keeps the binary pinned to the same tag as the server, which is the property
§10.3 actually demands. (The alternative — driving Garage's admin API on `:3903`
with `curl` — is rejected: it is an unauthenticated control plane whose exposure
§10.11 calls the sharpest risk in the stack, and the CLI is what the spec's
pseudocode uses.)

**On "rendered config".** §10.3 says Garage does not interpolate environment
variables inside a mounted TOML, so the config is "rendered from a template before
launch". Reading the actual config, every value is static *except* the RPC secret,
and that is already externalized via `rpc_secret_file`. So this plan commits
`garage.toml` as a plain file and renders **only** the secret. That satisfies the
constraint the spec is protecting against (no interpolation, no inline secret) with
one moving part instead of two. Note this deviation in the task report.

- [ ] **Step 1: Write `infra/garage/garage.toml`**

Copy §10.3's config exactly:

```toml
# Production Garage. Single node, replication factor 1 — zero redundancy,
# which is why §10.8's backup is the only copy of any object.
#
# No secret appears here. `rpc_secret_file` points at a file rendered from
# GARAGE_RPC_SECRET before launch, because Garage does NOT interpolate
# environment variables inside a mounted TOML.
replication_factor = 1
metadata_dir       = "/var/lib/garage/meta"
data_dir           = "/var/lib/garage/data"
db_engine          = "lmdb"
rpc_bind_addr      = "[::]:3901"
rpc_public_addr    = "garage:3901"
rpc_secret_file    = "/run/secrets/garage_rpc_secret"

[s3_api]
api_bind_addr = "[::]:3900"
s3_region     = "garage"

[s3_web]
bind_addr   = "[::]:3902"
root_domain = ".web.garage.internal"

[admin]
api_bind_addr = "[::]:3903"
```

Note `db_engine = "lmdb"` here versus `sqlite` in the test config — production uses
lmdb per the spec; do not "fix" the difference.

- [ ] **Step 2: Gitignore the rendered secret**

Add to `.gitignore`:

```
/infra/garage/rpc_secret
```

- [ ] **Step 3: Write `infra/garage.Dockerfile`**

```dockerfile
# syntax=docker/dockerfile:1
# A shell AND the garage CLI. dxflrs/garage:v1.1.0 ships no /bin/sh, so an
# init *service* cannot run a script inside that image — see
# backend/testing/garage-init.sh, which works around the same limitation on
# the host. The binary is copied from the identical pinned tag as the server,
# which is the property §10.3 requires: garage-init depends on CLI syntax.
FROM alpine:3.21
COPY --from=dxflrs/garage:v1.1.0 /garage /usr/local/bin/garage
COPY infra/garage/init.sh /usr/local/bin/init.sh
RUN chmod +x /usr/local/bin/init.sh
ENTRYPOINT ["/usr/local/bin/init.sh"]
```

- [ ] **Step 4: Write `infra/garage/init.sh`**

Every step guarded and idempotent — a deploy re-runs this unconditionally (§10.11).
The flags below are the ones `backend/testing/garage-init.sh` already verified
against this exact image; reuse them rather than guessing.

```sh
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
if $GARAGE bucket info "$S3_STAGING_BUCKET" | grep -qi "website.*enabled"; then
  echo "FATAL: $S3_STAGING_BUCKET is website-enabled; raw imports would be public" >&2
  exit 1
fi

echo "garage-init: ok"
```

**Verify the assertion's grep against real output before trusting it.** Run
`garage bucket info` against a website-enabled bucket and a disabled one, and confirm
the pattern matches the first and not the second. A guard whose pattern never matches
is worse than no guard — it reports success unconditionally. Paste both outputs in
the report.

- [ ] **Step 5: Verify against the running test Garage**

The test stack is already available and uses the same image:

```bash
cd backend && docker compose -f docker-compose.test.yml up -d
docker compose -f docker-compose.test.yml exec -T garage-test /garage bucket info triviador-media
docker compose -f docker-compose.test.yml exec -T garage-test /garage bucket info triviador-staging
```

Expected: the media bucket reports website access enabled, the staging bucket does
not. Use the real strings to finalise the grep in Step 4.

- [ ] **Step 6: Commit**

```bash
git add infra/garage/garage.toml infra/garage/init.sh infra/garage.Dockerfile .gitignore
git commit -m "feat(infra): garage config and the init image"
```

---

## Task 5: `compose.yaml` — the base stack and its dependency graph

**Files:**
- Create: `compose.yaml`

**Interfaces:**
- Consumes: `infra/backend.Dockerfile` (Task 2), `infra/garage.Dockerfile` (Task 4).
- Produces: services `db`, `garage`, `garage-init`, `migrate`, plus the named volumes
  `pgdata`, `garage_meta`, `garage_data`, and the network every overlay attaches to.

§10.1's graph:

```
db      healthy   ─┐
                   ├─→ migrate  completed ─┐
garage  healthy ──→ garage-init completed ─┴─→ backend ready ─→ caddy
```

**`migrate` is a dedicated one-shot service.** It must not live in the backend
entrypoint: a dev reload would re-run it, and a crash loop would re-run it repeatedly.

- [ ] **Step 1: Write `compose.yaml`**

```yaml
# The base stack: storage and the two one-shot bootstrap jobs. Nothing here
# is published — §10.11 publishes only Caddy, and only in the prod overlay.
name: triviador

services:
  db:
    image: postgres:17-alpine
    restart: unless-stopped
    environment:
      POSTGRES_USER: triviador
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD in .env}
      POSTGRES_DB: triviador
      # Containers run UTC: absolute deadlines are persisted, so a timezone
      # difference between host and container is a correctness bug.
      TZ: UTC
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U triviador -d triviador"]
      interval: 5s
      timeout: 3s
      retries: 20

  garage:
    image: dxflrs/garage:v1.1.0
    restart: unless-stopped
    environment:
      TZ: UTC
    volumes:
      - ./infra/garage/garage.toml:/etc/garage.toml:ro
      - garage_meta:/var/lib/garage/meta
      - garage_data:/var/lib/garage/data
    secrets:
      - garage_rpc_secret
    healthcheck:
      # The image has no shell, so this is an exec-form probe of the binary.
      test: ["CMD", "/garage", "status"]
      interval: 5s
      timeout: 3s
      retries: 30

  garage-init:
    build:
      context: .
      dockerfile: infra/garage.Dockerfile
    # One-shot. Never restart: a failed assertion must stay failed and stop
    # the deploy, not loop.
    restart: "no"
    depends_on:
      garage:
        condition: service_healthy
    environment:
      S3_ACCESS_KEY_ID: ${TRIVIADOR_S3_ACCESS_KEY_ID:?}
      S3_SECRET_ACCESS_KEY: ${TRIVIADOR_S3_SECRET_ACCESS_KEY:?}
      S3_MEDIA_BUCKET: ${TRIVIADOR_MEDIA_BUCKET:-triviador-media}
      S3_STAGING_BUCKET: ${TRIVIADOR_STAGING_BUCKET:-triviador-staging}
    volumes:
      - ./infra/garage/garage.toml:/etc/garage.toml:ro
    secrets:
      - garage_rpc_secret

  migrate:
    build:
      context: .
      dockerfile: infra/backend.Dockerfile
    restart: "no"
    depends_on:
      db:
        condition: service_healthy
    env_file: .env
    # Under an advisory lock: ADR-002 already guarantees one application
    # process, so this exists only to stop a stray manual `alembic` from
    # racing a deploy.
    command: ["triviador", "migrate"]

volumes:
  pgdata:
  garage_meta:
  garage_data:

secrets:
  garage_rpc_secret:
    file: ./infra/garage/rpc_secret
```

- [ ] **Step 2: Add the `migrate` CLI subcommand if it does not exist**

Check first: `cd backend && uv run triviador --help`.

If there is no `migrate` subcommand, add one to `backend/src/triviador/cli.py` that
takes `pg_advisory_lock` and runs `alembic upgrade head`. If a subcommand already
exists under another name, use that name in the compose `command:` and say so.

The advisory lock's reason, for the docstring: ADR-002 guarantees one application
process, so this guards only against a human running `alembic` by hand during a
deploy.

- [ ] **Step 3: Validate the file**

Run: `docker compose -f compose.yaml config >/dev/null`
Expected: exits 0. (It needs a `.env` with the referenced vars — copy `.env.example`
to `.env` first and leave the placeholders; `config` does not connect to anything.)

- [ ] **Step 4: Prove the required-variable guards fire**

Run: `POSTGRES_PASSWORD= docker compose -f compose.yaml config 2>&1 | head -3`
Expected: the `:?set POSTGRES_PASSWORD in .env` error.

A `${VAR:?}` that never fires is decoration. Confirm it does.

- [ ] **Step 5: Commit**

```bash
git add compose.yaml backend/src/triviador/cli.py
git commit -m "feat(infra): base compose stack"
```

---

## Task 6: `compose.dev.yaml` — the development overlay

**Files:**
- Create: `compose.dev.yaml`

**Interfaces:**
- Consumes: `compose.yaml` (Task 5).
- Produces: `backend` (reloadable) and `frontend` (Vite) services.

**The anonymous volumes are load-bearing.** §10.1: without them the host bind mount
shadows the container's `.venv` and `node_modules`, and both fail in ways that read
as dependency bugs. Do not remove them to "simplify".

File watching works because the repository sits on ext4 under `/home`; `/mnt/c` is
where inotify breaks (§10.12). Do not add polling.

- [ ] **Step 1: Write `compose.dev.yaml`**

```yaml
# Development overlay. The single origin here is Vite on :5173, not Caddy.
# Ports bind 127.0.0.1 — a dev stack is not a LAN service.
services:
  backend:
    build:
      context: .
      dockerfile: infra/backend.Dockerfile
    restart: unless-stopped
    depends_on:
      migrate:
        condition: service_completed_successfully
      garage-init:
        condition: service_completed_successfully
    env_file: .env
    environment:
      TZ: UTC
    volumes:
      - ./backend:/app
      # Anonymous volume OVER the bind mount: without it the host's
      # backend/.venv shadows the image's, and every import fails in a way
      # that reads as a dependency bug.
      - /app/.venv
      - ./data/maps:/data/maps:ro
    ports:
      - "127.0.0.1:8000:8000"
    # Longer than the database statement timeout (§10.12): a shorter grace
    # kills the container mid-COMMIT and manufactures the ambiguous-commit
    # case §5.6 exists to avoid.
    stop_grace_period: 30s
    command: ["uv", "run", "fastapi", "dev", "--host", "0.0.0.0", "--port", "8000"]

  frontend:
    image: node:22-alpine
    restart: unless-stopped
    working_dir: /app
    depends_on:
      - backend
    volumes:
      - ./frontend:/app
      # Same reasoning as .venv above.
      - /app/node_modules
      - ./contracts:/contracts:ro
      # §10.1: svg_url is served as a static asset in both environments and
      # never through the backend.
      - ./data/maps:/app/public/maps:ro
    environment:
      TZ: UTC
      # Read by vite.config.ts (Task 7) so the same file works on the host
      # and in the compose network.
      VITE_API_TARGET: http://backend:8000
      VITE_MEDIA_TARGET: http://garage:3902
      VITE_MEDIA_HOST: triviador-media.web.garage.internal
    ports:
      - "127.0.0.1:5173:5173"
    command: ["sh", "-c", "corepack enable && pnpm install && pnpm dev --host"]
```

- [ ] **Step 2: Validate the overlay**

Run: `docker compose -f compose.yaml -f compose.dev.yaml config >/dev/null`
Expected: exits 0.

- [ ] **Step 3: Commit**

```bash
git add compose.dev.yaml
git commit -m "feat(infra): development overlay"
```

---

## Task 7: The Vite dev proxy must reach Garage, not the backend

**Files:**
- Modify: `frontend/vite.config.ts:124-127` (the `server.proxy` block)
- Test: `frontend/vite.config.test.ts` (create)

**Interfaces:**
- Consumes: `VITE_API_TARGET`, `VITE_MEDIA_TARGET`, `VITE_MEDIA_HOST` (Task 6).

**This is a live bug, not new work.** The current config proxies `/media` to
`http://127.0.0.1:8000` — the backend. But media never goes through the backend (that
is the whole point of §9.1 and §10.1), so **every question image 404s in development
today**. §10.1 states the rule: `/media/*` proxies to Garage's web endpoint **with the
bucket Host header** — "without that last rule every question image 404s in
development."

Note the existing comment explaining why `changeOrigin` stays `false` for `/api` and
`/ws`: the browser's `Origin` must arrive unmodified because §6.4 checks it. That
reasoning does **not** extend to `/media` — Garage needs a rewritten `Host` to resolve
a bucket, and Garage performs no origin check. Keep `changeOrigin: false` for `/api`
and `/ws`; set the `Host` header explicitly for `/media`.

- [ ] **Step 1: Write the failing test**

`frontend/vite.config.test.ts`:

```ts
import { describe, expect, it } from "vitest";

// Importing the config module executes defineConfig, so the proxy table is
// inspectable without starting a server.
import config from "./vite.config";

function proxy() {
  const resolved = typeof config === "function" ? config({ command: "serve", mode: "development" }) : config;
  const table = (resolved as { server?: { proxy?: Record<string, unknown> } }).server?.proxy;
  if (!table) throw new Error("no server.proxy in vite config");
  return table;
}

describe("the dev proxy", () => {
  it("sends /media to Garage's web endpoint, never to the API", () => {
    const media = proxy()["/media"] as { target: string };
    // The bug this test exists to prevent: /media pointed at :8000, where
    // nothing serves media, so every question image 404d in development.
    expect(media.target).not.toContain("8000");
    expect(media.target).toBe("http://garage:3902");
  });

  it("rewrites Host for /media so Garage can resolve the bucket", () => {
    // Garage resolves a bucket from the Host header against
    // root_domain = ".web.garage.internal". Forwarding the browser's Host
    // (localhost:5173) means no bucket matches and every image 404s.
    const media = proxy()["/media"] as { headers?: Record<string, string> };
    expect(media.headers?.Host).toBe("triviador-media.web.garage.internal");
  });

  it("leaves the browser's Origin intact for /api and /ws", () => {
    // §6.4 checks Origin exactly; rewriting it would make development pass
    // a check production performs differently.
    const table = proxy();
    expect((table["/api"] as { changeOrigin: boolean }).changeOrigin).toBe(false);
    expect((table["/ws"] as { changeOrigin: boolean }).changeOrigin).toBe(false);
  });
});
```

- [ ] **Step 2: Run it and watch it fail**

Run: `cd frontend && pnpm vitest run vite.config.test.ts`
Expected: FAIL — the first test reports the target still contains `8000`.

Confirm it fails for that reason, not on an import error.

- [ ] **Step 3: Fix the proxy**

Replace the `server.proxy` block:

```ts
  server: {
    port: 5173,
    proxy: {
      // `changeOrigin` stays false on purpose: the browser's Origin header
      // must arrive at the backend as `http://localhost:5173`, which is what
      // `TRIVIADOR_ALLOWED_ORIGINS` has to contain and what the socket
      // handshake checks (§6.4). Rewriting it would make development pass a
      // check that production performs differently.
      "/api": { target: API_TARGET, changeOrigin: false },
      "/ws": { target: API_TARGET.replace(/^http/, "ws"), ws: true, changeOrigin: false },
      // NOT the backend. Media never passes through the API — that is §9.1's
      // whole point, and pointing this at :8000 (as it once did) 404s every
      // question image in development.
      //
      // Garage resolves a bucket from the Host header against
      // `root_domain = ".web.garage.internal"`, so the browser's own Host
      // (`localhost:5173`) matches no bucket. §10.2's Caddy config sets the
      // identical header for the same reason; this is the dev half of that
      // rule.
      "/media": {
        target: MEDIA_TARGET,
        changeOrigin: false,
        headers: { Host: MEDIA_HOST },
      },
    },
  },
```

with, near the top of the file:

```ts
// Defaults are the host-side ports (`pnpm dev` outside compose); the compose
// dev overlay overrides all three with service names.
const API_TARGET = process.env.VITE_API_TARGET ?? "http://127.0.0.1:8000";
const MEDIA_TARGET = process.env.VITE_MEDIA_TARGET ?? "http://127.0.0.1:3902";
const MEDIA_HOST = process.env.VITE_MEDIA_HOST ?? "triviador-media.web.garage.internal";
```

The test asserts the compose-network values, so run it with those set, or make the
test set them before importing. Choose one and be consistent — do not weaken the
assertions to accommodate whichever default happens to load.

- [ ] **Step 4: Run the test**

Run: `cd frontend && pnpm vitest run vite.config.test.ts`
Expected: PASS, 3 tests.

- [ ] **Step 5: Run the full gate**

Run: `cd frontend && pnpm check && pnpm test && pnpm check:bundle && pnpm codegen:check`
Expected: all clean.

- [ ] **Step 6: Commit**

```bash
git add frontend/vite.config.ts frontend/vite.config.test.ts
git commit -m "fix(frontend): /media must proxy to Garage, not the API"
```

---

## Task 8: Caddy and `compose.prod.yaml`

**Files:**
- Create: `infra/caddy/Caddyfile`, `compose.prod.yaml`

**Interfaces:**
- Consumes: `compose.yaml` (Task 5), both images (Tasks 2, 3).
- Produces: the single published origin, `0.0.0.0:80:80`.

**Two mutually exclusive `handle` blocks, not `header` matchers in one.** §10.2 is
explicit about why: a single block makes correctness depend on whether Caddy evaluates
`header` before or after `try_files` rewrites the URI — and if after, every deep link
is served as `/index.html` and silently inherits whichever policy matched that name.
Two blocks are order-independent.

`handle_path` strips the prefix. The `header_up` is **mandatory** — without it Caddy
forwards the client's `Host` (a bare LAN address) and Garage cannot resolve a bucket.
Proxying to `:3900` instead would demand SigV4 on every image.

This assumes Vite's default `build.assetsDir = "assets"`. Confirm that is still the
case before relying on the matcher.

- [ ] **Step 1: Write `infra/caddy/Caddyfile`**

```caddy
# The single published origin (§10.2). Everything else in the stack is
# reachable only over the compose network.
{
	admin off
}

:80 {
	encode gzip zstd

	handle_path /media/* {
		# header_up is MANDATORY: without it Caddy forwards the client's
		# Host (a bare LAN address) and Garage cannot resolve a bucket.
		# Proxying to :3900 instead would demand SigV4 on every image.
		reverse_proxy garage:3902 {
			header_up Host triviador-media.web.garage.internal
		}
	}

	handle_path /maps/* {
		root * /srv/maps
		file_server
	}

	handle /api/* {
		reverse_proxy backend:8000
	}

	handle /ws {
		reverse_proxy backend:8000
	}

	# Two mutually exclusive handle blocks, matched on the incoming path,
	# rather than header matchers inside one. A single block would make
	# correctness depend on whether Caddy evaluates `header` before or after
	# try_files rewrites the URI — and if after, every deep link is served
	# as /index.html and silently inherits whichever policy matched that
	# name. Assumes Vite's default build.assetsDir = "assets".
	handle /assets/* {
		root * /srv
		header Cache-Control "public, max-age=31536000, immutable"
		file_server
	}

	handle {
		root * /srv
		header Cache-Control "no-store"
		try_files {path} /index.html
		file_server
	}
}
```

**`admin off`** is added beyond the spec's snippet: Caddy's admin API binds
`localhost:2019` by default, and §10.11's principle is that no control plane is
reachable. Note it in the report as a deliberate addition.

- [ ] **Step 2: Write `compose.prod.yaml`**

```yaml
# Production overlay. Caddy is the only published port in the entire stack.
services:
  backend:
    build:
      context: .
      dockerfile: infra/backend.Dockerfile
    restart: unless-stopped
    depends_on:
      migrate:
        condition: service_completed_successfully
      garage-init:
        condition: service_completed_successfully
    env_file: .env
    environment:
      TZ: UTC
    volumes:
      - ./data/maps:/data/maps:ro
    # NOT published. Caddy reaches it over the compose network.
    expose:
      - "8000"
    stop_grace_period: 30s
    healthcheck:
      test: ["CMD-SHELL", "python -c \"import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health/ready').status==200 else 1)\""]
      interval: 10s
      timeout: 5s
      retries: 12
      start_period: 30s

  frontend-build:
    build:
      context: .
      dockerfile: infra/frontend.Dockerfile
      target: build
    # Build-only: produces the static output Caddy serves, then exits.
    restart: "no"
    command: ["true"]
    volumes:
      - web:/out
    entrypoint: ["sh", "-c", "cp -r /app/dist/. /out/ && echo frontend-build: ok"]

  caddy:
    image: caddy:2.8-alpine
    restart: unless-stopped
    depends_on:
      backend:
        condition: service_healthy
      frontend-build:
        condition: service_completed_successfully
    ports:
      # The ONLY published port in the stack (§10.11). Bind to the reserved
      # LAN address instead of 0.0.0.0 where practical, and allow inbound 80
      # only from the intended subnet.
      - "0.0.0.0:80:80"
    volumes:
      - ./infra/caddy/Caddyfile:/etc/caddy/Caddyfile:ro
      - web:/srv
      - ./data/maps:/srv/maps:ro
      - caddy_data:/data
      - caddy_config:/config
    environment:
      TZ: UTC

volumes:
  web:
  caddy_data:
  caddy_config:
```

- [ ] **Step 3: Validate both overlays**

Run:
```bash
docker compose -f compose.yaml -f compose.prod.yaml config >/dev/null
docker compose -f compose.yaml -f compose.dev.yaml config >/dev/null
```
Expected: both exit 0.

- [ ] **Step 4: Validate the Caddyfile**

Run: `docker run --rm -v "$PWD/infra/caddy/Caddyfile:/etc/caddy/Caddyfile:ro" caddy:2.8-alpine caddy validate --config /etc/caddy/Caddyfile`
Expected: `Valid configuration`.

- [ ] **Step 5: Assert nothing but Caddy is published**

Write `infra/assert-ports.sh`, and run it:

```sh
#!/bin/sh
# §10.11: production publishes 0.0.0.0:80:80 and NOTHING else. Garage's
# admin listener (3903) is the sharpest of these — an unauthenticated
# control plane that would let any LAN device turn the private staging
# bucket into a website.
set -eu
published="$(docker compose -f compose.yaml -f compose.prod.yaml config \
  | grep -E '^\s+- (published|target):' -A0 || true)"
echo "$published"
# Fail if any published port other than 80 appears.
if docker compose -f compose.yaml -f compose.prod.yaml config --format json \
  | grep -oE '"published":\s*"?[0-9]+"?' | grep -vE '"?80"?$' ; then
  echo "FATAL: a service other than caddy:80 publishes a port" >&2
  exit 1
fi
echo "assert-ports: ok — only caddy:80 is published"
```

**Prove this check can fail**: temporarily add `ports: ["127.0.0.1:5432:5432"]` to
`db` in the prod overlay, run the script, confirm it exits non-zero, then revert.
Paste both outputs. Adjust the JSON parsing if the installed Compose emits a different
shape — the requirement is a check that actually fails, not this exact grep.

- [ ] **Step 6: Commit**

```bash
git add infra/caddy/Caddyfile compose.prod.yaml infra/assert-ports.sh
git commit -m "feat(infra): caddy and the production overlay"
```

---

## Task 9: Readiness reports the Garage assertion

**Files:**
- Modify: `backend/src/triviador/api/http/health.py`
- Modify: whichever module owns the startup sequence and `deps.readiness`
  (find it: `grep -rn "recovery_complete" backend/src/triviador`)
- Test: `backend/tests/api/test_health.py`

**Interfaces:**
- Consumes: the existing `ReadinessReport` and `deps.readiness` record.
- Produces: `ReadinessReport.garage_ready: bool`.

§10.6 requires readiness to cover "database reachable · migrations current · startup
recovery complete · **Garage initialization verified**". The current
`ReadinessReport` has the first three and not the fourth.

**Report the recorded startup result, do not probe on every poll.** §10.6 is explicit:
readiness reports the *result* of the startup Garage assertion. A probe on every poll
turns a Garage blip into a backend that removes itself from rotation.

- [ ] **Step 1: Write the failing test**

```python
async def test_readiness_reports_garage_initialisation(client, readiness):
    """§10.6's fourth condition. The backend verifies at startup that its
    buckets exist and that the staging bucket is not website-enabled; a
    deploy where garage-init silently did not run must not report ready."""
    readiness.garage_ready = False

    response = await client.get("/api/health/ready")

    assert response.status_code == 503
    assert response.json()["garage_ready"] is False


async def test_readiness_does_not_probe_garage_per_poll(client, readiness, garage_calls):
    """Reporting the recorded startup result, not re-probing: a probe on
    every poll turns a Garage blip into a backend that takes itself out of
    rotation, which is the failure §10.6 explicitly rejects."""
    readiness.garage_ready = True
    before = garage_calls.count

    await client.get("/api/health/ready")

    assert garage_calls.count == before
```

Adapt the fixture names to the ones `backend/tests/api/conftest.py` actually provides;
`garage_calls` may need adding as a counting fake. Do not invent a fixture that does
not exist.

- [ ] **Step 2: Run it and watch it fail**

Run: `cd backend && uv run pytest tests/api/test_health.py -v`
Expected: FAIL — `garage_ready` is not a field.

- [ ] **Step 3: Add the field and the startup assertion**

Add `garage_ready: bool` to the `Readiness` record and to `ReadinessReport`, set it
during startup after verifying both buckets exist and the staging bucket is not
website-enabled, and include it in the 503 condition alongside the existing three.

Keep the existing comment's reasoning intact: `database` stays *probed* (a flag set at
startup reports a database that died an hour ago as reachable), while `garage_ready`
is *remembered*, for the opposite reason. Both behaviours are deliberate and the
comment should say why they differ.

- [ ] **Step 4: Run the tests**

Run: `cd backend && uv run pytest tests/api/test_health.py -v`
Expected: PASS.

- [ ] **Step 5: Full backend gate**

Run: `cd backend && uv run ruff check . && uv run mypy --strict src && uv run pytest`
Expected: all clean. Check the real exit code — `pytest | tail` masks it.

- [ ] **Step 6: Commit**

```bash
git add backend/src/triviador backend/tests/api/test_health.py
git commit -m "feat(api): readiness reports the startup Garage assertion"
```

---

## Task 10: The deploy command

**Files:**
- Create: `infra/deploy.sh`, `infra/render-secrets.sh`

**Interfaces:**
- Consumes: everything above.
- Produces: the one supported way to deploy.

§10.11: **Compose does not reliably re-run a completed one-shot container just because
the code inside it changed**, so a deploy that only does `up -d` can silently skip a
migration. The supported sequence is explicit:

```
docker compose run --rm garage-init
docker compose run --rm migrate
docker compose up -d --remove-orphans
```

- [ ] **Step 1: Write `infra/render-secrets.sh`**

```sh
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
```

- [ ] **Step 2: Write `infra/deploy.sh`**

```sh
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
```

- [ ] **Step 3: Make both executable and shellcheck them**

```bash
chmod +x infra/deploy.sh infra/render-secrets.sh infra/garage/init.sh infra/assert-ports.sh
docker run --rm -v "$PWD:/mnt" koalaman/shellcheck:stable /mnt/infra/*.sh /mnt/infra/garage/init.sh
```
Expected: no errors. Fix anything it reports.

- [ ] **Step 4: Prove the placeholder guard fires**

Run:
```bash
cp .env.example .env
./infra/render-secrets.sh
```
Expected: exits non-zero with "still holds its placeholder".

This is the assertion that stops a deploy running with a published secret. Confirm it
fires rather than assuming it does.

- [ ] **Step 5: Commit**

```bash
git add infra/deploy.sh infra/render-secrets.sh
git commit -m "feat(infra): the deploy command"
```

---

## Task 11: Backups and the restore drill

**Files:**
- Create: `infra/backup.sh`, `infra/restore-drill.md`
- Modify: `compose.prod.yaml` (a `backup` service under a profile)

**Interfaces:**
- Consumes: the running prod stack.
- Produces: `backups/db/<ts>.dump` and an append-only `backups/media/`.

§10.8's sequence, all inside one `flock`:

```
flock /var/lock/triviador-media.lock          ← media-gc binds the identical host path
  1. pg_dump -Fc                        → backups/db/<ts>.dump
  2. rclone copy garage:triviador-media → backups/media/     (append-only)
  3. verify: pg_restore --list on the dump succeeds
             rclone check garage:triviador-media backups/media/ --one-way
  4. retention: 7 daily, 4 weekly dumps
```

**Three details that are load-bearing and easy to get wrong:**

1. **`copy`, not `sync`.** A `sync` maintains one mutable mirror, so a retained weekly
   dump can reference an asset a later run deleted. Object keys are content-addressed,
   so `copy` deduplicates naturally and never removes an asset an older dump needs.
2. **The backup service and `media-gc` must bind the *same host lock path*,** so their
   `flock` calls resolve to the same inode. Two container-local paths do not exclude
   each other, and `media-gc` would delete blobs a running backup still needs.
3. **`pg_dump` runs first,** so media is always a *superset* of what the snapshot
   references. Verification asserts coverage, not equal object counts.

`rclone check --one-way` is the verification, not a per-object manifest walk: nothing
generates such a manifest and `pg_dump -Fc` does not contain one. It runs inside the
same `flock` so `media-gc` cannot delete an object between the copy and the check and
turn a healthy backup into a spurious failure.

- [ ] **Step 1: Confirm where `media-gc` takes its lock**

Run: `grep -rn "flock\|lock" backend/src/triviador/imports/retire.py backend/src/triviador/cli.py | head`

If `media-gc` does not currently take a `flock` at all, **add it** in this task, at the
host path `/var/lock/triviador-media.lock`, and say so — otherwise the exclusion this
whole section depends on does not exist. The two must agree on the path; write the
path as a constant used by both.

- [ ] **Step 2: Write `infra/backup.sh`**

```sh
#!/bin/sh
# §10.8. Everything below runs inside ONE flock, shared with media-gc via
# an identical HOST path — two container-local paths would resolve to
# different inodes and would not exclude each other.
set -eu

LOCK=/var/lock/triviador-media.lock
DEST="${BACKUP_DEST:?set BACKUP_DEST — a Windows disk, external drive or NAS, NEVER inside the WSL vhdx}"
TS="$(date -u +%Y%m%dT%H%M%SZ)"

exec 9>"$LOCK"
flock 9

mkdir -p "$DEST/db" "$DEST/media"

# 1. Database first, so media is always a SUPERSET of what this snapshot
#    references. Verification therefore asserts coverage, not equality.
pg_dump -Fc -h db -U triviador triviador > "$DEST/db/$TS.dump"

# 2. copy, NOT sync: a sync maintains one mutable mirror, so a retained
#    weekly dump can reference an asset a later run deleted. Keys are
#    content-addressed, so copy deduplicates naturally.
rclone copy garage:"$S3_MEDIA_BUCKET" "$DEST/media/"

# 3. Verify both halves, still inside the flock.
pg_restore --list "$DEST/db/$TS.dump" > /dev/null
rclone check garage:"$S3_MEDIA_BUCKET" "$DEST/media/" --one-way

# 4. Retention: 7 daily, 4 weekly. Media is append-only and never pruned —
#    an old dump may still reference an object no live row does.
ls -1t "$DEST"/db/*.dump 2>/dev/null | tail -n +8 \
  | grep -v 'T0[0-9]:.*-W' || true

echo "backup: ok $TS"
```

**Finish the retention logic properly** — the `ls | tail` sketch above keeps the 7
most recent and does not implement the weekly tier. Implement "7 daily, 4 weekly"
explicitly (for example: keep every dump from the last 7 days, plus the newest dump in
each of the last 4 ISO weeks, delete the rest), and write a test fixture directory of
timestamped filenames proving the right ones survive. A retention rule that silently
deletes the wrong file is worse than none.

- [ ] **Step 3: Add the `backup` service under a profile**

In `compose.prod.yaml`, so it never starts with `up -d`:

```yaml
  backup:
    profiles: ["backup"]
    image: triviador-backup
    build:
      context: .
      dockerfile: infra/backup.Dockerfile
    restart: "no"
    depends_on:
      - db
      - garage
    env_file: .env
    volumes:
      # The SAME host path media-gc binds — see infra/backup.sh.
      - /var/lock/triviador-media.lock:/var/lock/triviador-media.lock
      - ${BACKUP_DEST:?}:/backups
    environment:
      BACKUP_DEST: /backups
```

Write `infra/backup.Dockerfile` (alpine + `postgresql17-client` + `rclone` + the
script), and an `rclone` config pointing `garage:` at `http://garage:3900` with
**path-style addressing** — virtual-host style needs per-bucket DNS that does not
exist inside the compose network.

- [ ] **Step 4: Prove the lock actually excludes**

```bash
# Hold the lock, then try to back up; the second must block, not proceed.
flock /var/lock/triviador-media.lock -c 'sleep 10' &
sleep 1
timeout 3 docker compose -f compose.yaml -f compose.prod.yaml --profile backup run --rm backup || echo "blocked as expected"
```

Expected: the backup does not complete within the timeout. If it completes, the two
`flock` calls are not resolving to the same inode and the whole section is decorative
— fix it before continuing.

- [ ] **Step 5: Write `infra/restore-drill.md`**

Write §10.9's seven steps as a runnable procedure:

```
1. start fresh db + garage
2. run garage-init
3. restore the database (pg_restore)
4. restore media, re-applying Content-Type and Cache-Control object metadata
5. run migrations required by the current application
6. expire every non-confirmed import (§9.3) — their staged objects were not backed up
7. start backend and caddy
```

Then the three verifications that cover all three failure surfaces: **a finished game
replays** from the log, **an active game with a persisted deadline** resumes and
expires at its original absolute time, and **a question image loads** through
Caddy → Garage.

State plainly that staging imports are deliberately not backed up — an unfinished
import simply becomes expired after a restore.

The document must say it has been exercised, with the date, once someone does. Do not
pre-write that line.

- [ ] **Step 6: Commit**

```bash
git add infra/backup.sh infra/backup.Dockerfile infra/restore-drill.md compose.prod.yaml backend/src/triviador
git commit -m "feat(infra): backups and the restore drill"
```

---

## Task 12: CI

**Files:**
- Create: `.github/workflows/ci.yml`

There is no CI in this repository today. §10.7 specifies seven jobs:

| Job | Gate |
|---|---|
| `backend` | `ruff check` · `ruff format --check` · `mypy --strict` on `domain`+`services` · `pytest` against a Postgres service container · **100 % branch on `domain/game/reducer.py`** · golden event corpus |
| `frontend` | `biome` · `steiger` · `tsc --noEmit` · `vitest` |
| `contracts` | `export-contracts` + `pnpm codegen` → `git diff --exit-code` |
| `maps` | validator over `data/maps/*` — `map.json` topology **and** the `map.svg` whitelist |
| `migrations` | `alembic check` clean · `upgrade head` succeeds from an empty database |
| `compose` | `docker compose config` validates for both the dev and prod overlays |
| `e2e` | prod compose up; one Playwright scenario (Task 13) |

- [ ] **Step 1: Find the existing local equivalents**

Every gate above already exists as a local command. Find each before writing the
workflow, so CI runs the same thing developers do rather than a second, drifting
definition:

```bash
grep -n '"scripts"' -A 20 frontend/package.json
grep -rn "export-contracts" backend/src backend/pyproject.toml | head
ls backend/tests/domain | head
grep -rn "map.json\|svg" backend/src/triviador/maps/*.py | head
```

- [ ] **Step 2: Write the workflow**

Jobs run in parallel except `e2e`, which needs images. Pin action versions. Use the
same Postgres image as `docker-compose.test.yml` (`postgres:17-alpine`) and the same
Garage tag (`dxflrs/garage:v1.1.0`) — CI drifting from local is how a green build
ships a broken deploy.

The `compose` job is one command and must cover **both** overlays:

```yaml
      - run: cp .env.example .env
      - run: docker compose -f compose.yaml -f compose.dev.yaml config >/dev/null
      - run: docker compose -f compose.yaml -f compose.prod.yaml config >/dev/null
```

The `contracts` job is the one that catches silent drift:

```yaml
      - run: cd backend && uv run triviador export-contracts
      - run: cd frontend && pnpm codegen
      - run: git diff --exit-code
```

- [ ] **Step 3: Verify each job's command locally first**

Run every job's command sequence on your machine before pushing. A workflow whose
commands were never run is a workflow that fails on the first push for reasons that
have nothing to do with the code.

- [ ] **Step 4: Commit**

```bash
git add .github/workflows/ci.yml
git commit -m "ci: the seven gates"
```

---

## Task 13: The one end-to-end scenario

**Files:**
- Create: `e2e/package.json`, `e2e/playwright.config.ts`, `e2e/smoke.spec.ts`,
  `e2e/seed/…`
- Modify: `.github/workflows/ci.yml` (enable the `e2e` job)

Spec 1 §12.4: **exactly one** Playwright scenario. Three browser contexts, invite
redemption, create → join → start, a full match on a shortened preset (exp 1 /
battle 1) to `FINISHED`. **Not a suite** — one smoke test proving the seams line up.

**The seed must include at least one media question** (§10.7), so the run exercises
Caddy → Garage delivery rather than only the API.

- [ ] **Step 1: Write the seed**

A fixture that creates: an admin, an invite code per player, a category, a shortened
preset (`expansion_rounds: 1`, `battle_rounds: 1`), and enough questions to cover the
match — **including one with an image**, uploaded through the real media path so it
lands in Garage.

Prefer driving the existing admin API over inserting rows directly: a seed that
bypasses the application can set up a state the application cannot produce, and then
the smoke test proves nothing about the seams.

- [ ] **Step 2: Write the scenario**

```ts
test("three players redeem, play a shortened match, and reach FINISHED", async ({ browser }) => {
  // Three independent contexts — not three tabs. Sessions are cookie-based
  // and host-only; sharing a context would share a session and the test
  // would silently exercise one player three times.
  const contexts = await Promise.all([browser.newContext(), browser.newContext(), browser.newContext()]);
  // … redeem → create → join → start → play to FINISHED
  // Assert the media question's image actually loads: a 200 through
  // Caddy → Garage, which is the seam no API test covers.
});
```

- [ ] **Step 3: Run it against the prod compose stack**

```bash
./infra/deploy.sh
cd e2e && pnpm install && pnpm exec playwright test
```
Expected: PASS.

- [ ] **Step 4: Prove the media assertion is load-bearing**

Temporarily point the Caddyfile's `/media` block at the backend instead of Garage,
re-run, and confirm the test goes RED on the image assertion. Restore.

An E2E that would pass with media broken does not satisfy §10.7's reason for
requiring a media question.

- [ ] **Step 5: Enable the CI job and commit**

```bash
git add e2e .github/workflows/ci.yml
git commit -m "test(e2e): one smoke scenario through the production stack"
```

---

## What this plan deliberately does not do

- **No TLS, no secret manager.** §10.11 states plain HTTP is a trust assumption for a
  trusted LAN. If untrusted devices ever share the network the answer is Caddy's
  internal TLS or an overlay network — not a cookie-flag tweak, and not this plan.
- **No multi-node Garage, no replication.** `replication_factor = 1` is the spec's
  choice; backups are the redundancy.
- **No `docker compose up` as a deploy path.** §10.11's three-command wrapper is the
  only supported deploy, because `up -d` can silently skip a one-shot.
- **No systemd timer for backups.** §10.12: systemd services do not keep a WSL
  instance alive, so scheduling is a Windows scheduled task. This plan ships the
  script; wiring the Windows task is an operator step documented in the drill.
- **No second E2E.** §12.4 says exactly one. Resist growing a suite.
- **No Spec 2 anything** — no spectating, match history, or analytics.

## Self-review

**Spec coverage.** §10.1 compose → Tasks 5, 6, 8. §10.2 Caddy → Task 8. §10.3 Garage →
Task 4. §10.4 config → Task 1 (the settings module already exists). §10.5 startup order
→ Tasks 5 (`migrate` one-shot) and 9. §10.6 health → Task 9. §10.7 CI → Task 12.
§10.8 backups → Task 11. §10.9 restore drill → Task 11. §10.10 logging → **already
implemented** (`structlog` is in the running app); the redaction test it requires is
worth confirming exists — check `grep -rn "redact" backend/tests` during Task 12 and
add it to CI if present, or raise it as a finding if not. §10.11 exposure/lifecycle →
Tasks 8 (`assert-ports.sh`, `stop_grace_period`) and 10. §10.12 WSL → documented
constraints in Task 11's drill and the Global Constraints above. §12.4 E2E → Task 13.

**Two things a reviewer should push on before execution starts.**

1. **Task 4's init-image decision.** The spec says `garage-init` is a Compose service,
   but the Garage image has no shell, so this plan builds a small alpine image with the
   binary copied from the pinned tag. If that copy is fragile across Garage versions,
   the alternative is driving the admin API — say so before Task 4, not after Task 13
   depends on it.
2. **`data/maps` is mounted in three places** (backend read-only, frontend
   `public/maps`, Caddy `/srv/maps`). That is three chances for one path to drift.
   Consider whether a single named volume populated once is better than three bind
   mounts of the same host directory — cheap to change now, annoying later.
