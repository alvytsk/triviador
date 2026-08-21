# syntax=docker/dockerfile:1
FROM node:22-alpine AS build
# corepack ships with node:22 and pins pnpm from packageManager in package.json,
# so the image cannot drift from what developers run locally.
RUN corepack enable
# pnpm 11 runs a non-interactive "deps status check" before lifecycle scripts; if it
# thinks node_modules needs to be purged and reinstalled it normally asks for a TTY
# confirmation, which a Docker build never has. CI=true is pnpm's own documented way
# to make it proceed unattended instead of failing with ERR_PNPM_ABORTED_REMOVE_MODULES_DIR_NO_TTY.
ENV CI=true
WORKDIR /app

COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN --mount=type=cache,target=/pnpm-store \
    pnpm config set store-dir /pnpm-store && pnpm install --frozen-lockfile

COPY frontend/ ./
# No `contracts/` copy: `pnpm build` is `tsc --noEmit && vite build`, which
# does not run codegen, and `src/shared/api/generated` is committed. CI's
# `contracts` job (Task 12) is what catches drift between the two.
RUN pnpm build

# A scratch stage holding only the artifacts. `docker build --target dist
# --output` extracts them without running a container.
FROM scratch AS dist
COPY --from=build /app/dist /dist
